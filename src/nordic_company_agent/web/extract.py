"""Deterministic signal/fact extraction from company web pages.

`extract_page(html, url, target_orgnr, legal_name)` returns a dict of signals and candidate facts:
org numbers found (with keyword context), contact links, phones, social links, JSON-LD organisations,
parked/placeholder/bot-wall signals and name-match measures. Publication decisions are made by
`verify.py`; this module never decides identity.
Ported from the research prototype (research/scripts/web_extract.py), validated on 276 live sites.
"""
from __future__ import annotations

import json
import re
import unicodedata
from urllib.parse import unquote, urljoin, urlsplit

import tldextract
from selectolax.lexbor import LexborHTMLParser

from ..orgnr import is_valid as _is_valid

PARSER_VERSION = "web_extract@1"
_TLD = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)

SEP = r"[    .\-]"          # space, nbsp, thin space, nnbsp, dot, hyphen
ORGNR_RX = re.compile(
    r"(?<![\d])(?P<pre>NO" + SEP + r"?)?(?P<num>[89]\d{2}(?:" + SEP + r"?\d{3}){2})(?P<mva>" + SEP + r"?MVA)?(?![\d])",
    re.I)
ORG_KEYWORD_RX = re.compile(
    r"(org(?:anisasjons)?[\s.\-]*(?:n(?:umme)?r|no|number|num)\b\.?|orgnr|foretaksregisteret|"
    r"organisasjonsnummer|org\.?\s*id|reg(?:istration)?\.?\s*(?:no|nr|number)|business\s*(?:id|reg)|"
    r"\bvat\b|\bmva\b|foretaksnr|bedriftsnr|enhetsregisteret)", re.I)
CREDIT_RX = re.compile(r"(levert av|laget av|utviklet av|design(?:et)? (?:av|by)|webdesign|nettside(?:r)? (?:av|fra)|"
                       r"powered by|made by|developed by|produsert av|hjemmeside fra|built by|site by|"
                       r"drevet av|publisert med|hosting)", re.I)
GROUP_RX = re.compile(r"(en del av|del av konsernet|part of|datterselskap|subsidiary|morselskap|parent company|"
                      r"konsern|group|gruppen|holding|franchise|kjede|avdeling)", re.I)

EMAIL_RX = re.compile(r"(?<![\w.+-])[\w.+-]{1,64}@(?:[a-z0-9æøå-]+\.)+[a-z]{2,24}(?![\w-])", re.I)
EMAIL_OBF_RX = re.compile(r"([\w.+-]{1,64})\s*(?:\[at\]|\(at\)|\s@\s|\{at\}| at )\s*([a-z0-9-]+)\s*(?:\[dot\]|\(dot\)|\.| dot )\s*([a-z]{2,6})\b", re.I)
PHONE_RX = re.compile(
    r"(?<![\w+])(?P<cc>(?:\+|00)" + r"[  ]?47[  .\-]?|\(\+47\)[  ]?)?"
    r"(?P<num>[2-9]\d(?:[  .\-]?\d{2}){3}|[2-9]\d{2}[  .\-]?\d{2}[  .\-]?\d{3}|[2-9]\d{3}[  .\-]?\d{4})(?!\d|[,.]\d)")
