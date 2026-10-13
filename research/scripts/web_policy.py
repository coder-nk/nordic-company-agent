"""Recommended verification policy (v2), simulated on cached pages with early stopping + request accounting."""
import json, re, sys, pathlib, collections
from urllib.parse import urlsplit
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from web_extract import extract_page, reg_domain, orgnr_block, social_matches_company, name_core_tokens, GENERIC_TOKENS
from web_corroborate import corroborate

ROOT = pathlib.Path(__file__).resolve().parent.parent
H = ROOT / "work" / "html"
R = json.loads((ROOT / "work" / "brreg_records.json").read_text())
ADDRESS_NAME_RX = re.compile(r"^[A-ZÆØÅÉ][A-ZÆØÅÉ\-\. ']+ \d+[A-Z]?(?:\s?-\s?\d+)?(?: [A-Z])?(?: (?:AS|ASA|BRL|SA|ANS|DA|ESEK|SAMEIE|BORETTSLAG|EIENDOM AS))?$")
ORDER = ["home", "home_http", "home_clientredirect", "contact", "about", "terms", "privacy", "impressum"]

def co_names(rec):
    out = []
    for k in ("forretningsadresse", "postadresse"):
        for line in (rec.get(k) or {}).get("adresse") or []:
            m = re.match(r"\s*c/o\s+(.+)", line or "", re.I)
            if m: out.append(m.group(1).lower())
    return out

