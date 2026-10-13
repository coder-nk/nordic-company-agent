#!/usr/bin/env python3
"""Declared cache of Støtteregisteret (Brønnøysund state-aid register) grants for universe companies.

Source: https://stotte.brreg.no/nb/oppslag/stoettetildeling/totalbestand/csv (UTF-16, ';'-separated,
~300 MB, NLOD 2.0). The recipient birth-date column is dropped at read time.

Output: cache/state_aid.jsonl.gz, one row per grant, at most 10 most recent per company, with exactly
the value shape the live state_aid module produces (scheme names come from the same scheme list),
plus the verbatim CSV line for evidence. Used at run time only when the request budget cannot
afford the live per-company search.

Usage: uv run python tools/build_state_aid_cache.py [--csv data/bulk/stotte.csv]
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nordic_company_agent.orgnr import is_valid  # noqa: E402

DUMP_URL = "https://stotte.brreg.no/nb/oppslag/stoettetildeling/totalbestand/csv"
SCHEMES_URL = "https://stottetiltak-registerinfo-api.app.brreg.no/api/v1/soek/stoetteordning?pageSize=10000"
UNIVERSE = ROOT / "data" / "signalpost-company-universe-2025.jsonl.gz"
MAX_PER_COMPANY = 10


def iso(d: str) -> str | None:
    d = (d or "").strip()
    try:
        return datetime.strptime(d[:10], "%d.%m.%Y").date().isoformat()
    except ValueError:
        return None


def amount(s: str) -> str | None:
    s = (s or "").strip().replace("\xa0", "").replace(" ", "")
    if not s:
        return None
    s = s.replace(",", ".")
    try:
        float(s)
    except ValueError:
        return None
    return s


def scheme_names() -> dict[str, str]:
    r = httpx.post(SCHEMES_URL, content=b"[]", headers={"Content-Type": "application/json", "Accept": "application/json",
                                                       "User-Agent": "nordic-company-agent/1.0 (cache builder)"},
                   timeout=60)
    r.raise_for_status()
    out = {}
    for s in r.json().get("stoetteordning") or []:
        names = s.get("navn") or []
        nm = next((n.get("tekst") for n in names if n.get("spraak") == "NOR"), None) or \
            next((n.get("tekst") for n in names), None)
        if s.get("stoettetiltaksnummer") and nm:
            out[s["stoettetiltaksnummer"]] = nm
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=str(ROOT / "data" / "bulk" / "stotte.csv"))
    a = ap.parse_args()
    path = Path(a.csv)
    universe = set()
    with gzip.open(UNIVERSE, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                universe.add(json.loads(line)["organisation_number"])
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    file_sha = h.hexdigest()
    schemes = scheme_names()
    built_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    raw = path.read_bytes()
    text = raw.decode("utf-16")
    lines = text.splitlines()
    header = next(csv.reader([lines[0]], delimiter=";"))
    col = {name.strip(): i for i, name in enumerate(header)}
    need = ["Støttetiltaksnummer", "Organisasjonsnummer støttemottaker", "Tildelingsdato", "Tildelt beløp",
            "Tildelt beløp valuta", "Organisasjonsnummer støttegiver", "Navn støttegiver", "Formål",
            "Støtteinstrument", "Status", "Tilknyttet støtteordning", "Beløpsintervall fra beløp",
            "Beløpsintervall til beløp", "Beløpsintervall valuta"]
    missing = [n for n in need if n not in col]
    if missing:
        raise SystemExit(f"unexpected CSV header, missing {missing}: {header}")
    per: dict[str, list[dict]] = defaultdict(list)
    n_rows = 0
    for lineno, line in enumerate(lines[1:], start=2):
        if not line.strip():
            continue
        n_rows += 1
        try:
            cells = next(csv.reader([line], delimiter=";"))
        except Exception:
            continue
        get = lambda k: cells[col[k]].strip() if col[k] < len(cells) else ""
        org = get("Organisasjonsnummer støttemottaker").replace(" ", "")
        if org not in universe or not is_valid(org):
            continue
        nr = get("Støttetiltaksnummer")
        amt = amount(get("Tildelt beløp"))
        cur = get("Tildelt beløp valuta") or get("Beløpsintervall valuta") or None
        scheme_id = get("Tilknyttet støtteordning") or None
        value = {
            "date": iso(get("Tildelingsdato")), "amount": amt, "currency": cur,
            "grantor": get("Navn støttegiver") or None,
            "grantor_organisation_number": get("Organisasjonsnummer støttegiver") or None,
            "scheme": schemes.get(scheme_id, scheme_id) if scheme_id else None, "scheme_id": scheme_id,
            "purpose": get("Formål") or None, "instrument": get("Støtteinstrument") or None,
            "status": get("Status") or None,
        }
        if amt is None and (get("Beløpsintervall fra beløp") or get("Beløpsintervall til beløp")):
            value["amount_range"] = {"from": amount(get("Beløpsintervall fra beløp")),
                                     "to": amount(get("Beløpsintervall til beløp")), "currency": cur}
        # Verbatim evidence: the source fields up to and including the amount, without the birth-date column.
        keep = [cells[col[k]] for k in ("Støttetiltaksnummer", "Organisasjonsnummer støttemottaker",
                                         "Navn støttegiver", "Tildelingsdato", "Tildelt beløp") if col[k] < len(cells)]
        per[org].append({"organisation_number": org, "record_id": nr, "value": value,
                         "span_cells": keep, "row_key": f"csv line {lineno}"})
    out = ROOT / "cache" / "state_aid.jsonl.gz"
    n_out = 0
    with gzip.open(out, "wt", encoding="utf-8") as fh:
        for org in sorted(per):
            rows = sorted(per[org], key=lambda r: (r["value"]["date"] or "", r["record_id"] or ""), reverse=True)
            for r in rows[:MAX_PER_COMPANY]:
                r["total_grants"] = len(rows)
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
                n_out += 1
    meta = {"name": "state_aid", "built_at": built_at, "source_url": DUMP_URL, "source_sha256": file_sha,
            "source_bytes": len(raw), "source_rows": n_rows, "companies": len(per), "rows": n_out,
            "licence": "NLOD 2.0 (Brønnøysundregistrene)", "scheme_names_from": SCHEMES_URL,
            "privacy": "recipient birth-date column dropped at read time; only organisation-number recipients kept",
            "use": "fallback when the run budget cannot afford the live per-company Støtteregisteret search"}
    (ROOT / "cache" / "state_aid.meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(meta, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