PHONE_KW_RX = re.compile(r"(tlf|telefon|tel\b|phone|mob(?:il)?|ring\b|sentralbord|kontakt|call|fax|faks)", re.I)
SOCIAL_RX = {
    "linkedin": re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/(company|school|showcase)/([^/?#\s\"']+)", re.I),
    "linkedin_person": re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/in/([^/?#\s\"']+)", re.I),
    "facebook": re.compile(r"https?://(?:www\.|m\.|web\.|business\.|[a-z]{2}-[a-z]{2}\.)?(?:facebook|fb)\.com/(?!sharer|share|dialog|plugins|tr\b|tr\?|l\.php|policy|privacy|help|login|events/|groups/|ads\b|business\b|watch\b|hashtag|photo|story\.php|permalink)((?:profile\.php\?id=\d+)|(?:(?:p|pg|pages)/[^?#\s\"']+)|[^/?#\s\"'<>]+)", re.I),
    "instagram": re.compile(r"https?://(?:www\.)?instagram\.com/(?!p/|reel/|explore/|accounts/|share)([^/?#\s\"'<>]+)", re.I),
    "twitter": re.compile(r"https?://(?:www\.)?(?:twitter|x)\.com/(?!share|intent|home|search|hashtag|i/|en\b|no\b|nb\b|login|signup|tos|privacy)([A-Za-z0-9_]{1,15})(?![\w])", re.I),
    "youtube": re.compile(r"https?://(?:www\.|m\.)?youtube\.com/((?:channel|c|user)/[^/?#\s\"']+|@[^/?#\s\"']+)", re.I),
    "tiktok": re.compile(r"https?://(?:www\.)?tiktok\.com/(@[^/?#\s\"'<>]+)", re.I),
}
PARKING_HOSTS = ("sedo.", "sedoparking", "parkingcrew", "bodis.", "dan.com", "afternic", "hugedomains", "undeveloped.",
                 "domainmarket", "parked", "parkering", "above.com", "uniregistry", "sav.com", "namebright",
                 "domenesalg", "domainnameshop", "domeneshop.no/parking", "one.com/no/parked", "godaddysites",
                 "webhuset.no/parkering", "/parking", "domainlore", "brandbucket", "squadhelp", "atom.com")
# PARKING_HOSTS above was matched as plain substrings of the whole URL, which flagged real sites such as
# jordan.com ('dan.com'), parkedcars.no ('parked') or any path containing '/parking'. `parking_url()` matches
# broker/parking brands on the registrable domain and parking markers only as the first path segment.
_PARKING_BRANDS = {"sedoparking", "parkingcrew", "bodis", "afternic", "hugedomains", "undeveloped", "domainmarket",
                   "uniregistry", "namebright", "domenesalg", "domainlore", "brandbucket", "squadhelp", "parklogic"}
_PARKING_DOMAINS = {"sedo.com", "dan.com", "above.com", "sav.com", "atom.com"}
_PARKING_PATH_RX = re.compile(r"^/(?:[a-z]{2}/)?(?:parking|parkering|parked|parkert)(?:[/.?_-]|$)|/cgi-sys/suspendedpage\.cgi", re.I)


def parking_url(url: str) -> bool:
    """True when the URL is a domain-parking / for-sale / suspended-account page."""
    sp = urlsplit((url or "").strip().lower())
    host = (sp.hostname or "").removeprefix("www.")
    dom = reg_domain(host) if host else ""
    if dom in _PARKING_DOMAINS or (dom and dom.split(".")[0] in _PARKING_BRANDS):
        return True
    return bool(_PARKING_PATH_RX.search(sp.path or ""))


def decode_cfemail(hexstr: str) -> str | None:
    """Decode a Cloudflare e-mail-protection token (data-cfemail / /cdn-cgi/l/email-protection#...)."""
    h = (hexstr or "").strip()
    if len(h) < 4 or len(h) % 2 or not re.fullmatch(r"[0-9a-fA-F]+", h):
        return None
    k = int(h[:2], 16)
    try:
        out = "".join(chr(int(h[i:i + 2], 16) ^ k) for i in range(2, len(h), 2))
    except ValueError:
        return None
    return out.lower() if EMAIL_RX.fullmatch(out) else None


_CF_HREF_RX = re.compile(r"/cdn-cgi/l/email-protection#([0-9a-fA-F]{4,})")
PARKED_TEXT_RX = re.compile(
    r"(this domain (?:is|may be) (?:for sale|parked|registered)|domain (?:is )?for sale|buy this domain|kjøp (?:dette )?domenet|"
    r"(?:dette )?domenet (?:er|kan være) (?:til salgs|parkert|registrert|reservert)|domenet .{0,40} er (?:parkert|registrert hos)|"
    r"parkert (?:domene|hos)|parked (?:domain|free|by)|domain parking|"
    r"under construction|under oppbygging|siden er under (?:arbeid|utvikling|oppbygging|konstruksjon)|kommer snart|coming soon|launching soon|"
    r"website (?:expired|is (?:currently )?unavailable)|account (?:has been )?suspended|this account has been suspended|"
    r"welcome to nginx|apache2? (?:\w+ )?default page|it works!|iis windows server|index of /|default web site page|"
    r"looks like this domain isn.t connected|domain (?:is )?not connected|this site can.t be reached|"
    r"hosted by one\.com|webhotellet ditt er klart|your (?:new )?website is (?:almost )?ready|future home of|"
    r"nettstedet er ikke tilgjengelig|denne nettsiden er ikke publisert|ikke publisert ennå|"
    r"\bis parked\b|parkert hos|\(offline\)|midlertidig stengt|under maintenance|maintenance mode|"
    r"denne siden er ikke aktiv|siden er ikke aktiv|new page 1|default parallels plesk|plesk default page|"
    r"web server(?:'s)? default page|this is the default (?:welcome )?page|site not found|domain has expired|"
    r"domenet har utløpt|registrert (?:hos|via) (?:domeneshop|one\.com|webhuset|uniweb|pro ?isp|loopia))", re.I)
BOT_WALL_RX = re.compile(r"(just a moment\.\.\.|attention required! \| cloudflare|cf-chl-|cf_chl_|vercel security checkpoint|"
                         r"bekrefter at du ikke er en bot|checking your browser|ddos-guard|captcha-delivery|"
                         r"are you a robot|access denied|request unsuccessful\. incapsula|sgcaptcha)", re.I)
CJK_RX = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")
PLACEHOLDER_EMAIL_RX = re.compile(r"@(example\.(?:com|no|org)|domain\.(?:com|no)|yourdomain|dittdomene|email\.com$|"
                                  r"sentry(?:\.io|-next)|wixpress\.com|mysite\.com|test\.com|firma\.no$|company\.com$)|"
                                  r"^(?:name|navn|your|din|ola\.nordmann|john\.doe|user)@", re.I)
GENERIC_TOKENS = {"holding", "eiendom", "eiendommer", "invest", "investering", "gruppen", "group", "bygg", "byggservice",
    "regnskap", "regnskapskontor", "revisjon", "consulting", "konsulent", "service", "services", "transport", "drift",
    "utvikling", "handel", "import", "export", "norge", "norway", "norsk", "nordic", "scandinavia", "management",
    "solutions", "systems", "design", "media", "entreprenør", "maskin", "elektro", "rør", "tak", "maler", "frisør",
    "kafe", "restaurant", "borettslag", "sameie", "forening", "velforening", "idrettslag", "kommune", "avdeling",
    "stiftelsen", "senter", "klinikk", "tannlege", "advokat", "advokatfirma", "advokatfirmaet", "partners", "i", "på",
    "of", "co", "company", "kompani", "firma", "international", "consult", "tjenester", "produkter", "anlegg", "bil",
    "og", "utleie", "salg", "prosjekt", "prosjekter", "projects", "capital", "ventures", "studio", "agency", "hotell",
    "foreningen", "lag", "laget", "klubb", "musikklag", "barnehage", "skole", "trafikkskole", "apotek",
    "rekruttering", "renhold", "renholdsservice", "takst", "bilskade", "medical", "teknikk", "marine", "travel", "fylkeskommune"}
ORGFORM_TOKENS = {"as", "asa", "enk", "nuf", "sa", "da", "ans", "ba", "ks", "stiftelse", "sti", "fli", "brl", "esek",
                  "iks", "kf", "the", "og", "and", "&", "v/", "avd", "avd.", "norge", "norway"}

def mod11_ok(n: str) -> bool:
    return _is_valid(n)

def target_rx(org: str):
    return re.compile(r"(?<!\d)" + (SEP + "?").join(org) + r"(?!\d)")

def classify_format(m_text: str) -> str:
    s = m_text
    if re.fullmatch(r"\d{9}", s): return "plain"
    if re.fullmatch(r"\d{3} \d{3} \d{3}", s): return "3-3-3 space"
    if re.fullmatch(r"\d{3}[   ]\d{3}[   ]\d{3}", s): return "3-3-3 nbsp"
    if re.fullmatch(r"\d{3}\.\d{3}\.\d{3}", s): return "3-3-3 dot"
    if re.fullmatch(r"\d{3}-\d{3}-\d{3}", s): return "3-3-3 hyphen"
    return "other"

def visible_text(tree) -> str:
    for sel in ("script", "style", "noscript", "template", "svg"):
        for n in tree.css(sel):
            n.decompose()
    body = tree.body or tree.root
    t = body.text(separator=" ", strip=False) if body else ""
    return re.sub(r"[ \t\r\f\v]+", " ", re.sub(r"\n\s*", "\n", t)).strip()

def parse_jsonld(tree):
    out, errors = [], 0
    for s in tree.css('script[type="application/ld+json"]'):
        raw = s.text(strip=False) or ""
        raw = re.sub(r"^\s*(<!\[CDATA\[|<!--)|(\]\]>|-->)\s*$", "", raw.strip())
        try:
            data = json.loads(raw)
        except Exception:
            try:
                data = json.loads(re.sub(r",\s*([}\]])", r"\1", raw), strict=False)
            except Exception:
                errors += 1
                continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            o = stack.pop()
            if isinstance(o, list): stack.extend(o); continue
            if not isinstance(o, dict): continue
            if "@graph" in o: stack.extend(o["@graph"] if isinstance(o["@graph"], list) else [o["@graph"]])
            if "@type" in o: out.append(o)
    return out, errors

ORG_TYPES_RX = re.compile(r"Organization|Corporation|LocalBusiness|Store|Restaurant|Service|Dentist|Physician|Clinic|"
                          r"Contractor|Agency|Office|Shop|Hotel|LodgingBusiness|NGO|EducationalOrganization|"
                          r"GovernmentOrganization|SportsOrganization|Attorney|LegalService|AccountingService|"
                          r"Electrician|Plumber|HomeAndConstructionBusiness|AutomotiveBusiness|MedicalBusiness|"
                          r"HealthAndBeautyBusiness|FoodEstablishment|CafeOrCoffeeShop|Bakery|Museum|Church|"
                          r"CollegeOrUniversity|School|SportsClub|NewsMediaOrganization|Airline|BankOrCreditUnion|"
                          r"FinancialService|InsuranceAgency|RealEstateAgent|TravelAgency|ProfessionalService", re.I)

def jsonld_orgs(objs):
    res = []
    for o in objs:
        t = o.get("@type")
        ts = t if isinstance(t, list) else [t]
        if any(isinstance(x, str) and ORG_TYPES_RX.search(x) for x in ts):
            keep = {k: o.get(k) for k in ("@type", "@id", "name", "legalName", "url", "telephone", "email", "address",
                                          "vatID", "taxID", "identifier", "leiCode", "sameAs", "logo", "foundingDate",
                                          "numberOfEmployees", "geo", "openingHours", "openingHoursSpecification",
                                          "contactPoint", "description", "parentOrganization", "founder") if k in o}
            res.append(keep)
    return res

def reg_domain(host: str) -> str:
    """Registrable domain using the bundled Public Suffix List (kristiansand.kommune.no stays whole)."""
    host = (host or "").lower().strip(".").removeprefix("www.")
    if not host:
        return ""
    r = _TLD(host)
    if hasattr(r, "top_domain_under_public_suffix"):
        dom = r.top_domain_under_public_suffix      # tldextract >= 5.3 (registered_domain is deprecated)
    else:  # pragma: no cover - older tldextract
        dom = r.registered_domain
    return dom or host

def name_core_tokens(name: str):
    n = unicodedata.normalize("NFKC", name.lower())
    toks = re.findall(r"[a-z0-9æøåäöüé]+", n)
    return [t for t in toks if t not in ORGFORM_TOKENS and len(t) >= 2]

def extract_page(html: str, url: str, target: str | None = None, legal_name: str | None = None):
    raw = html
    tree = LexborHTMLParser(html)
    title = (tree.css_first("title").text(strip=True) if tree.css_first("title") else "")[:200]
    metas = {}
    for m in tree.css("meta"):
        k = (m.attributes.get("property") or m.attributes.get("name") or "").lower()
        if k in ("og:site_name", "og:title", "description", "og:description", "generator", "author", "application-name",
                 "og:url", "og:type", "twitter:site", "copyright", "dc.publisher", "geo.placename", "geo.region"):
            metas[k] = (m.attributes.get("content") or "")[:200]
    html_lang = (tree.css_first("html").attributes.get("lang") if tree.css_first("html") else None)
    jsonld, jsonld_err = parse_jsonld(tree)
    jsonld_types = sorted({(x if isinstance(x, str) else str(x)) for o in jsonld
                           for x in (o["@type"] if isinstance(o["@type"], list) else [o["@type"]])})
    # microdata
    microdata = sorted({n.attributes.get("itemtype", "").rsplit("/", 1)[-1] for n in tree.css("[itemtype]")})
    links = []
    for a in tree.css("a[href]"):
        links.append(((a.attributes.get("href") or "").strip(), a.text(separator=" ", strip=True)[:80]))
    # CSS-reversed emails: <a style="unicode-bidi:bidi-override;direction:rtl">on.firma@tsop</a>
    rtl_emails = []
    for n in tree.css("[style]"):
        st = (n.attributes.get("style") or "").replace(" ", "").lower()
        if "direction:rtl" in st:
            rev = (n.text(strip=True) or "")[::-1]
            if EMAIL_RX.fullmatch(rev): rtl_emails.append(rev.lower())
    cf_emails = []
    for n in tree.css("[data-cfemail]"):
        dec = decode_cfemail(n.attributes.get("data-cfemail") or "")
        if dec:
            cf_emails.append(dec)
    for href, _ in links:
        m = _CF_HREF_RX.search(href)
        dec = decode_cfemail(m.group(1)) if m else None
        if dec:
            cf_emails.append(dec)
    footer_nodes = tree.css("footer, #footer, .footer, [class*=footer], [id*=footer]")
    footer_text = " | ".join(n.text(separator=" ", strip=True)[:1500] for n in footer_nodes[:3])
    text = visible_text(tree)
    res = {"url": url, "title": title, "meta": metas, "lang": html_lang, "text_len": len(text),
           "html_len": len(raw), "jsonld_types": jsonld_types, "jsonld_errors": jsonld_err,
           "jsonld_orgs": jsonld_orgs(jsonld), "microdata_types": microdata}
    # --- org numbers
    found = []
    for src_name, src in (("text", text), ("html", raw)):
        for m in ORGNR_RX.finditer(src):
            num = re.sub(r"\D", "", m.group("num"))
            if not mod11_ok(num): continue
            ctx_before = src[max(0, m.start() - 80):m.start()]
            ctx_after = src[m.end():m.end() + 40]
            found.append({"src": src_name, "orgnr": num, "match": m.group(0),
                          "format": ("NO " if m.group("pre") else "") + classify_format(m.group("num")) + (" MVA" if m.group("mva") else ""),
                          "kw": bool(ORG_KEYWORD_RX.search(ctx_before[-45:])) or bool(m.group("pre") or m.group("mva")),
                          "credit_ctx": bool(CREDIT_RX.search(ctx_before + ctx_after)),
                          "group_ctx": bool(GROUP_RX.search(ctx_before + ctx_after)),
                          "ctx": re.sub(r"\s+", " ", ctx_before[-60:] + "[[" + m.group(0) + "]]" + ctx_after)[:200]})
    res["orgnrs"] = found
    res["orgnr_text_set"] = sorted({f["orgnr"] for f in found if f["src"] == "text"})
    res["orgnr_html_only"] = sorted({f["orgnr"] for f in found if f["src"] == "html"} - set(res["orgnr_text_set"]))
    if target:
        trx = target_rx(target)
        tm = [m.group(0) for m in trx.finditer(text)]
        res["target_in_text"] = len(tm)
        res["target_in_html"] = len(trx.findall(raw))
        res["target_in_footer"] = bool(trx.search(footer_text))
        res["target_formats"] = sorted({classify_format(x) for x in tm})
        res["target_kw_ctx"] = any(f["kw"] for f in found if f["orgnr"] == target)
        res["other_orgnrs_text"] = sorted({f["orgnr"] for f in found if f["src"] == "text" and f["orgnr"] != target})
        res["other_orgnrs_kw"] = sorted({f["orgnr"] for f in found if f["kw"] and f["orgnr"] != target})
    # --- contact facts
    mailto, tel = [], []
    res["tel_anchors"] = sorted({(unquote(h.split(":", 1)[1]).strip(), t) for h, t in links
                                 if h.lower().startswith(("tel:", "callto:"))})
    for href, txt in links:
        if href.lower().startswith("mailto:"):
            mailto.append(unquote(href[7:].split("?")[0]).strip().lower())
        elif href.lower().startswith("tel:") or href.lower().startswith("callto:"):
            tel.append(unquote(href.split(":", 1)[1]).strip())
    res["mailto"] = sorted({m for m in mailto if EMAIL_RX.fullmatch(m) and not PLACEHOLDER_EMAIL_RX.search(m)})
    res["mailto_rejected"] = sorted({m for m in mailto if m not in res["mailto"]})
    res["tel_links"] = sorted(set(tel))
    res["emails_text"] = sorted({e.lower() for e in EMAIL_RX.findall(text) if not re.search(r"\.(png|jpe?g|gif|webp|svg)$", e, re.I)
                                 and not PLACEHOLDER_EMAIL_RX.search(e)})
    res["emails_obfuscated"] = sorted({f"{a}@{b}.{c}".lower() for a, b, c in EMAIL_OBF_RX.findall(text)} - set(res["emails_text"]))
    res["cf_emails"] = sorted(set(cf_emails))
    res["rtl_emails"] = sorted(set(rtl_emails))
    res["brreg_links"] = brreg_links(raw)
    phones = []
    for m in PHONE_RX.finditer(text):
        digits = re.sub(r"\D", "", m.group("num"))
        if len(digits) != 8: continue
        before = text[max(0, m.start() - 25):m.start()]
        # reject if part of an orgnr / date / price / postal code context
        if re.search(r"(org|kr\.?|nok|,-)", before[-8:], re.I): continue
        phones.append({"digits": digits, "match": m.group(0).strip(), "kw": bool(PHONE_KW_RX.search(before)),
                       "cc": bool(m.group("cc"))})
    res["phones_text"] = phones
    socials = {}
    allhrefs = " ".join(h for h, _ in links)
    for k, rx in SOCIAL_RX.items():
        vals = sorted({(m.group(0).rstrip("/")) for m in rx.finditer(allhrefs)})
        if vals: socials[k] = vals
    res["social"] = socials
    # --- copyright / footer
    cm = re.findall(r"(?:©|&copy;|\(c\)|copyright)\s*(?:\d{4}\s*[-–]?\s*)?(?:\d{4})?\s*([^|\n©]{2,80})", text, re.I)
    res["copyright"] = [re.sub(r"\s+", " ", c).strip()[:80] for c in cm[:3]]
    res["footer_excerpt"] = footer_text[:600]
    # --- parked / placeholder
    sig = []
    host = (urlsplit(url).hostname or "").lower()
    if parking_url(url): sig.append("parking_url")
    pm = PARKED_TEXT_RX.search(title + " \n " + text[:3000])
    if pm: sig.append("parked_text:" + pm.group(0)[:40])
    if len(text) < 200 and not res["jsonld_types"]: sig.append("tiny_text")
    if re.search(r"<frameset|<frame\s", raw, re.I): sig.append("frameset")
    if re.search(r'<meta[^>]+http-equiv=["\']?refresh', raw, re.I): sig.append("meta_refresh")
    if re.search(r'id=["\'](root|app|__next|__nuxt)["\']', raw) and len(text) < 500: sig.append("js_app_shell")
    bw = BOT_WALL_RX.search(title + " " + raw[:6000])
    if bw: sig.append("bot_wall:" + bw.group(0)[:30])
    cjk = len(CJK_RX.findall(text[:5000]))
    if cjk > 50: sig.append("cjk_text")
    res["placeholder_signals"] = sig
    # --- name match
    # prominent strings: where a site states who it is
    prominent = [title, metas.get("og:site_name", ""), metas.get("og:title", ""), metas.get("application-name", ""),
                 metas.get("copyright", ""), " ".join(res["copyright"]), footer_text[:1500]]
    for o in res["jsonld_orgs"]:
        for k in ("name", "legalName"):
            if isinstance(o.get(k), str): prominent.append(o[k])
    for n in tree.css("h1")[:2]:
        prominent.append(n.text(separator=" ", strip=True)[:200])
    for n in tree.css("img[alt]")[:30]:
        if re.search(r"logo", (n.attributes.get("class") or "") + (n.attributes.get("src") or "") + (n.attributes.get("alt") or ""), re.I):
            prominent.append(n.attributes.get("alt") or "")
    res["prominent_text"] = " | ".join(x for x in prominent if x)[:2500]
    if legal_name:
        core = name_core_tokens(legal_name)
        dist = [t for t in core if t not in GENERIC_TOKENS and not t.isdigit()]
        ph = res["prominent_text"].lower()
        res["name_distinct_tokens"] = dist
        res["name_prominent_hits"] = [t for t in (dist or core) if re.search(r"(?<![a-z0-9æøå])" + re.escape(t) + r"(?![a-z0-9æøå])", ph)
                                      or (len(t) >= 5 and re.search(re.escape(t), re.sub(r"[^a-z0-9æøå]", "", ph)))]
        res["name_prominent_match"] = round(len(res["name_prominent_hits"]) / len(dist or core), 2) if (dist or core) else None
        hay = (title + " " + " ".join(metas.values()) + " " + text).lower()
        hits = [t for t in core if re.search(r"(?<![a-z0-9æøå])" + re.escape(t) + r"(?![a-z0-9æøå])", hay)]
        res["name_tokens"] = core
        res["name_token_hits"] = hits
        res["name_match"] = round(len(hits) / len(core), 2) if core else None
        full = " ".join(core)
        res["name_full_phrase"] = bool(full and re.search(re.escape(full).replace(r"\ ", r"[\s\-&]+"), hay))
    res["generator"] = metas.get("generator") or next((g for g in ("wix.com", "squarespace", "wp-content", "shopify",
                        "webflow", "framer", "jimdo", "webnode", "weebly", "one.com", "mono.net", "hubspot", "cdn.sanity", "_next/static")
                        if g in raw[:200000].lower()), None)
    return res


BRREG_LINK_RX = re.compile(r"(?:brreg\.no|proff\.no|purehelp\.no|forvalt\.no)[^\"'\s>]*?(?<!\d)([89]\d{8})(?!\d)", re.I)

def brreg_links(html: str):
    """Org numbers that appear inside links to registry/lookup sites (strong self-identification signal)."""
    return sorted({m.group(1) for m in BRREG_LINK_RX.finditer(html) if mod11_ok(m.group(1))})

FOREIGN_CC_BEFORE_RX = re.compile(r"(?:\+|00)\s?(?!47)\d{1,3}\s?(?:\(0\)\s?)?[\s.\-]*$")
FAX_BEFORE_RX = re.compile(r"(?:tele)?fa(?:x|ks)\b[^0-9]{0,14}$", re.I)
FOREIGN_POSTCODE_RX = re.compile(r"(?<![\w-])(?:DK|SE|S|FI|SF|D|DE|UK|GB|PL|NL|IS|EE|LT|LV)-\s?\d{3,5}\b")


def block_phones(text: str, exclude: str = "") -> list[str]:
    """8-digit Norwegian numbers in a text block: not foreign-prefixed (+45 7020 3455 is Danish, never +47),
    not fax lines, not part of the org number."""
    out = set()
    for m in PHONE_RX.finditer(text or ""):
        d = re.sub(r"\D", "", m.group("num"))
        if len(d) != 8 or (exclude and d in exclude):
            continue
        if not m.group("cc") and FOREIGN_CC_BEFORE_RX.search(text[max(0, m.start() - 14):m.start()]):
            continue
        if FAX_BEFORE_RX.search(text[max(0, m.start() - 22):m.start()]):
            continue
        out.add(d)
    return sorted(out)


def _foreign_bits(txt: str, target: str | None, own_words: list[str] | None, own_foreign: bool = False) -> list[str]:
    """Signals that a text block also describes another legal entity: another valid org number, another legal
    entity name (when the target's legal name is known), a foreign registration number or a foreign postcode."""
    bits = []
    for m in ORGNR_RX.finditer(txt):
        d = re.sub(r"\D", "", m.group("num"))
        if mod11_ok(d) and d != target:
            bits.append("orgnr:" + d)
    if own_words:
        for e in legal_entities(txt, prefix_forms=True):
            if not is_target_entity(e, own_words, own_foreign):
                bits.append("entity:" + e["name"])
    if FOREIGN_REG_RX.search(txt):
        bits.append("foreign_registration")
    if FOREIGN_POSTCODE_RX.search(txt):
        bits.append("foreign_postcode")
    return bits


def orgnr_block(html: str, target: str, max_len: int = 700, legal_name: str | None = None):
    """Find the smallest DOM block containing the target orgnr, grow it while it stays < max_len chars and
    contains no OTHER valid orgnr, no other legal-entity name (when `legal_name` is given), no foreign registration
    number and no foreign postcode. Facts (emails/phones/address-ish lines) inside this block can be attributed
    to the target with high confidence even on multi-company pages."""
    tree = LexborHTMLParser(html)
    for sel in ("script", "style", "noscript", "template", "svg"):
        for n in tree.css(sel): n.decompose()
    trx = target_rx(target)
    own_key = entity_words(legal_name) if legal_name else None
    own_foreign = legal_form_is_foreign(legal_name) if legal_name else False
    best = None
    for node in (tree.body.traverse() if tree.body else []):
        t = node.text(separator=" ")
        if trx.search(t) and (best is None or len(t) < len(best.text(separator=" "))):
            best = node
    if best is None:
        return None
    block = best
    while block.parent is not None and block.parent.tag not in ("html", "body"):
        pt = block.parent.text(separator=" ")
        if len(pt) > max_len or _foreign_bits(pt, target, own_key, own_foreign):
            break
        block = block.parent
    btxt = re.sub(r"\s+", " ", block.text(separator=" ")).strip()
    bhtml = block.html or ""
    obf = set()
    for n in block.css("[data-cfemail]"):
        dec = decode_cfemail(n.attributes.get("data-cfemail") or "")
        if dec:
            obf.add(dec)
    for m in _CF_HREF_RX.finditer(bhtml):
        dec = decode_cfemail(m.group(1))
        if dec:
            obf.add(dec)
    for n in block.css("[style]"):
        if "direction:rtl" in (n.attributes.get("style") or "").replace(" ", "").lower():
            rev = (n.text(strip=True) or "")[::-1]
            if EMAIL_RX.fullmatch(rev):
                obf.add(rev.lower())
    anchors = [((a.attributes.get("href") or "").strip(), a.text(separator=" ", strip=True)) for a in block.css("a[href]")]
    hrefs = sorted({h for h, _ in anchors} - {""})
    emails = sorted({e.lower() for e in EMAIL_RX.findall(btxt)} | obf |
                    {unquote(m).lower() for m in re.findall(r'href=["\']mailto:([^"\'?]+)', bhtml, re.I)})
    post = re.findall(r"(?<!\d)(\d{4})\s+([A-ZÆØÅ][A-Za-zÆØÅæøå\-]+(?:\s[A-ZÆØÅ][A-Za-zÆØÅæøå\-]+)?)", btxt)
    bits = _foreign_bits(btxt, target, own_key, own_foreign)
    return {"block_tag": block.tag, "block_text": btxt[:max_len], "emails": [e for e in emails if not PLACEHOLDER_EMAIL_RX.search(e)],
            "phones": block_phones(btxt, target), "postcode_place": post[:3], "isolated": not bits,
            "foreign_bits": bits[:5],
            "links": [h for h in hrefs if not h.lower().startswith(("mailto:", "tel:", "javascript:", "#"))],
            "tel_links": sorted({unquote(h.split(":", 1)[1]).strip() for h in hrefs if h.lower().startswith(("tel:", "callto:"))}),
            "tel_anchors": sorted({(unquote(h.split(":", 1)[1]).strip(), t) for h, t in anchors
                                   if h.lower().startswith(("tel:", "callto:"))})}


# Social handles owned by site builders / themes / hosts: never attribute to the customer company
PLATFORM_SOCIAL_RX = re.compile(r"/(wix|wixcom|wix-com|squarespace|wordpress|wordpressdotcom|webflow|shopify|hubspot|elementor|"
    r"godaddy|onecom|one\.com|onecomgroup|domeneshop|webhuset|useformat|weebly|jimdo|webnode|mono\.?net|framer|"
    r"strikingly|sitejet|wpengine|automattic|yoast|cookiebot|trustpilot|google|youtube|facebook|instagram|"
    r"linkedin|x|twitter|tiktok|spond|spondapp|idrettsforbundet|klubbenonline|norid)(?:[/?#]|$)", re.I)

def social_handle(url: str) -> str:
    u = re.sub(r"https?://(?:[a-z0-9-]+\.)?", "", url.lower()).split("?")[0].rstrip("/")
    parts = u.split("/")[1:]
    parts = [p for p in parts if p not in ("company", "school", "showcase", "in", "channel", "c", "user", "p", "pg", "pages")]
    return re.sub(r"[^a-z0-9æøå]", "", (parts[0] if parts else "").lstrip("@"))

def social_matches_company(url: str, legal_name: str, site_domain: str) -> bool:
    """True if the social handle plausibly belongs to the company (name token or domain label inside handle, or vice versa)."""
    if PLATFORM_SOCIAL_RX.search(url): return False
    h = social_handle(url)
    if len(h) < 3: return False
    label = re.sub(r"[^a-z0-9]", "", (site_domain or "").split(".")[0])
    toks = [re.sub(r"[^a-z0-9æøå]", "", t) for t in name_core_tokens(legal_name)]
    toks = [t for t in toks if len(t) >= 4]
    tr = lambda x: x.replace("æ", "ae").replace("ø", "o").replace("å", "a")
    cands = {label} | set(toks) | {tr(t) for t in toks}
    hn = tr(h)
    return any(c and len(c) >= 4 and (c in hn or hn in c) for c in cands)


# --------------------------------------------------------------------------- crawl helpers
SUBPAGE_PATTERNS = [  # (kind, regex on href path + anchor text, base score)
    ("contact", re.compile(r"(?<![a-zæøå])(kontakt|contact|finn[- ]oss|find[- ]us|ta[- ]kontakt)", re.I), 10),
    ("about", re.compile(r"(?<![a-zæøå])(om[- _]?oss|about|hvem[- ]er[- ]vi|om[- ]firma|om[- ]selskapet|selskapet|bedriften)", re.I), 8),
    ("terms", re.compile(r"vilk[aå]r|betingelser|kj[oø]psvilk|salgsvilk|terms|conditions|handelsbeting", re.I), 6),
    ("impressum", re.compile(r"impressum|imprint|juridisk", re.I), 6),
    ("privacy", re.compile(r"personvern|privacy|gdpr", re.I), 5),
    ("careers", re.compile(r"(?<![a-zæøå])(karriere|career|jobb(?:e|er)?(?:[- ]hos)?|ledige[- ]stillinger|stillinger|vacanc|jobs)(?![a-z])", re.I), 4),
    ("news", re.compile(r"(?<![a-zæøå])(nyheter|news|aktuelt|presse|press|blogg?|artikler|nytt)(?![a-z])", re.I), 3),
    ("team", re.compile(r"(?<![a-zæøå])(ledelse|ledergruppe|management|team|ansatte|medarbeidere|styret|board|people)(?![a-z])", re.I), 3),
    ("locations", re.compile(r"(?<![a-zæøå])(avdelinger|kontorer|lokasjoner|locations|offices|butikker|stores|finn[- ]butikk)(?![a-z])", re.I), 3),
]
SKIP_EXT_RX = re.compile(r"\.(pdf|jpe?g|png|gif|webp|svg|zip|docx?|xlsx?|pptx?|mp4|mp3|ics)$", re.I)


def pick_subpages(html: str, base_url: str) -> dict[str, tuple[str, str]]:
    """Best same-site link per page kind: {kind: (url, anchor_text)}."""
    tree = LexborHTMLParser(html)
    base_host = (urlsplit(base_url).hostname or "").lower().removeprefix("www.")
    base_dom = reg_domain(base_host)
    best: dict[str, tuple[float, str, str]] = {}
    for a in tree.css("a[href]"):
        href = (a.attributes.get("href") or "").strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#", "callto:", "sms:")):
            continue
        u = urljoin(base_url, href).split("#")[0]
        sp = urlsplit(u)
        if sp.scheme not in ("http", "https"):
            continue
        h = (sp.hostname or "").lower().removeprefix("www.")
        if h != base_host and reg_domain(h) != base_dom:
            continue
        if SKIP_EXT_RX.search(sp.path) or sp.path in ("", "/"):
            continue
        txt = (a.text(separator=" ", strip=True) or "")[:60]
        hay = unquote(sp.path) + " " + txt
        for kind, rx, score in SUBPAGE_PATTERNS:
            if rx.search(hay):
                sc = score - sp.path.count("/") * 0.5 - (2 if sp.query else 0) - (1 if h != base_host else 0)
                if kind not in best or sc > best[kind][0]:
                    best[kind] = (sc, u, txt)
                break
    return {k: (v[1], v[2]) for k, v in best.items()}


META_REFRESH_RX = re.compile(r"<meta[^>]+http-equiv=[\"']?refresh[\"']?[^>]*content=[\"']?\s*\d+\s*;\s*url\s*=\s*['\"]?([^'\">\s]+)", re.I)
JS_LOC_RX = re.compile(r"(?:window\.|document\.|top\.|self\.)?location(?:\.href)?\s*=\s*['\"]([^'\"]+)['\"]|location\.replace\(\s*['\"]([^'\"]+)['\"]", re.I)
FRAME_RX = re.compile(r"<(?:frame|iframe)[^>]+src=[\"']?([^\"'>\s]+)", re.I)


def client_side_target(html: str, base: str) -> str | None:
    """Target of a meta-refresh / tiny-JS / frameset redirect, if the page is only a redirect shell."""
    small = len(html) < 6000
    m = META_REFRESH_RX.search(html[:20000])
    if m:
        return urljoin(base, m.group(1))
    if small:
        m = JS_LOC_RX.search(html)
        if m:
            return urljoin(base, m.group(1) or m.group(2))
    if re.search(r"<frameset", html, re.I) or (small and re.search(r"<iframe", html, re.I)):
        m = FRAME_RX.search(html)
        if m and not re.search(r"youtube|vimeo|google|facebook", m.group(1), re.I):
            return urljoin(base, m.group(1))
    return None


FREEMAIL = {"gmail.com", "hotmail.com", "hotmail.no", "online.no", "yahoo.com", "yahoo.no", "outlook.com", "live.no",
            "live.com", "icloud.com", "me.com", "msn.com", "frisurf.no", "broadpark.no", "start.no", "c2i.net",
            "getmail.no", "lyse.net", "altibox.no", "telia.no", "bluezone.no", "mail.com", "googlemail.com",
            "epost.no", "sensewave.com", "tele2.no", "chello.no", "nextgentel.com", "ebnett.no", "haugnett.no",
            "loqal.no", "netcom.no", "hotmail.co.uk", "aol.com", "protonmail.com", "proton.me", "outlook.no",
            "yahoo.co.uk", "gmx.com", "gmx.net", "mail.ru", "tdcspace.dk", "frisurf.net", "enivest.net",
            "tussa.com", "tdc.no", "monsternett.no", "nordnet.no", "kvinnherad.net", "ntebb.no", "portalen.no",
            "knett.no", "online.com", "hotmail.se", "live.se", "yahoo.se", "gmail.no", "icloud.no", "mac.com",
            "privat.no", "ymail.com", "rocketmail.com", "post.no", "gmx.de", "web.de", "t-online.de", "tiscali.co.uk",
            "kpnmail.nl", "ziggo.nl", "free.fr", "orange.fr", "wanadoo.fr", "hotmail.fr", "yahoo.fr", "libero.it",
            "tele.no", "nettpost.no", "skynet.be", "telenor.no", "telia.com", "dbmail.no", "fiber.no",
            "neasenett.no", "sfjbb.net", "vikenfiber.no", "bkkfiber.no", "eidsiva.net", "hlk.no", "eltele.no"}


def plain_text(html: str) -> str:
    """Visible text of a page (scripts/styles removed), whitespace collapsed to single spaces."""
    return _plain_text(html)


def _plain_text(html: str) -> str:
    t = LexborHTMLParser(html)
    for sel in ("script", "style", "noscript", "template", "svg"):
        for n in t.css(sel):
            n.decompose()
    body = t.body or t.root
    return re.sub(r"\s+", " ", body.text(separator=" ") if body else "").strip()


def corroborate(htmls: list[str], rec: dict, site_url: str) -> dict:
    """Registry corroboration signals between an Enhetsregisteret record and fetched site pages."""
    texts = [_plain_text(h) for h in htmls]
    raw = " ".join(htmls).lower()
    T = " ".join(texts)
    tl = T.lower()
    digits = re.sub(r"\D", "", T + " " + " ".join(re.findall(r'href=["\']tel:([^"\']+)', raw)))
    sig: dict = {}
    phones = [re.sub(r"\D", "", rec.get(k) or "")[-8:] for k in ("telefon", "mobil")]
    sig["phone_match"] = [p for p in phones if p and len(p) == 8 and p in digits]
    em = (rec.get("epostadresse") or "").lower().strip()
    sig["email_match"] = bool(em) and (em in tl or em in raw)
    dom = em.split("@")[-1] if "@" in em else ""
    site_dom = reg_domain(urlsplit(site_url).hostname or "")
    sig["email_domain_match"] = bool(dom) and dom not in FREEMAIL and (reg_domain(dom) == site_dom)
    addr_hits = []
    for k in ("forretningsadresse", "postadresse"):
        a = rec.get(k) or {}
        pn = a.get("postnummer")
        lines = a.get("adresse") or []
        if not pn or not lines:
            continue
        street = re.findall(r"[a-zæøåäöü][a-zæøåäöü\.\-]{3,}", (lines[0] or "").lower())
        for m in re.finditer(r"(?<!\d)" + re.escape(pn) + r"(?!\d)", T):
            win = tl[max(0, m.start() - 120): m.end() + 60]
            if street and street[0] in win and not re.match(r"(postboks|pb|boks)$", street[0]):
                addr_hits.append(f"{k}:{pn}+{street[0]}")
                break
    sig["address_match"] = addr_hits
    name = re.sub(r"\s+", " ", (rec.get("navn") or "").lower()).strip()
    sig["legal_name_full"] = bool(name) and bool(re.search(
        r"(?<![a-z0-9æøå])" + re.escape(name).replace(r"\ ", r"[\s\-]+") + r"(?![a-z0-9æøå])", tl))
    sig["n_corroborations"] = sum([bool(sig["phone_match"]), bool(sig["email_match"] or sig["email_domain_match"]),
                                   bool(addr_hits), sig["legal_name_full"]])
    return sig


# --------------------------------------------------------------------------- legal entities named on a page
# Legal-form tokens (Norwegian and foreign). Matched case-sensitively so English prose ("recommend Yabimo as a
# solid supplier") never reads as the legal name "YABIMO AS".
NO_FORMS = ("AS", "ASA", "ANS", "DA", "SA", "BA", "KS", "NUF", "IKS", "KF", "ENK", "SE", "BRL")
_FORMS_RX = (r"AS|ASA|ANS|DA|SA|BA|KS|NUF|IKS|KF|ENK|BRL|AB|ApS|APS|A/S|EHF|ehf\.?|Oy|Oyj|OY|GmbH|GMBH|AG|"
             r"Ltd\.?|LTD\.?|Limited|LIMITED|LLC|Inc\.?|INC\.?|B\.V\.|BV|N\.V\.|NV|S\.r\.l\.|Srl|SRL|S\.p\.A\.|SpA|"
             r"S\.A\.|SAS|SARL|PLC|plc|LLP|Pty\.? Ltd\.?|Pvt\.? Ltd\.?|Private Limited|Private Ltd\.?|"
             r"Sp\. z o\.o\.|sp\. z o\.o\.|SP\. Z O\.O\.|S\.L\.|s\.r\.o\.|Kft\.?|AE|SIA|UAB|OÜ")
_WORD = r"[A-ZÆØÅÄÖÜÉ0-9][\wÆØÅæøåÄÖÜäöüÉé&'’.\-!]*"
ENTITY_RX = re.compile(
    r"(?<![\wÆØÅæøå])(?P<name>" + _WORD + r"(?:(?:\s+(?:og|and|&|of|for|i)\s+|\s+|\s*&\s*|\s*\(\w+\)\s*)" + _WORD +
    r"){0,5})\s*,?\s+(?P<form>" + _FORMS_RX + r")(?![\wÆØÅæøå])")
FOREIGN_FORMS_RX = re.compile(r"^(?!(?:AS|ASA|ANS|DA|SA|BA|KS|NUF|IKS|KF|ENK|BRL)$)")
FOREIGN_REG_RX = re.compile(
    r"(registered in (?:england|scotland|wales|ireland|england (?:and|&) wales)\b[^.|]{0,40}|"
    r"company (?:registration )?(?:no|number|reg\.?(?: no)?)\.?\s*:?\s*\d{6,8}\b|"
    r"organisationsnummer\s*:?\s*\d{6}-\d{4}|org\.?\s*nr\.?\s*:?\s*\d{6}-\d{4}|"
    r"\bcvr(?:-nr\.?|\s*nr\.?|-nummer)?\s*:?\s*\d{8}\b|\bkrs\s*:?\s*\d{10}\b|\bnip\s*:?\s*\d{3}-?\d{3}-?\d{2}-?\d{2}\b|"
    r"\by-tunnus\b|\bhrb\s*\d{3,}|\bhandelsregister\b|\bkvk\b(?:-nummer)?\s*:?\s*\d{8}|\bsire[nt]\s*:?\s*\d{9}|"
    r"\babn\s*:?\s*\d{2}\s?\d{3}|\bvat\s*(?:reg\.?\s*)?(?:no|number)?\.?\s*:?\s*(?:gb|de|se|dk|pl|fi|nl|fr|it|es|ie|be|at)\s?\d{6,})",
    re.I)
_FORM_WORDS = {"as", "asa", "ans", "da", "sa", "ba", "ks", "nuf", "iks", "kf", "enk", "brl", "ab", "aps", "a/s", "ehf",
               "oy", "oyj", "gmbh", "ag", "ltd", "limited", "llc", "inc", "bv", "nv", "srl", "spa", "sas", "sarl", "plc",
               "llp", "pty", "pvt", "private", "sp", "z", "o", "oo", "sl", "sro", "kft", "ae", "sia", "uab", "ou"}
_KEY_FOLD = str.maketrans({"æ": "ae", "ø": "o", "å": "a", "ä": "a", "ö": "o", "ü": "u", "é": "e", "è": "e"})


def entity_words(name: str) -> list[str]:
    """Name tokens of a legal name: case/diacritics/punctuation and legal-form words removed, '&' = 'og'."""
    n = unicodedata.normalize("NFKC", name or "").lower().translate(_KEY_FOLD).replace("&", " og ").replace("+", " og ")
    n = re.sub(r"\b(?:sp\.?\s*z\s*o\.?\s*o\.?|a/s|s\.r\.l\.|s\.p\.a\.|b\.v\.|n\.v\.|s\.a\.|pty\.?\s*ltd\.?|pvt\.?\s*ltd\.?)",
               " ", n)
    toks = []
    for t in re.findall(r"[a-z0-9]+", n):
        m = re.fullmatch(r"((?:19|20)\d\d)([a-z]{3,}.*)", t)       # "2026Noma" (copyright year glued to the name)
        toks.extend([m.group(1), m.group(2)] if m else [t])
    return [t for t in toks if t not in _FORM_WORDS and t not in ("og", "and")]


def entity_key(name: str) -> str:
    """Comparable key of a legal name (see entity_words). Country words are kept: 'YABIMO Norge AS' and
    'YABIMO AS' are different legal entities."""
    return "".join(entity_words(name))


def same_entity(key: str, own_key: str, foreign: bool | None = None, own_foreign: bool | None = None) -> bool:
    """Entity mention `key` names the entity `own_key` (a leading label such as a country or heading may have
    been captured: 'Norway Hydreco Hydraulics Norway AS'). A foreign legal form never names a Norwegian-form
    entity: 'YABIMO Sp. z o.o.' is not 'YABIMO AS'."""
    if not key or not own_key:
        return False
    if foreign is not None and own_foreign is not None and foreign != own_foreign:
        return False
    return key == own_key or (len(own_key) >= 5 and key.endswith(own_key))


def is_target_entity(e: dict, own_words: list[str], own_foreign: bool = False) -> bool:
    """Does entity mention `e` (from legal_entities) name the target? Same name words, possibly preceded by
    captured junk ('Registrer CV Oslo Stavanger Fabi AS', '2026 Keel AS', 'Norway Hydreco Hydraulics Norway AS');
    a foreign legal form never names a Norwegian-form company."""
    if not own_words or e.get("foreign", False) != own_foreign:
        return False
    w = e.get("words") or entity_words(e.get("name", ""))
    if w == own_words or "".join(w) == "".join(own_words):
        return True
    if len(w) > len(own_words) and w[-len(own_words):] == own_words:
        return True
    # middle initials ("Gunnar T. Høvik AS" for GUNNAR HØVIK AS)
    w2 = [t for t in w if len(t) > 1]
    o2 = [t for t in own_words if len(t) > 1]
    if o2 and (w2 == o2 or (len(w2) > len(o2) and w2[-len(o2):] == o2)):
        return True
    # one-letter typos in long words ("Rjuken Hotell AS" for RJUKAN HOTELL AS)
    return len(w2) == len(o2) > 0 and all(a == b or (len(b) >= 5 and _edit1(a, b)) for a, b in zip(w2, o2))


def _edit1(a: str, b: str) -> bool:
    """Levenshtein distance <= 1."""
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) <= 1
    if len(a) > len(b):
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    return a[i:] == b[i + 1:]


def legal_form_is_foreign(legal_name: str) -> bool:
    words = re.findall(r"[\wÆØÅæøå/.]+", legal_name or "")
    return not (words and words[-1].upper().rstrip(".") in NO_FORMS)


_LABEL_WORDS = (r"(?:Org|ORG|Tlf|TLF|Tel|Telefon|E-post|Epost|Email|E-mail|Adresse|Postboks|Besøk|Mva|MVA|Foretak|"
                r"Kontakt|Faks|Fax)\b")
_LABEL_NAME_WORDS = {"faktura", "fakturaadresse", "fakturering", "fakturamottak", "send", "sendes", "via", "som",
                     "kun", "eller", "motta", "mottak", "format", "adresse", "epost", "post", "elektronisk", "vi",
                     "onsker", "invoice", "invoices", "billing", "please", "send", "to", "kontakt", "info"}
PREFIX_ENTITY_RX = re.compile(r"(?<![\wÆØÅæøå])(?P<form>Stiftelsen|STIFTELSEN|Sameiet|SAMEIET|Borettslaget|BORETTSLAGET|"
                              r"Andelslaget|ANDELSLAGET)\s+(?P<name>(?!" + _LABEL_WORDS + r")[A-ZÆØÅ][\wÆØÅæøå&'’\-]*"
                              r"(?:\s+(?!" + _LABEL_WORDS + r")[A-ZÆØÅ][\wÆØÅæøå&'’\-]*){0,4})")


def legal_entities(text: str, prefix_forms: bool = False) -> list[dict]:
    """Legal entities named in text: [{"name","form","key","span","foreign"}] (name + legal-form token).
    With `prefix_forms`, also Norwegian prefix-named entities ("Stiftelsen ReturGass", "Sameiet Solsiden")."""
    out = []
    if prefix_forms:
        for m in PREFIX_ENTITY_RX.finditer(text or ""):
            if re.match(r"\s+(?:av|for|i|og|på|til|ved|in|of|for|and)\s", (text or "")[m.end():m.end() + 6]):
                continue                                   # "Stiftelsen Kritikk av kunst ...": name continues
            nm = f"{m.group('form')} {m.group('name')}".strip()
            out.append({"name": nm, "form": m.group("form"), "key": entity_key(nm), "words": entity_words(nm),
                        "foreign": False,
                        "start": m.start(), "end": m.end(),
                        "span": re.sub(r"\s+", " ", text[max(0, m.start() - 40):m.end() + 60]).strip()})
    for m in ENTITY_RX.finditer(text or ""):
        raw_before = (text or "")[max(0, m.start() - 40):m.start()]
        before = raw_before[max(raw_before.rfind(c) for c in "©|.\n") + 1:]      # same phrase only
        if CREDIT_RX.search(before + " " + m.group(0)) or re.search(r"\btheme|\bplugin|\btemplate", before + m.group(0), re.I):
            continue                                       # web agency / theme credit, not the site owner
        name = m.group("name").strip(" ,")
        # drop leading generic words that start a sentence/label ("Kontakt", "Copyright", "Org")
        name = re.sub(r"^(?:(?:Copyright|Kontakt|Contact|Om|About|Levert av|Faktura|Org\.?(?:nr)?\.?|Tlf\.?|"
                      r"E-post|Email|Adresse|Address|Velkommen til|Welcome to)\s+)+", "", name)
        key = entity_key(name)
        if not key or len(key) < 2 or not re.search(r"[a-z]", key):
            continue
        if set(entity_words(name)) <= _LABEL_NAME_WORDS or (m.group("form").lower().startswith("ehf") and
                                                            re.search(r"faktur|invoice|send", name, re.I)):
            continue                                       # "Fakturaadresse EHF", "Send faktura som EHF"
        if re.search(r"(?:\bv/|\btidl\.?|\btidligere|\bformerly|\bfør|\bav|\bby|\bfra)\s*[:(]?\s*$", raw_before, re.I) or \
                re.search(r"\bc/o\b[^.|\n]{0,30}$", raw_before, re.I):
            continue                                       # c/o address, former name or "løsninger av X AS" credit
        form = m.group("form")
        out.append({"name": f"{name} {form}", "form": form, "key": key, "words": entity_words(name),
                    "start": m.start(), "end": m.end(),
                    "foreign": form.upper().rstrip(".") not in NO_FORMS,
                    "span": re.sub(r"\s+", " ", text[max(0, m.start() - 40):m.end() + 60]).strip()})
    return out


def foreign_registrations(text: str) -> list[str]:
    return sorted({re.sub(r"\s+", " ", m.group(0)).strip()[:80] for m in FOREIGN_REG_RX.finditer(text or "")})


def legal_name_rx(legal_name: str) -> re.Pattern | None:
    """Regex for the exact registered name: name words case-insensitive with flexible separators, the
    legal-form token case-sensitive ('AS', not 'as'), not followed by an English function word."""
    words = re.findall(r"[\wÆØÅæøåÄÖÜäöüÉé]+", unicodedata.normalize("NFKC", legal_name or ""))
    if not words:
        return None
    form = words[-1] if words[-1].upper() in NO_FORMS and words[-1].isupper() else None
    body = words[:-1] if form else words
    if not body:
        return None
    sep = r"[\s.,\-–&]*"
    parts = [r"(?i:" + re.escape(w) + r")" for w in body]
    rx = r"(?<![\wÆØÅæøå])" + sep.join(parts)
    if form:
        rx += r"[\s.,]+" + re.escape(form) + r"(?![\wÆØÅæøå])(?!\s+(?:a|an|the|well|of|to|in|for|and|or|with|we|is)\b)"
    else:
        rx += r"(?![\wÆØÅæøå])"
    return re.compile(rx)


def name_block(html: str, legal_name: str, target: str | None = None, max_len: int = 500):
    """Smallest DOM block naming the exact legal name, grown while it stays <= max_len chars and names no other
    legal entity and no other org number (the legal-name analogue of `orgnr_block`, for pages that list several
    group companies). Returns orgnr_block-shaped dict or None."""
    nrx = legal_name_rx(legal_name)
    if nrx is None:
        return None
    own_words = entity_words(legal_name)
    own_foreign = legal_form_is_foreign(legal_name)
    tree = LexborHTMLParser(html)
    for sel in ("script", "style", "noscript", "template", "svg"):
        for n in tree.css(sel):
            n.decompose()
    best = None
    best_len = None
    for node in (tree.body.traverse() if tree.body else []):
        t = node.text(separator=" ")
        if len(t) < 4000 and nrx.search(t) and (best is None or len(t) < best_len):
            best, best_len = node, len(t)
    if best is None:
        return None

    def foreign_bits(txt: str) -> bool:
        if any(not is_target_entity(e, own_words, own_foreign) for e in legal_entities(txt)):
            return True
        for m in ORGNR_RX.finditer(txt):
            d = re.sub(r"\D", "", m.group("num"))
            if mod11_ok(d) and d != target:
                return True
        return False

    block = best
    while block.parent is not None and block.parent.tag not in ("html", "body"):
        pt = block.parent.text(separator=" ")
        if len(pt) > max_len or foreign_bits(pt):
            break
        block = block.parent
    btxt = re.sub(r"\s+", " ", block.text(separator=" ")).strip()
    bhtml = block.html or ""
    obf = set()
    for n in block.css("[data-cfemail]"):
        dec = decode_cfemail(n.attributes.get("data-cfemail") or "")
        if dec:
            obf.add(dec)
    for m in _CF_HREF_RX.finditer(bhtml):
        dec = decode_cfemail(m.group(1))
        if dec:
            obf.add(dec)
    hrefs = sorted({(a.attributes.get("href") or "").strip() for a in block.css("a[href]")} - {""})
    emails = sorted({e.lower() for e in EMAIL_RX.findall(btxt)} | obf |
                    {unquote(m).lower() for m in re.findall(r'href=["\']mailto:([^"\'?]+)', bhtml, re.I)})
    phones = block_phones(btxt, target or "")
    return {"block_tag": block.tag, "block_text": btxt[:max_len], "emails": [e for e in emails if not PLACEHOLDER_EMAIL_RX.search(e)],
            "phones": phones, "isolated": not foreign_bits(btxt), "kind": "legal_name",
            "links": [h for h in hrefs if not h.lower().startswith(("mailto:", "tel:", "javascript:", "#"))],
            "tel_links": sorted({unquote(h.split(":", 1)[1]).strip() for h in hrefs if h.lower().startswith(("tel:", "callto:"))})}
