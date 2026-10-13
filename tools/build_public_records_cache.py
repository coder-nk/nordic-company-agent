#!/usr/bin/env python3
"""Offline builder for the declared public-records cache (`cache/public_records.jsonl.gz` + `.meta.json`).

Sources (all keyless public files; downloaded once per build, conditional GET on rebuild):
  skattefunn      Forskningsrådet SkatteFUNN application lists (xlsx). The current file name is
                  date-stamped (e.g. 260902-webliste-skattefunn.xlsx), so both links are scraped from
                  https://www.forskningsradet.no/skattefunn/suksesshistorier/ :
                    * <yymmdd>-webliste-skattefunn.xlsx            (new application system, May 2024+)
                    * skattefunn-innsendte-soknader-2002-2024.xlsx (historical file)
                  Licence: NLOD 2.0 per the national data catalogue (data.norge.no dataset
                  f82d3d4a-b0f8-3220-83ef-15fbd854c54f); the download page itself states no licence.
  research_grant  Forskningsrådet open data "Søknader - versjon 2" CSV on GitHub. NLOD 2.0 per
                  data.norge.no dataset 349766ba-b5fd-3a04-8d82-77ec8928d6f2 (the GitHub repo has no
                  LICENSE file).
  food_inspection Mattilsynet smilefjes inspections CSV. The documented host data.mattilsynet.no
                  serves robots.txt `Disallow: /`, so the builder uses the distribution registered in
                  the national data catalogue (smilefjes.mattilsynet.no/api/tilsyn.csv, same columns,
                  no robots restriction). Licence: CC BY 4.0 per data.norge.no datasets
                  06c286c0-abc7-37ad-92da-376d0ed9c242 / 288aa74c-e3d3-492e-9ede-e71503b3bfd9.
                  `orgnummer` is usually the sub-unit (location); it is mapped to its parent enhet via
                  GET /enhetsregisteret/api/underenheter?organisasjonsnummer=<<=1000>&size=1000
                  (then /enheter for numbers that are main units themselves), <= 2 req/s.

Only rows whose organisation number is MOD11-valid and belongs to the Signalpost universe
(data/signalpost-company-universe-2025.jsonl.gz) are kept, at most MAX_PER_KIND most recent per
company per kind. No personal data is stored (project leaders, gender and similar person fields are
dropped; the SkatteFUNN "Prosjektansvarlig"/"Bedriftsnavn" is the company name).

Phases (resumable; state under data/work/public_records/):
  download  fetch the files (robots.txt honoured, conditional GET with ETag/Last-Modified)
  map       Mattilsynet location orgnr -> parent enhet (cached in orgmap.json)
  write     cache/public_records.jsonl.gz + cache/public_records.meta.json

Usage:
  uv run python tools/build_public_records_cache.py all
  uv run python tools/build_public_records_cache.py write      # rebuild from the downloaded files
  uv run python tools/build_public_records_cache.py status
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import html
import io
import json
import os
import re
import sys
import time
import urllib.robotparser
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urljoin, urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nordic_company_agent.http import USER_AGENT  # noqa: E402
from nordic_company_agent.orgnr import is_valid  # noqa: E402

WORK = ROOT / "data" / "work" / "public_records"
RAW = WORK / "raw"
DOWNLOADS = WORK / "downloads.json"
ORGMAP = WORK / "orgmap.json"
CACHE = ROOT / "cache"
OUT_ROWS = CACHE / "public_records.jsonl.gz"
OUT_META = CACHE / "public_records.meta.json"
UNIVERSE = ROOT / "data" / "signalpost-company-universe-2025.jsonl.gz"

SKATTEFUNN_PAGE = "https://www.forskningsradet.no/skattefunn/suksesshistorier/"
SKATTEFUNN_CURRENT_RX = re.compile(r'href="([^"]*/\d{6}-webliste-skattefunn\.xlsx)"', re.IGNORECASE)
SKATTEFUNN_HISTORY_RX = re.compile(r'href="([^"]*/skattefunn-innsendte-soknader-2002-\d{4}\.xlsx)"', re.IGNORECASE)
NFR_CSV = "https://raw.githubusercontent.com/Forskningsradet/open-data/main/datasets/soknader2/dataset.csv"
# Documented by the research notes / task, but robots.txt on data.mattilsynet.no disallows everything;
# the national catalogue distribution is tried next.
MATTILSYNET_CANDIDATES = ("https://data.mattilsynet.no/smilefjes-tilsyn.csv",
                          "https://smilefjes.mattilsynet.no/api/tilsyn.csv")
BRREG = "https://data.brreg.no/enhetsregisteret/api"

MAX_PER_KIND = 10
SUMMARY_MAX = 600
MIN_INTERVAL_S = 0.6        # >= 0.6 s between request starts to any host (< 2 req/s)

LICENCES = {
    "skattefunn": {
        "id": "NLOD-2.0",
        "text": ("SkatteFUNN application lists, Norges forskningsråd. Licence: Norwegian Licence for Open "
                 "Government Data (NLOD) 2.0 per the national data catalogue entry 'Innsendte søknader til "
                 "Skattefunn' (https://data.norge.no/datasets/f82d3d4a-b0f8-3220-83ef-15fbd854c54f). The "
                 "download page itself states no licence."),
    },
    "research_grant": {
        "id": "NLOD-2.0",
        "text": ("Forskningsrådet open data 'Søknader - versjon 2' (https://github.com/Forskningsradet/open-data). "
                 "Licence: NLOD 2.0 per the national data catalogue "
                 "(https://data.norge.no/datasets/349766ba-b5fd-3a04-8d82-77ec8928d6f2); the GitHub repository "
                 "has no LICENSE file. Excludes applications in the new case system Tibi."),
    },
    "food_inspection": {
        "id": "CC-BY-4.0",
        "text": ("Mattilsynet smilefjes inspection data (Smilefjestilsyn på serveringssteder). Licence: Creative "
                 "Commons Attribution 4.0 per the national data catalogue "
                 "(https://data.norge.no/datasets/288aa74c-e3d3-492e-9ede-e71503b3bfd9). Mattilsynet withdraws "
                 "invalidated inspections from the open data and asks users to refresh daily."),
    },
    "brreg": {
        "id": "NLOD-2.0",
        "text": "Sub-unit to parent mapping from Enhetsregisteret (Brønnøysundregistrene), NLOD 2.0.",
    },
}


def log(msg: str) -> None:
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def utc_iso(dt: datetime | None = None) -> str:
    dt = dt or datetime.now(timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path, default: Any) -> Any:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ----------------------------------------------------------------------------- polite HTTP
class Polite:
    """Sequential httpx client: one request at a time, >= MIN_INTERVAL_S between request starts,
    robots.txt checked per host, retries with back-off on 429/5xx."""

    def __init__(self) -> None:
        self.client = httpx.Client(timeout=httpx.Timeout(300.0, connect=20.0),
                                   headers={"User-Agent": USER_AGENT}, follow_redirects=True)
        self._last = 0.0
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self.requests = 0

    def _pace(self) -> None:
        delay = self._last + MIN_INTERVAL_S - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self._last = time.monotonic()

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            rp: urllib.robotparser.RobotFileParser | None = urllib.robotparser.RobotFileParser()
            self._pace()
            self.requests += 1
            try:
                r = self.client.get(origin + "/robots.txt")
                if r.status_code == 200 and "text/html" not in r.headers.get("content-type", ""):
                    rp.parse(r.text.splitlines())
                elif r.status_code in (401, 403):
                    rp.disallow_all = True
                else:
                    rp = None              # no robots.txt: everything allowed
            except httpx.HTTPError:
                rp = None
            self._robots[origin] = rp
        rp = self._robots[origin]
        return True if rp is None else rp.can_fetch(USER_AGENT, url)

    def get(self, url: str, headers: dict[str, str] | None = None, stream_to: Path | None = None) -> httpx.Response:
        delay = 5.0
        for attempt in range(6):
            self._pace()
            self.requests += 1
            try:
                if stream_to is None:
                    r = self.client.get(url, headers=headers)
                else:
                    with self.client.stream("GET", url, headers=headers) as r:
                        if r.status_code == 200:
                            tmp = stream_to.with_suffix(stream_to.suffix + ".part")
                            with open(tmp, "wb") as fh:
                                for chunk in r.iter_bytes(1 << 16):
                                    fh.write(chunk)
                            os.replace(tmp, stream_to)
                        else:
                            r.read()
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                log(f"  transport error {type(exc).__name__} on {url} (attempt {attempt + 1}); sleeping {delay:.0f}s")
                time.sleep(delay)
                delay = min(delay * 2, 120)
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                ra = r.headers.get("retry-after", "")
                wait = float(ra) if ra.isdigit() else delay
                log(f"  HTTP {r.status_code} on {url}; sleeping {min(wait, 300):.0f}s")
                time.sleep(min(wait, 300))
                delay = min(delay * 2, 120)
                continue
            return r
        raise RuntimeError(f"giving up on {url}")

    def close(self) -> None:
        self.client.close()


# ----------------------------------------------------------------------------- download
def _download(http: Polite, url: str, name: str, downloads: dict[str, Any], kind: str) -> dict[str, Any] | None:
    path = RAW / name
    prev = downloads.get(url)
    headers: dict[str, str] = {}
    if prev and path.exists() and sha256_file(path) == prev.get("sha256"):
        if prev.get("etag"):
            headers["If-None-Match"] = prev["etag"]
        if prev.get("last_modified"):
            headers["If-Modified-Since"] = prev["last_modified"]
    log(f"download {url}" + (" (conditional)" if headers else ""))
    r = http.get(url, headers=headers, stream_to=path)
    if r.status_code == 304 and prev:
        log("  304 not modified; keeping the downloaded copy")
        prev["checked_at"] = utc_iso()
        return prev
    if r.status_code != 200:
        log(f"  HTTP {r.status_code}; skipped")
        return None
    entry = {"url": url, "final_url": str(r.url), "kind": kind, "path": str(path.relative_to(ROOT)),
             "sha256": sha256_file(path), "bytes": path.stat().st_size,
             "last_modified": r.headers.get("last-modified"), "etag": r.headers.get("etag"),
             "content_type": r.headers.get("content-type"), "fetched_at": utc_iso(), "checked_at": utc_iso()}
    log(f"  {entry['bytes']:,} bytes sha256 {entry['sha256'][:16]}… last-modified {entry['last_modified']}")
    downloads[url] = entry
    return entry


def phase_download() -> dict[str, Any]:
    RAW.mkdir(parents=True, exist_ok=True)
    downloads: dict[str, Any] = load_json(DOWNLOADS, {})
    http = Polite()
    selected: dict[str, list[str]] = {"skattefunn": [], "research_grant": [], "food_inspection": []}
    notes: list[str] = []
    try:
        # (a) SkatteFUNN: scrape the date-stamped links.
        if not http.allowed(SKATTEFUNN_PAGE):
            raise RuntimeError(f"robots.txt disallows {SKATTEFUNN_PAGE}")
        r = http.get(SKATTEFUNN_PAGE)
        r.raise_for_status()
        page = r.text
        sf_links = []
        for rx in (SKATTEFUNN_CURRENT_RX, SKATTEFUNN_HISTORY_RX):
            m = rx.search(page)
            if m:
                sf_links.append(urljoin(SKATTEFUNN_PAGE, html.unescape(m.group(1))))
            else:
                notes.append(f"SkatteFUNN link matching {rx.pattern} not found on {SKATTEFUNN_PAGE}")
        for url in sf_links:
            if not http.allowed(url):
                notes.append(f"robots.txt disallows {url}")
                continue
            e = _download(http, url, url.rsplit("/", 1)[-1], downloads, "skattefunn")
            if e:
                selected["skattefunn"].append(url)
        # (b) Forskningsrådet open data CSV.
        if http.allowed(NFR_CSV):
            if _download(http, NFR_CSV, "forskningsradet_soknader2_dataset.csv", downloads, "research_grant"):
                selected["research_grant"].append(NFR_CSV)
        else:
            notes.append(f"robots.txt disallows {NFR_CSV}")
        # (c) Mattilsynet smilefjes: first candidate that robots.txt allows.
        for url in MATTILSYNET_CANDIDATES:
            if not http.allowed(url):
                notes.append(f"robots.txt on {urlsplit(url).netloc} disallows {url}; not fetched")
                log(f"robots.txt disallows {url}; trying the next distribution")
                continue
            name = "mattilsynet_" + re.sub(r"[^A-Za-z0-9.]+", "_", urlsplit(url).netloc + urlsplit(url).path)
            if _download(http, url, name, downloads, "food_inspection"):
                selected["food_inspection"].append(url)
                break
    finally:
        http.close()
    downloads["_selected"] = selected
    downloads["_notes"] = notes
    downloads["_download_requests"] = http.requests
    save_json(DOWNLOADS, downloads)
    for n in notes:
        log("note: " + n)
    log(f"download: {http.requests} requests; selected {json.dumps(selected)}")
    return downloads


# ----------------------------------------------------------------------------- parsing helpers
def digits(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return re.sub(r"\D", "", str(v))


def cell_text(v: Any) -> str | None:
    """Cell value as a string: text verbatim, integers without '.0', dates ISO-8601 (as read by openpyxl)."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, bool):
        return str(v).upper()
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else repr(v)
    s = str(v)
    return s if s.strip() else None


