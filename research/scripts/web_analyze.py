"""Analyze cached pages from web_fetch_study.py output.
Usage: uv run --with httpx --with selectolax python web_analyze.py work/sample_with_site.json work/fetch_with_site.json work/analysis_with_site.json
"""
import json, sys, pathlib, re, collections, time, urllib.request
from urllib.parse import urlsplit
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from web_extract import extract_page, reg_domain

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML = ROOT / "work" / "html"
UA = "nordic-company-agent/0.1 (research prototype)"
CACHE = ROOT / "work" / "brreg_cache.json"
cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}

def brreg(org):
    if org in cache: return cache[org]
    for kind in ("enheter", "underenheter"):
        try:
            req = urllib.request.Request(f"https://data.brreg.no/enhetsregisteret/api/{kind}/{org}", headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=15) as r:
                d = json.load(r)
            time.sleep(0.4)
            cache[org] = {"kind": kind, "navn": d.get("navn"), "orgform": (d.get("organisasjonsform") or {}).get("kode"),
                          "hjemmeside": d.get("hjemmeside"), "overordnet": d.get("overordnetEnhet") or d.get("overordnetEnhet"),
                          "parent": d.get("overordnetEnhet"), "slettet": d.get("slettedato")}
            return cache[org]
        except Exception as e:
            time.sleep(0.4)
            continue
    cache[org] = None
    return None

def main(sample_f, fetch_f, out_f):
    sample = {e["organisasjonsnummer"]: e for e in json.loads(pathlib.Path(sample_f).read_text())}
    fetched = json.loads(pathlib.Path(fetch_f).read_text())
    out = []
    for r in fetched:
        org = r["orgnr"]; ent = sample[org]
        row = {"orgnr": org, "navn": ent["navn"], "orgform": ent["orgform"], "hjemmeside": ent.get("hjemmeside"),
               "brreg_email": ent.get("epostadresse"), "brreg_phone": ent.get("telefon") or ent.get("mobil"),
               "requests": r["requests"], "robots": r["robots"], "pages": []}
        home = [p for p in r["pages"] if p["label"].startswith("home") and p.get("file")]
        hp = [p for p in r["pages"] if p["label"].startswith("home")]
        row["home_status"] = hp[-1].get("status") if hp else None
        row["home_error"] = hp[-1].get("error") if hp else r.get("skipped")
        row["final_url"] = hp[-1].get("final_url") if hp else None
        row["redirects"] = hp[-1].get("redirects") if hp else None
        start_host = (urlsplit(("https://" + ent["hjemmeside"]) if "://" not in ent["hjemmeside"] else ent["hjemmeside"]).hostname or "").lower()
        fin_host = (urlsplit(row["final_url"] or "").hostname or "").lower()
        row["domain_changed"] = bool(fin_host) and reg_domain(fin_host) != reg_domain(start_host)
        for p in r["pages"]:
            if not p.get("file"): 
                row["pages"].append({"label": p["label"], "status": p.get("status"), "error": p.get("error")})
                continue
            html = (HTML / p["file"]).read_text()
            x = extract_page(html, p.get("final_url") or p["url"], org, ent["navn"])
            x.update(label=p["label"], status=p.get("status"))
            row["pages"].append(x)
        # other orgnrs -> brreg lookup
        others = sorted({o for x in row["pages"] for o in x.get("other_orgnrs_text", [])})
        row["other_orgnrs"] = {o: brreg(o) for o in others[:6]}
        out.append(row)
    pathlib.Path(out_f).write_text(json.dumps(out, ensure_ascii=False, indent=1))
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1))

if __name__ == "__main__":
    main(*sys.argv[1:4])
