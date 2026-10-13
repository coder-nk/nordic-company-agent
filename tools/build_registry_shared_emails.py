#!/usr/bin/env python3
"""cache/registry_shared_emails.json.gz: e-mail addresses filed in Enhetsregisteret by >= 2 org numbers
(property managers, accountants, groups). Used to label shared registry contacts as not company-specific."""
import gzip, json, sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from nordic_company_agent.registry_csv import iter_snapshot  # noqa: E402
bulk = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "bulk" / "enheter.csv.gz"
m = defaultdict(set)
for rec in iter_snapshot(bulk):
    e = (rec.get("epostadresse") or "").strip().lower()
    if "@" in e:
        m[e].add(str(rec.get("organisasjonsnummer")))
shared = {e: sorted(o) for e, o in m.items() if len(o) >= 2}
with gzip.open(ROOT / "cache" / "registry_shared_emails.json.gz", "wt", encoding="utf-8") as fh:
    json.dump(shared, fh, sort_keys=True)
(ROOT / "cache" / "registry_shared_emails.meta.json").write_text(json.dumps({
    "name": "registry_shared_emails", "built_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    "source": "Enhetsregisteret bulk CSV (epostadresse), NLOD 2.0", "shared_emails": len(shared)}, indent=2) + "\n")
print(len(shared))