def iso_date(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    s = str(v).strip()
    if not s:
        return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.match(r"^(\d{2})(\d{2})(\d{4})$", s)              # Mattilsynet ddmmyyyy
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    m = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$", s)       # dd.mm.yyyy
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return None


def year_of(v: Any) -> int | None:
    if v is None:
        return None
    if isinstance(v, (datetime, date)):
        return v.year
    if isinstance(v, (int, float)) and 1900 <= int(v) <= 2100 and float(v).is_integer():
        return int(v)
    m = re.match(r"^\s*(\d{4})(?:\.0)?(?:\b|-)", str(v))
    return int(m.group(1)) if m else None


def clean(s: Any) -> str | None:
    if s is None:
        return None
    t = str(s).strip()
    return t or None


def truncate(s: str | None, n: int = SUMMARY_MAX) -> tuple[str | None, bool]:
    """Verbatim prefix of at most n characters (cut at a word boundary when possible); no ellipsis is
    added so the stored text stays a verbatim fragment of the source cell."""
    if not s:
        return None, False
    s = s.strip()
    if len(s) <= n:
        return s, False
    cut = s[:n]
    sp = cut.rfind(" ")
    if sp >= n * 0.8:
        cut = cut[:sp]
    return cut.rstrip(), True


# ----------------------------------------------------------------------------- SkatteFUNN
SF_HEADERS = {
    # canonical -> accepted header spellings (both files)
    "date": ("Søknadsdato", "Innsendt dato"),
    "application_number": ("Søknadsnummer",),
    "project_number": ("Prosjektnummer",),
    "company_name": ("Prosjektansvarlig", "Bedriftsnavn"),
    "title": ("Prosjekttittel",),
    "orgnr": ("Organisasjonsnr", "Organisasjonsnummer"),
    "approved": ("Godkjent", "Søknad godkjent"),
    "rejected": ("Søknad avslått",),
    "from_year": ("Fra-år", "Prosjektets fra-år"),
    "to_year": ("Til-år", "Prosjektets til-år"),
    "decision_date": ("Vedtaksdato",),
    "summary": ("Populærvitenskapelig sammendrag",),
}


def _norm_header(h: Any) -> str:
    return re.sub(r"\s+", " ", str(h or "")).strip().lower()


def iter_skattefunn(path: Path, url: str, sha: str, universe: set[str], stats: Counter) -> Iterator[dict[str, Any]]:
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        for ws in wb.worksheets:
            rows = ws.iter_rows(values_only=True)
            header_idx: dict[str, int] | None = None
            header_names: dict[str, str] = {}
            rownum = 0
            for values in rows:
                rownum += 1
                if header_idx is None:
                    norm = [_norm_header(v) for v in values]
                    found: dict[str, int] = {}
                    for canon, names in SF_HEADERS.items():
                        for nm in names:
                            if nm.lower() in norm:
                                found[canon] = norm.index(nm.lower())
                                header_names[canon] = str(values[found[canon]]).strip()
                                break
                    if {"orgnr", "title"} <= set(found) and rownum <= 20:
                        header_idx = found
                    elif rownum > 20:
                        break
                    continue

                def g(k: str, values: tuple = values, idx: dict[str, int] = header_idx) -> Any:
                    i = idx.get(k)
                    return values[i] if i is not None and i < len(values) else None

                stats["skattefunn_rows"] += 1
                org = digits(g("orgnr"))
                if not is_valid(org):
                    stats["skattefunn_invalid_orgnr"] += 1
                    continue
                if org not in universe:
                    continue
                approved = clean(g("approved"))
                rejected = clean(g("rejected"))
                a = (approved or "").upper()
                rj = (rejected or "").upper()
                if a == "JA":
                    status = "approved"
                elif a == "NEI" or rj == "JA":
                    status = "rejected"
                else:
                    status = "no_decision_recorded"
                pn = cell_text(g("project_number"))
                an = cell_text(g("application_number"))
                fy, ty = year_of(g("from_year")), year_of(g("to_year"))
                raw_cells: dict[str, str] = {}
                for k in ("orgnr", "project_number", "application_number", "title", "date", "approved", "rejected",
                          "decision_date", "from_year", "to_year"):
                    t = cell_text(g(k))
                    if t is not None and k in header_names:
                        raw_cells[header_names[k]] = t
                summary, summary_truncated = truncate(clean(g("summary")))
                # Stable ids that do not depend on the date-stamped file name: the new application system
                # (May 2024+) numbers applications (Søknadsnummer; Prosjektnummer only once approved), the
                # historical file numbers projects.
                if "application_number" in header_idx and an:
                    rid = f"skattefunn:soknad:{an}"
                elif pn:
                    rid = f"skattefunn:prosjekt:{pn}"
                else:
                    rid = f"skattefunn:{path.stem}:row:{rownum}"
                yield {
                    "organisation_number": org,
                    "kind": "skattefunn",
                    "record_id": rid,
                    "project_number": pn,
                    "application_number": an,
                    "date": iso_date(g("date")),
                    "decision_date": iso_date(g("decision_date")),
                    "title": clean(g("title")),
                    "status": status,
                    "period": {"from": fy, "to": ty} if (fy or ty) else None,
                    "summary": summary,
                    "summary_truncated": summary_truncated or None,
                    "company_name": clean(g("company_name")),
                    "source_url": url,
                    "source_sha256": sha,
                    "row_key": f"sheet:{ws.title}!row:{rownum}",
                    "raw_cells": raw_cells,
                }
    finally:
        wb.close()


# ----------------------------------------------------------------------------- Forskningsrådet
NFR_STATUS = {"bevilgning": "granted", "avsluttet": "completed", "avslag": "rejected", "vurderes": "under_review",
              "mottatt": "received"}


def iter_nfr(path: Path, url: str, sha: str, universe: set[str], stats: Counter) -> Iterator[dict[str, Any]]:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        idx = {h.strip(): i for i, h in enumerate(header)}
        need = {"prosjektnummer", "prosjekttittel", "organisasjonsnummer", "prosjektfase"}
        if not need <= set(idx):
            raise RuntimeError(f"Forskningsrådet CSV header changed: missing {need - set(idx)}")
        line_no = 1
        for values in reader:
            # csv.reader tracks physical lines (titles may contain line breaks); report the record's first line.
            start_line = line_no + 1
            line_no = reader.line_num

            def g(k: str, values: list[str] = values) -> str | None:
                i = idx.get(k)
                return values[i] if i is not None and i < len(values) else None

            stats["research_grant_rows"] += 1
            if (clean(g("prosjektansvarlig_type")) or "").lower() == "person":
                stats["research_grant_personal_stipend_skipped"] += 1   # person-centric rows: never stored
                continue
            org = digits(g("organisasjonsnummer"))
            if not is_valid(org):
                stats["research_grant_invalid_orgnr"] += 1
                continue
            if org not in universe:
                continue
            phase = clean(g("prosjektfase"))
            status = NFR_STATUS.get((phase or "").lower(), (phase or "unknown").lower())
            amount_raw = clean(g("tildelt_belop"))
            amount = None
            if amount_raw is not None and status in ("granted", "completed"):
                try:
                    if Decimal(amount_raw) > 0:
                        amount = amount_raw
                except InvalidOperation:
                    pass
            fy, ty = year_of(g("prosjektstart")), year_of(g("prosjektslutt"))
            raw_cells = {k: v for k in ("organisasjonsnummer", "prosjektnummer", "prosjekttittel", "prosjektfase",
                                        "soknadsdato", "tildelt_belop", "prosjektstart", "prosjektslutt", "aktivitet")
                         if (v := clean(g(k))) is not None}
            yield {
                "organisation_number": org,
                "kind": "research_grant",
                "record_id": f"forskningsradet:{clean(g('prosjektnummer'))}",
                "project_number": clean(g("prosjektnummer")),
                "date": iso_date(g("soknadsdato")) or iso_date(g("soknadsfrist")),
                "title": clean(g("prosjekttittel")),
                "status": status,
                "phase": phase,
                "amount": amount,
                "currency": "NOK" if amount else None,
                "period": {"from": fy, "to": ty} if (fy or ty) else None,
                "programme": clean(g("aktivitet")),
                "call": clean(g("utlysningtittel")),
                "project_type": clean(g("prosjekttype")),
                "company_name": clean(g("prosjektansvarlig_navn")),
                "source_url": url,
                "source_sha256": sha,
                "row_key": f"csv:line:{start_line}",
                "raw_cells": raw_cells,
            }


# ----------------------------------------------------------------------------- Mattilsynet
SMILEY = {"0": "smile", "1": "smile", "2": "straight_mouth", "3": "sad_mouth"}


def read_smilefjes(path: Path) -> tuple[list[str], list[tuple[int, list[str]]]]:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=";")
    header = next(reader)
    out = []
    line_no = 1
    for values in reader:
        start = line_no + 1
        line_no = reader.line_num
        out.append((start, values))
    return header, out


