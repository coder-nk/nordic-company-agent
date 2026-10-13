"""Aggregate statistics + verification-level assignment over analysis files."""
import json, sys, re, collections, pathlib
from urllib.parse import urlsplit
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from web_extract import reg_domain

def level(r):
    """Return (level, reason). L1 = target orgnr on site; L2 = registry-linked + prominent name + no foreign orgnr; L0 = do not publish."""
    pages = [p for p in r["pages"] if "text_len" in p]
    home = [p for p in pages if p["label"].startswith("home")]
    if not home:
        return "L0", "unreachable:" + str(r.get("home_error") or r.get("home_status"))[:40]
    h = home[-1]
    if h["status"] and h["status"] >= 400:
        if any(s.startswith("bot_wall") for s in h["placeholder_signals"]):
            return "L0", "bot_wall"
        return "L0", f"http_{h['status']}"
    ph = [s for s in h["placeholder_signals"] if not s.startswith("tiny_text") and s != "meta_refresh"]
    if ph:
        return "L0", "placeholder:" + ph[0][:40]
    same = [p for p in pages if p.get("status") == 200]
    tgt = [p for p in same if p.get("target_in_text") or p.get("target_in_html")]
    others_kw = sorted({o for p in same for o in p.get("other_orgnrs_kw", [])})
    if tgt:
        if others_kw:
            return "L1-multi", "target + other orgnrs " + ",".join(others_kw)
        return "L1", "target orgnr on " + ",".join(sorted({p["label"] for p in tgt}))
    if others_kw:
        return "L0", "foreign orgnr " + ",".join(others_kw)
    if h["text_len"] < 200:
        return "L0", "no_content(js/tiny)"
    nm = max((p.get("name_prominent_match") or 0) for p in same) if same else 0
    if nm >= 0.99:
        return "L2", "registry hjemmeside + prominent name" + (" (domain changed)" if r["domain_changed"] else "")
    return "L0", f"name mismatch ({nm})"

