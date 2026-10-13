"""Larger random sample (with hjemmeside) for quantitative stats; different seed from web_sample_entities.py."""
import json, random, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import web_sample_entities as S
random.seed(777)
seen = {e["organisasjonsnummer"] for e in json.loads((S.OUT / "sample_with_site.json").read_text())}
res = []
for form, n, days in [("AS", 70, 20), ("ENK", 15, 30), ("FLI", 20, 60), ("SA", 5, 365), ("ASA", 2, 2000),
                      ("NUF", 4, 120), ("STI", 3, 365), ("DA", 2, 365), ("ANS", 2, 365), ("KOMM", 2, 9000),
                      ("STAT", 1, 9000), ("ORGL", 2, 2000), ("BRL", 2, 365)]:
    for e in S.collect(form, n + 3, True, days=days, per_page=1, max_tries=60):
        if e["organisasjonsnummer"] not in seen and sum(1 for x in res if x["orgform"] == form) < n:
            seen.add(e["organisasjonsnummer"]); res.append(e)
(S.OUT / "sample_large_with_site.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
print(len(res))