def smilefjes_location_orgnrs(path: Path) -> set[str]:
    header, rows = read_smilefjes(path)
    i = header.index("orgnummer")
    return {o for _ln, v in rows if i < len(v) and is_valid(o := digits(v[i]))}


def iter_smilefjes(path: Path, url: str, sha: str, universe: set[str], orgmap: dict[str, Any],
                   stats: Counter) -> Iterator[dict[str, Any]]:
    header, rows = read_smilefjes(path)
    idx = {h.strip(): i for i, h in enumerate(header)}
    for line, values in rows:
        def g(k: str, values: list[str] = values) -> str | None:
            i = idx.get(k)
            return values[i] if i is not None and i < len(values) else None

        stats["food_inspection_rows"] += 1
        loc = digits(g("orgnummer"))
        if not is_valid(loc):
            stats["food_inspection_invalid_orgnr"] += 1
            continue
        m = orgmap.get(loc) or {}
        if m.get("kind") == "underenhet" and m.get("parent"):
            org, basis = str(m["parent"]), "underenhet_overordnetEnhet"
        elif m.get("kind") == "enhet":
            org, basis = loc, "enhet"
        else:
            stats["food_inspection_unmapped"] += 1
            continue
        if org not in universe:
            continue
        grade = clean(g("total_karakter"))
        addr_lines = [x for x in (clean(g("adrlinje1")), clean(g("adrlinje2"))) if x]
        postal = " ".join(x for x in (clean(g("postnr")), clean(g("poststed"))) if x)
        address = ", ".join(addr_lines + ([postal] if postal else [])) or None
        raw_cells = {k: v for k in ("orgnummer", "navn", "tilsynid", "dato", "total_karakter", "tilsynsbesoektype",
                                    "adrlinje1", "postnr", "poststed") if (v := clean(g(k))) is not None}
        yield {
            "organisation_number": org,
            "kind": "food_inspection",
            "record_id": f"mattilsynet:{clean(g('tilsynid'))}",
            "date": iso_date(g("dato")),
            "title": clean(g("navn")),
            "status": grade,
            "grade": grade,
            "smiley": SMILEY.get(grade or ""),
            "location_organisation_number": loc,
            "location_name": clean(g("navn")),
            "location_registry_name": m.get("name"),
            "location_kind": m.get("kind"),
            "address": address,
            "inspection_object_id": clean(g("tilsynsobjektid")),
            "parent_basis": basis,
            "source_url": url,
            "source_sha256": sha,
            "row_key": f"csv:line:{line}",
            "raw_cells": raw_cells,
        }


