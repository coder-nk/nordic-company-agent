"""Website source module: find the company's own website, prove it is this exact entity, publish its facts.

`async def run(ctx, company)` (per-company module, DESIGN.md "Website identity gate"):

leads, in order
  1. declared discovery cache `site_discovery` (domains where this org number was printed at crawl time;
     re-fetched and re-verified live, never used as evidence),
  2. the registry `hjemmeside`,
  3. Wikidata P856 website, when the wikidata module already ran (L1 only),
  4. live candidates from web/candidates.py, only with `config.live_discovery` and discovery-tier budget:
     DNS-resolved first (no HTTP), at most two tried. Never search engines or directories.
fetch (<= 5 requests per company, robots.txt and redirect hops included)
  home (+ one client-side redirect), then the cached proof page, or contact/about/terms/privacy until the org
  number is found and the contact page is done; finally the RSS/Atom feed of a verified site.
verify  web/verify.py: L1 / L2 publish; ambiguous / not_available / blocked never publish facts.
"""

from __future__ import annotations

import asyncio
import dataclasses
import re
import socket
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from ..envelope import (COMPANY_LINKED_PROFILE, COMPANY_OWNED, COMPANY_SITE, Claim, CompanyResult, Evidence,
                        ModuleOutcome, utc_iso)
from ..http import BROWSER_ACCEPT, BudgetExceeded, DeadlineExceeded, RobotsDisallowed, UnsafeURL
from ..web import candidates as C
from ..web import extract as X
from ..web import verify as V

MODULE = "website"
METHOD = "website@1"
MAX_REQUESTS = 5                 # per company, including robots.txt and redirect hops
MAX_LIVE_CANDIDATES = 2
MAX_DNS_CANDIDATES = 6
DNS_TIMEOUT = 5.0
MAX_NEWS = 10
SUBPAGE_ORDER = ("contact", "about", "terms", "privacy", "impressum")
STATE_RANK = {"available": 0, "ambiguous": 1, "blocked": 2, "not_available": 3, "failed": 4}
FEED_ACCEPT = "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5"
BUDGET_NOTE = "not checked: request budget reserved for higher-priority sources"
FIELD_ORDER = ("official_website", "website_verification", "website_title", "website_description", "brand_name",
               "logo_url", "website_language", "website_address", "contact_email", "contact_phone", "social_profile",
               "careers_page", "news_item")


# --------------------------------------------------------------------------- data
@dataclass
class Lead:
    kind: str                      # registry_website | email_domain | name_* | wikidata_website
    source: str                    # cache | registry | wikidata | live
    start: str                     # URL as filed / candidate home URL (redirect rules compare against it)
    fetch_url: str                 # first URL requested (cache: the home URL seen at crawl time)
    registry: bool                 # the lead is the registry-filed hjemmeside
    proof_url: str | None = None
    proof_page: str | None = None

    def describe(self) -> dict[str, Any]:
        d = {"kind": self.kind, "source": self.source, "start": self.start}
        if self.proof_url:
            d["cached_proof_url"] = self.proof_url
        return d


@dataclass
class LeadResult:
    lead: Lead
    state: str                     # available | ambiguous | blocked | not_available | failed
    reason: str
    verdict: V.Verdict | None = None
    pages: list[V.Page] = field(default_factory=list)


@dataclass
class _State:
    spent: int = 0                 # requests charged to this company (estimate incl. robots/redirects)
    origins: set[str] = field(default_factory=set)
    records: list[dict[str, Any]] = field(default_factory=list)
    budget_hit: bool = False
    analysis: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- entry point
async def run(ctx, company: CompanyResult) -> None:
    try:
        await _run(ctx, company)
    except Exception as exc:  # contract: never raise
        msg = f"internal error: {type(exc).__name__}: {exc}"
        company.error(MODULE, "failed", msg)
        company.outcome(ModuleOutcome(MODULE, "failed", note=msg[:300]))