def main(files):
    rows = []
    for f in files:
        rows += json.loads(pathlib.Path(f).read_text())
    N = len(rows)
    C = collections.Counter
    out = {"N": N}
    # reachability
    reach = C()
    for r in rows:
        e = r.get("home_error") or ""
        st = r.get("home_status")
        if r["robots"].get("allowed") is False: reach["robots_disallow"] += 1
        elif "Name or service" in e: reach["dns_fail"] += 1
        elif "Timeout" in e: reach["timeout"] += 1
        elif e: reach["conn_error:" + e.split(":")[0]] += 1
        elif st == 200: reach["200"] += 1
        else: reach[f"http_{st}"] += 1
    out["reach"] = reach
    # redirects
    red = C()
    for r in rows:
        if not r.get("final_url"): continue
        start = r["hjemmeside"] if "://" in r["hjemmeside"] else "https://" + r["hjemmeside"]
        s, f = urlsplit(start), urlsplit(r["final_url"])
        sh, fh = (s.hostname or "").lower(), (f.hostname or "").lower()
        kinds = []
        if f.scheme == "http": kinds.append("final_http_only")
        if r["domain_changed"]: kinds.append("other_domain")
        elif sh != fh: kinds.append("www_toggle")
        if r.get("redirects"): kinds.append("had_redirect")
        for k in kinds: red[k] += 1
        if not kinds: red["none"] += 1
    out["redirects"] = red
    lv = C(); reasons = collections.defaultdict(list)
    for r in rows:
        l, why = level(r)
        r["_level"], r["_why"] = l, why
        lv[l] += 1
        reasons[l].append((r["orgnr"], r["orgform"], r["navn"][:30], why[:60]))
    out["levels"] = lv
    # where target found
    where = C(); fmts = C(); kwctx = C(); first_hit = C()
    for r in rows:
        pages = [p for p in r["pages"] if "text_len" in p and p.get("status") == 200]
        hit_labels = [p["label"] for p in pages if p.get("target_in_text") or p.get("target_in_html")]
        if hit_labels:
            first_hit[hit_labels[0].replace("home_clientredirect", "home")] += 1
            for l in set(hit_labels): where[l] += 1
            for p in pages:
                for f in p.get("target_formats", []): fmts[f] += 1
                if p.get("target_in_text"): kwctx["kw" if p.get("target_kw_ctx") else "no_kw"] += 1
            if any(p.get("target_in_footer") for p in pages): where["_in_footer_any"] += 1
            if any((p.get("target_in_html") or 0) > (p.get("target_in_text") or 0) for p in pages): where["_also_html_only"] += 1
    out["target_first_found_on"] = first_hit
    out["target_found_on_page_kind"] = where
    out["target_formats"] = fmts
    out["target_kw_ctx"] = kwctx
    # orgnr raw formats across all occurrences (all orgnrs)
    allfmt = C()
    for r in rows:
        for p in r["pages"]:
            for o in p.get("orgnrs", []):
                if o["src"] == "text": allfmt[o["format"]] += 1
    out["all_orgnr_formats_text"] = allfmt
    # subpage availability
    sub = C()
    for r in rows:
        for p in r["pages"]:
            if not p["label"].startswith("home"): sub[p["label"] + ("_ok" if p.get("status") == 200 else "_fail")] += 1
    out["subpages_fetched"] = sub
    # json-ld, social, contact
    reachable = [r for r in rows if r.get("home_status") == 200]
    out["reachable_200"] = len(reachable)
    jl = C(); jlorg_fields = C(); soc = C(); contact = C(); gen = C()
    for r in reachable:
        pages = [p for p in r["pages"] if "text_len" in p and p.get("status") == 200]
        hp = [p for p in pages if p["label"].startswith("home")][-1:] or pages[:1]
        types = set(t for p in pages for t in p["jsonld_types"])
        if types: jl["any_jsonld"] += 1
        if hp and hp[0]["jsonld_types"]: jl["home_jsonld"] += 1
        orgs = [o for p in pages for o in p["jsonld_orgs"]]
        if orgs: jl["org_like_jsonld"] += 1
        for o in orgs[:1]:
            for k in o: jlorg_fields[k] += 1
        for t in types: jl["type:" + t] += 1
        md = set(t for p in pages for t in p["microdata_types"])
        if md: jl["any_microdata"] += 1
        s_all = collections.defaultdict(set)
        for p in pages:
            for k, v in p["social"].items(): s_all[k] |= set(v)
        for k in s_all: soc[k] += 1
        if any(p["mailto"] for p in pages): contact["mailto_any"] += 1
        if any(p["emails_text"] for p in pages): contact["email_text_any"] += 1
        if any(p["cf_emails"] for p in pages): contact["cloudflare_email"] += 1
        if any(p["emails_obfuscated"] for p in pages): contact["obfuscated_email"] += 1
        if any(p["mailto_rejected"] for p in pages): contact["placeholder_or_bad_mailto"] += 1
        if any(p["tel_links"] for p in pages): contact["tel_link_any"] += 1
        if any(any(x["kw"] for x in p["phones_text"]) for p in pages): contact["phone_text_kw"] += 1
        be = (r.get("brreg_email") or "").lower()
        if be:
            contact["brreg_email_present"] += 1
            if any(be in (p["mailto"] + p["emails_text"]) for p in pages): contact["brreg_email_on_site"] += 1
            site_dom = reg_domain(urlsplit(r["final_url"] or "").hostname or "")
            if be.split("@")[-1] == site_dom or be.endswith("." + site_dom): contact["brreg_email_domain_eq_site"] += 1
        bp = re.sub(r"\D", "", r.get("brreg_phone") or "")[-8:]
        if bp:
            contact["brreg_phone_present"] += 1
            on = any(bp in re.sub(r"\D", "", " ".join(p["tel_links"])) or any(bp == x["digits"] for x in p["phones_text"]) for p in pages)
            if on: contact["brreg_phone_on_site"] += 1
        g = (hp[0]["generator"] if hp else None) or "unknown"
        gen[re.split(r"[ ;]", g.lower())[0][:20]] += 1
    out["jsonld"] = jl; out["jsonld_org_fields"] = jlorg_fields; out["social"] = soc; out["contact"] = contact
    out["generator"] = gen
    # other orgnrs classification
    oth = C()
    for r in rows:
        for p in r["pages"]:
            for o in p.get("orgnrs", []):
                if o["src"] == "text" and o["orgnr"] != r["orgnr"] and o["kw"]:
                    tag = "credit" if o["credit_ctx"] else ("group" if o["group_ctx"] else ("privacy_page" if p["label"] == "privacy" else "other"))
                    oth[tag] += 1
    out["other_orgnr_ctx_occurrences"] = oth
    out["entities_with_other_orgnr_kw"] = sum(1 for r in rows if any(p.get("other_orgnrs_kw") for p in r["pages"]))
    req = [r["requests"] for r in rows]
    out["requests_avg"] = round(sum(req) / N, 2); out["requests_max"] = max(req)
    print(json.dumps(out, ensure_ascii=False, indent=1, default=dict))
    for l in sorted(reasons):
        print("\n##", l, len(reasons[l]))
        for x in reasons[l]: print("  ", *x)
    return rows

if __name__ == "__main__":
    main(sys.argv[1:])