# ----------------------------------------------------------------------------- map (brreg)
def phase_map(remap_days: float = 7.0) -> dict[str, Any]:
    """Resolve location orgnrs not yet mapped, or mapped more than `remap_days` ago (sub-units can move)."""
    downloads = load_json(DOWNLOADS, {})
    urls = (downloads.get("_selected") or {}).get("food_inspection") or []
    if not urls:
        log("map: no Mattilsynet file downloaded; nothing to map")
        return load_json(ORGMAP, {})
    path = ROOT / downloads[urls[0]]["path"]
    wanted = sorted(smilefjes_location_orgnrs(path))
    orgmap: dict[str, Any] = load_json(ORGMAP, {})
    cutoff = utc_iso(datetime.now(timezone.utc) - timedelta(days=remap_days))
    todo = [o for o in wanted if o not in orgmap or (orgmap[o].get("checked_at") or "") < cutoff]
    for o in todo:
        orgmap.pop(o, None)
    log(f"map: {len(wanted)} distinct valid location orgnrs, {len(todo)} to resolve")
    if not todo:
        return orgmap
    http = Polite()
    try:
        def batch(kind: str, nums: list[str]) -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            for i in range(0, len(nums), 1000):
                chunk = nums[i:i + 1000]
                r = http.get(f"{BRREG}/{kind}?organisasjonsnummer={','.join(chunk)}&size=1000",
                             headers={"Accept": "application/json"})
                if r.status_code != 200:
                    raise RuntimeError(f"brreg {kind} HTTP {r.status_code}: {r.text[:200]}")
                got = (r.json().get("_embedded") or {}).get(kind) or []
                out.extend(got)
                log(f"  {kind}: chunk {i // 1000 + 1}: {len(chunk)} asked, {len(got)} returned")
            return out

        now = utc_iso()
        for u in batch("underenheter", todo):
            orgmap[u["organisasjonsnummer"]] = {"kind": "underenhet", "parent": u.get("overordnetEnhet"),
                                                "name": u.get("navn"), "closed": u.get("nedleggelsesdato"),
                                                "checked_at": now}
        save_json(ORGMAP, orgmap)
        rest = [o for o in todo if o not in orgmap]
        for e in batch("enheter", rest):
            orgmap[e["organisasjonsnummer"]] = {"kind": "enhet", "parent": None, "name": e.get("navn"),
                                                "checked_at": now}
        for o in rest:
            # Deleted/removed units are omitted by the batch search: no parent can be proven.
            orgmap.setdefault(o, {"kind": "unknown", "parent": None, "name": None, "checked_at": now})
        save_json(ORGMAP, orgmap)
    finally:
        http.close()
    kinds = Counter(v["kind"] for v in orgmap.values())
    log(f"map: {dict(kinds)} ({http.requests} requests)")
    return orgmap


