#!/usr/bin/env python3
"""Website-discovery leads from Common Crawl: .no pages that print a labelled organisation number.

The name-guess discovery (tools/build_site_discovery.py) only finds a company's site when the domain is derived
from its legal name or e-mail. This builder reads the archived HTML of every crawled .no host (home page plus up to
3 contact/about/privacy/terms pages, as captured by a Common Crawl crawl) and records which organisation numbers
each page prints with a label ("Org.nr", "Organisasjonsnummer", ...). The result is a declared lead cache in the
site_discovery row format; the agent re-fetches and re-verifies every lead live before publishing anything.

Source: Common Crawl (https://commoncrawl.org, open data, terms https://commoncrawl.org/terms-of-use), read over
HTTPS from data.commoncrawl.org with byte-range requests: the CDX index slice for the 'no,' SURT prefix, then one
range request per selected WARC record. No crawling of company sites happens here.

Phases (each resumable):
  index   download the .no slice of the crawl's CDX index (cluster.idx -> byte ranges)
  select  pick candidate pages per host                       -> data/cc/<crawl>/select.jsonl
  fetch   range-fetch the WARC records, extract org numbers    -> data/cc/<crawl>/pages.jsonl
  live-dns / live-crawl   resolve + crawl (robots.txt honoured) the web graph's .no domains no other source covered
  import  page rows from the live crawl and from the site_discovery crawl's non-target labelled hits
  join    universe companies with labelled hits               -> cache/site_discovery_cc.jsonl.gz (+ .meta.json)

Usage: uv run python tools/build_cc_discovery.py index|select|fetch|join [--crawl CC-MAIN-2026-39]
       [--sample N] [--concurrency 24]
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import random
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nordic_company_agent.web.extract import extract_page, reg_domain  # noqa: E402

BASE = "https://data.commoncrawl.org/"
UNIVERSE = ROOT / "data" / "signalpost-company-universe-2025.jsonl.gz"
CACHE = ROOT / "cache"
UA = "nordic-company-agent/1.0 (company-website discovery; Common Crawl range reads)"
INFO_RX = re.compile(r"kontakt|contact|om-?oss|/om(?:/|$)|about|personvern|privacy|vilk[aå]r|terms|betingelser|"
                     r"impressum|bedriftsinfo|firmainfo|selskapet|informasjon|juridisk|legal", re.I)
HOME_RX = re.compile(r"^/(?:(?:index|default|home|hjem|forside)(?:\.\w+)?|no/?|nb/?|nn/?|en/?|no/forside/?)?$", re.I)
MAX_INFO = 3
MAX_KW_ORGNRS = 3          # a page printing more labelled org numbers is a directory / group list, not one company


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def work(crawl: str) -> Path:
    d = ROOT / "data" / "cc" / crawl
    d.mkdir(parents=True, exist_ok=True)
    return d


# --------------------------------------------------------------------------- index
def phase_index(crawl: str) -> None:
    d = work(crawl)
    idx_path = d / "cluster.idx"
    with httpx.Client(headers={"User-Agent": UA}, timeout=600, follow_redirects=True) as cl:
        if not idx_path.exists():
            r = cl.get(f"{BASE}cc-index/collections/{crawl}/indexes/cluster.idx")
            r.raise_for_status()
            idx_path.write_bytes(r.content)
        lines = idx_path.read_text().splitlines()
        hits = [i for i, ln in enumerate(lines) if ln.startswith("no,")]
        sel = [lines[i] for i in range(max(0, hits[0] - 1), min(len(lines), hits[-1] + 2))]
        ranges: dict[str, tuple[int, int]] = {}
        for ln in sel:
            _key, f, off, length, _seq = ln.split("\t")
            a, b = int(off), int(off) + int(length)
            lo, hi = ranges.get(f, (a, b))
            ranges[f] = (min(lo, a), max(hi, b))
        for f, (a, b) in sorted(ranges.items()):
            out = d / f"no-{f}"
            if out.exists() and out.stat().st_size == b - a:
                continue
            log(f"index {f} bytes {a}-{b} ({(b - a) / 1e6:.0f} MB)")
            with cl.stream("GET", f"{BASE}cc-index/collections/{crawl}/indexes/{f}",
                           headers={"Range": f"bytes={a}-{b - 1}"}) as r:
                r.raise_for_status()
                with out.open("wb") as fh:
                    for chunk in r.iter_bytes(1 << 20):
                        fh.write(chunk)
    log("index done")


# --------------------------------------------------------------------------- select
def _host_of(surt: str) -> str:
    return ".".join(reversed(surt.split(")", 1)[0].split(","))).lower().removeprefix("www.")


def phase_select(crawl: str) -> None:
    d = work(crawl)
    pages: dict[str, dict[str, dict]] = defaultdict(dict)      # host -> url path -> cdx record
    n = 0
    for f in sorted(d.glob("no-cdx-*.gz")):
        with gzip.open(f, "rt", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.startswith("no,"):
                    continue
                try:
                    surt, _ts, js = line.split(" ", 2)
                    rec = json.loads(js)
                except ValueError:
                    continue
                n += 1
                if rec.get("status") != "200" or "html" not in (rec.get("mime-detected") or rec.get("mime") or ""):
                    continue
                host = _host_of(surt)
                sp = urlsplit(rec["url"])
                path = sp.path or "/"
                if sp.query:
                    continue
                if not (HOME_RX.match(path) or INFO_RX.search(path)):
                    continue
                prev = pages[host].get(path)
                if prev is None or rec.get("timestamp", "") > prev.get("timestamp", ""):
                    pages[host][path] = {"url": rec["url"], "filename": rec["filename"], "offset": int(rec["offset"]),
                                         "length": int(rec["length"]), "charset": rec.get("charset")}
    out = (d / "select.jsonl").open("w", encoding="utf-8")
    stats = Counter()
    for host in sorted(pages):
        ps = pages[host]
        homes = sorted((p for p in ps if HOME_RX.match(p)), key=len)
        infos = sorted((p for p in ps if not HOME_RX.match(p)), key=lambda p: (p.count("/"), len(p)))[:MAX_INFO]
        for kind, p in ([("home", homes[0])] if homes else []) + [("info", p) for p in infos]:
            out.write(json.dumps({"host": host, "kind": kind, **ps[p]}) + "\n")
            stats[kind] += 1
        stats["hosts"] += 1
    out.close()
    log(f"select: {n} .no captures -> {stats['hosts']} hosts, {stats['home']} home + {stats['info']} info pages")


# --------------------------------------------------------------------------- fetch
def _parse_warc(raw: bytes, charset: str | None) -> str | None:
    try:
        data = gzip.decompress(raw)
    except Exception:
        return None
    parts = data.split(b"\r\n\r\n", 2)            # WARC headers, HTTP headers, payload
    if len(parts) < 3:
        return None
    http_head, body = parts[1], parts[2]
    m = re.search(rb"charset=([\w-]+)", http_head, re.I)
    enc = (m.group(1).decode() if m else None) or charset or "utf-8"
    try:
        return body.decode(enc, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


async def phase_fetch(crawl: str, concurrency: int, sample: int | None, seed: int) -> None:
    d = work(crawl)
    sel = [json.loads(x) for x in (d / "select.jsonl").open(encoding="utf-8")]
    if sample:
        hosts = sorted({r["host"] for r in sel})
        random.Random(seed).shuffle(hosts)
        keep = set(hosts[:sample])
        sel = [r for r in sel if r["host"] in keep]
    out_path = d / ("pages.jsonl" if not sample else f"pages-sample{sample}.jsonl")
    done = set()
    if out_path.exists():
        for line in out_path.open(encoding="utf-8"):
            try:
                row = json.loads(line)
                if "error" not in row:
                    done.add(row["url"])
            except Exception:
                pass
    todo = [r for r in sel if r["url"] not in done]
    log(f"fetch: {len(sel)} selected, {len(done)} done, {len(todo)} to go")
    out = out_path.open("a", encoding="utf-8")
    stats = Counter()
    t0 = time.time()
    sem = asyncio.Semaphore(concurrency)
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    resume = asyncio.Event()
    resume.set()

    async def blocked_pause() -> None:
        # CloudFront rate block: stop all workers, then probe once every 5 minutes
        if not resume.is_set():
            return await resume.wait()
        resume.clear()
        stats["blocked_pauses"] += 1
        log("Common Crawl CDN block (HTTP 403): pausing all workers")
        while True:
            await asyncio.sleep(300)
            try:
                pr = await cl.get(BASE + "crawl-data/index.html")
                if pr.status_code != 403:
                    break
            except httpx.HTTPError:
                pass
        log("block lifted: resuming")
        resume.set()

    async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=60, limits=limits, http2=False) as cl:
        async def one(r: dict) -> None:
            async with sem:
                rng = f"bytes={r['offset']}-{r['offset'] + r['length'] - 1}"
                for attempt in range(8):
                    await resume.wait()
                    try:
                        res = await cl.get(BASE + r["filename"], headers={"Range": rng})
                    except httpx.HTTPError:
                        await asyncio.sleep(2 ** min(attempt, 5))
                        continue
                    if res.status_code == 403:
                        await blocked_pause()
                        continue
                    if res.status_code in (429, 500, 502, 503, 504):
                        stats["slowdown"] += 1
                        await asyncio.sleep(2 ** min(attempt, 5) + random.random())
                        continue
                    break
                else:
                    stats["failed"] += 1
                    return
                if res.status_code != 206:
                    stats[f"http_{res.status_code}"] += 1
                    return
                html = _parse_warc(res.content, r.get("charset"))
                if html is None:
                    stats["bad_record"] += 1
                    return
                try:
                    x = extract_page(html, r["url"])
                except Exception:
                    stats["extract_error"] += 1
                    return
                found = x["orgnrs"]
                kw = sorted({f["orgnr"] for f in found if f["kw"]} | set(x["brreg_links"]))
                hits = {}
                for f in found:
                    if f["kw"] and f["orgnr"] not in hits:
                        hits[f["orgnr"]] = {"src": f["src"], "ctx": f["ctx"][:200], "credit": f["credit_ctx"],
                                            "group": f["group_ctx"]}
                for o in x["brreg_links"]:
                    hits.setdefault(o, {"src": "brreg_link", "ctx": "link to registry page", "credit": False,
                                        "group": False})
                stats["pages"] += 1
                stats["with_kw"] += bool(kw)
                out.write(json.dumps({"url": r["url"], "host": r["host"], "kind": r["kind"],
                                      "title": x["title"][:160], "site_name": x["meta"].get("og:site_name", "")[:120],
                                      "orgnrs_kw": kw[:20], "orgnrs_all": sorted({f["orgnr"] for f in found})[:20],
                                      "hits": dict(list(hits.items())[:10])}, ensure_ascii=False) + "\n")

        for i in range(0, len(todo), 2000):
            await asyncio.gather(*(one(r) for r in todo[i:i + 2000]))
            out.flush()
            el = time.time() - t0
            log(f"fetch {min(i + 2000, len(todo))}/{len(todo)} {dict(stats)} {(i + 2000) / max(el, 1e-6):.0f}/s")
    out.close()


# --------------------------------------------------------------------------- live crawl of web-graph domains
LIVE = ROOT / "data" / "cc" / "live"


def _universe() -> dict[str, tuple[str, str]]:
    out = {}
    with gzip.open(UNIVERSE, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                r = json.loads(line)
                out[r["organisation_number"]] = (r.get("name") or "", r.get("legal_form") or "")
    return out


async def phase_live_dns(nameserver: str, concurrency: int) -> None:
    import dns.asyncresolver
    import dns.resolver
    LIVE.mkdir(parents=True, exist_ok=True)
    todo_all = [x.strip() for x in (ROOT / "data" / "cc" / "live-todo.txt").open() if x.strip()]
    path = LIVE / "dns.jsonl"
    done = {json.loads(x)["key"] for x in path.open()} if path.exists() else set()
    todo = [d for d in todo_all if d not in done]
    log(f"live dns: {len(done)} done, {len(todo)} to resolve via {nameserver}")
    r = dns.asyncresolver.Resolver(configure=False)
    r.nameservers = [nameserver]
    r.timeout, r.lifetime = 1.5, 7
    sem = asyncio.Semaphore(concurrency)
    out = path.open("a", encoding="utf-8")
    stats = Counter()

    async def resolve(host: str) -> bool:
        for rtype in ("A", "AAAA"):
            try:
                await r.resolve(host, rtype)
                return True
            except (dns.resolver.NXDOMAIN, dns.resolver.NoNameservers):
                return False
            except dns.resolver.NoAnswer:
                continue
            except Exception:
                return False
        return False

    async def one(d: str) -> None:
        async with sem:
            ok = None
            for h in (d, f"www.{d}"):
                if await resolve(h):
                    ok = h
                    break
            stats["ok" if ok else "nx"] += 1
            out.write(json.dumps({"key": d, "ok": bool(ok), "host": ok}) + "\n")

    for k in range(0, len(todo), 5000):
        await asyncio.gather(*(one(d) for d in todo[k:k + 5000]))
        out.flush()
        log(f"live dns {min(k + 5000, len(todo))}/{len(todo)} {dict(stats)}")
    out.close()


async def phase_live_crawl(concurrency: int, shard: int, shards: int) -> None:
    import zlib
    sys.path.insert(0, str(ROOT / "tools"))
    import build_site_discovery as BSD
    from nordic_company_agent.http import BudgetedClient
    targets = set(_universe())
    ok = {}
    for line in (LIVE / "dns.jsonl").open():
        x = json.loads(line)
        if x.get("ok"):
            ok[x["key"]] = x["host"]
    path = LIVE / f"crawl-{shard}.jsonl"
    done = set()
    for f in LIVE.glob("crawl-*.jsonl"):
        for line in f.open():
            try:
                done.add(json.loads(line)["key"])
            except Exception:
                pass
    todo = [d for d in sorted(ok) if f"https://{d}/" not in done and zlib.crc32(d.encode()) % shards == shard]
    log(f"live crawl shard {shard}/{shards}: {len(todo)} to crawl")
    client = BudgetedClient(max_requests=10 ** 9, timeout=12.0, max_connections=concurrency * 2,
                            record_calls=False, max_body_bytes=1_500_000)
    out = path.open("a", encoding="utf-8")
    sem = asyncio.Semaphore(concurrency)
    stats = Counter()
    t0 = time.time()

    async def one(d: str) -> None:
        async with sem:
            e = {"key": f"https://{d}/", "kinds": ["webgraph"], "orgnrs": [], "prio": 2, "_targets": targets}
            try:
                rec = await asyncio.wait_for(BSD.crawl_one(client, e, ok[d]), timeout=120)
            except Exception as exc:
                rec = {"key": e["key"], "host": ok[d], "kinds": e["kinds"], "targets": [], "pages": [],
                       "errors": ["crawl:" + type(exc).__name__]}
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            stats["done"] += 1
            stats["hit"] += any(pg.get("target_hits") for pg in rec["pages"])

    for k in range(0, len(todo), 1000):
        await asyncio.gather(*(one(d) for d in todo[k:k + 1000]))
        out.flush()
        client._robots.clear()
        client._robots_locks.clear()
        client._gates.clear()
        rate = stats["done"] / max(time.time() - t0, 1e-6)
        log(f"live crawl {stats['done']}/{len(todo)} hit={stats['hit']} req={client.used} {rate:.1f}/s "
            f"eta={(len(todo) - stats['done']) / max(rate, 1e-6) / 60:.0f}m")
    out.close()
    await client.aclose()


# --------------------------------------------------------------------------- import crawl records as pages
def _crawl_pages(files, other_only: bool) -> list[dict]:
    """Page rows (same shape as the Common Crawl pages) from tools/build_site_discovery.py crawl records.
    other_only: the name-guess crawl's own targets are already leads in site_discovery; keep the other labelled
    organisation numbers the pages printed (their hit context was not recorded: the brand filter and the live
    gate decide)."""
    rows = []
    for f in files:
        for line in open(f, encoding="utf-8"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            own = set(r.get("targets") or [])
            for pg in r.get("pages") or []:
                if pg.get("status") != 200 or not (pg.get("orgnrs_kw") or pg.get("target_hits")):
                    continue
                host = (urlsplit(pg.get("final_url") or "").hostname or "").lower().removeprefix("www.")
                if not host:
                    continue
                hits = {}
                for o, h in (pg.get("target_hits") or {}).items():
                    if not other_only:
                        hits[o] = {"src": h.get("src"), "ctx": (h.get("ctx") or "")[:200], "credit": h.get("credit"),
                                   "group": h.get("group")}
                for o in pg.get("orgnrs_kw") or []:
                    if o not in hits and not (other_only and o in own):
                        hits[o] = {"src": "labelled", "ctx": "", "credit": False, "group": False}
                rows.append({"url": pg["final_url"], "host": host, "kind": "home" if pg["kind"] == "home" else "info",
                             "title": pg.get("title") or "", "site_name": pg.get("site_name") or "",
                             "orgnrs_kw": pg.get("orgnrs_kw") or [], "hits": hits})
    return rows


def phase_import() -> None:
    disc = ROOT / "data" / "work" / "discovery"
    sources = {"namecrawl": (sorted(disc.glob("crawl-[0-9].jsonl")) + [disc / "crawl-retry.jsonl", disc / "crawl.jsonl"], True),
               "live": (sorted(LIVE.glob("crawl-*.jsonl")), False)}
    for name, (files, other_only) in sources.items():
        files = [f for f in files if f.exists()]
        rows = _crawl_pages(files, other_only)
        d = ROOT / "data" / "cc" / name
        d.mkdir(parents=True, exist_ok=True)
        with (d / "pages.jsonl").open("w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        log(f"import {name}: {len(files)} crawl files -> {len(rows)} pages with labelled org numbers")


# --------------------------------------------------------------------------- join
def _brand_ok(name: str, form: str, host: str, texts: list[str]) -> bool:
    """Cheap pre-check of the website gate's brand rule (verify.py, policy I): a distinctive legal-name token in the
    domain label or in a page title / site name. Leads that cannot pass it would only spend run-time requests."""
    from nordic_company_agent.web import verify as V
    prof = V.name_profile(name, form)
    dist = V._distinctive_tokens(prof)
    label = V.squash(reg_domain(host).split(".")[0])
    text = " | ".join(t for t in texts if t)
    if len(prof.joined) >= 5 and prof.joined in label:
        return True
    if any(len(V.squash(t)) >= 3 and V.squash(t) in label for t in dist):
        return True
    if any(V.token_in(t, text) for t in dist):
        return True
    return not dist and bool(prof.joined) and V.token_in(prof.joined, V.squash(text))


SOURCES = {"commoncrawl": None, "namecrawl": "namecrawl", "live": "live"}   # lead kind -> data/cc/<dir>/pages.jsonl


def phase_join(crawl: str, pages_file: str | None) -> None:
    uni = _universe()
    existing: dict[str, set[str]] = defaultdict(set)              # orgnr -> registrable domains already leads
    with gzip.open(CACHE / "site_discovery.jsonl.gz", "rt", encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            for ld in row.get("leads") or []:
                u = ld.get("final_url") or ld.get("start") or ""
                existing[row["organisation_number"]].add(reg_domain(urlsplit(u if "://" in u else "https://" + u).hostname or ""))
    files = {"commoncrawl": work(crawl) / (pages_file or "pages.jsonl")}
    if not pages_file:
        files.update({"namecrawl": ROOT / "data" / "cc" / "namecrawl" / "pages.jsonl",
                      "live": ROOT / "data" / "cc" / "live" / "pages.jsonl"})
    by_host: dict[str, list[dict]] = defaultdict(list)
    for src, path in files.items():
        if not path.exists():
            continue
        for line in path.open(encoding="utf-8"):
            pg = json.loads(line)
            if "error" not in pg:
                pg["_src"] = src
                by_host[pg["host"]].append(pg)
    leads: dict[str, list[dict]] = defaultdict(list)
    stats = Counter()
    for host, ps in by_host.items():
        home = next((pg for pg in ps if pg["kind"] == "home"), None)
        host_kw = set().union(*(set(pg["orgnrs_kw"]) for pg in ps))
        stats["hosts"] += 1
        if not host_kw:
            continue
        stats["hosts_with_kw"] += 1
        if len(host_kw) > MAX_KW_ORGNRS:
            stats["hosts_directory"] += 1
            continue
        texts = [x for pg in ps for x in (pg.get("title"), pg.get("site_name"))]
        for pg in sorted(ps, key=lambda pg: pg["kind"] != "home"):
            for org, h in pg["hits"].items():
                if org not in uni or h.get("credit") or any(L["start"] == f"https://{host}/" for L in leads.get(org, [])):
                    continue
                if reg_domain(host) in existing.get(org, set()):
                    stats["already_lead"] += 1
                    continue
                if not _brand_ok(*uni[org], host, texts):
                    stats["brand_filtered"] += 1
                    continue
                stats[f"lead_{pg['_src']}"] += 1
                leads[org].append({
                    "kind": "commoncrawl" if pg["_src"] == "commoncrawl" else f"{pg['_src']}_crawl",
                    "start": f"https://{host}/", "final_url": home["url"] if home else None,
                    "proof_url": pg["url"], "proof_page": pg["kind"], "proof_ctx": h.get("ctx"),
                    "proof_src": h.get("src"), "proof_kw": True, "credit_ctx": bool(h.get("credit")),
                    "group_ctx": bool(h.get("group")), "other_orgnrs": sorted(host_kw - {org})[:10],
                    "title": (home or pg).get("title"), "site_name": (home or pg).get("site_name"),
                    "checked_at": crawl if pg["_src"] == "commoncrawl" else "2026-10-14",
                })
    # a hit on a sub-domain (gavekort.firma.no, nettbank.bank.no): try the registrable domain's home page first
    for org, ls in leads.items():
        roots = {f"https://{reg_domain(urlsplit(L['start']).hostname or '')}/" for L in ls}
        for root in sorted(roots - {L["start"] for L in ls}):
            if reg_domain(urlsplit(root).hostname or "") in existing.get(org, set()):
                continue
            ls.append({"kind": "commoncrawl_root", "start": root, "final_url": None, "proof_url": None,
                       "proof_page": None, "proof_ctx": None, "proof_src": None, "proof_kw": False,
                       "credit_ctx": False, "group_ctx": False, "other_orgnrs": [], "title": None,
                       "site_name": None, "checked_at": crawl})
            stats["subdomain_roots"] += 1

    def order(L: dict) -> tuple:
        h = urlsplit(L["start"]).hostname or ""
        return (h != reg_domain(h), bool(L["other_orgnrs"]), L["group_ctx"], L["proof_page"] != "home")

    out_path = CACHE / "site_discovery_cc.jsonl.gz"
    with gzip.open(out_path, "wt", encoding="utf-8") as fh:
        for org in sorted(o for o in leads if leads[o]):
            fh.write(json.dumps({"organisation_number": org, "leads": sorted(leads[org], key=order)[:3],
                                 "checked": []}, ensure_ascii=False) + "\n")
    meta = {"name": "site_discovery_cc", "built_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            .replace("+00:00", "Z"),
            "sources": {"commoncrawl": f"Common Crawl {crawl} archived .no pages (CDX + WARC range reads)",
                        "live_crawl": "live crawl (robots.txt honoured) of .no domains listed in the Common Crawl "
                                      "web graph cc-main-2026-jul-aug-sep that no other discovery source covered",
                        "namecrawl": "labelled organisation numbers of other companies on pages fetched by the "
                                     "site_discovery crawl"},
            "source_terms": "https://commoncrawl.org/terms-of-use",
            "companies_with_lead": sum(1 for o in leads if leads[o]), "stats": dict(stats),
            "description": "Discovery leads only, for companies without a site_discovery lead on the same domain: "
                           "hosts whose home/contact/about/privacy pages printed this labelled organisation number "
                           "(at most 3 labelled numbers per host; web-agency credit lines and hosts without a "
                           "distinctive legal-name token in the domain or title skipped). The agent re-fetches and "
                           "re-verifies every lead live before publishing any claim."}
    (CACHE / "site_discovery_cc.meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    log(json.dumps(meta, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["index", "select", "fetch", "live-dns", "live-crawl", "import", "join"])
    ap.add_argument("--crawl", default="CC-MAIN-2026-39")
    ap.add_argument("--sample", type=int)
    ap.add_argument("--seed", type=int, default=20261014)
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--nameserver", default="10.184.0.11")
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--pages-file")
    a = ap.parse_args()
    if a.phase == "index":
        phase_index(a.crawl)
    elif a.phase == "select":
        phase_select(a.crawl)
    elif a.phase == "fetch":
        asyncio.run(phase_fetch(a.crawl, a.concurrency, a.sample, a.seed))
    elif a.phase == "live-dns":
        asyncio.run(phase_live_dns(a.nameserver, max(a.concurrency, 100)))
    elif a.phase == "live-crawl":
        asyncio.run(phase_live_crawl(a.concurrency, a.shard, a.shards))
    elif a.phase == "import":
        phase_import()
    else:
        phase_join(a.crawl, a.pages_file)


if __name__ == "__main__":
    main()
