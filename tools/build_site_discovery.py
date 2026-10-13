#!/usr/bin/env python3
"""Offline website-discovery crawl over the public Signalpost universe (declared cache builder).

For every universe company it proposes candidate domains (registry website, own-domain e-mail,
legal-name slugs), resolves them, fetches robots.txt + home page (+ up to three contact/about/
terms/privacy pages) and records which checksum-valid organisation numbers each site prints.

Output `cache/site_discovery.jsonl.gz` lists, per organisation number, the domains on which that
exact number was found (a *discovery lead*). The runtime agent re-fetches the site live and
re-runs the identity gate before publishing anything, so the cache never acts as evidence.

Phases (each resumable; state under data/work/discovery/):
  candidates -> dns -> crawl -> join
Usage:
  uv run python tools/build_site_discovery.py all --universe data/signalpost-company-universe-2025.jsonl.gz \
      --bulk data/bulk/enheter.csv.gz
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import dns.asyncresolver  # noqa: E402
import dns.resolver  # noqa: E402

from nordic_company_agent.http import (BROWSER_ACCEPT, BudgetedClient, RobotsDisallowed,  # noqa: E402
                                       UnsafeURL, utcnow)
from nordic_company_agent.registry_csv import iter_snapshot  # noqa: E402
from nordic_company_agent.web.candidates import candidates  # noqa: E402
from nordic_company_agent.web.extract import (client_side_target, extract_page, pick_subpages,  # noqa: E402
                                              reg_domain)

WORK = ROOT / "data" / "work" / "discovery"
CACHE = ROOT / "cache"
KIND_PRIO = {"registry_website": 0, "email_domain": 1}
SUBPAGE_ORDER = ("contact", "about", "impressum", "terms", "privacy")
BLOCKING_SIGNALS = ("parking_url", "parked_text", "bot_wall", "cjk_text")


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def read_jsonl(path: Path):
    if not path.exists():
        return
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue  # tolerate a torn last line after a crash


# --------------------------------------------------------------------------- phase 1
def phase_candidates(universe: Path, bulk: Path) -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    uni: dict[str, dict] = {}
    for row in read_jsonl(universe):
        uni[str(row["organisation_number"])] = row
    log(f"universe: {len(uni)} companies")
    domains: dict[str, dict] = {}
    n = 0
    with gzip.open(WORK / "companies.jsonl.gz", "wt", encoding="utf-8") as out:
        for rec in iter_snapshot(bulk):
            org = str(rec.get("organisasjonsnummer") or "")
            if org not in uni:
                continue
            n += 1
            emp = uni[org].get("employees") or rec.get("antallAnsatte") or 0
            cands = candidates(rec)
            row = {"orgnr": org, "name": rec.get("navn"), "form": (rec.get("organisasjonsform") or {}).get("kode"),
                   "employees": emp, "cands": cands,
                   "rec": {k: rec.get(k) for k in ("navn", "hjemmeside", "epostadresse", "telefon", "mobil",
                                                   "forretningsadresse", "postadresse") if rec.get(k)}}
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            for kind, d in cands:
                if kind == "registry_website":
                    key = d  # crawl the exact filed URL (may be a sub-path on a shared host)
                    host = urlsplit(d).hostname or ""
                else:
                    key = d
                    host = d
                e = domains.setdefault(key, {"key": key, "host": host, "kinds": [], "orgnrs": [], "prio": 9, "emp": 0})
                if kind not in e["kinds"]:
                    e["kinds"].append(kind)
                e["orgnrs"].append(org)
                pr = KIND_PRIO.get(kind, 2 if (emp or 0) >= 5 else 3)
                e["prio"] = min(e["prio"], pr)
                e["emp"] = max(e["emp"], emp or 0)
    with gzip.open(WORK / "domains.jsonl.gz", "wt", encoding="utf-8") as out:
        for e in sorted(domains.values(), key=lambda e: (e["prio"], -e["emp"])):
            out.write(json.dumps(e, ensure_ascii=False) + "\n")
    c = Counter(e["prio"] for e in domains.values())
    log(f"companies with registry rows: {n}; unique crawl keys: {len(domains)}; by prio {dict(c)}")


# --------------------------------------------------------------------------- phase 2
async def phase_dns(nameserver: str, concurrency: int) -> None:
    done = {r["key"] for r in read_jsonl(WORK / "dns.jsonl")}
    todo = [e for e in read_jsonl(WORK / "domains.jsonl.gz") if e["key"] not in done]
    log(f"dns: {len(done)} done, {len(todo)} to resolve via {nameserver}")
    r = dns.asyncresolver.Resolver(configure=False)
    r.nameservers = [nameserver]
    r.timeout = 1.5   # per-try: the campus resolver drops some UDP queries; retry fast
    r.lifetime = 7
    sem = asyncio.Semaphore(concurrency)
    out = (WORK / "dns.jsonl").open("a", encoding="utf-8")
    stats = Counter()
    t0 = time.time()

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

    async def one(e: dict) -> None:
        async with sem:
            host = e["host"]
            ok_host = None
            for h in ([host] + ([f"www.{host}"] if not host.startswith("www.") else [])):
                if await resolve(h):
                    ok_host = h
                    break
            stats["ok" if ok_host else "nx"] += 1
            out.write(json.dumps({"key": e["key"], "ok": bool(ok_host), "host": ok_host}) + "\n")

    for i in range(0, len(todo), 5000):
        await asyncio.gather(*(one(e) for e in todo[i:i + 5000]))
        out.flush()
        rate = (i + 5000) / max(1e-6, time.time() - t0)
        log(f"dns {min(i + 5000, len(todo))}/{len(todo)} {dict(stats)} {rate:.0f}/s")
    out.close()


# --------------------------------------------------------------------------- phase 3
def _page_record(kind: str, res, html: str, targets: set[str]) -> dict:
    x = extract_page(html, res.final_url)
    found = x["orgnrs"]
    hits = {}
    for f in found:
        if f["orgnr"] in targets and f["orgnr"] not in hits:
            hits[f["orgnr"]] = {"src": f["src"], "kw": f["kw"], "ctx": f["ctx"], "credit": f["credit_ctx"],
                                "group": f["group_ctx"]}
    for o in x["brreg_links"]:
        if o in targets and o not in hits:
            hits[o] = {"src": "brreg_link", "kw": True, "ctx": "link to registry page", "credit": False, "group": False}
    jl_names = [o.get("name") for o in x["jsonld_orgs"] if isinstance(o.get("name"), str)][:3]
    return {
        "kind": kind, "url": res.url, "final_url": res.final_url, "status": res.status, "sha256": res.sha256,
        "ctype": res.headers.get("content-type", "")[:60], "title": x["title"][:160],
        "site_name": x["meta"].get("og:site_name", "")[:120],
        "description": (x["meta"].get("description") or x["meta"].get("og:description") or "")[:300],
        "lang": x["lang"], "text_len": x["text_len"], "signals": x["placeholder_signals"],
        "orgnrs_kw": sorted({f["orgnr"] for f in found if f["kw"]} | set(x["brreg_links"])),
        "orgnrs_all": sorted({f["orgnr"] for f in found}), "target_hits": hits, "jsonld_names": jl_names,
        "generator": x["generator"],
    }


def _blocking(rec: dict) -> bool:
    return any(s.startswith(BLOCKING_SIGNALS) for s in rec.get("signals", []))


async def crawl_one(client: BudgetedClient, e: dict, host: str) -> dict:
    targets = set(e["orgnrs"])
    max_sub = 3 if e.get("prio", 9) <= 2 else 2
    rec: dict = {"key": e["key"], "host": host, "kinds": e["kinds"], "targets": sorted(targets), "pages": [],
                 "checked_at": utcnow().isoformat().replace("+00:00", "Z")}
    if "registry_website" in e["kinds"]:
        start = e["key"]
        sp = urlsplit(start)
        tries = [start, f"http://{sp.netloc}{sp.path or '/'}"]
        if host != sp.hostname:
            tries.insert(1, f"https://{host}{sp.path or '/'}")
    else:
        tries = [f"https://{host}/", f"http://{host}/"]
    home = None
    errors = []
    for u in tries:
        try:
            res = await client.get(u, source="discovery", headers={"Accept": BROWSER_ACCEPT}, respect_robots=True,
                                   public_only=True, max_bytes=1_500_000, retries=0, max_redirects=6)
        except RobotsDisallowed as exc:
            rec["robots_blocked"] = True
            rec["robots_reason"] = str(exc)[-90:]
            break
        except UnsafeURL as exc:
            errors.append(f"unsafe:{exc}"[:80])
            break
        except Exception as exc:  # transport errors, TLS, timeouts
            errors.append(type(exc).__name__)
            continue
        if res.status >= 400 and res.status not in (401, 403, 429):
            errors.append(f"http_{res.status}")
            continue
        home = res
        break
    if errors:
        rec["errors"] = errors
    if home is None:
        return rec
    html = home.text if ("html" in home.headers.get("content-type", "").lower() or home.content[:200].lstrip()[:1] == b"<") else ""
    if home.status in (401, 403, 429) or not html:
        rec["home_status"] = home.status
        return rec
    cs = client_side_target(html, home.final_url)
    if cs and cs.rstrip("/") != home.final_url.rstrip("/"):
        try:
            res2 = await client.get(cs, source="discovery", headers={"Accept": BROWSER_ACCEPT}, respect_robots=True,
                                    public_only=True, max_bytes=1_500_000, retries=0, max_redirects=6)
            if res2.status < 400:
                rec["client_redirect"] = cs
                home, html = res2, res2.text
        except Exception as exc:
            rec.setdefault("errors", []).append("client_redirect:" + type(exc).__name__)
    hp = _page_record("home", home, html, targets)
    rec["pages"].append(hp)
    rec["final_url"] = home.final_url
    rec["final_domain"] = reg_domain(urlsplit(home.final_url).hostname or "")
    if not hp["target_hits"] and not _blocking(hp):
        subs = pick_subpages(html, home.final_url)
        n = 0
        for kind in SUBPAGE_ORDER:
            if kind not in subs or n >= max_sub:
                continue
            url = subs[kind][0]
            try:
                r3 = await client.get(url, source="discovery", headers={"Accept": BROWSER_ACCEPT}, respect_robots=True,
                                      public_only=True, max_bytes=1_500_000, retries=0, max_redirects=4)
            except RobotsDisallowed:
                continue
            except Exception as exc:
                rec.setdefault("errors", []).append(f"{kind}:{type(exc).__name__}")
                n += 1
                continue
            n += 1
            if r3.status >= 400 or "html" not in r3.headers.get("content-type", "html").lower():
                continue
            pr = _page_record(kind, r3, r3.text, targets)
            rec["pages"].append(pr)
            if pr["target_hits"]:
                break
    return rec


def crawl_files() -> list[Path]:
    return sorted(WORK.glob("crawl*.jsonl"))


async def phase_crawl(concurrency: int, limit: int | None, max_prio: int, shard: int = 0, shards: int = 1) -> None:
    import zlib
    done = {r["key"] for f in crawl_files() for r in read_jsonl(f)}
    dns_ok = {r["key"]: r["host"] for r in read_jsonl(WORK / "dns.jsonl") if r.get("ok")}
    todo = [e for e in read_jsonl(WORK / "domains.jsonl.gz")
            if e["key"] in dns_ok and e["key"] not in done and e["prio"] <= max_prio
            and zlib.crc32(e["key"].encode()) % shards == shard]
    if limit:
        todo = todo[:limit]
    log(f"crawl: {len(done)} done, {len(todo)} to crawl (concurrency {concurrency})")
    client = BudgetedClient(max_requests=10 ** 9, timeout=12.0, max_connections=concurrency * 2,
                            record_calls=False, max_body_bytes=1_500_000)
    out = (WORK / (f"crawl-{shard}.jsonl" if shards > 1 else "crawl.jsonl")).open("a", encoding="utf-8")
    sem = asyncio.Semaphore(concurrency)
    stats = Counter()
    t0 = time.time()
    last = [t0]

    async def one(e: dict) -> None:
        async with sem:
            try:
                rec = await asyncio.wait_for(crawl_one(client, e, dns_ok[e["key"]]), timeout=120)
            except Exception as exc:
                rec = {"key": e["key"], "host": dns_ok[e["key"]], "kinds": e["kinds"], "targets": e["orgnrs"],
                       "pages": [], "errors": ["crawl:" + type(exc).__name__]}
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            stats["done"] += 1
            if any(p.get("target_hits") for p in rec["pages"]):
                stats["hit"] += 1
            if rec["pages"]:
                stats["reachable"] += 1
            now = time.time()
            if now - last[0] > 30:
                last[0] = now
                out.flush()
                rate = stats["done"] / (now - t0)
                log(f"crawl {stats['done']}/{len(todo)} reach={stats['reachable']} hit={stats['hit']} "
                    f"req={client.used} {rate:.1f} sites/s eta={(len(todo) - stats['done']) / max(rate, 1e-6) / 60:.0f}m")

    # Interleave hosts: process in chunks to keep memory bounded.
    for i in range(0, len(todo), 2000):
        await asyncio.gather(*(one(e) for e in todo[i:i + 2000]))
        out.flush()
        # robots cache can grow unbounded: one entry per origin; drop it between chunks.
        client._robots.clear()
        client._robots_locks.clear()
        client._gates.clear()
    out.close()
    await client.aclose()
    log(f"crawl finished: {dict(stats)} requests={client.used}")


# --------------------------------------------------------------------------- phase 4
TRANSIENT = ("ConnectTimeout", "ReadTimeout", "PoolTimeout", "WriteTimeout", "ConnectError", "RemoteProtocolError",
             "ReadError", "crawl:TimeoutError")


def _transient(r: dict) -> bool:
    if r.get("pages"):
        return False
    if r.get("robots_blocked"):
        return "timed out" in (r.get("robots_reason") or "timed out")  # old records lack a reason
    errs = r.get("errors") or []
    return bool(errs) and all(e.split(":")[0] in TRANSIENT or e.startswith("crawl:") for e in errs)


async def phase_recrawl(concurrency: int) -> None:
    """Re-crawl keys whose only failures were transient network errors (timeouts/connection resets)."""
    last = {}
    for f in crawl_files():
        for r in read_jsonl(f):
            last[r["key"]] = r
    keys = {k for k, r in last.items() if _transient(r)}
    dns_ok = {r["key"]: r["host"] for r in read_jsonl(WORK / "dns.jsonl") if r.get("ok")}
    todo = [e for e in read_jsonl(WORK / "domains.jsonl.gz") if e["key"] in keys and e["key"] in dns_ok]
    log(f"recrawl: {len(todo)} keys with only transient errors")
    client = BudgetedClient(max_requests=10 ** 9, timeout=20.0, max_connections=concurrency * 2,
                            record_calls=False, max_body_bytes=1_500_000)
    out = (WORK / "crawl-retry.jsonl").open("a", encoding="utf-8")
    sem = asyncio.Semaphore(concurrency)
    stats = Counter()

    async def one(e: dict) -> None:
        async with sem:
            try:
                rec = await asyncio.wait_for(crawl_one(client, e, dns_ok[e["key"]]), timeout=150)
            except Exception as exc:
                rec = {"key": e["key"], "host": dns_ok[e["key"]], "kinds": e["kinds"], "targets": e["orgnrs"],
                       "pages": [], "errors": ["crawl:" + type(exc).__name__]}
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            stats["done"] += 1
            stats["reach"] += bool(rec["pages"])
            stats["hit"] += any(p.get("target_hits") for p in rec["pages"])

    for i in range(0, len(todo), 1000):
        await asyncio.gather(*(one(e) for e in todo[i:i + 1000]))
        out.flush()
        client._robots.clear(); client._robots_locks.clear(); client._gates.clear()
        log(f"recrawl {min(i + 1000, len(todo))}/{len(todo)} {dict(stats)}")
    out.close()
    await client.aclose()


def phase_join() -> None:
    by_key = {}
    for f in crawl_files():
        for r in read_jsonl(f):
            by_key[r["key"]] = r  # last write wins on re-crawls
    dns_nx = {r["key"] for r in read_jsonl(WORK / "dns.jsonl") if not r.get("ok")}
    CACHE.mkdir(parents=True, exist_ok=True)
    n_comp = n_hit = 0
    kinds = Counter()
    out_path = CACHE / "site_discovery.jsonl.gz"
    tmp = out_path.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as out:
        for c in read_jsonl(WORK / "companies.jsonl.gz"):
            n_comp += 1
            org = c["orgnr"]
            leads = []
            checked = []
            for kind, d in c["cands"]:
                r = by_key.get(d)
                if r is None:
                    if d in dns_nx:
                        checked.append(kind)  # neither the domain nor www.<domain> resolves
                    continue
                checked.append(kind)
                for p in r.get("pages", []):
                    hit = p.get("target_hits", {}).get(org)
                    if not hit or _blocking(p):
                        continue
                    others = sorted(set(p.get("orgnrs_kw", [])) - {org})
                    leads.append({
                        "kind": kind, "start": d, "final_url": r.get("final_url"), "final_domain": r.get("final_domain"),
                        "proof_url": p["final_url"], "proof_page": p["kind"], "proof_ctx": hit["ctx"][:200],
                        "proof_src": hit["src"], "proof_kw": hit["kw"], "credit_ctx": hit.get("credit"),
                        "group_ctx": hit.get("group"), "other_orgnrs": others[:10], "title": p.get("title"),
                        "site_name": p.get("site_name"), "checked_at": r.get("checked_at"),
                    })
                    break
            if leads:
                n_hit += 1
                leads.sort(key=lambda L: (KIND_PRIO.get(L["kind"], 2), bool(L["other_orgnrs"]), L["credit_ctx"]))
                kinds[leads[0]["kind"]] += 1
            if leads or checked:
                out.write(json.dumps({"organisation_number": org, "leads": leads, "checked": checked},
                                     ensure_ascii=False) + "\n")
    tmp.replace(out_path)
    meta = {"name": "site_discovery", "built_at": utcnow().isoformat().replace("+00:00", "Z"),
            "companies": n_comp, "companies_with_lead": n_hit, "lead_kinds": dict(kinds),
            "crawled_keys": len(by_key),
            "description": "Discovery leads only: domains on which the exact organisation number was printed when "
                           "crawled. The agent re-fetches and re-verifies live before publishing any claim.",
            "method": "candidates (registry website, own-domain e-mail, legal-name slugs) -> DNS -> robots.txt -> "
                      "home + up to 3 contact/about/terms/privacy pages -> checksum-valid org numbers"}
    (CACHE / "site_discovery.meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    log(f"join: {n_hit}/{n_comp} companies with an org-number lead; {dict(kinds)}")


def phase_domains(bulk: Path) -> None:
    """cache/registry_website_domains.json.gz: registry-filed website hosts shared by >= 2 org numbers.
    Such hosts (property managers, chains, umbrella bodies) never prove identity on their own."""
    from nordic_company_agent.web.candidates import normalize_registry_website
    hosts: dict[str, set[str]] = {}
    for rec in iter_snapshot(bulk):
        url = normalize_registry_website(rec.get("hjemmeside"))
        if not url:
            continue
        host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
        if host:
            hosts.setdefault(host, set()).add(str(rec.get("organisasjonsnummer")))
    shared = {h: sorted(o) for h, o in hosts.items() if len(o) >= 2}
    CACHE.mkdir(parents=True, exist_ok=True)
    with gzip.open(CACHE / "registry_website_domains.json.gz", "wt", encoding="utf-8") as fh:
        json.dump(shared, fh, ensure_ascii=False, sort_keys=True)
    meta = {"name": "registry_website_domains", "built_at": utcnow().isoformat().replace("+00:00", "Z"),
            "source": "Enhetsregisteret bulk CSV (hjemmeside), NLOD 2.0", "shared_hosts": len(shared),
            "organisation_numbers": sum(len(v) for v in shared.values()),
            "description": "Website hosts filed in Enhetsregisteret by two or more organisation numbers; a site on such "
                           "a host is not exclusive to one entity, so only an org number printed on it can prove identity."}
    (CACHE / "registry_website_domains.meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    log(f"domains: {len(shared)} shared hosts covering {meta['organisation_numbers']} org numbers")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["candidates", "dns", "crawl", "recrawl", "join", "domains", "all"])
    ap.add_argument("--universe", default=str(ROOT / "data" / "signalpost-company-universe-2025.jsonl.gz"))
    ap.add_argument("--bulk", default=str(ROOT / "data" / "bulk" / "enheter.csv.gz"))
    ap.add_argument("--nameserver", default=os.environ.get("NCA_DNS", "10.184.0.11"))
    ap.add_argument("--dns-concurrency", type=int, default=200)
    ap.add_argument("--concurrency", type=int, default=250)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--max-prio", type=int, default=9)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--shards", type=int, default=1)
    a = ap.parse_args()
    if a.phase in ("candidates", "all"):
        phase_candidates(Path(a.universe), Path(a.bulk))
    if a.phase in ("dns", "all"):
        asyncio.run(phase_dns(a.nameserver, a.dns_concurrency))
    if a.phase in ("crawl", "all"):
        asyncio.run(phase_crawl(a.concurrency, a.limit, a.max_prio, a.shard, a.shards))
    if a.phase == "recrawl":
        asyncio.run(phase_recrawl(a.concurrency))
    if a.phase in ("join", "all"):
        phase_join()
    if a.phase in ("domains", "all"):
        phase_domains(Path(a.bulk))


if __name__ == "__main__":
    main()
