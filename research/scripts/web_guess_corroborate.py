"""For guessed domains with a name match but no orgnr: fetch up to 2 subpages and test registry corroboration."""
import asyncio, json, sys, pathlib
import httpx
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from web_fetch_study import fetch, pick_subpages, UA, HTML_DIR
from web_extract import extract_page
from web_corroborate import corroborate

R = json.loads(pathlib.Path("work/brreg_records.json").read_text())
async def main():
    cands = []
    for fn in ("work/domain_guess.json", "work/domain_guess2.json"):
        for r in json.loads(pathlib.Path(fn).read_text()):
            for c in r["cands"]:
                if c.get("file") and c.get("status") == 200 and not c.get("placeholder") and (c.get("name_match") or 0) > 0:
                    cands.append((r, c))
    out = []
    async with httpx.AsyncClient(headers={"User-Agent": UA}, follow_redirects=True, timeout=10) as client:
        for r, c in cands:
            rec = {"requests": 0}
            home = (HTML_DIR / c["file"]).read_text()
            base = c.get("final_url") or ("https://" + c["domain"] + "/")
            htmls = [home]
            for kind, u, _ in pick_subpages(home, base)[:2]:
                p, h = await fetch(client, u, rec, kind)
                if h:
                    htmls.append(h)
                    (HTML_DIR / f"{c['file'][:-5]}__{kind}.html").write_text(h)
            s = corroborate(htmls, R[r["orgnr"]], base)
            tgt = any(extract_page(h, base, r["orgnr"], r["navn"])["target_in_html"] for h in htmls)
            out.append({"orgnr": r["orgnr"], "navn": r["navn"], "domain": c["domain"], "kind": c["kind"], "final": base,
                        "title": c.get("title"), "target_found": tgt, "other_orgnrs": c.get("other_orgnrs"), "corr": s,
                        "extra_requests": rec["requests"]})
            print(r["navn"][:30].ljust(30), c["domain"][:26].ljust(26), "TGT" if tgt else "---", {k: v for k, v in s.items() if v}, "|", (c.get("title") or "")[:40])
    pathlib.Path("work/guess_corroboration.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
asyncio.run(main())
