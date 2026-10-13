#!/usr/bin/env python3
"""cache/registry_contact_filers.json.gz: every phone (last 8 digits) and e-mail filed in Enhetsregisteret
(main units and sub-units) -> the organisation numbers that file it. A website contact is publishable for a
company only if no OTHER organisation number files it."""
import gzip, json, re, sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from nordic_company_agent.registry_csv import iter_snapshot  # noqa: E402
phones, emails = defaultdict(set), defaultdict(set)
def add(rec, parent=None):
    o = str(rec.get("organisasjonsnummer"))
    owner = parent or o
    for k in ("telefon", "mobil"):
        d = re.sub(r"\D", "", rec.get(k) or "")
        if len(d) >= 8:
            phones[d[-8:]].add(owner)
    e = (rec.get("epostadresse") or "").strip().lower()
    if "@" in e:
        emails[e].add(owner)
for rec in iter_snapshot(ROOT / "data" / "bulk" / "enheter.csv.gz"):
    add(rec)
sub = ROOT / "data" / "bulk" / "underenheter.json.gz"
if sub.exists():
    import ijson
    with gzip.open(sub, "rb") as fh:
        for item in ijson.items(fh, "item"):
            add(item, parent=str(item.get("overordnetEnhet") or item.get("organisasjonsnummer")))
out = {"phones": {k: sorted(v) for k, v in phones.items()}, "emails": {k: sorted(v) for k, v in emails.items()}}
with gzip.open(ROOT / "cache" / "registry_contact_filers.json.gz", "wt", encoding="utf-8") as fh:
    json.dump(out, fh, separators=(",", ":"))
(ROOT / "cache" / "registry_contact_filers.meta.json").write_text(json.dumps({
    "name": "registry_contact_filers", "built_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    "source": "Enhetsregisteret bulk files (enheter CSV, underenheter JSON), NLOD 2.0; sub-unit contacts count for "
              "their parent", "phones": len(phones), "emails": len(emails)}, indent=2) + "\n")
print(len(phones), len(emails))
