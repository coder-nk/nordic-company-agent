"""Registry corroboration signals between a Brreg record and fetched site pages."""
import re, sys, pathlib, json
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from web_extract import extract_page, reg_domain, EMAIL_RX
from selectolax.lexbor import LexborHTMLParser

FREEMAIL = {"gmail.com", "hotmail.com", "hotmail.no", "online.no", "yahoo.com", "yahoo.no", "outlook.com", "live.no", "live.com",
            "icloud.com", "me.com", "msn.com", "frisurf.no", "broadpark.no", "start.no", "c2i.net", "getmail.no", "lyse.net",
            "altibox.no", "telia.no", "bluezone.no", "mail.com", "googlemail.com", "epost.no", "outlook.no", "ntebb.no",
            "portalen.no", "knett.no", "tele2.no", "nextgentel.com", "ebnett.no", "haugnett.no", "enivest.net", "tussa.com"}

def _text(html):
    t = LexborHTMLParser(html)
    for s in ("script", "style", "noscript", "template", "svg"):
        for n in t.css(s): n.decompose()
    body = t.body or t.root
    return re.sub(r"\s+", " ", body.text(separator=" ") if body else "").strip()

def corroborate(htmls, rec, site_url):
    texts = [_text(h) for h in htmls]
    raw = " ".join(htmls).lower()
    T = " ".join(texts)
    tl = T.lower()
    digits = re.sub(r"\D", "", T + " " + " ".join(re.findall(r'href=["\']tel:([^"\']+)', raw)))
    sig = {}
    phones = [re.sub(r"\D", "", rec.get(k) or "")[-8:] for k in ("telefon", "mobil")]
    sig["phone_match"] = any(p and len(p) == 8 and p in digits for p in phones)
    em = (rec.get("epostadresse") or "").lower().strip()
    sig["email_match"] = bool(em) and (em in tl or em in raw)
    dom = em.split("@")[-1] if "@" in em else ""
    site_dom = reg_domain(re.sub(r"^https?://", "", site_url).split("/")[0])
    sig["email_domain_match"] = bool(dom) and dom not in FREEMAIL and (dom == site_dom or dom.endswith("." + site_dom))
    addr_hits = []
    for k in ("forretningsadresse", "postadresse"):
        a = rec.get(k) or {}
        pn = a.get("postnummer")
        lines = a.get("adresse") or []
        if not pn or not lines: continue
        street = re.findall(r"[a-zæøåäöü][a-zæøåäöü\.\-]{3,}", (lines[0] or "").lower())
        for m in re.finditer(r"(?<!\d)" + pn + r"(?!\d)", T):
            win = tl[max(0, m.start() - 120): m.end() + 60]
            if street and street[0] in win:
                addr_hits.append(f"{k}:{pn}+{street[0]}")
                break
    sig["address_match"] = addr_hits
    name = re.sub(r"\s+", " ", (rec.get("navn") or "").lower()).strip()
    sig["legal_name_full"] = bool(name) and bool(re.search(r"(?<![a-z0-9æøå])" + re.escape(name).replace(r"\ ", r"[\s\-]+") + r"(?![a-z0-9æøå])", tl))
    sig["n_corroborations"] = sum([sig["phone_match"], sig["email_match"] or sig["email_domain_match"], bool(addr_hits), sig["legal_name_full"]])
    return sig
