#!/usr/bin/env python3
"""Offline builder for the declared NAV job-ad cache (`cache/nav_jobs.jsonl.gz` + `.meta.json`).

Source: NAV arbeidsplassen public job-vacancy feed (https://pam-stilling-feed.nav.no), terms at
https://arbeidsplassen.nav.no/vilkar-api (free; inactive ads must be removed). Access uses the
public bearer token served at /api/publicToken.

Phases (each resumable; state under data/work/nav/):
  feed     page /api/v1/feed from ~N days back (If-Modified-Since jumps to that date), follow
           next_url until the open last page (next_url == null); keep every item compactly.
  entries  fold the items to the latest state per ad uuid; for every ad ACTIVE at the end fetch
           /api/v1/feedentry/<uuid> (<= 4 req/s) and keep only the employer / ad fields needed.
           Contact persons (names, e-mails, phones) are never stored.
  catchup  page forward again from the cursor (the build takes hours) and fetch new entries.
  map      employer orgnr (usually an UNDERENHET) -> parent enhet via brreg batch lookups
           (/underenheter?organisasjonsnummer=<<=1000>, then /enheter for the rest).
  write    cache/nav_jobs.jsonl.gz rows (ACTIVE ads, expiry not passed) + meta with the feed cursor.

Usage:
  nohup uv run python tools/build_nav_jobs_cache.py all --days 120 > data/work/nav/build.log 2>&1 &
  uv run python tools/build_nav_jobs_cache.py status
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nordic_company_agent.http import USER_AGENT  # noqa: E402

WORK = ROOT / "data" / "work" / "nav"
CACHE = ROOT / "cache"
NAV = "https://pam-stilling-feed.nav.no"
TOKEN_URL = NAV + "/api/publicToken"
FEED_URL = NAV + "/api/v1/feed"
BRREG = "https://data.brreg.no/enhetsregisteret/api"
JWT_RX = re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+")

PAGES = WORK / "feed_pages.jsonl"
STATE = WORK / "feed_state.json"
ENTRIES = WORK / "entries.jsonl"
ORGMAP = WORK / "orgmap.json"

MAX_RPS = 4.0          # hard politeness cap towards NAV (request starts per second)
CONCURRENCY = 14       # entries take ~3-4 s each, so ~14 in flight gives ~4 req/s


def log(msg: str) -> None:
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def utc_iso(dt: datetime | None = None) -> str:
    dt = dt or datetime.now(timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def read_jsonl(path: Path):
    if not path.exists():
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue  # torn trailing line after a kill: ignore


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


class RateLimiter:
    def __init__(self, rps: float):
        self.interval = 1.0 / rps
        self._lock = asyncio.Lock()
        self._next = 0.0

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            delay = self._next - now
            self._next = max(now, self._next) + self.interval
        if delay > 0:
            await asyncio.sleep(delay)


class Nav:
    def __init__(self) -> None:
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0),
                                        headers={"User-Agent": USER_AGENT}, follow_redirects=False)
        self.limiter = RateLimiter(MAX_RPS)
        self.token: str | None = None
        self.requests = 0
        self._tok_lock = asyncio.Lock()

    async def refresh_token(self) -> None:
        async with self._tok_lock:
            await self.limiter.wait()
            self.requests += 1
            r = await self.client.get(TOKEN_URL)
            r.raise_for_status()
            m = JWT_RX.search(r.text)
            if not m:
                raise RuntimeError("no JWT in publicToken response")
            self.token = m.group(0)
            log("token refreshed")

    async def get(self, url: str, headers: dict[str, str] | None = None) -> httpx.Response:
        if not self.token:
            await self.refresh_token()
        delay = 5.0
        for attempt in range(8):
            await self.limiter.wait()
            self.requests += 1
            h = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
            h.update(headers or {})
            try:
                r = await self.client.get(url, headers=h)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                log(f"transport error {type(exc).__name__} on {url} (attempt {attempt + 1})")
                await asyncio.sleep(delay)
                delay = min(delay * 2, 120)
                continue
            if r.status_code == 401:
                await self.refresh_token()
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                ra = r.headers.get("retry-after", "")
                wait = float(ra) if ra.isdigit() else delay
                log(f"HTTP {r.status_code} on {url}; sleeping {min(wait, 300):.0f}s")
                await asyncio.sleep(min(wait, 300))
                delay = min(delay * 2, 120)
                continue
            return r
        raise RuntimeError(f"giving up on {url}")

    async def aclose(self) -> None:
        await self.client.aclose()


# ----------------------------------------------------------------------------- feed
def compact_item(it: dict[str, Any]) -> dict[str, Any]:
    fe = it.get("_feed_entry") or {}
    return {"uuid": fe.get("uuid") or it.get("id"), "status": fe.get("status"),
            "modified": it.get("date_modified") or fe.get("sistEndret"),
            "business_name": fe.get("businessName") or "", "title": fe.get("title") or it.get("title") or "",
            "municipal": fe.get("municipal") or ""}


def load_state() -> dict[str, Any]:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict[str, Any]) -> None:
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    os.replace(tmp, STATE)


async def phase_feed(nav: Nav, days: int, max_pages: int | None = None) -> dict[str, Any]:
    """Page forward until the open last page. Resumes from feed_state.json (`cursor_url`)."""
    state = load_state()
    headers: dict[str, str] = {}
    if state.get("cursor_url"):
        url = state["cursor_url"]
        log(f"feed: resuming from {url}")
    else:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        state = {"since": utc_iso(since), "pages": 0}
        url = FEED_URL
        headers = {"If-Modified-Since": format_datetime(since.replace(microsecond=0), usegmt=True)}
        log(f"feed: starting at If-Modified-Since {headers['If-Modified-Since']}")
    n = 0
    while True:
        r = await nav.get(url, headers=headers)
        headers = {}
        if r.status_code != 200:
            raise RuntimeError(f"feed page {url} -> HTTP {r.status_code}: {r.text[:200]}")
        j = r.json()
        items = [compact_item(i) for i in j.get("items") or []]
        page_id = j.get("id")
        page_url = NAV + (j.get("feed_url") or f"/api/v1/feed/{page_id}")
        nxt = j.get("next_url")
        append_jsonl(PAGES, {"page_id": page_id, "page_url": page_url, "next_url": nxt,
                             "fetched_at": utc_iso(), "last_modified": r.headers.get("last-modified"),
                             "items": items})
        n += 1
        state["pages"] = state.get("pages", 0) + 1
        state["cursor_url"] = page_url if not nxt else NAV + nxt
        state["cursor_page_id"] = page_id if not nxt else j.get("next_id")
        state["last_item_modified"] = items[-1]["modified"] if items else state.get("last_item_modified")
        state["at_end"] = not nxt
        state["updated_at"] = utc_iso()
        save_state(state)
        if n % 10 == 0 or not nxt:
            log(f"feed: {state['pages']} pages, last item {state.get('last_item_modified')}, "
                f"{nav.requests} requests")
        if not nxt:
            state["cursor_url"] = page_url
            state["cursor_page_id"] = page_id
            save_state(state)
            return state
        if max_pages and n >= max_pages:
            return state
        url = NAV + nxt


def fold_items() -> dict[str, dict[str, Any]]:
    """Latest state per ad uuid over every stored feed page (chronological order)."""
    latest: dict[str, dict[str, Any]] = {}
    for page in read_jsonl(PAGES):
        for it in page.get("items") or []:
            u = it.get("uuid")
            if not u:
                continue
            prev = latest.get(u)
            if prev is None or (it.get("modified") or "") >= (prev.get("modified") or ""):
                latest[u] = it
    return latest


# ----------------------------------------------------------------------------- entries
def extract_entry(uuid: str, j: dict[str, Any], http_status: int) -> dict[str, Any]:
    ad = j.get("ad_content") or {}
    emp = ad.get("employer") or {}
    pc = str(ad.get("positioncount") or "").strip()
    locs = []
    for w in ad.get("workLocations") or []:
        locs.append({k: w.get(src) for k, src in (("city", "city"), ("municipality", "municipal"),
                                                    ("county", "county"), ("postal_code", "postalCode"),
                                                    ("country", "country")) if w.get(src)})
    return {
        "uuid": uuid, "http_status": http_status, "status": j.get("status"), "sist_endret": j.get("sistEndret"),
        "fetched_at": utc_iso(),
        "employer_organisation_number": re.sub(r"\D", "", str(emp.get("orgnr") or "")) or None,
        "employer_name": emp.get("name"), "title": ad.get("title"), "job_title": ad.get("jobtitle"),
        "published": ad.get("published"), "expires": ad.get("expires"), "updated": ad.get("updated"),
        "link": ad.get("link"), "locations": locs,
        "positions": int(pc) if pc.isdigit() else None,
        "engagement_type": ad.get("engagementtype"), "extent": ad.get("extent"),
    }


def done_entries() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(ENTRIES):
        out[row["uuid"]] = row
    return out


async def phase_entries(nav: Nav) -> None:
    latest = fold_items()
    active = {u: it for u, it in latest.items() if it.get("status") == "ACTIVE"}
    have = done_entries()
    # Re-fetch an entry when the feed reports a newer modification than the one we stored.
    todo = sorted(u for u, it in active.items()
                  if u not in have or (have[u].get("feed_modified") or "") < (it.get("modified") or ""))
    log(f"entries: {len(latest)} ads seen, {len(active)} ACTIVE, {len(have)} stored, {len(todo)} to fetch")
    if not todo:
        return
    sem = asyncio.Semaphore(CONCURRENCY)
    t0 = time.monotonic()
    done = 0
    lock = asyncio.Lock()

    async def one(u: str) -> None:
        nonlocal done
        async with sem:
            try:
                r = await nav.get(f"{NAV}/api/v1/feedentry/{u}")
                if r.status_code == 200:
                    row = extract_entry(u, r.json(), 200)
                else:
                    row = {"uuid": u, "http_status": r.status_code, "status": None, "fetched_at": utc_iso()}
            except Exception as exc:  # keep going; the uuid stays in todo for the next run
                log(f"entry {u} failed: {exc}")
                return
            row["feed_modified"] = active[u].get("modified")
            row["feed_business_name"] = active[u].get("business_name")
            async with lock:
                append_jsonl(ENTRIES, row)
                done += 1
                if done % 250 == 0:
                    el = time.monotonic() - t0
                    rate = done / el if el else 0
                    eta = (len(todo) - done) / rate / 60 if rate else 0
                    log(f"entries: {done}/{len(todo)} ({rate:.2f}/s, ETA {eta:.0f} min)")

    await asyncio.gather(*(one(u) for u in todo))
    log(f"entries: fetched {done} in {(time.monotonic() - t0) / 60:.1f} min")


# ----------------------------------------------------------------------------- map
async def phase_map() -> dict[str, dict[str, Any]]:
    orgmap: dict[str, dict[str, Any]] = json.loads(ORGMAP.read_text()) if ORGMAP.exists() else {}
    entries = done_entries()
    wanted = sorted({e["employer_organisation_number"] for e in entries.values()
                     if e.get("employer_organisation_number") and len(e["employer_organisation_number"]) == 9})
    todo = [o for o in wanted if o not in orgmap]
    log(f"map: {len(wanted)} employer orgnrs, {len(todo)} to resolve")
    async with httpx.AsyncClient(timeout=60, headers={"User-Agent": USER_AGENT}) as client:
        async def batch(kind: str, nums: list[str]) -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            for i in range(0, len(nums), 1000):
                chunk = nums[i:i + 1000]
                for attempt in range(5):
                    r = await client.get(f"{BRREG}/{kind}", params={"organisasjonsnummer": ",".join(chunk),
                                                                    "size": "1000"})
                    if r.status_code == 200:
                        out.extend((r.json().get("_embedded") or {}).get(kind) or [])
                        break
                    log(f"map: brreg {kind} HTTP {r.status_code}; retrying")
                    await asyncio.sleep(5 * (attempt + 1))
                await asyncio.sleep(0.3)
            return out

        for u in await batch("underenheter", todo):
            orgmap[u["organisasjonsnummer"]] = {"kind": "underenhet", "parent": u.get("overordnetEnhet"),
                                                "name": u.get("navn")}
        rest = [o for o in todo if o not in orgmap]
        for e in await batch("enheter", rest):
            orgmap[e["organisasjonsnummer"]] = {"kind": "enhet", "parent": None, "name": e.get("navn")}
        for o in rest:
            orgmap.setdefault(o, {"kind": "unknown", "parent": None, "name": None})
    ORGMAP.write_text(json.dumps(orgmap, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    kinds: dict[str, int] = {}
    for v in orgmap.values():
        kinds[v["kind"]] = kinds.get(v["kind"], 0) + 1
    log(f"map: {kinds}")
    return orgmap


# ----------------------------------------------------------------------------- write
def location_text(locs: list[dict[str, Any]]) -> str | None:
    seen: list[str] = []
    for loc in locs or []:
        name = loc.get("city") or loc.get("municipality") or loc.get("county") or loc.get("country")
        if name and name not in seen:
            seen.append(name)
    return ", ".join(seen) or None


def phase_write() -> dict[str, Any]:
    latest = fold_items()
    entries = done_entries()
    orgmap = json.loads(ORGMAP.read_text()) if ORGMAP.exists() else {}
    state = load_state()
    now = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    dropped = {"feed_inactive": 0, "entry_not_active": 0, "expired": 0, "no_orgnr": 0, "unmapped": 0,
               "no_entry": 0}
    for u, it in latest.items():
        if it.get("status") != "ACTIVE":
            dropped["feed_inactive"] += 1
            continue
        e = entries.get(u)
        if not e:
            dropped["no_entry"] += 1
            continue
        if e.get("status") != "ACTIVE":
            dropped["entry_not_active"] += 1
            continue
        exp = parse_ts(e.get("expires"))
        if exp is not None and exp < now:
            dropped["expired"] += 1
            continue
        emp = e.get("employer_organisation_number")
        if not emp or len(emp) != 9:
            dropped["no_orgnr"] += 1
            continue
        m = orgmap.get(emp) or {}
        if m.get("kind") == "underenhet" and m.get("parent"):
            org = m["parent"]
        elif m.get("kind") == "enhet":
            org = emp
        else:
            dropped["unmapped"] += 1
            continue
        rows.append({
            "organisation_number": org, "employer_organisation_number": emp,
            "employer_kind": m.get("kind"), "employer_registry_name": m.get("name"),
            "employer_name": e.get("employer_name"), "uuid": u, "title": e.get("title"),
            "job_title": e.get("job_title"),
            "url": e.get("link") or f"https://arbeidsplassen.nav.no/stillinger/stilling/{u}",
            "published": e.get("published"), "expires": e.get("expires"), "updated": e.get("updated"),
            "location": location_text(e.get("locations") or []), "locations": e.get("locations") or [],
            "positions": e.get("positions"), "engagement_type": e.get("engagement_type"),
            "extent": e.get("extent"), "status": "ACTIVE", "feed_modified": it.get("modified"),
            "checked_at": e.get("fetched_at"), "feed_entry_url": f"{NAV}/api/v1/feedentry/{u}",
        })
    rows.sort(key=lambda r: (r["organisation_number"], r["uuid"]))
    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = CACHE / "nav_jobs.jsonl.gz.tmp"
    with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=9) as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n")
    os.replace(tmp, CACHE / "nav_jobs.jsonl.gz")
    meta = {
        "name": "nav_jobs",
        "built_at": utc_iso(now),
        "source": "NAV arbeidsplassen stillingsfeed (https://pam-stilling-feed.nav.no/api/v1/feed)",
        "licence": "NAV arbeidsplassen job-ad feed, https://arbeidsplassen.nav.no/vilkar-api",
        "feed_since": state.get("since"),
        "feed_cursor": {"page_id": state.get("cursor_page_id"), "page_url": state.get("cursor_url"),
                        "last_item_modified": state.get("last_item_modified"), "at_end": state.get("at_end")},
        "feed_pages": state.get("pages"),
        "ads_seen": len(latest),
        "active_ads": len(rows),
        "companies": len({r["organisation_number"] for r in rows}),
        "dropped": dropped,
        "key": "organisation_number (parent enhet of the advertising underenhet, or the enhet itself)",
        "note": ("Only ads whose latest feed state and feed entry are ACTIVE and whose expiry has not passed. "
                 "Contact persons are not stored. The runtime pages the feed forward from feed_cursor and "
                 "removes ads that became INACTIVE."),
    }
    (CACHE / "nav_jobs.meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False) + "\n",
                                              encoding="utf-8")
    log(f"write: {len(rows)} rows for {meta['companies']} companies; dropped {dropped}")
    return meta


def status() -> None:
    state = load_state()
    pages = sum(1 for _ in read_jsonl(PAGES))
    latest = fold_items()
    active = sum(1 for it in latest.values() if it.get("status") == "ACTIVE")
    ent = done_entries()
    print(json.dumps({"state": state, "pages_stored": pages, "ads_seen": len(latest), "active": active,
                      "entries_stored": len(ent)}, indent=1))


async def main_async(args) -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    nav = Nav()
    try:
        if args.phase in ("feed", "all"):
            await phase_feed(nav, args.days, args.max_pages)
        if args.phase in ("entries", "all"):
            await phase_entries(nav)
        if args.phase in ("catchup", "all"):
            st = load_state()
            if st.get("cursor_url"):
                await phase_feed(nav, args.days)
                await phase_entries(nav)
        if args.phase in ("map", "all"):
            await phase_map()
        if args.phase in ("write", "all"):
            phase_write()
    finally:
        log(f"done phase={args.phase}; NAV requests this process: {nav.requests}")
        await nav.aclose()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("phase", choices=("all", "feed", "entries", "catchup", "map", "write", "status"))
    ap.add_argument("--days", type=int, default=120, help="how far back the first feed page starts")
    ap.add_argument("--max-pages", type=int, default=None)
    args = ap.parse_args()
    if args.phase == "status":
        status()
        return
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
