#!/usr/bin/env python3
"""Offline builder for the declared cache `cache/wikidata_p2333.json.gz`.

One SPARQL query lists every non-deprecated Wikidata statement of P2333 (Norwegian organisation
number) -> `{orgnr: [QID, ...]}` (QIDs sorted). The runtime Wikidata module uses it only to
pre-select which batch organisation numbers have items, so a batch without any Wikidata company
costs zero requests. It never acts as evidence: every published fact is re-read live.

Usage:  uv run python tools/build_wikidata_index.py
"""

from __future__ import annotations

import gzip
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nordic_company_agent.http import USER_AGENT  # noqa: E402

ENDPOINT = "https://query.wikidata.org/sparql"
CACHE = ROOT / "cache"
QUERY = """SELECT ?item ?orgnr WHERE {
  ?item p:P2333 ?st . ?st ps:P2333 ?orgnr .
  FILTER NOT EXISTS { ?st wikibase:rank wikibase:DeprecatedRank }
}"""


def utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def fetch(max_attempts: int = 3) -> tuple[dict, int, str]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json"}
    requests = 0
    with httpx.Client(timeout=httpx.Timeout(120.0, connect=15.0), headers=headers) as client:
        for attempt in range(1, max_attempts + 1):
            requests += 1
            r = client.get(ENDPOINT, params={"format": "json", "query": QUERY})
            if r.status_code == 200:
                return r.json(), requests, str(r.request.url)
            ra = r.headers.get("retry-after", "")
            wait = min(float(ra), 120.0) if ra.isdigit() else 30.0
            print(f"HTTP {r.status_code} (retry-after {ra!r}); attempt {attempt}/{max_attempts}; "
                  f"waiting {wait:.0f}s", flush=True)
            if attempt < max_attempts:
                time.sleep(wait)
        raise SystemExit(f"WDQS refused the query after {requests} requests (last HTTP {r.status_code})")


def main() -> None:
    t0 = time.time()
    doc, requests, url = fetch()
    bindings = doc["results"]["bindings"]
    index: dict[str, set[str]] = {}
    skipped = 0
    for b in bindings:
        val = b.get("orgnr", {}).get("value", "")
        qid = b.get("item", {}).get("value", "").rsplit("/", 1)[-1]
        digits = re.sub(r"\D", "", val)
        if len(digits) != 9 or not qid.startswith("Q"):
            skipped += 1
            continue
        index.setdefault(digits, set()).add(qid)
    out = {k: sorted(v, key=lambda q: int(q[1:])) for k, v in sorted(index.items())}
    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = CACHE / "wikidata_p2333.json.gz.tmp"
    with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=9) as fh:
        json.dump(out, fh, separators=(",", ":"), sort_keys=True)
    os.replace(tmp, CACHE / "wikidata_p2333.json.gz")
    meta = {
        "name": "wikidata_p2333",
        "built_at": utc_iso(),
        "source": "Wikidata Query Service, P2333 (Norwegian organisation number)",
        "licence": "Wikidata, CC0 1.0",
        "endpoint": ENDPOINT,
        "query": QUERY,
        "statements": len(bindings),
        "organisation_numbers": len(out),
        "multi_item_organisation_numbers": sum(1 for v in out.values() if len(v) > 1),
        "skipped_non_9_digit_values": skipped,
        "requests": requests,
        "use": "pre-selection only; the runtime re-reads every published fact live",
    }
    (CACHE / "wikidata_p2333.meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False) + "\n",
                                                    encoding="utf-8")
    print(json.dumps({k: v for k, v in meta.items() if k != "query"}, indent=1),
          f"\n{time.time() - t0:.1f}s; url length {len(url)}")


if __name__ == "__main__":
    main()