async def _run(ctx, company: CompanyResult) -> None:
    target = company.organisation_number
    rec = company.data.get("registry") if isinstance(company.data.get("registry"), dict) else {}
    st = _State()
    leads, cache_checked, reg_note = _leads(ctx, company, rec)
    results: list[LeadResult] = []
    tried_sites: set[str] = set()
    for lead in leads:
        key = _site_key(lead.fetch_url)
        if key in tried_sites:
            continue
        if MAX_REQUESTS - st.spent < 2 and not _robots_known(ctx, _origin(lead.fetch_url)):
            results.append(LeadResult(lead, "failed", f"lead {lead.start} not checked: per-company request cap"))
            continue
        tried_sites.add(key)
        r = await _check_lead(ctx, st, lead, rec, target, lead_no=len(results))
        results.append(r)
        if r.state == "available":
            break
    if not any(r.state == "available" for r in results) and rec:
        live = await _live_leads(ctx, st, rec, cache_checked, tried_sites)
        for lead in live:
            if MAX_REQUESTS - st.spent < 2:
                break
            tried_sites.add(_site_key(lead.fetch_url))
            r = await _check_lead(ctx, st, lead, rec, target, lead_no=len(results), tier_primary="discovery",
                                  tier_secondary="discovery")
            results.append(r)
            if r.state == "available":
                break
    best = _choose(results)
    news: list[dict] = []
    feed_ev: tuple[V.Page, str] | None = None
    if best is not None and best.state == "available" and best.verdict is not None:
        news, feed_ev = await _news(ctx, st, best.verdict)
    _emit(ctx, company, rec, st, results, best, news, feed_ev, reg_note)


# --------------------------------------------------------------------------- leads
def _site_key(url: str) -> str:
    sp = urlsplit(url if "://" in url else "https://" + url)
    host = (sp.hostname or "").lower().removeprefix("www.")
    path = sp.path or "/"
    return host + ("" if V.HOMEISH_PATH_RX.match(path) else path.rstrip("/").lower())


def _leads(ctx, company: CompanyResult, rec: dict) -> tuple[list[Lead], set[str], str | None]:
    target = company.organisation_number
    leads: list[Lead] = []
    checked: set[str] = set()
    reg_url = C.normalize_registry_website(rec.get("hjemmeside")) if rec else None
    reg_dom = X.reg_domain(urlsplit(reg_url).hostname or "") if reg_url else None
    try:
        rows = ctx.caches.jsonl_index("site_discovery").get(target, [])
    except Exception:
        rows = []
    for row in rows:
        checked |= set(row.get("checked") or [])
        for ld in row.get("leads") or []:
            start = ld.get("start") or ld.get("final_url")
            if not start:
                continue
            if "://" not in start:
                start = "https://" + start.strip("/") + "/"
            fetch = ld.get("final_url") or start
            kind = ld.get("kind") or "cache"
            is_reg = kind == "registry_website" and bool(reg_dom) and \
                X.reg_domain(urlsplit(start).hostname or "") == reg_dom
            leads.append(Lead(kind=kind, source="cache", start=start, fetch_url=fetch, registry=is_reg,
                              proof_url=ld.get("proof_url"), proof_page=ld.get("proof_page")))
    note = None
    if reg_url:
        leads.append(Lead("registry_website", "registry", reg_url, reg_url, True))
    elif rec and rec.get("hjemmeside"):
        note = f"registry website {rec.get('hjemmeside')!r} is a social-media/directory page, not a company site"
    wd = company.data.get("wikidata") if isinstance(company.data.get("wikidata"), dict) else None
    if wd and isinstance(wd.get("website"), str) and wd["website"].startswith(("http://", "https://")):
        u = wd["website"]
        leads.append(Lead("wikidata_website", "wikidata", u, u, False))
    return leads, checked, note


async def _resolves(host: str) -> bool:
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(loop.getaddrinfo(host, 443, type=socket.SOCK_STREAM), DNS_TIMEOUT)
        return bool(infos)
    except Exception:
        return False


async def _live_leads(ctx, st: _State, rec: dict, checked: set[str], tried: set[str]) -> list[Lead]:
    if not getattr(ctx.config, "live_discovery", False) or MAX_REQUESTS - st.spent < 2:
        return []
    if not ctx.allow("discovery", 3):
        return []
    cands = [(k, d) for k, d in C.candidates(rec) if k != "registry_website" and k not in checked]
    cands = [(k, d) for k, d in cands if _site_key(d) not in tried][:MAX_DNS_CANDIDATES]
    if not cands:
        return []
    hosts: list[tuple[str, str, str]] = []
    for k, d in cands:
        host = (urlsplit(d).hostname if "://" in d else d).lower()
        hosts.append((k, host, "www." + host if not host.startswith("www.") else host))
    checks = await asyncio.gather(*(_resolves(h) for _, h, _ in hosts), *(_resolves(w) for _, _, w in hosts))
    n = len(hosts)
    out: list[Lead] = []
    for j, (k, h, w) in enumerate(hosts):
        ok_host = h if checks[j] else (w if checks[n + j] else None)
        if ok_host:
            u = f"https://{ok_host}/"
            out.append(Lead(k, "live", u, u, False))
        if len(out) >= MAX_LIVE_CANDIDATES:
            break
    return out


