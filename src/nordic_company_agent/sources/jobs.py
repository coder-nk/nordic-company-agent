"""NAV arbeidsplassen job ads (batch module, tier ``batch_external``).

Inputs: the declared cache ``cache/nav_jobs.jsonl.gz`` (+ ``.meta.json``) built offline by
``tools/build_nav_jobs_cache.py`` (every ACTIVE ad with its employer organisation number, mapped to
the parent enhet), and the live public feed:

1. one request for the public bearer token (``/api/publicToken``);
2. page the feed forward from the cache's cursor (3 pages by default, more when the planner allows)
   to catch new, changed and inactivated ads; ads that became INACTIVE are removed (NAV terms);
3. new/changed ACTIVE ads whose ``businessName`` matches a batch company's legal name or one of its
   subunit names are confirmed by fetching the feed entry (<= 20 per run) and comparing the
   employer organisation number with the company and its registered subunits.

Claims: ``job_posting`` [nav:uuid], ``active_job_count`` (only when the source was checked) and
``hiring_status``. Contact persons in feed entries are never stored or published.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from typing import Any

from .registry import members_span, span_at
from ..envelope import (NAV_TERMS, OFFICIAL_JOBS_FEED, Claim, CompanyResult, Evidence, ModuleOutcome, canonical,
                        sha256_hex, utc_iso)
from ..web.extract import name_core_tokens

MODULE = "jobs"
VERSION = "jobs@1"
TIER = "batch_external"
NAV = "https://pam-stilling-feed.nav.no"
TOKEN_URL = NAV + "/api/publicToken"
FEED_URL = NAV + "/api/v1/feed"
CACHE_NAME = "nav_jobs"
SOURCE_NAME = "NAV arbeidsplassen stillingsfeed"
DEFAULT_PAGES = 3
MAX_PAGES = 60
ENTRY_RESERVE = 10            # keep this many requests for entry confirmations when paging beyond 3 pages
MAX_ENTRY_FETCHES = 20
MAX_POSTINGS = 50             # postings published per company (the count covers all)
STALE_DAYS = 3
MAX_GAP_DAYS = 14             # beyond this without live coverage, cached ads are not published (NAV terms)
NO_CACHE_LOOKBACK_DAYS = 2
BUDGET_NOTE = "not checked: request budget reserved for higher-priority sources"
JWT_RX = re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+")


def entry_url(uuid: str) -> str:
    return f"{NAV}/api/v1/feedentry/{uuid}"


def public_url(uuid: str) -> str:
    return f"https://arbeidsplassen.nav.no/stillinger/stilling/{uuid}"


def norm_name(s: str | None) -> str:
    return " ".join(name_core_tokens((s or "").replace("&", " og ")))


def _parse_ts(s: Any) -> datetime | None:
    if not isinstance(s, str) or not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = datetime.fromisoformat(s[:10])
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _eligible(company: CompanyResult) -> bool:
    ident = company.data.get("identity") or {}
    return ident.get("match_status") in (None, "exact")


def _legal_name(company: CompanyResult) -> str:
    reg = company.data.get("registry") or {}
    return reg.get("navn") or company.first_value("legal_name") or ""


def _subunits(company: CompanyResult) -> dict[str, str] | None:
    """{subunit orgnr: name} from company.data['subunits'] (list of API-shaped or normalised dicts),
    or None when the subunits module has not provided a list."""
    raw = company.data.get("subunits")
    if raw is None:
        return None
    if isinstance(raw, dict):
        raw = raw.get("items") or raw.get("underenheter") or raw.get("subunits") or []
    out: dict[str, str] = {}
    for s in raw or []:
        if not isinstance(s, dict):
            continue
        o = str(s.get("organisasjonsnummer") or s.get("organisation_number") or "")
        if len(o) == 9 and o.isdigit():
            out[o] = s.get("navn") or s.get("name") or ""
    return out


def _span(**kv: Any) -> str:
    return ",".join(f"{json.dumps(k)}:{json.dumps(v, ensure_ascii=False)}" for k, v in kv.items())[:300]


def _redact_entry(body: bytes) -> bytes | None:
    """Remove contact persons (names, e-mails, phones) before the entry is stored as a snapshot."""
    try:
        j = json.loads(body)
    except ValueError:
        return None
    ad = j.get("ad_content") if isinstance(j, dict) else None
    if isinstance(ad, dict) and "contactList" in ad:
        ad["contactList"] = []
    return json.dumps(j, ensure_ascii=False, sort_keys=True).encode("utf-8")


def _location_text(locs: list[dict[str, Any]]) -> str | None:
    seen: list[str] = []
    for w in locs or []:
        name = w.get("city") or w.get("municipal") or w.get("municipality") or w.get("county") or w.get("country")
        if name and name not in seen:
            seen.append(name)
    return ", ".join(seen) or None


class _Run:
    def __init__(self, ctx):
        self.ctx = ctx
        self.token: str | None = ctx.shared.get("nav_token")
        self.pages: list[dict[str, Any]] = []    # {"url","sha","retrieved_at","status","items"}
        self.reached_end = False
        self.last_item_modified: str | None = None
        self.notes: list[str] = []
        self.live_ok = False
        self.requests = 0

    async def _get(self, url: str, headers: dict[str, str] | None = None):
        before = self.ctx.client.used
        try:
            return await self.ctx.get(url, tier=TIER, source=MODULE, headers=headers)
        finally:
            self.requests += self.ctx.client.used - before

    async def get_token(self, force: bool = False) -> bool:
        if self.token and not force:
            return True
        if not self.ctx.allow(TIER, 1):
            self.notes.append("NAV token not fetched: request budget reserved for higher-priority sources")
            return False
        try:
            res = await self._get(TOKEN_URL, headers={"Accept": "text/plain,*/*"})
        except Exception as exc:  # noqa: BLE001
            self.notes.append(f"NAV public token request failed: {type(exc).__name__}")
            return False
        m = JWT_RX.search(res.text) if res.ok else None
        if not m:
            self.notes.append(f"NAV public token unavailable (HTTP {res.status})")
            return False
        self.token = m.group(0)
        self.ctx.shared["nav_token"] = self.token
        return True

    async def authed(self, url: str, extra: dict[str, str] | None = None):
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json", **(extra or {})}
        res = await self._get(url, headers=headers)
        if res.status == 401 and self.ctx.allow(TIER, 2) and await self.get_token(force=True):
            headers["Authorization"] = f"Bearer {self.token}"
            res = await self._get(url, headers=headers)
        return res

    async def page_feed(self, start_url: str, since: datetime | None) -> None:
        url, extra = start_url, ({"If-Modified-Since": format_datetime(since, usegmt=True)} if since else None)
        n = 0
        while url:
            if n >= MAX_PAGES or self.ctx.time_left() < 120:
                break
            need = 1 if n < DEFAULT_PAGES else 1 + ENTRY_RESERVE
            if not self.ctx.allow(TIER, need):
                break
            try:
                res = await self.authed(url, extra)
            except Exception as exc:  # noqa: BLE001
                self.notes.append(f"feed page request failed: {type(exc).__name__}")
                break
            extra = None
            if not res.ok:
                self.notes.append(f"feed page HTTP {res.status}")
                break
            retrieved_at = utc_iso(res.retrieved_at)
            sha = self.ctx.snapshots.save_result(res, retrieved_at)
            try:
                doc = res.json()
            except ValueError:
                self.notes.append("feed page was not JSON")
                break
            items = doc.get("items") or []
            self.pages.append({"url": res.url, "sha": sha, "retrieved_at": retrieved_at, "status": res.status,
                               "items": items, "text": res.text})
            self.live_ok = True
            if items:
                self.last_item_modified = items[-1].get("date_modified") or self.last_item_modified
            n += 1
            nxt = doc.get("next_url")
            if not nxt:
                self.reached_end = True
                break
            url = NAV + nxt if nxt.startswith("/") else nxt

    def latest_items(self) -> dict[str, dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for p_i, page in enumerate(self.pages):
            for i, it in enumerate(page["items"]):
                fe = it.get("_feed_entry") or {}
                u = fe.get("uuid") or it.get("id")
                if not u:
                    continue
                mod = it.get("date_modified") or fe.get("sistEndret") or ""
                # The feed is chronological, so the last occurrence is the latest state (string
                # comparison of timestamps would break across DST offset changes).
                latest[u] = {"uuid": u, "status": fe.get("status"), "modified": mod,
                             "business_name": fe.get("businessName") or "", "title": fe.get("title") or "",
                             "page": p_i, "index": i}
        return latest

    async def fetch_entry(self, uuid: str) -> dict[str, Any] | None:
        url = entry_url(uuid)
        try:
            res = await self.authed(url)
        except Exception as exc:  # noqa: BLE001
            self.notes.append(f"feed entry {uuid} failed: {type(exc).__name__}")
            return None
        if not res.ok:
            return {"uuid": uuid, "http_status": res.status, "status": None}
        retrieved_at = utc_iso(res.retrieved_at)
        redacted = _redact_entry(res.content)
        if redacted is None:
            return {"uuid": uuid, "http_status": res.status, "status": None}
        self.ctx.snapshots.save(redacted, url=url, retrieved_at=retrieved_at,
                                content_type=res.headers.get("content-type"), sha256=res.sha256, redacted=True,
                                redaction_note="contact-person names, e-mails and phones removed before storage")
        j = json.loads(res.content)
        ad = j.get("ad_content") or {}
        emp = ad.get("employer") or {}
        pc = str(ad.get("positioncount") or "").strip()
        locs = ad.get("workLocations") or []
        return {
            "text": redacted.decode("utf-8", errors="replace"),
            "uuid": uuid, "http_status": res.status, "status": j.get("status"), "sha": res.sha256,
            "retrieved_at": retrieved_at, "employer_organisation_number": re.sub(r"\D", "", str(emp.get("orgnr") or "")),
            "employer_name": emp.get("name"), "title": ad.get("title"), "published": ad.get("published"),
            "expires": ad.get("expires"), "updated": ad.get("updated"),
            "url": ad.get("link") or public_url(uuid), "location": _location_text(locs),
            "positions": int(pc) if pc.isdigit() else None,
        }


async def run_batch(ctx, companies: list[CompanyResult]) -> None:
    try:
        await _run_batch(ctx, companies)
    except Exception as exc:  # noqa: BLE001 - modules never raise
        for c in companies:
            if MODULE not in c.modules:
                c.error(MODULE, "failed", f"{type(exc).__name__}: {exc}")
                c.outcome(ModuleOutcome(MODULE, "failed", note=f"internal error: {type(exc).__name__}"))


async def _run_batch(ctx, companies: list[CompanyResult]) -> None:
    now = datetime.now(timezone.utc)
    targets: list[CompanyResult] = []
    for c in companies:
        if _eligible(c):
            targets.append(c)
        else:
            c.outcome(ModuleOutcome(MODULE, "not_applicable",
                                    note="identity not confirmed as an exact organisation-number match"))
    if not targets:
        return

    rows_by_org = ctx.caches.jsonl_index(CACHE_NAME)
    meta = ctx.caches.meta(CACHE_NAME)
    cache_present = meta is not None or bool(rows_by_org)
    meta = meta or {}
    rows_by_emp: dict[str, list[dict[str, Any]]] = {}
    cached_uuids: set[str] = set()
    for rows in rows_by_org.values():
        for r in rows:
            rows_by_emp.setdefault(str(r.get("employer_organisation_number")), []).append(r)
            cached_uuids.add(r.get("uuid"))
    built_at = meta.get("built_at")
    built_dt = _parse_ts(built_at)
    cursor = meta.get("feed_cursor") or {}

    run = _Run(ctx)
    if await run.get_token():
        if cache_present and (cursor.get("page_url") or cursor.get("page_id")):
            start = cursor.get("page_url") or f"{FEED_URL}/{cursor['page_id']}"
            await run.page_feed(start, None)
        elif not cache_present:
            await run.page_feed(FEED_URL, (now - timedelta(days=NO_CACHE_LOOKBACK_DAYS)).replace(microsecond=0))
        else:
            run.notes.append("cache has no feed cursor; live paging skipped")
    latest = run.latest_items()
    inactive = {u for u, it in latest.items() if it["status"] != "ACTIVE"}

    # Company lookups.
    subs: dict[str, dict[str, str] | None] = {c.organisation_number: _subunits(c) for c in targets}
    subs_complete = {c.organisation_number: subs[c.organisation_number] is not None
                     and (c.data.get("subunits_meta") or {}).get("complete", True) for c in targets}
    name_index: dict[str, set[str]] = {}
    for c in targets:
        for nm in [_legal_name(c), *((subs[c.organisation_number] or {}).values())]:
            k = norm_name(nm)
            if len(k) >= 3:
                name_index.setdefault(k, set()).add(c.organisation_number)
    batch_cached_uuids = {r.get("uuid"): r for c in targets for r in rows_by_org.get(c.organisation_number, [])}

    # Live confirmation candidates: new ACTIVE ads whose business name matches, and changed cached ads.
    cands: list[tuple[str, str]] = []
    for u, it in latest.items():
        if it["status"] != "ACTIVE":
            continue
        if u in batch_cached_uuids:
            live_mod = _parse_ts(it["modified"])
            cached_mod = _parse_ts(batch_cached_uuids[u].get("feed_modified"))
            if live_mod and (cached_mod is None or live_mod > cached_mod):
                cands.append((it["modified"], u))
        elif u not in cached_uuids and norm_name(it["business_name"]) in name_index:
            cands.append((it["modified"], u))
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    cands.sort(key=lambda t: (_parse_ts(t[0]) or epoch, t[1]), reverse=True)
    fetch_list = []
    for _m, u in cands[:MAX_ENTRY_FETCHES]:
        if not ctx.allow(TIER, 1):
            run.notes.append(f"{len(cands) - len(fetch_list)} candidate ads not confirmed (request budget)")
            break
        fetch_list.append(u)
    entries = {u: e for u, e in zip(fetch_list, await asyncio.gather(*(run.fetch_entry(u) for u in fetch_list)), strict=True)
               if e is not None}
    if len(cands) > MAX_ENTRY_FETCHES:
        run.notes.append(f"{len(cands) - MAX_ENTRY_FETCHES} further candidate ads not confirmed (per-run cap)")

    # Freshness of what we know.
    age_days = (now - built_dt).total_seconds() / 86400 if built_dt else None
    covered_to = now if run.reached_end else (_parse_ts(run.last_item_modified) or built_dt)
    gap_days = (now - covered_to).total_seconds() / 86400 if covered_to else None
    too_old = cache_present and not run.reached_end and (gap_days is None or gap_days > MAX_GAP_DAYS)
    if too_old:
        confidence, fresh_note = 0.7, (f"NAV cache built {built_at or 'at an unknown time'} is too old and the live "
                                       f"feed could not be paged to now (reached {run.last_item_modified or 'nothing'})"
                                       "; cached ads are not published because closed ads must be removed")
    elif cache_present and run.reached_end:
        confidence, fresh_note = 0.9, (f"NAV cache built {built_at}, brought up to date by paging the live feed to "
                                       f"{run.last_item_modified}")
    elif cache_present and age_days is not None and age_days > STALE_DAYS:
        confidence, fresh_note = 0.6, (f"NAV cache built {built_at} ({age_days:.1f} days old) and the live feed could "
                                       f"not be paged to now (reached {run.last_item_modified or 'nothing'}); newer ads "
                                       "are missing and some listed ads may have closed")
    elif cache_present:
        confidence, fresh_note = 0.8, (f"NAV cache built {built_at}; live feed paged to "
                                       f"{run.last_item_modified or 'n/a (not reached)'}")
    else:
        confidence, fresh_note = 0.7, (f"declared cache cache/{CACHE_NAME}.jsonl.gz missing; only the live feed since "
                                       f"{NO_CACHE_LOOKBACK_DAYS} days ago was checked")
    if run.notes:
        fresh_note += "; " + "; ".join(dict.fromkeys(run.notes))
    checked = cache_present and not too_old  # a complete active-ad picture needs a reasonably fresh cache

    cache_ev = None
    if cache_present:
        meta_text = canonical(meta)
        meta_bytes = meta_text.encode("utf-8")
        meta_sha = ctx.snapshots.save(meta_bytes, url=f"cache/{CACHE_NAME}.meta.json", retrieved_at=built_at or utc_iso(now),
                                      content_type="application/json")
        cache_ev = Evidence(
            source_url=cursor.get("page_url") or FEED_URL, source_class=OFFICIAL_JOBS_FEED, source_name=SOURCE_NAME,
            retrieved_at=built_at or utc_iso(now), content_sha256=meta_sha,
            claim_span=members_span(meta_text, "", ["active_ads", "built_at"]) or span_at(meta_text, "/built_at"),
            locator=f"cache:{CACHE_NAME}.meta.json", extraction_method=f"{VERSION}:cache",
            page_url="https://arbeidsplassen.nav.no/stillinger", licence=NAV_TERMS,
            retrieved_via=f"declared cache cache/{CACHE_NAME}.jsonl.gz (built {built_at} by tools/build_nav_jobs_cache.py)")
    live_ev = None
    if run.pages:
        last = run.pages[-1]
        live_ev = Evidence(source_url=last["url"], source_class=OFFICIAL_JOBS_FEED, source_name=SOURCE_NAME,
                           retrieved_at=last["retrieved_at"], content_sha256=last["sha"], locator="/next_url",
                           claim_span=span_at(last.get("text"), "/next_url") or span_at(last.get("text"), "/id"),
                           extraction_method=VERSION, page_url="https://arbeidsplassen.nav.no/stillinger",
                           http_status=last["status"], licence=NAV_TERMS)

    if too_old:
        rows_by_org, rows_by_emp = {}, {}
    for c in targets:
        _emit(ctx, c, subs[c.organisation_number], rows_by_org, rows_by_emp, entries, latest, inactive, run,
              subunits_complete=subs_complete[c.organisation_number], now=now, checked=checked, confidence=confidence, fresh_note=fresh_note, cache_ev=cache_ev,
              live_ev=live_ev, built_at=built_at)


def _row_evidence(ctx, row: dict[str, Any], built_at: str | None) -> Evidence:
    body_text = canonical(row)
    body = body_text.encode("utf-8")
    checked_at = row.get("checked_at") or built_at or utc_iso()
    sha = ctx.snapshots.save(body, url=row.get("feed_entry_url") or entry_url(row["uuid"]), retrieved_at=checked_at,
                             content_type="application/json")
    return Evidence(
        source_url=row.get("feed_entry_url") or entry_url(row["uuid"]), source_class=OFFICIAL_JOBS_FEED,
        source_name=SOURCE_NAME, retrieved_at=checked_at, content_sha256=sha,
        claim_span=(members_span(body_text, "", ["employer_organisation_number", "status", "title"])
                    or span_at(body_text, "/employer_organisation_number")),
        locator=f"cache:{CACHE_NAME}.jsonl.gz#uuid={row.get('uuid')}", extraction_method=f"{VERSION}:cache",
        page_url=row.get("url") or public_url(row["uuid"]), effective_at=(row.get("updated") or "")[:10] or None,
        licence=NAV_TERMS,
        retrieved_via=f"declared cache cache/{CACHE_NAME}.jsonl.gz (built {built_at}); feed entry checked {checked_at}")


def _entry_evidence(e: dict[str, Any]) -> Evidence:
    return Evidence(source_url=entry_url(e["uuid"]), source_class=OFFICIAL_JOBS_FEED, source_name=SOURCE_NAME,
                    retrieved_at=e["retrieved_at"], content_sha256=e["sha"],
                    claim_span=(members_span(e.get("text"), "/ad_content/employer", ["name", "orgnr"])
                                or span_at(e.get("text"), "/ad_content/employer/orgnr")),
                    locator="/ad_content/employer/orgnr", extraction_method=VERSION, page_url=e.get("url"),
                    effective_at=(e.get("updated") or "")[:10] or None, http_status=e.get("http_status"),
                    licence=NAV_TERMS)


def _emit(ctx, company: CompanyResult, subunits: dict[str, str] | None, rows_by_org, rows_by_emp, entries, latest,
          inactive, run: _Run, *, subunits_complete: bool, now: datetime, checked: bool, confidence: float, fresh_note: str,
          cache_ev: Evidence | None, live_ev: Evidence | None, built_at: str | None) -> None:
    o = company.organisation_number
    sub_set = set(subunits or {})
    postings: dict[str, tuple[dict[str, Any], list[Evidence], str]] = {}  # uuid -> (value-ish row, evidence, basis)
    removed: list[str] = []
    expired: list[str] = []

    def basis_for(emp: str, row_org: str | None, kind: str | None) -> str | None:
        if emp == o:
            return "self"
        if emp in sub_set:
            return "subunit"
        if not subunits_complete and row_org == o and kind == "underenhet":
            return "subunit_cached"   # no (complete) subunit list this run: rely on the cached brreg mapping
        return None

    cached = {r["uuid"]: r for r in rows_by_org.get(o, [])}
    for s in sub_set:
        for r in rows_by_emp.get(s, []):
            cached.setdefault(r["uuid"], r)
    for u, r in sorted(cached.items()):
        if u in inactive:
            removed.append(u)
            continue
        e = entries.get(u)
        if e is not None and e.get("status") == "ACTIVE":
            continue  # superseded by the live entry below
        if e is not None and e.get("http_status") == 200 and e.get("status") != "ACTIVE":
            removed.append(u)
            continue
        emp = str(r.get("employer_organisation_number") or "")
        b = basis_for(emp, r.get("organisation_number"), r.get("employer_kind"))
        if not b:
            continue
        exp = _parse_ts(r.get("expires"))
        if exp is not None and exp < now:
            expired.append(u)
            continue
        evs = [_row_evidence(ctx, r, built_at)]
        it = latest.get(u)
        if it and it["status"] == "ACTIVE" and live_ev is not None:
            page = run.pages[it["page"]]
            evs.append(Evidence(source_url=page["url"], source_class=OFFICIAL_JOBS_FEED, source_name=SOURCE_NAME,
                                retrieved_at=page["retrieved_at"], content_sha256=page["sha"],
                                locator=f"/items/{it['index']}/_feed_entry",
                                claim_span=(members_span(page.get("text"), f"/items/{it['index']}/_feed_entry",
                                                         ["status", "uuid"])
                                            or span_at(page.get("text"), f"/items/{it['index']}/_feed_entry/status")),
                                extraction_method=VERSION, page_url=r.get("url"), http_status=page["status"],
                                licence=NAV_TERMS))
        postings[u] = (r, evs, b)

    for u, e in sorted(entries.items()):
        if e.get("status") != "ACTIVE" or not e.get("sha"):
            continue
        emp = e.get("employer_organisation_number") or ""
        b = "self" if emp == o else ("subunit" if emp in sub_set else None)
        if not b:
            continue
        exp = _parse_ts(e.get("expires"))
        if exp is not None and exp < now:
            expired.append(u)
            continue
        postings[u] = (e, [_entry_evidence(e)], b)

    def sort_key(u: str) -> tuple:
        r = postings[u][0]
        return (r.get("published") or "", u)

    ordered = sorted(postings, key=sort_key, reverse=True)
    claims: list[Claim] = []
    for u in ordered[:MAX_POSTINGS]:
        r, evs, b = postings[u]
        emp = str(r.get("employer_organisation_number") or "")
        value = {"title": r.get("title"), "url": r.get("url") or public_url(u), "published": r.get("published"),
                 "expires": r.get("expires"), "employer_name": r.get("employer_name"),
                 "employer_organisation_number": emp, "location": r.get("location"),
                 "positions": r.get("positions")}
        if b == "self":
            note, basis = None, "employer_organisation_number_exact"
        else:
            sub_name = (subunits or {}).get(emp) or r.get("employer_registry_name") or r.get("employer_name")
            note = f"advertised by registered workplace (underenhet) {emp} {sub_name or ''}".rstrip()
            if b == "subunit_cached":
                note += " (overordnetEnhet per Enhetsregisteret when the NAV cache was built)"
            basis = "employer_subunit_of_organisation_number"
        if evs[0].retrieved_via:
            note = (note + "; " if note else "") + f"from the declared NAV cache (checked {r.get('checked_at')})"
        exp = (r.get("expires") or "")[:10]
        claims.append(Claim(field="job_posting", value=value, evidence=evs, qualifier=f"nav:{u}",
                            confidence=confidence if b != "subunit_cached" else min(confidence, 0.8),
                            display=f"{r.get('title')} ({r.get('employer_name')}"
                                    f"{', ' + r['location'] if r.get('location') else ''})"
                                    f"{' - expires ' + exp if exp else ''}",
                            effective_at=(r.get("published") or "")[:10] or None, method="nav_feed_employer_orgnr",
                            match_basis=basis, note=note))
    count = len(postings)
    company.data["jobs"] = {
        "active_job_count": count if checked else None, "postings": [c.value for c in claims],
        "removed_inactive": sorted(set(removed)), "expired": sorted(set(expired)),
        "cache_built_at": built_at, "feed_checked_to": run.last_item_modified, "feed_reached_end": run.reached_end,
        "subunits_known": subunits is not None, "note": fresh_note,
    }
    agg_evs = [e for e in (cache_ev, live_ev) if e is not None]
    if checked and agg_evs:
        count_note = fresh_note
        if count > MAX_POSTINGS:
            count_note += f"; {MAX_POSTINGS} most recent of {count} postings listed"
        claims.append(Claim(field="active_job_count", value=count, evidence=list(agg_evs), confidence=confidence,
                            display=f"{count} active job ad{'s' if count != 1 else ''} on NAV arbeidsplassen",
                            effective_at=(run.last_item_modified or built_at or "")[:10] or None,
                            method="nav_feed_active_ads_by_employer_orgnr", note=count_note,
                            match_basis="employer_organisation_number_or_subunit"))
        claims.append(Claim(field="hiring_status", value="hiring" if count else "no_active_ads_on_nav",
                            evidence=list(agg_evs), confidence=confidence,
                            display=("hiring (active ads on NAV arbeidsplassen)" if count else
                                     "no active job ads on NAV arbeidsplassen"),
                            method="nav_feed_active_ads_by_employer_orgnr", note=count_note,
                            match_basis="employer_organisation_number_or_subunit"))
    company.add(claims)
    src = (live_ev.source_url if live_ev else (cache_ev.source_url if cache_ev else None))
    if count:
        company.outcome(ModuleOutcome(MODULE, "available", note=fresh_note, source_url=src))
    elif checked:
        company.outcome(ModuleOutcome(MODULE, "not_available", source_url=src,
                                      note="no active NAV job ads for this organisation number or its subunits; "
                                           + fresh_note))
    elif run.live_ok:
        company.outcome(ModuleOutcome(MODULE, "failed", source_url=src,
                                      note="not checked completely: " + fresh_note))
    else:
        if built_at:
            msg = "not checked: " + fresh_note
        elif run.notes:
            msg = "not checked: " + "; ".join(dict.fromkeys(run.notes))
        else:
            msg = "not checked: NAV feed unavailable and no declared cache"
        if not built_at and any("budget" in n for n in run.notes):
            msg = BUDGET_NOTE
        company.error(MODULE, "failed", msg)
        company.outcome(ModuleOutcome(MODULE, "failed", note=msg))
