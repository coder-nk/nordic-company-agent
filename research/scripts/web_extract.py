"""Prototype deterministic extractor for company websites (research version).

extract_page(html, url, target_orgnr, legal_name) -> dict of signals/facts.
All regexes here are candidates for the production parser.
"""
import json, re, unicodedata
from urllib.parse import urlsplit, urljoin, unquote
from selectolax.lexbor import LexborHTMLParser

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
    "facebook": re.compile(r"https?://(?:www\.|m\.|web\.|business\.|[a-z]{2}-[a-z]{2}\.)?(?:facebook|fb)\.com/(?!sharer|share|dialog|plugins|tr\b|tr\?|l\.php|policy|privacy|help|login|events/|groups/|ads\b|business\b|watch\b|hashtag|photo|story\.php|permalink)((?:profile\.php\?id=\d+)|(?:(?:p|pg|pages)/[^?#\s\"']+)|[A-Za-z0-9.\-_%]+)", re.I),
    "instagram": re.compile(r"https?://(?:www\.)?instagram\.com/(?!p/|reel/|explore/|accounts/|share)([A-Za-z0-9._]+)", re.I),
    "twitter": re.compile(r"https?://(?:www\.)?(?:twitter|x)\.com/(?!share|intent|home|search|hashtag|i/|en\b|no\b|nb\b|login|signup|tos|privacy)([A-Za-z0-9_]{1,15})(?![\w])", re.I),
    "youtube": re.compile(r"https?://(?:www\.|m\.)?youtube\.com/((?:channel|c|user)/[^/?#\s\"']+|@[^/?#\s\"']+)", re.I),
    "tiktok": re.compile(r"https?://(?:www\.)?tiktok\.com/(@[A-Za-z0-9._]+)", re.I),
}
PARKING_HOSTS = ("sedo.", "sedoparking", "parkingcrew", "bodis.", "dan.com", "afternic", "hugedomains", "undeveloped.",
                 "domainmarket", "parked", "parkering", "above.com", "uniregistry", "sav.com", "namebright",
                 "domenesalg", "domainnameshop", "domeneshop.no/parking", "one.com/no/parked", "godaddysites",
                 "webhuset.no/parkering", "/parking", "domainlore", "brandbucket", "squadhelp", "atom.com")
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
    if len(n) != 9 or not n.isdigit(): return False
    w = [3, 2, 7, 6, 5, 4, 3, 2]
    s = sum(int(a) * b for a, b in zip(n[:8], w))
    r = 11 - s % 11
    r = 0 if r == 11 else r
    return r != 10 and r == int(n[8])

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
    host = (host or "").lower().removeprefix("www.")
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "net", "ac", "gov") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])

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
        h = n.attributes.get("data-cfemail") or ""
        try:
            k = int(h[:2], 16)
            cf_emails.append("".join(chr(int(h[i:i + 2], 16) ^ k) for i in range(2, len(h), 2)))
        except Exception:
            pass
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
    if any(p in url.lower() for p in PARKING_HOSTS): sig.append("parking_url")
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

def orgnr_block(html: str, target: str, max_len: int = 700):
    """Find the smallest DOM block containing the target orgnr, grow it while it stays < max_len chars and
    contains no OTHER valid orgnr. Facts (emails/phones/address-ish lines) inside this block can be attributed
    to the target with high confidence even on multi-company pages."""
    tree = LexborHTMLParser(html)
    for sel in ("script", "style", "noscript", "template", "svg"):
        for n in tree.css(sel): n.decompose()
    trx = target_rx(target)
    best = None
    for node in (tree.body.traverse() if tree.body else []):
        t = node.text(separator=" ")
        if trx.search(t) and (best is None or len(t) < len(best.text(separator=" "))):
            best = node
    if best is None:
        return None
    def other_orgnrs(txt):
        return {re.sub(r"\D", "", m.group("num")) for m in ORGNR_RX.finditer(txt)
                if mod11_ok(re.sub(r"\D", "", m.group("num"))) and re.sub(r"\D", "", m.group("num")) != target}
    block = best
    while block.parent is not None and block.parent.tag not in ("html", "body"):
        pt = block.parent.text(separator=" ")
        if len(pt) > max_len or other_orgnrs(pt):
            break
        block = block.parent
    btxt = re.sub(r"\s+", " ", block.text(separator=" ")).strip()
    bhtml = block.html or ""
    emails = sorted({e.lower() for e in EMAIL_RX.findall(btxt)} |
                    {unquote(m).lower() for m in re.findall(r'href=["\']mailto:([^"\'?]+)', bhtml, re.I)})
    phones = sorted({re.sub(r"\D", "", m.group("num")) for m in PHONE_RX.finditer(btxt)
                     if len(re.sub(r"\D", "", m.group("num"))) == 8 and re.sub(r"\D", "", m.group("num")) not in target})
    post = re.findall(r"(?<!\d)(\d{4})\s+([A-ZÆØÅ][A-Za-zÆØÅæøå\-]+(?:\s[A-ZÆØÅ][A-Za-zÆØÅæøå\-]+)?)", btxt)
    return {"block_tag": block.tag, "block_text": btxt[:max_len], "emails": [e for e in emails if not PLACEHOLDER_EMAIL_RX.search(e)],
            "phones": phones, "postcode_place": post[:3], "isolated": not other_orgnrs(btxt)}


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