# --------------------------------------------------------------------------- fetching
def _origin(url: str) -> str:
    sp = urlsplit(url)
    return f"{sp.scheme}://{sp.netloc}"


def _robots_keys(ctx) -> set[str] | None:
    r = getattr(getattr(ctx, "client", None), "_robots", None)
    return set(r.keys()) if isinstance(r, dict) else None


def _robots_known(ctx, origin: str) -> bool:
    keys = _robots_keys(ctx)
    return keys is not None and origin in keys


async def _get(ctx, st: _State, url: str, *, tier: str, accept: str = BROWSER_ACCEPT, max_redirects: int = 1):
    """Budgeted GET of a company-controlled URL. Returns (FetchResult | None, error_code | None, message)."""
    origin = _origin(url)
    known_before = _robots_keys(ctx)
    cost = 1 + (0 if (known_before is not None and origin in known_before) else 1)
    if st.spent + cost > MAX_REQUESTS:
        st.budget_hit = True
        return None, "budget", "per-company website request cap reached"
    if not ctx.allow(tier, cost):
        st.budget_hit = True
        return None, "budget", BUDGET_NOTE
    res = None
    err: tuple[str, str] | None = None
    try:
        # Redirect hops (and robots.txt of every new origin) are counted by the client; keep chains short
        # once part of the per-company allowance is used.
        res = await ctx.get(url, tier=tier, source=MODULE, headers={"Accept": accept}, public_only=True,
                            respect_robots=True, max_bytes=2_000_000, retries=0,
                            max_redirects=max_redirects)
    except RobotsDisallowed:
        err = ("robots", f"robots.txt disallows {url} (or robots.txt was unreachable)")
    except UnsafeURL as exc:
        err = ("unsafe", f"not a public web address ({exc})")
    except (BudgetExceeded, DeadlineExceeded) as exc:
        st.budget_hit = True
        err = ("budget", f"not checked: {exc}")
    except httpx.ConnectError as exc:
        msg = str(exc)
        err = ("dns", f"domain does not resolve ({urlsplit(url).hostname})") if "DNS resolution failed" in msg \
            else ("connect", f"connection/TLS error at {url}: {msg[:120]}")
    except httpx.TimeoutException:
        err = ("timeout", f"timed out fetching {url}")
    except httpx.HTTPError as exc:
        err = ("error", f"{type(exc).__name__} fetching {url}")
    hops = (list(res.redirects) + [res.final_url]) if res is not None else [url]
    hop_origins = {_origin(h) for h in hops}
    page_reqs = len(hops) if res is not None else (1 if err and err[0] in ("connect", "timeout", "error") else 0)
    known_after = _robots_keys(ctx)
    if known_before is not None and known_after is not None:
        robots = len((known_after - known_before) & (hop_origins | {origin}))
    else:
        robots = 0 if (err and err[0] in ("dns", "unsafe", "budget")) else len(hop_origins - st.origins)
    st.spent += page_reqs + robots
    st.origins |= hop_origins
    if res is None:
        return None, err[0] if err else "error", err[1] if err else "unknown error"
    return res, None, ""


def _to_page(ctx, kind: str, res) -> V.Page:
    retrieved_at = utc_iso(res.retrieved_at)
    sha = res.sha256
    try:
        sha = ctx.snapshots.save_result(res, retrieved_at)
    except Exception:
        pass
    ctype = res.headers.get("content-type", "")
    body = res.content[:3000].lstrip().lower()
    looks_html = "html" in ctype.lower() or body.startswith((b"<!doctype html", b"<html", b"<head", b"<!--"))
    html = res.text if (looks_html or not ctype) else ""
    return V.Page(kind=kind, url=res.url, final_url=res.final_url, status=res.status, html=html, sha256=sha,
                  retrieved_at=retrieved_at, redirects=list(res.redirects), content_type=ctype)