def evaluate(row):
    org, name = row["orgnr"], row["navn"]
    rec = R[org]
    pages = sorted([p for p in row["pages"]], key=lambda p: ORDER.index(p["label"]) if p["label"] in ORDER else 99)
    req = 1  # robots.txt
    fetched, htmls = [], []
    found = False; contact_done = False
    for p in pages:
        if p["label"] in ("contact", "about", "terms", "privacy", "impressum"):
            if found and (contact_done or p["label"] != "contact"):
                break
        req += 1 + len(p.get("redirects") or [])
        fetched.append(p)
        if p["label"] == "contact": contact_done = True
        fn = H / f"{org}__{'home2' if p['label']=='home_clientredirect' else p['label']}.html"
        if "text_len" in p and fn.exists():
            htmls.append((p, fn.read_text()))
            if p.get("target_in_text") or p.get("target_in_html"): found = True
        if p["label"].startswith("home") and ("text_len" not in p or (p.get("status") or 0) >= 400 or
                                             [s for s in p.get("placeholder_signals", []) if not s.startswith(("tiny_text", "meta_refresh"))]):
            if p["label"] == "home" and any(q["label"] in ("home_http", "home_clientredirect") for q in pages):
                continue
            break
    home = [p for p, _ in htmls if p["label"].startswith("home")]
    res = {"orgnr": org, "navn": name, "orgform": row["orgform"], "requests": req, "pages_used": [p["label"] for p in fetched]}
    if not home:
        res.update(level="L0", why="unreachable/blocked"); return res
    hp = home[-1]
    bad = [s for s in hp["placeholder_signals"] if not s.startswith(("tiny_text", "meta_refresh"))]
    if (hp.get("status") or 0) >= 400 or bad:
        res.update(level="L0", why=("blocked" if any("bot_wall" in s for s in bad) or hp.get("status") in (403, 429) else "dead/parked:" + ",".join(bad)[:40]))
        return res
    ok = [(p, h) for p, h in htmls if p.get("status") == 200]
    tgt = [p for p, _ in ok if p.get("target_in_text") or p.get("target_in_html")]
    foreign = sorted({o for p, _ in ok for o in p.get("other_orgnrs_kw", [])})
    site = row["final_url"] or ""
    dom = reg_domain(urlsplit(site).hostname or "")
    facts = collections.defaultdict(set)
    if tgt:
        res["level"] = "L1-multi" if foreign else "L1"
        res["why"] = "orgnr on " + ",".join(p["label"] for p in tgt)
        for p, h in ok:
            if foreign:
                b = orgnr_block(h, org)
                if b and b["isolated"]:
                    facts["email"] |= set(b["emails"]); facts["phone"] |= set(b["phones"])
            else:
                facts["email"] |= {e for e in p["mailto"] + p["emails_text"] + p.get("rtl_emails", []) + p.get("cf_emails", [])}
                facts["phone"] |= {re.sub(r"\D", "", t)[-8:] for t in p["tel_links"]} | {x["digits"] for x in p["phones_text"] if x["kw"]}
    else:
        if foreign:
            res.update(level="L0", why="foreign orgnr " + ",".join(foreign)); return res
        if hp["text_len"] < 200:
            res.update(level="L0", why="no content (JS/image-only)"); return res
        corr = corroborate([h for _, h in ok], rec, site)
        dist = [t for t in name_core_tokens(name) if t not in GENERIC_TOKENS and not t.isdigit()]
        strong_fields = (hp["title"] + " " + hp["meta"].get("og:site_name", "") + " " + " ".join(o.get("name", "") if isinstance(o.get("name"), str) else "" for o in hp["jsonld_orgs"])).lower()
        nm = max((p.get("name_prominent_match") or 0) for p, _ in ok)
        strong = bool(dist) and all(t in re.sub(r"[^a-z0-9æøå ]", " ", strong_fields) or t in re.sub(r"[^a-z0-9æøå]", "", strong_fields) for t in dist)
        if ADDRESS_NAME_RX.match(name.upper()):
            res.update(level="L0", why="address-named entity (property SPV) - needs orgnr"); return res
        if any(any(t in c for t in re.findall(r"[a-zæøå]{4,}", strong_fields)) for c in co_names(rec)):
            res.update(level="L0", why="site of c/o manager"); return res
        if row["domain_changed"] and not strong:
            res.update(level="L0", why="redirected to other domain without strong name match"); return res
        if nm < 0.99:
            res.update(level="L0", why=f"name not prominent ({nm})"); return res
        if corr["n_corroborations"] < 1:
            res.update(level="L0", why="no registry corroboration"); return res
        res.update(level="L2", why="registry hjemmeside + name + " + ",".join(k for k in ("phone_match", "email_match", "email_domain_match", "address_match", "legal_name_full") if corr[k]))
        for p, h in ok:
            facts["email"] |= {e for e in p["mailto"] + p["emails_text"] + p.get("rtl_emails", []) if e.split("@")[-1].endswith(dom) or e == (rec.get("epostadresse") or "").lower()}
            facts["phone"] |= {re.sub(r"\D", "", t)[-8:] for t in p["tel_links"]} & {re.sub(r"\D", "", rec.get(k) or "")[-8:] for k in ("telefon", "mobil")}
    for p, h in ok:
        for k, v in p["social"].items():
            if k == "linkedin_person": continue
            for u in v:
                if social_matches_company(u, name, dom):
                    facts["social." + k].add(u)
        if p["label"].startswith("home"):
            d = p["meta"].get("description") or p["meta"].get("og:description")
            if d: facts["description"].add(d[:120])
    facts["website"].add(site)
    res["facts"] = {k: sorted(v) for k, v in facts.items() if v}
    return res

def main():
    rows = json.loads((ROOT / "work/analysis_with_site.json").read_text()) + json.loads((ROOT / "work/analysis_large.json").read_text())
    out = [evaluate(r) for r in rows]
    C = collections.Counter(o["level"] for o in out)
    print("levels", dict(C), "N", len(out))
    print("avg requests", round(sum(o["requests"] for o in out) / len(out), 2), "max", max(o["requests"] for o in out))
    pub = [o for o in out if o["level"] in ("L1", "L1-multi", "L2")]
    fc = collections.Counter(k for o in pub for k in o.get("facts", {}))
    print("published-entities", len(pub), "fact families:", dict(fc))
    why = collections.Counter(o["why"].split(" ")[0] if o["level"] == "L0" else o["level"] for o in out)
    print("L0 reasons", dict(why))
    for o in out:
        if o["level"] in ("L2", "L0") and ("name" in o["why"] or "registry" in o["why"] or "address" in o["why"] or "manager" in o["why"] or "redirected" in o["why"] or "corroboration" in o["why"]):
            print("  ", o["level"], o["orgnr"], o["navn"][:32].ljust(32), o["why"][:90])
    (ROOT / "work" / "policy_v2_results.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))

if __name__ == "__main__":
    main()