# ----------------------------------------------------------------------------- write
def load_universe() -> set[str]:
    with gzip.open(UNIVERSE, "rt", encoding="utf-8") as fh:
        return {json.loads(line)["organisation_number"] for line in fh if line.strip()}


def _recency_key(r: dict[str, Any]) -> tuple:
    p = r.get("period") or {}
    return (r.get("date") or "", r.get("decision_date") or "", p.get("to") or 0, r.get("record_id") or "")


def phase_write() -> dict[str, Any]:
    downloads = load_json(DOWNLOADS, {})
    selected = downloads.get("_selected") or {}
    orgmap = load_json(ORGMAP, {})
    universe = load_universe()
    log(f"write: universe {len(universe):,} organisation numbers")
    stats: Counter = Counter()
    by_key: dict[tuple[str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    files_meta = []
    for kind in ("skattefunn", "research_grant", "food_inspection"):
        for url in selected.get(kind) or []:
            d = downloads[url]
            path = ROOT / d["path"]
            sha = sha256_file(path)
            if sha != d["sha256"]:
                raise RuntimeError(f"{path} changed since download (sha mismatch); re-run download")
            t0 = time.monotonic()
            if kind == "skattefunn":
                it = iter_skattefunn(path, url, sha, universe, stats)
            elif kind == "research_grant":
                it = iter_nfr(path, url, sha, universe, stats)
            else:
                it = iter_smilefjes(path, url, sha, universe, orgmap, stats)
            kept = 0
            for row in it:
                key = (row["organisation_number"], kind)
                rid = row["record_id"]
                prev = by_key[key].get(rid)
                if prev is not None:
                    stats[f"{kind}_duplicate_record_id"] += 1
                    if (rid.startswith("skattefunn:soknad:") and row.get("project_number")
                            and row.get("project_number") != prev.get("project_number")):
                        # One application listed with two project numbers: keep both, deterministically.
                        rid = row["record_id"] = f"{rid}:prosjekt:{row['project_number']}"
                    elif _recency_key(prev) >= _recency_key(row):
                        continue
                by_key[key][rid] = row
                kept += 1
            log(f"write: {kind} {url.rsplit('/', 1)[-1]}: {kept:,} universe rows ({time.monotonic() - t0:.1f}s)")
            files_meta.append({"url": url, "final_url": d.get("final_url"), "kind": kind, "sha256": sha,
                               "bytes": d["bytes"], "last_modified": d.get("last_modified"), "etag": d.get("etag"),
                               "fetched_at": d.get("fetched_at"), "checked_at": d.get("checked_at"),
                               "universe_rows": kept, "rows_kept": 0})
    built_at = utc_iso()
    out_rows: list[dict[str, Any]] = []
    for org, kind in sorted(by_key):
        ordered = sorted(by_key[(org, kind)].values(), key=_recency_key, reverse=True)   # most recent first
        for r in ordered[:MAX_PER_KIND]:
            r["company_kind_total"] = len(ordered)
            out_rows.append(r)
        if len(ordered) > MAX_PER_KIND:
            stats[f"{kind}_truncated_companies"] += 1
    kept_by_url = Counter(r["source_url"] for r in out_rows)
    for f in files_meta:
        f["rows_kept"] = kept_by_url.get(f["url"], 0)
    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = OUT_ROWS.with_suffix(".gz.tmp")
    with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=9) as fh:
        for r in out_rows:
            r = {k: v for k, v in r.items() if v is not None}
            fh.write(json.dumps(r, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n")
    os.replace(tmp, OUT_ROWS)
    counts = Counter(r["kind"] for r in out_rows)
    companies = {k: len({r["organisation_number"] for r in out_rows if r["kind"] == k}) for k in counts}
    any_companies = len({r["organisation_number"] for r in out_rows})
    meta = {
        "name": "public_records",
        "built_at": built_at,
        "built_by": "tools/build_public_records_cache.py",
        "universe": {"file": str(UNIVERSE.relative_to(ROOT)), "organisation_numbers": len(universe)},
        "key": "organisation_number (the company; for food inspections the parent enhet of the inspected "
               "underenhet per Enhetsregisteret, or the enhet itself)",
        "max_records_per_company_per_kind": MAX_PER_KIND,
        "files": files_meta,
        "counts": dict(sorted(counts.items())),
        "companies": dict(sorted(companies.items())),
        "companies_any": any_companies,
        "subunit_mapping": {
            "source": f"{BRREG}/underenheter?organisasjonsnummer=<<=1000 numbers>&size=1000 (then /enheter)",
            "resolved": dict(Counter(v["kind"] for v in orgmap.values())),
            "checked_at": max((v.get("checked_at") or "" for v in orgmap.values()), default=None),
            "licence": LICENCES["brreg"]["text"],
        },
        "stats": dict(sorted(stats.items())),
        "licences": {k: v for k, v in LICENCES.items()},
        "notes": [
            ("SkatteFUNN status: Godkjent/Søknad godkjent JA -> approved; NEI (or Søknad avslått JA) -> rejected; "
            "empty -> no_decision_recorded. date = Søknadsdato/Innsendt dato, decision_date = Vedtaksdato."),
            ("Forskningsrådet status from prosjektfase: Bevilgning -> granted, Avsluttet -> completed, Avslag -> "
            "rejected, Vurderes -> under_review, Mottatt -> received. amount = tildelt_belop verbatim, only for "
            "granted/completed projects with a positive amount (total for the whole project period). The file does "
            "not state a currency; Forskningsrådet amounts are NOK."),
            ("Food inspections: grade = total_karakter as given (0/1 smile, 2 straight mouth, 3 sad mouth per "
            "Mattilsynet's documented scale). Inspections at units that are deleted from Enhetsregisteret (no parent "
            "provable) are dropped. Mattilsynet withdraws invalidated inspections, so rebuild daily."),
            "raw_cells: original cell values used, as strings (xlsx dates as read by openpyxl, ISO-8601).",
            ("summary: verbatim popular-science summary, cut to <= 600 characters at a word boundary "
            "(summary_truncated=true when cut; no ellipsis added)."),
            ("record_id: skattefunn:soknad:<Søknadsnummer> (new system), skattefunn:prosjekt:<Prosjektnummer> "
            "(2002-2024 file), forskningsradet:<prosjektnummer>, mattilsynet:<tilsynid>."),
            "Person fields (project leader names, gender) are never stored.",
        ] + list(downloads.get("_notes") or []),
    }
    save_json(OUT_META, meta)
    log(f"write: {len(out_rows):,} rows -> {OUT_ROWS.relative_to(ROOT)} ({OUT_ROWS.stat().st_size:,} bytes); "
        f"counts {dict(counts)}; companies {companies}; any {any_companies}")
    return meta


def phase_status() -> None:
    d = load_json(DOWNLOADS, {})
    for url, e in d.items():
        if url.startswith("_"):
            continue
        print(f"{e.get('kind'):16} {e.get('bytes', 0):>12,} {e.get('last_modified')}  {url}")
    print("selected:", json.dumps(d.get("_selected")), "notes:", d.get("_notes"))
    m = load_json(ORGMAP, {})
    print("orgmap:", dict(Counter(v["kind"] for v in m.values())))
    meta = load_json(OUT_META, None)
    if meta:
        print("cache built", meta["built_at"], "counts", meta["counts"], "companies", meta["companies"])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("phase", choices=("all", "download", "map", "write", "status"))
    ap.add_argument("--remap-days", type=float, default=7.0,
                    help="re-resolve sub-unit -> parent mappings older than this many days (default 7)")
    args = ap.parse_args()
    t0 = time.monotonic()
    if args.phase == "status":
        phase_status()
        return
    if args.phase in ("all", "download"):
        phase_download()
    if args.phase in ("all", "map"):
        phase_map(args.remap_days)
    if args.phase in ("all", "write"):
        phase_write()
    log(f"done in {time.monotonic() - t0:.1f}s")


if __name__ == "__main__":
    main()