def _record(st: _State, lead_no: int, page: V.Page | None = None, *, kind: str = "", url: str = "",
            error: str | None = None) -> None:
    if page is not None:
        st.records.append({"lead": lead_no, "kind": page.kind, "url": page.url, "final_url": page.final_url,
                           "status": page.status, "sha256": page.sha256, "retrieved_at": page.retrieved_at,
                           "redirects": page.redirects, "content_type": page.content_type.split(";")[0]})
    else:
        st.records.append({"lead": lead_no, "kind": kind, "url": url, "error": error})


def _same(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    def n(u: str) -> str:
        sp = urlsplit(u)
        return (sp.hostname or "").lower().removeprefix("www.") + (sp.path or "/").rstrip("/").lower() + \
            ("?" + sp.query if sp.query else "")
    return n(a) == n(b)


def _http_variant(url: str) -> str | None:
    return "http://" + url[len("https://"):] if url.startswith("https://") else None


async def _check_lead(ctx, st: _State, lead: Lead, rec: dict, target: str, *, lead_no: int = 0,
                      tier_primary: str = "website_primary", tier_secondary: str = "website_secondary") -> LeadResult:
    pages: list[V.Page] = []
    queue = [lead.fetch_url]
    if not _same(lead.start, lead.fetch_url):
        queue.append(lead.start)
    tried: list[str] = []
    home = None
    last: tuple[str, str] = ("error", "no request made")
    while queue:
        u = queue.pop(0)
        if u in tried:
            continue
        tried.append(u)
        res, code, msg = await _get(ctx, st, u, tier=tier_primary, max_redirects=3)
        if res is None:
            _record(st, lead_no, kind="home", url=u, error=msg)
            last = (code or "error", msg)
            if code == "connect" and _http_variant(u):
                queue.insert(0, _http_variant(u))      # https failed (TLS/connect): try plain http once
            if code in ("budget", "robots", "unsafe"):
                break
            continue
        page = _to_page(ctx, "home", res)
        _record(st, lead_no, page)
        if res.status >= 400 and res.status not in (401, 403, 429) and queue:
            last = ("http", f"HTTP {res.status} at {res.final_url}")
            home = page
            continue
        home = page
        break
    if home is None:
        state = {"budget": "failed", "timeout": "failed", "error": "failed", "robots": "blocked"}.get(last[0], "not_available")
        reason = {"dns": "registered domain does not resolve (dead)", "connect": "site unreachable (connection/TLS error)",
                  "unsafe": last[1]}.get(last[0], last[1])
        return LeadResult(lead, state, f"{lead.start}: {reason}")
    pages.append(home)
    # one client-side redirect (meta refresh / tiny JS / frameset)
    if 200 <= home.status < 300 and home.html:
        cs = X.client_side_target(home.html, home.final_url)
        if cs and cs.startswith(("http://", "https://")) and not _same(cs, home.final_url):
            res, code, msg = await _get(ctx, st, cs, tier=tier_primary, max_redirects=2)
            if res is not None:
                p2 = _to_page(ctx, "home", res)
                _record(st, lead_no, p2)
                if 200 <= p2.status < 300 and p2.html:
                    home.kind = "redirect_shell"
                    pages = [p2]
            else:
                _record(st, lead_no, kind="client_redirect", url=cs, error=msg)
    home = pages[0]
    lead_kind = "registry_website" if lead.registry else lead.kind
    pre = V.verify(pages, rec, target, lead_kind=lead_kind, start_url=lead.start, cache=st.analysis)
    if pre.level in ("blocked", "not_available"):
        return LeadResult(lead, pre.level, f"{lead.start}: {pre.reason}", pre, pages)
    trx = X.target_rx(target)
    found = any(trx.search(p.html) for p in pages if p.html)
    plan: list[tuple[str, str, str]] = []
    if lead.proof_url and not any(_same(lead.proof_url, p.final_url) or _same(lead.proof_url, p.url) for p in pages):
        plan.append((lead.proof_page or "proof", lead.proof_url, tier_primary))
    subs = X.pick_subpages(home.html, home.final_url) if home.html else {}
    for kind in SUBPAGE_ORDER:
        if kind in subs:
            plan.append((kind, subs[kind][0], tier_secondary))
    promising = True
    if lead.source == "live":
        # live guesses get a subpage only when the home page carries the company name prominently
        promising = bool(pre.infos and pre.profile and
                         V.name_matches(pre.infos[0].strong_text() + " | " + pre.infos[0].h1, pre.profile))
    max_pages = max(2, int(getattr(ctx.config, "website_max_pages", 6) or 6))
    for kind, url, tier in plan:
        if any(_same(url, p.final_url) or _same(url, p.url) for p in pages):
            continue
        contact_done = any(p.kind == "contact" for p in pages)
        if found:
            if contact_done:
                break
            if kind != "contact":
                continue                      # number already found: only the contact page adds facts
        if not promising:
            break
        if len(pages) >= max_pages or st.spent >= MAX_REQUESTS:
            break
        if not under_site(url, home.final_url):
            continue
        res, code, msg = await _get(ctx, st, url, tier=tier)
        if res is None:
            _record(st, lead_no, kind=kind, url=url, error=msg)
            if code == "budget":
                break
            continue
        p = _to_page(ctx, kind, res)
        _record(st, lead_no, p)
        if 200 <= p.status < 300 and p.html:
            pages.append(p)
            found = found or bool(trx.search(p.html))
    shared = _shared_orgnrs(ctx, target, home.final_url)
    v = V.verify(pages, rec, target, lead_kind=lead_kind, start_url=lead.start, shared_orgnrs=shared,
                 cache=st.analysis)
    state = {"L1": "available", "L2": "available"}.get(v.level, v.level)
    reason = v.reason if state == "available" else f"{lead.start}: {v.reason}"
    if state == "ambiguous" and st.budget_hit and not found:
        reason += " (not all subpages could be checked within the request budget)"
    return LeadResult(lead, state, reason, v, pages)


def under_site(url: str, home_url: str) -> bool:
    a, b = urlsplit(url), urlsplit(home_url)
    return X.reg_domain(a.hostname or "") == X.reg_domain(b.hostname or "")


# --------------------------------------------------------------------------- shared-domain index
def _shared_index(ctx) -> dict[str, set[str]]:
    """host (no www) -> organisation numbers whose discovery leads are the *root* site on that host, plus an
    optional declared registry index `registry_website_domains` ({host_or_domain: [orgnr, ...]})."""
    idx = ctx.shared.get("website_shared_index") if isinstance(getattr(ctx, "shared", None), dict) else None
    if idx is not None:
        return idx
    idx = {}
    try:
        rows = ctx.caches.jsonl_index("site_discovery")
    except Exception:
        rows = {}
    for org, recs in rows.items():
        for row in recs:
            for ld in row.get("leads") or []:
                u = ld.get("final_url") or ld.get("start") or ""
                sp = urlsplit(u if "://" in u else "https://" + u)
                host = (sp.hostname or "").lower().removeprefix("www.")
                if host and V.HOMEISH_PATH_RX.match(sp.path or "/"):
                    idx.setdefault(host, set()).add(str(org))
    for extra in (_safe_json(ctx, "registry_website_domains"), getattr(ctx, "shared", {}).get("registry_website_domains")):
        if isinstance(extra, dict):
            for k, orgs in extra.items():
                if isinstance(orgs, (list, set, tuple)):
                    idx.setdefault(str(k).lower().removeprefix("www."), set()).update(str(o) for o in orgs)
    if isinstance(getattr(ctx, "shared", None), dict):
        ctx.shared["website_shared_index"] = idx
    return idx


def _safe_json(ctx, name: str) -> Any:
    try:
        return ctx.caches.json(name)
    except Exception:
        return None


def _shared_orgnrs(ctx, target: str, final_url: str) -> list[str]:
    host = (urlsplit(final_url).hostname or "").lower().removeprefix("www.")
    if not host:
        return []
    idx = _shared_index(ctx)
    out = set(idx.get(host, set()))
    dom = X.reg_domain(host)
    if dom == host:
        out |= set(idx.get(dom, set()))
    out.discard(target)
    return sorted(out)


# --------------------------------------------------------------------------- outcome selection
def _choose(results: list[LeadResult]) -> LeadResult | None:
    if not results:
        return None
    return min(enumerate(results), key=lambda ir: (STATE_RANK.get(ir[1].state, 9), ir[0]))[1]


# --------------------------------------------------------------------------- news (RSS / Atom)
def _parse_date(s: str | None) -> datetime | None:
    s = (s or "").strip()
    if not s:
        return None
    dt = None
    try:
        dt = parsedate_to_datetime(s)
    except (TypeError, ValueError, IndexError):
        dt = None
    if dt is None:
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if dt.year < 1995 or dt > datetime.now(timezone.utc) + timedelta(days=2):
        return None
    return dt


def parse_feed(data: bytes, feed_url: str, accept_url) -> list[dict]:
    """Dated items of an RSS 2.0 / RSS 1.0 / Atom feed: [{"title","url","published","span"}], newest first.
    `accept_url(url) -> bool` restricts items to the verified site. DTD/entity declarations are refused."""
    head = data[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in head:
        return []
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return []
    raw = data.decode("utf-8", errors="replace")

    def local(tag: Any) -> str:
        return str(tag).rsplit("}", 1)[-1].lower()

    rt = local(root.tag)
    if rt == "rss":
        ch = next((c for c in root if local(c.tag) == "channel"), None)
        entries = [e for e in (ch if ch is not None else []) if local(e.tag) == "item"]
    elif rt == "feed":
        entries = [e for e in root if local(e.tag) == "entry"]
    elif rt == "rdf":
        entries = [e for e in root if local(e.tag) == "item"]
    else:
        return []
    out: dict[str, dict] = {}
    for e in entries:
        title = link = None
        dates: dict[str, str] = {}
        for c in e:
            t = local(c.tag)
            if t == "title":
                title = re.sub(r"\s+", " ", "".join(c.itertext())).strip()
            elif t == "link":
                href = c.get("href")
                if href and c.get("rel", "alternate") == "alternate":
                    link = link or href.strip()
                elif not href and (c.text or "").strip():
                    link = link or c.text.strip()
            elif t in ("pubdate", "published", "date", "issued", "updated", "modified"):
                dates.setdefault(t, (c.text or "").strip())
        raw_date = next((dates[k] for k in ("pubdate", "published", "date", "issued", "updated", "modified")
                         if dates.get(k)), None)
        dt = _parse_date(raw_date)
        if not (title and link and dt):
            continue
        url = urljoin(feed_url, link)
        if not url.startswith(("http://", "https://")) or not accept_url(url):
            continue
        span = V.raw_window(raw, link, 160, 120) or f"{title} {raw_date}"
        out.setdefault(url, {"title": title[:300], "url": url, "published": utc_iso(dt), "span": span})
    items = sorted(out.values(), key=lambda it: (it["published"], it["url"]), reverse=True)
    return items[:MAX_NEWS]


async def _news(ctx, st: _State, v: V.Verdict) -> tuple[list[dict], tuple[V.Page, str] | None]:
    fu = V.feed_url(v)
    if not fu or st.spent >= MAX_REQUESTS:
        return [], None
    if not ctx.allow("website_secondary", 1):
        return [], None
    res, code, msg = await _get(ctx, st, fu, tier="website_secondary", accept=FEED_ACCEPT)
    if res is None:
        _record(st, -1, kind="feed", url=fu, error=msg)
        return [], None
    retrieved_at = utc_iso(res.retrieved_at)
    sha = res.sha256
    try:
        sha = ctx.snapshots.save_result(res, retrieved_at)
    except Exception:
        pass
    page = V.Page("feed", res.url, res.final_url, res.status, "", sha, retrieved_at, list(res.redirects),
                  res.headers.get("content-type", ""))
    _record(st, -1, page)
    if not res.ok:
        return [], None
    items = parse_feed(res.content, res.final_url,
                       lambda u: V.under_base(u, v.site_url or "", v.subpath, v.exclusive))
    root = "rss/channel/item" if b"<rss" in res.content[:2000].lower() else "feed/entry"
    return items, (page, root)


# --------------------------------------------------------------------------- claims
def _ev(page: V.Page, locator: str, span: str | None, *, source_class: str = COMPANY_OWNED,
        effective_at: str | None = None) -> Evidence:
    host = (urlsplit(page.final_url).hostname or "").lower()
    return Evidence(source_url=page.final_url, source_class=source_class, source_name=f"Company website ({host})",
                    retrieved_at=page.retrieved_at, content_sha256=page.sha256,
                    claim_span=(span or "")[:300] or None, locator=locator, extraction_method=METHOD,
                    page_url=page.final_url, effective_at=effective_at, http_status=page.status,
                    final_url=page.final_url, licence=COMPANY_SITE)


def _registry_hjemmeside_ev(company: CompanyResult, rec: dict) -> Evidence | None:
    h = rec.get("hjemmeside")
    try:
        from .registry import registry_evidence  # helper exported by the registry module
        ev = registry_evidence(company, "/hjemmeside")
    except Exception:
        ev = None
    if ev is None:
        base = company.data.get("registry_evidence")
        if not isinstance(base, Evidence):
            return None
        ev = dataclasses.replace(base, locator="/hjemmeside", claim_span=None)
    if ev.claim_span is None and h:
        ev = dataclasses.replace(ev, claim_span=f'"hjemmeside":"{h}"')
    return ev


def _registry_social_claims(company: CompanyResult, rec: dict) -> list[Claim]:
    """A Facebook/Instagram/LinkedIn URL filed as `hjemmeside` is the company's own declaration of its profile."""
    h = (rec.get("hjemmeside") or "").strip()
    if not h or C.normalize_registry_website(h):
        return []
    u = h if re.match(r"https?://", h, re.I) else "https://" + h
    for platform in V.SOCIAL_PLATFORMS:
        m = X.SOCIAL_RX[platform].search(u)
        can = V.canonical_social(platform, m.group(0)) if m else None
        if can and not X.PLATFORM_SOCIAL_RX.search(can[0]):
            ev = _registry_hjemmeside_ev(company, rec)
            if ev is None:
                return []
            return [Claim(field="social_profile", value={"platform": platform, "url": can[0], "handle": can[1]},
                          evidence=[ev], qualifier=f"{platform}|{can[0]}", confidence=0.9,
                          display=f"{platform}: {can[0]}", method="registry_hjemmeside_social_url",
                          match_basis="registry_filed_url",
                          note="registry-filed website field (hjemmeside) is this social profile")]
    return []


def _display(field_name: str, value: Any) -> str | None:
    if field_name == "social_profile":
        return f"{value['platform']}: {value['url']}"
    if field_name == "news_item":
        return f"{value['published'][:10]} {value['title']}"
    if field_name == "website_verification":
        return f"{value['level']} ({value['method']})"
    return None


def _emit(ctx, company: CompanyResult, rec: dict, st: _State, results: list[LeadResult], best: LeadResult | None,
          news: list[dict], feed_ev: tuple[V.Page, str] | None, reg_note: str | None) -> None:
    claims: list[Claim] = []
    data: dict[str, Any] = {"level": None, "url": None, "domain": None, "pages": st.records, "title": None,
                            "description": None, "emails": [], "phones": [], "socials": [], "careers": None,
                            "news": [], "reason": None, "state": None,
                            "leads": [{**r.lead.describe(), "state": r.state, "reason": r.reason} for r in results]}
    claims.extend(_registry_social_claims(company, rec))
    if best is None:
        state = "not_available"
        parts = ["no website lead: no registry website, no discovery-cache lead"]
        if reg_note:
            parts = [reg_note]
        if getattr(ctx.config, "live_discovery", False) and rec:
            parts.append("no live candidate domain resolved" if not st.budget_hit else BUDGET_NOTE + " (live discovery)")
        reason = "; ".join(parts)
        if st.budget_hit and not st.records:
            state, reason = "failed", BUDGET_NOTE
        claims.append(Claim(field="official_website", value=None, availability=state, note=reason, confidence=1.0,
                            method=METHOD))
        data.update(state=state, reason=reason)
        company.add(claims)
        company.data[MODULE] = data
        company.outcome(ModuleOutcome(MODULE, state, note=reason[:500], requests=st.spent))
        return
    v = best.verdict
    home = best.pages[0] if best.pages else None
    if best.state != "available" or v is None or not v.published:
        reason = best.reason
        others = [r.reason for r in results if r is not best and r.reason]
        if others:
            reason += " | other leads: " + "; ".join(others)[:400]
        evs = []
        if home is not None:
            evs.append(_ev(home, "title" if home.html else "http_status",
                           (v.infos[0].title if v and v.infos else None) or f"HTTP {home.status} {home.final_url}"))
        claims.append(Claim(field="official_website", value=None, availability=best.state, evidence=evs,
                            confidence=0.5 if best.state == "ambiguous" else 1.0, note=reason[:600],
                            method=f"{V.GATE_VERSION}:{v.level if v else best.state}"))
        data.update(state=best.state, reason=reason, level=None,
                    url=(v.site_url if v else None) if best.state == "ambiguous" else None,
                    domain=(v.domain if v else None))
        if best.state == "ambiguous" and v is not None:
            data["candidate_url"] = v.site_url
            data["other_orgnrs"] = v.other_orgnrs
        company.add(claims)
        company.data[MODULE] = data
        company.outcome(ModuleOutcome(MODULE, best.state, note=reason[:500], source_url=home.final_url if home else None,
                                      retrieved_at=home.retrieved_at if home else None,
                                      http_status=home.status if home else None, requests=st.spent))
        return
    # ------------------------------------------------------------------ published (L1 / L2)
    conf = v.confidence
    basis = "organisation_number_exact" if v.level == "L1" else "registry_website_corroborated"
    method = f"{V.GATE_VERSION}:{v.level}"
    proof_page = v.infos[v.proof["page"]].page
    proof_ev = _ev(proof_page, v.proof["locator"], v.proof["span"])
    site_evs = [proof_ev]
    if v.level == "L2":
        rev = _registry_hjemmeside_ev(company, rec)
        if rev is not None:
            site_evs.append(rev)
    host_disp = (v.site_url or "").split("://", 1)[-1].rstrip("/")
    claims.append(Claim(field="official_website", value=v.site_url, evidence=list(site_evs), confidence=conf,
                        display=host_disp, method=method, match_basis=basis, note=v.reason[:600]))
    ver = {"level": v.level, "proof_url": v.proof["url"], "proof_span": v.proof["span"], "method": v.proof["method"]}
    claims.append(Claim(field="website_verification", value=ver, evidence=list(site_evs), confidence=conf,
                        display=_display("website_verification", ver), method=method, match_basis=basis))
    facts = V.collect_facts(v, rec)
    for f in facts:
        page = v.infos[f.page].page
        sc = COMPANY_LINKED_PROFILE if f.field == "social_profile" else COMPANY_OWNED
        claims.append(Claim(field=f.field, value=f.value, evidence=[_ev(page, f.locator, f.span, source_class=sc)],
                            qualifier=f.qualifier, confidence=conf, display=_display(f.field, f.value),
                            method=METHOD, match_basis=basis))
    if news and feed_ev is not None:
        fpage, root = feed_ev
        for it in news:
            val = {"title": it["title"], "url": it["url"], "published": it["published"]}
            claims.append(Claim(field="news_item", value=val,
                                evidence=[_ev(fpage, root, it["span"], effective_at=it["published"][:10])],
                                qualifier=it["url"], confidence=conf, display=_display("news_item", val),
                                effective_at=it["published"][:10], method=METHOD, match_basis=basis))
    order = {f: i for i, f in enumerate(FIELD_ORDER)}
    claims.sort(key=lambda c: (order.get(c.field, 99), c.qualifier or "", V._ws(str(c.value))))
    company.add(claims)
    by = {f.field: [] for f in facts}
    for f in facts:
        by[f.field].append(f.value)
    data.update(
        level=v.level, url=v.site_url, domain=v.domain, state="available", reason=v.reason,
        title=(by.get("website_title") or [None])[0], description=(by.get("website_description") or [None])[0],
        brand=(by.get("brand_name") or [None])[0], logo=(by.get("logo_url") or [None])[0],
        language=(by.get("website_language") or [None])[0], address=(by.get("website_address") or [None])[0],
        emails=sorted(by.get("contact_email", [])), phones=sorted(by.get("contact_phone", [])),
        socials=sorted(by.get("social_profile", []), key=lambda s: (s["platform"], s["url"])),
        careers=(by.get("careers_page") or [None])[0],
        news=[{"title": it["title"], "url": it["url"], "published": it["published"]} for it in news],
        proof_url=v.proof["url"], proof_span=v.proof["span"], multi=v.multi, other_orgnrs=v.other_orgnrs,
        shared_with=v.shared_with, subpath=v.subpath, corroboration=(v.corroboration or {}).get("kinds"),
        confidence=conf)
    company.data[MODULE] = data
    company.outcome(ModuleOutcome(MODULE, "available", note=v.reason[:500], source_url=proof_page.final_url,
                                  retrieved_at=proof_page.retrieved_at, http_status=proof_page.status,
                                  requests=st.spent))
