"""Write trimmed HTML samples + index for unit tests."""
import json, re, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from selectolax.lexbor import LexborHTMLParser
from web_extract import extract_page, orgnr_block

ROOT = pathlib.Path(__file__).resolve().parent.parent
H = ROOT / "work" / "html"
OUT = ROOT / "samples"

def trim(html: str) -> str:
    t = LexborHTMLParser(html)
    small = len(html) < 6000
    for n in t.css("script"):
        keep = (n.attributes.get("type") or "").lower() == "application/ld+json"
        keep = keep or (small and "location" in (n.text(strip=False) or ""))   # client-side redirect samples
        if not keep:
            n.decompose()
    # mega-menus: drop huge lists (keeps footer/contact markup)
    for n in t.css("ul, ol"):
        if len(n.css("li")) > 40:
            n.decompose()
    for sel in ("style", "svg", "noscript", "iframe", "video", "picture source", "link[rel=preload]", "link[rel=stylesheet]",
                "link[rel=preconnect]", "link[rel=dns-prefetch]", "link[rel=modulepreload]", "template"):
        for n in t.css(sel): n.decompose()
    for n in t.css("*"):
        attrs = dict(n.attributes)
        for k, v in attrs.items():
            if (k.startswith("data-") and k != "data-cfemail") or k.startswith(("x-", "aria-", "@", ":")) or k in ("srcset", "sizes", "onclick", "onload", "nonce", "integrity", "role"):
                del n.attrs[k]
            elif k == "style" and "direction" not in (v or ""):
                del n.attrs[k]
            elif k == "src" and (v or "").startswith("data:"):
                n.attrs[k] = "data:,"
    out = t.html or ""
    out = re.sub(r"<!--(?!\s*-->).*?-->", "", out, flags=re.S)
    out = re.sub(r"\n\s*\n+", "\n", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out

SAMPLES = [
    # (orgnr, page file label, url, descr, legal name, notes)
    ("912342344", "home", "https://ekomas.no/", "L1_footer_orgnr_simple", "EKOM AS", "Org nr '912 342 344' in footer with address+email; clean L1 case."),
    ("928913406", "home", "https://www.biltransporten.no/", "L1_plain_orgnr_MVA_nospace", "BILTRANSPORTEN AS", "'Orgnr:928913406MVA' (no separators, MVA glued)."),
    ("917387079", "contact", "https://mf.no/kontakt", "L1_contact_page_NO_MVA_format", "MF VITENSKAPELIG HØYSKOLE STI", "Only on contact page: 'Org.nr.: NO 917 387 079 MVA' in invoicing block."),
    ("920209319", "home", "https://www.advokateneiba47.no", "L1_multi_company_footer", "OS ADVOKATER DA", "Shared office site: 3 law firms each with phone/email/orgnr. Only the block of 920 209 319 belongs to target."),
    ("985946698", "privacy", "https://www.polarfakta.no/personvernerklaering/", "L1_privacy_with_processor_orgnrs", "POLARFAKTA AS", "Privacy policy: target orgnr + 4 data-processor orgnrs (must not be attributed)."),
    ("982182107", "home", "https://www.webhuset.no/parkering", "L0_parked_host_page_with_host_orgnr", "NYE NOR AS", "Registry hjemmeside www.nyenor.no redirects to Webhuset parking page that shows WEBHUSET AS orgnr, phone, socials."),
    ("982322553", "home", "https://www.atticum.no", "L0_sister_company_orgnr_and_jsonld_taxid", "ATTICUM NETTLØSNINGER AS", "Site belongs to ATTICUM INCUBATOR AS (982574544) - footer + JSON-LD taxID; target absent."),
    ("932973855", "about", "https://www.2hd.uk/about", "L1_orgnr_only_in_brreg_link_href", "2HD ARCHITECTURE WORKSHOP AS", "Orgnr appears only inside links to virksomhet.brreg.no (html, not text); JSON-LD has UK phone of UK sister."),
    ("975971554", "home", "https://www.elektrikerservice.no", "L1_plain_orgnr_rtl_reversed_email", "ELEKTRIKERSERVICE AS", "'Org nr: 975971554'; e-mail obfuscated with CSS direction:rtl (text 'on.ecivresrekirtkele@tsop')."),
    ("975576205", "home", "https://www.drammenturn.no", "L1_FLI_footer_ehf_and_socials", "DRAMMENS TURNFORENING", "'(org.nr.975 576 205)' inside EHF invoice sentence; LinkedIn/Facebook/Instagram/YouTube company links."),
    ("929237617", "home", "http://www.lbl.as", "redirect_js_window_location", "BULLGRUPPEN AS", "107-byte page with window.location JS redirect to another domain; must follow client-side redirect then re-verify."),
    ("976939395", "home2", "https://sakarina.com/", "L0_hijacked_expired_domain_cjk", "SAKARINA ANS", "Expired domain now serving Chinese spam; name absent, CJK text."),
    ("984421664", "home", "https://www.hugedomains.com/domain_profile.cfm?d=hopetravel.com", "L0_parked_for_sale_hugedomains", "HOPENORWAY ANDRE HOPE", "Registry site redirects to HugeDomains 'for sale' page."),
    ("882912272", "home", "https://www.tent.no", "L0_cloudflare_bot_wall", "TENT AS", "HTTP 403 Cloudflare 'Just a moment...' challenge: treat as blocked, do not retry aggressively."),
    ("979448708", "about", "https://www.fil.no/om-oss", "L1_sports_club_with_sponsor_social", "FINNSNES IL FOTBALL", "Orgnr on about page; Facebook link to sponsor 'exploenergi' must not be attributed."),
    ("879887682", "home", "https://www.nmg.no", "L2_wix_platform_socials", "NEW MEDIA GENERATION Lage Thune Myrberget", "Wix site whose only social links are Wix's own (wix-com) -> reject socials; no orgnr on site."),
    ("976913019", "contact", "https://www.koolart.no/kontakt", "L1_ENK_vat_no_jsonld", "KOOLART John Martin Sanvik", "'MVA/VAT no. 976913019'; JSON-LD Organization with telephone/address; webshop."),
    ("992137452", "guess:core-international.com", "http://core-international.com/", "guess_false_positive_similar_name_gmbh", "CORE INTERNATIONAL AS", "Domain guessed from name; site is Core International GmbH (Germany). Name matches, orgnr absent -> reject."),
]

def main():
    index = []
    for org, label, url, descr, name, notes in SAMPLES:
        if label.startswith("guess:"):
            src = H / f"guess_{org}__{label.split(':', 1)[1]}.html"
        else:
            src = H / f"{org}__{label}.html"
        raw = src.read_text()
        tr = trim(raw)
        fn = f"company_websites__{descr}__{org}.html"
        (OUT / fn).write_text(tr)
        x_raw = extract_page(raw, url, org, name)
        x = extract_page(tr, url, org, name)
        blk = orgnr_block(tr, org) if (x["target_in_text"] or x["target_in_html"]) else None
        exp = {
            "target_orgnr_in_text": x["target_in_text"] > 0,
            "target_orgnr_in_html": x["target_in_html"] > 0,
            "target_formats": x["target_formats"],
            "other_orgnrs_with_keyword": x["other_orgnrs_kw"],
            "brreg_links": x["brreg_links"],
            "mailto": x["mailto"], "emails_text": x["emails_text"], "rtl_emails": x["rtl_emails"], "cf_emails": x["cf_emails"],
            "tel_links": x["tel_links"],
            "social": x["social"],
            "jsonld_types": x["jsonld_types"],
            "jsonld_orgs": [{k: v for k, v in o.items() if k in ("@type", "name", "legalName", "telephone", "email", "taxID", "identifier", "vatID")} for o in x["jsonld_orgs"]],
            "placeholder_signals": x["placeholder_signals"],
            "title": x["title"],
            "orgnr_block": ({k: blk[k] for k in ("emails", "phones", "isolated")} | {"text_prefix": blk["block_text"][:160]}) if blk else None,
        }
        same = (x_raw["target_in_text"] == x["target_in_text"] and x_raw["other_orgnrs_kw"] == x["other_orgnrs_kw"])
        index.append({"file": fn, "orgnr": org, "legal_name": name, "url": url, "page_kind": label,
                      "bytes_raw": len(raw), "bytes_trimmed": len(tr), "trim_preserves_orgnr_signals": same,
                      "notes": notes, "expected": exp})
        print(fn, len(raw), "->", len(tr), "same" if same else "DIFF")
    (OUT / "company_websites__index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1))

if __name__ == "__main__":
    main()
