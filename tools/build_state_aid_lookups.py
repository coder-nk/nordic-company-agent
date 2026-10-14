#!/usr/bin/env python3
"""Declared fallback for Støtteregisteret labels: scheme names and code-list labels.

The live module resolves scheme names (POST /soek/stoetteordning) and code labels (GET /kodeliste/...) once per run.
When that API is slow or down, grant values would fall back to bare ids and a refresh would report false changes.
This cache keeps the last known labels: scheme names from the state_aid dump cache (resolved when it was built)
plus the live code lists when they answer.

Usage: uv run python tools/build_state_aid_lookups.py
"""

from __future__ import annotations

import gzip
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from nordic_company_agent.sources.state_aid import API, CODELISTS, SCHEMES_URL  # noqa: E402

CACHE = ROOT / "cache"


def main() -> None:
    schemes: dict[str, str] = {}
    for line in gzip.open(CACHE / "state_aid.jsonl.gz", "rt", encoding="utf-8"):
        v = json.loads(line)["value"]
        if v.get("scheme_id") and v.get("scheme") and v["scheme"] != v["scheme_id"]:
            schemes[v["scheme_id"]] = v["scheme"]
    codes: dict[str, str] = {}
    with httpx.Client(timeout=30, headers={"User-Agent": "nordic-company-agent/1.0 (cache builder)",
                                           "Accept": "application/json"}) as cl:
        for name in CODELISTS:
            try:
                r = cl.get(f"{API}/kodeliste/{name}?language=NOB")
                if r.status_code == 200:
                    for row in r.json():
                        if isinstance(row, dict) and row.get("identifikator") and row.get("navn"):
                            codes[row["identifikator"]] = row["navn"]
            except httpx.HTTPError:
                pass
        try:
            r = cl.post(SCHEMES_URL, content=b"[]", headers={"Content-Type": "application/json"}, timeout=60)
            if r.status_code == 200:
                for s in r.json().get("stoetteordning") or []:
                    names = s.get("navn") or []
                    nm = next((n.get("tekst") for n in names if n.get("spraak") == "NOR"), None) or \
                        next((n.get("tekst") for n in names), None)
                    if s.get("stoettetiltaksnummer") and nm:
                        schemes[s["stoettetiltaksnummer"]] = nm
        except httpx.HTTPError:
            pass
    built = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    body = {"built_at": built, "schemes_source": SCHEMES_URL, "codes_source": f"{API}/kodeliste/<name>",
            "schemes": dict(sorted(schemes.items())), "codes": dict(sorted(codes.items()))}
    data = json.dumps(body, ensure_ascii=False, indent=1).encode()
    (CACHE / "state_aid_lookups.json.gz").write_bytes(gzip.compress(data, mtime=0))
    meta = {"name": "state_aid_lookups", "built_at": built, "schemes": len(schemes), "codes": len(codes),
            "sha256": hashlib.sha256(data).hexdigest(), "licence": "NLOD 2.0 (Brønnøysundregistrene)",
            "use": "labels for scheme ids and codes when the live Støtteregisteret lookups do not answer"}
    (CACHE / "state_aid_lookups.meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(meta, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
