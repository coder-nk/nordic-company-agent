"""Website identity gate: do these fetched pages belong to *this exact* registry entity?

Pure functions over fetched pages and the Enhetsregisteret record (no I/O, deterministic):

  analyse_page(page, target, name)  -> PageInfo   per-page signals: org-number hits with verbatim spans,
                                                  other org numbers, isolated org-number block, meta, JSON-LD
  verify(pages, rec, target, ...)   -> Verdict    L1 | L2 | ambiguous | not_available | blocked
  collect_facts(verdict, rec)       -> [Fact]     publishable facts (page, locator, verbatim span) for L1/L2

Rules (DESIGN.md "Website identity gate", research/company_websites.md §3-§10, plus fixes found on live data):
  blocked        home 401/403/429 or a bot-wall challenge page.
  not_available  home dead (HTTP >= 400, non-HTML), parked/for-sale/suspended/placeholder, hijacked (CJK/spam).
  L1  the target org number is printed on a live page of the site (visible text, raw HTML such as a JSON-LD
      taxID, or a link to its registry page). "Restricted" attribution applies when the site also shows other
      org numbers, the host is shared with other organisation numbers (discovery cache / registry index) or the
      site is a sub-path of someone else's host: contact facts then come only from the smallest DOM block holding
      the target number and no other (extract.orgnr_block) or equal the registry record, and descriptive facts
      (title, description, brand, logo, socials, careers, news) need the full name in title/og:site_name/JSON-LD.
      A non-registry lead on such a site must carry the company name in those fields, else `ambiguous`.
  L2  registry-filed `hjemmeside`, exclusive non-sub-path domain, no other org number anywhere, every distinctive
      name token prominent, >= 1 registry corroboration (phone, e-mail, e-mail domain, postcode+street, full legal
      name), not an address-named SPV, not a c/o manager's site; cross-domain redirects need the name in strong
      fields; a partially matching name (parent/brand site) needs two corroborations or the full legal name; a
      different legal entity named as owner (copyright / JSON-LD legalName) refuses L2 unless the registry match
      is overwhelming, and then only registry-equal contacts are published.
  Facts: generic role mailboxes or the registry e-mail (never fornavn.etternavn@, never invoice-scanning
  addresses); phones normalised to E.164, staff lines (named person / personal mailbox next to the number,
  directory pages, non-registry mobiles) skipped; socials whose handle is dominated by the company name or the
  exclusive domain label (platform, sponsor, place-name and brand+foreign-country handles rejected).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable
from urllib.parse import unquote, urljoin, urlsplit

import phonenumbers
from selectolax.lexbor import LexborHTMLParser

from ..orgnr import is_valid
from . import extract as X

GATE_VERSION = "website_gate@1"
CONFIDENCE = {"L1": 0.97, "L2": 0.85}

# --------------------------------------------------------------------------- regexes / vocabularies
# Weak placeholder phrases: only decisive in the <title> or on near-empty pages (research §3 lesson 1).
WEAK_PARKED_RX = re.compile(r"under construction|under oppbygging|siden er under|kommer snart|coming soon|launching soon|"
                            r"under maintenance|maintenance mode|midlertidig stengt|new page 1|\(offline\)", re.I)
SPAM_RX = re.compile(r"\b(?:slot gacor|judi (?:online|bola)|togel|situs slot|slot online|casino online|poker online|"
                     r"sbobet|agen slot|bandar ?q|viagra|cialis|replica watches|escort service)\b", re.I)
ADDRESS_NAME_RX = re.compile(r"^[A-ZÆØÅÉ][A-ZÆØÅÉ\-\. ']+ \d+[A-Z]?(?:\s?-\s?\d+)?(?: [A-Z])?"
                             r"(?: (?:AS|ASA|BRL|SA|ANS|DA|ESEK|SAMEIE|BORETTSLAG|EIENDOM AS))?$")
ORGFORM_WORD_RX = re.compile(r"(?<![\w])(AS|ASA|ANS|DA|SA|BA|KS|NUF|IKS|KF|AB|GmbH|Ltd|Oy|ApS|A/S|Inc|LLC|BV|AG)(?![\w/])")
COMPANY_WORD_RX = re.compile(r"\b(as|asa|ans|da|sa|ba|ks|nuf|iks|kf|&\s*co|advokat\w*|regnskap\w*|revisjon|eiendom\w*|"
                             r"forvaltning|management|holding|gruppen|group|boligbyggelag|bbl|obos|usbl|partners?|"
                             r"kontor\w*|selskap\w*|consult\w*)\b", re.I)
HOMEISH_PATH_RX = re.compile(r"^/(?:(?:no|nb|nn|en|se|sv|da|de|fi|nb-no|no-nb|nn-no|en-gb|en-us|nor|norsk|english)/?)?"
                             r"(?:(?:index|default|home|hjem|forside|framside|start|startside|main|velkommen|welcome)"
                             r"(?:\.[a-z]{2,5})?/?)?$", re.I)
USELESS_TITLES = {"hjem", "home", "forside", "framside", "startside", "start", "velkommen", "welcome", "new page 1",
                  "my wordpress site", "untitled", "index", "default", "hjemmeside", "homepage", "main", "page"}
# Injected SEO spam on compromised sites ("Our Partner casaapostas.org") must never become a company description.
SPAM_DESC_RX = re.compile(r"(?:casino|apostas|bet(?:ting)?\b|slot|poker|gambl|kasino|viagra|cialis|porn|escort|"
                          r"payday|loan offer|bitcoin|forex|our partner|sponsored by|backlink)", re.I)
DOMAIN_TOKEN_RX = re.compile(r"\b((?:[a-z0-9-]+\.)+(?:com|net|org|no|info|biz|io|xyz|top|site|online|club|bet))\b", re.I)


def _spammy(text: str, site_domain: str | None) -> bool:
    if SPAM_RX.search(text) or SPAM_DESC_RX.search(text):
        return True
    for m in DOMAIN_TOKEN_RX.finditer(text):
        if X.reg_domain(m.group(1)) != (site_domain or ""):
            return True
    return False


PLACEHOLDER_DESC_RX = re.compile(r"just another wordpress site|lorem ipsum|^description$|^beskrivelse$|my wordpress blog|"
                                 r"site description|^test$|^home$", re.I)
CAREERS_RX = re.compile(r"(?<![a-zæøå])(karriere|careers?|jobb(?:e|er)?(?:[- ]hos(?:[- ]oss)?)?|ledige[- ]stillinger|"
                        r"stillinger|vacanc(?:y|ies)|jobs|join[- ]us|work[- ]with[- ]us)(?![a-zæøå])", re.I)
FEED_TYPES = ("application/rss+xml", "application/atom+xml")

ROLE_LOCALS = {
    "post", "postmottak", "postkasse", "info", "informasjon", "kontakt", "kontaktoss", "contact", "contactus",
    "firmapost", "firma", "mail", "epost", "hello", "hei", "hallo", "salg", "sales", "ordre", "order", "orders",
    "bestilling", "bestill", "booking", "bookings", "reservasjon", "reservasjoner", "reservations", "service",
    "kundeservice", "customerservice", "kunde", "kunder", "kundesenter", "support", "hjelp", "help", "office",
    "kontor", "sentralbord", "resepsjon", "reception", "admin", "administrasjon", "marked", "marketing", "presse",
    "press", "media", "jobb", "jobs", "job", "karriere", "career", "careers", "rekruttering", "recruitment", "hr",
    "butikk", "shop", "nettbutikk", "webshop", "verksted", "drift", "teknisk", "tips", "redaksjon", "innkjop",
    "purchasing", "lager", "styret", "styreleder", "leder", "sekretariat", "sekretaer", "klubb", "club", "utleie",
    "rental", "export", "logistikk", "tilbud", "quote", "enquiries", "inquiries", "general", "mottak",
    "ekspedisjon", "avdeling", "hovedkontor", "felles", "bedrift", "team", "medlem", "medlemskap", "billett",
    "billetter", "tickets", "event", "arrangement", "kurs", "elev", "skole", "barnehage", "legekontor", "time",
    "timebestilling", "akutt", "vakt", "kantine", "restaurant", "bord", "cafe", "kafe", "hotell", "hotel",
    "frontdesk",
}
INVOICE_LOCALS = {"faktura", "fakturamottak", "fakturaer", "invoice", "invoices", "regning", "regninger", "bilag",
                  "ehf", "fakturering", "billing", "inngaaende", "inngaende", "scan", "skann", "kreditor",
                  "noreply", "no-reply", "donotreply", "do-not-reply", "postmaster", "webmaster", "abuse", "privacy",
                  "personvern", "gdpr", "dpo"}

_FOLD = str.maketrans({"æ": "ae", "ø": "o", "å": "a", "ä": "a", "ö": "o", "ü": "u", "é": "e", "è": "e"})


def fold(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "").lower().translate(_FOLD)


def squash(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", fold(s))


_ZW_RX = re.compile(r"[\u200b-\u200f\u2060\ufeff\u00ad]")


def _ws(s: str) -> str:
    return re.sub(r"\s+", " ", _ZW_RX.sub("", s or "")).strip()


def _clip(s: str, n: int = 300) -> str:
    s = _ws(s)
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


# --------------------------------------------------------------------------- name profile
@dataclass
class NameProfile:
    name: str
    core: list[str]          # lower-case tokens without legal-form words
    required: list[str]      # tokens that must be matched (distinctive, minus person tokens of ENK names)
    optional: list[str]      # person / v/-name tokens (may be absent)
    distinctive: bool        # at least one non-generic token exists

    @property
    def joined(self) -> str:
        return squash("".join(self.core))

    @property
    def joined_required(self) -> str:
        return squash("".join(self.required))


def name_profile(name: str, form: str | None = None) -> NameProfile:
    name = name or ""
    core = X.name_core_tokens(name)
    dist = [t for t in core if t not in X.GENERIC_TOKENS and not t.isdigit()]
    optional: set[str] = set()
    # Tokens after "v/" or "c/o" name a person or a manager, not the business.
    m = re.search(r"\b(?:v/|c/o)\s*(.+)$", name, re.I)
    if m:
        optional |= set(X.name_core_tokens(m.group(1)))
    # Brreg writes the business part of ENK-style names in capitals and the owner's name in title case:
    # "KOOLART John Martin Sanvik". When both styles occur, the title-case words are the person.
    words = re.findall(r"[A-Za-zÆØÅæøåÄÖÜäöüÉé0-9]+", name)
    has_caps = any(w.isupper() and len(w) >= 2 and not w.isdigit() for w in words)
    if has_caps:
        for w in words:
            if w[:1].isupper() and not w.isupper():
                optional.add(w.lower())
    base = dist or [t for t in core if not t.isdigit()] or core
    required = [t for t in base if t not in optional] or base
    return NameProfile(name=name, core=core, required=required, optional=sorted(optional & set(core)),
                       distinctive=bool(dist))


def token_in(tok: str, hay: str) -> bool:
    """Whole-word match of a name token (æøå-folded; squashed for >=5 chars; genitive -s tolerated)."""
    if not tok or not hay:
        return False
    h = fold(hay)
    t = fold(tok)
    if re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", h):
        return True
    if len(t) >= 5 and t in re.sub(r"[^a-z0-9]", "", h):
        return True
    if len(t) >= 6 and t.endswith("s") and re.search(r"(?<![a-z0-9])" + re.escape(t[:-1]), h):
        return True
    return False


def name_matches(text: str, prof: NameProfile) -> bool:
    return bool(prof.required) and all(token_in(t, text) for t in prof.required)


# --------------------------------------------------------------------------- pages
@dataclass
class Page:
    kind: str                       # home | proof | contact | about | terms | privacy | impressum | ...
    url: str                        # URL requested
    final_url: str                  # URL after HTTP redirects
    status: int
    html: str = ""
    sha256: str = ""
    retrieved_at: str = ""
    redirects: list[str] = field(default_factory=list)
    content_type: str = "text/html"

    @property
    def is_html(self) -> bool:
        ct = (self.content_type or "").lower()
        if "html" in ct:
            return True
        if ct and not any(t in ct for t in ("text/plain", "octet-stream", "/xml")):
            return False
        return re.search(r"<(?:!doctype html|html|head|body)\b", self.html[:3000], re.I) is not None


@dataclass
class PageInfo:
    idx: int
    page: Page
    x: dict
    text: str
    title: str
    meta: dict[str, str]
    lang: str | None
    blocking: list[str]
    hits: list[dict]
    others: set[str]
    block: dict | None
    orgs: list[dict]
    feeds: list[tuple[str, str]]
    links: list[tuple[str, str]]
    h1: str = ""

    @property
    def url(self) -> str:
        return self.page.final_url

    def strong_text(self) -> str:
        """Fields where a site states who it is: title, og:site_name/og:title, application-name, JSON-LD names."""
        parts = [self.title, self.meta.get("og:site_name", ""), self.meta.get("og:title", ""),
                 self.meta.get("application-name", "")]
        for o in self.orgs:
            for k in ("name", "legalName"):
                if isinstance(o.get(k), str):
                    parts.append(o[k])
        return " | ".join(p for p in parts if p)

    def prominent_text(self) -> str:
        return self.x.get("prominent_text", "") or ""


def _window(text: str, s: int, e: int, before: int = 110, after: int = 70, limit: int = 300) -> str:
    a = max(0, s - before)
    b = min(len(text), e + after)
    if a > 0:
        sp = text.find(" ", a, s)
        a = sp + 1 if sp != -1 else a
    if b < len(text):
        sp = text.rfind(" ", e, b)
        b = sp if sp != -1 else b
    return _clip(text[a:b], limit)


def raw_window(html: str, needle: str, before: int = 60, after: int = 120) -> str | None:
    i = html.lower().find(needle.lower())
    if i < 0:
        return None
    return _clip(html[max(0, i - before): i + len(needle) + after])


def text_window(text: str, needle: str) -> str | None:
    i = text.lower().find(needle.lower())
    if i < 0:
        return None
    return _window(text, i, i + len(needle))


def _jsonld_ids(o: dict) -> set[str]:
    ids: set[str] = set()
    vals: list[Any] = []
    for k in ("taxID", "vatID", "identifier", "leiCode"):
        v = o.get(k)
        vals.extend(v if isinstance(v, list) else [v])
    for v in vals:
        if isinstance(v, dict):
            v = v.get("value") or v.get("@value") or v.get("name")
        if isinstance(v, (str, int)):
            d = re.sub(r"\D", "", str(v))
            if len(d) == 9 and is_valid(d):
                ids.add(d)
    return ids


def _meta_full(tree) -> tuple[str, dict[str, str], str | None, list[tuple[str, str]]]:
    t = tree.css_first("title")
    title = _ws(t.text(separator=" ")) if t else ""
    meta: dict[str, str] = {}
    for m in tree.css("meta"):
        k = (m.attributes.get("property") or m.attributes.get("name") or m.attributes.get("itemprop") or "").lower().strip()
        if k in ("description", "og:description", "og:site_name", "og:title", "og:image", "og:url", "application-name",
                 "twitter:description", "generator") and k not in meta:
            v = _ws(m.attributes.get("content") or "")
            if v:
                meta[k] = v[:1000]
    h = tree.css_first("html")
    lang = (h.attributes.get("lang") or h.attributes.get("xml:lang")) if h else None
    feeds: list[tuple[str, str]] = []
    for ln in tree.css("link[href]"):
        rel = (ln.attributes.get("rel") or "").lower()
        typ = (ln.attributes.get("type") or "").lower().strip()
        if "alternate" in rel and typ in FEED_TYPES:
            feeds.append(((ln.attributes.get("href") or "").strip(), _ws(ln.attributes.get("title") or "")))
    return title[:500], meta, (lang.strip() if lang else None), feeds


def _blocking(x: dict, page: Page, title: str, text: str) -> list[str]:
    out: list[str] = []
    tl = x.get("text_len", len(text))
    # Every placeholder phrase is judged on its own: strong ones (for sale, parked, suspended, default page)
    # anywhere in the first 3,000 chars; weak ones (coming soon, under construction) only in the <title> or on
    # near-empty pages, because real sites announce products that are "coming soon".
    for m in X.PARKED_TEXT_RX.finditer(title + " \n " + text[:3000]):
        phrase = m.group(0)
        if WEAK_PARKED_RX.search(phrase) and m.start() > len(title) and tl >= 1500:
            continue
        out.append("parked_text:" + phrase[:40])
        break
    for s in x.get("placeholder_signals", []):
        if s == "parking_url":
            out.append(s)
        elif s.startswith("bot_wall:"):
            if page.status >= 400 or tl < 1500:
                out.append(s)
        elif s == "cjk_text":
            out.append(s)
    sm = SPAM_RX.search(title + " " + text[:5000])
    if sm:
        out.append("spam_text:" + sm.group(0)[:30])
    return out


def analyse_page(page: Page, target: str, name: str = "", idx: int = 0) -> PageInfo:
    html = page.html or ""
    x = X.extract_page(html, page.final_url, target, name or None)
    text = X.plain_text(html)
    tree = LexborHTMLParser(html)
    title, meta, lang, feeds_raw = _meta_full(tree)
    links: list[tuple[str, str]] = []
    for a in tree.css("a[href]"):
        href = (a.attributes.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        links.append((urljoin(page.final_url, href).split("#")[0], _ws(a.text(separator=" "))[:80]))
    feeds = [(urljoin(page.final_url, h), t) for h, t in feeds_raw if h]
    h1 = " | ".join(_ws(n.text(separator=" "))[:200] for n in tree.css("h1")[:2])
    orgs = [o for o in x.get("jsonld_orgs", []) if isinstance(o, dict)]
    ids = set().union(*(_jsonld_ids(o) for o in orgs)) if orgs else set()
    # ---- target hits with verbatim spans
    hits: list[dict] = []
    trx = X.target_rx(target)
    for m in trx.finditer(text):
        before = text[max(0, m.start() - 45):m.start()]
        after = text[m.end():m.end() + 6]
        kw = bool(X.ORG_KEYWORD_RX.search(before)) or bool(re.search(r"NO[\s.\-]?$", before)) or \
            bool(re.match(r"[\s.\-]?MVA", after, re.I))
        credit = bool(X.CREDIT_RX.search(text[max(0, m.start() - 80):m.end() + 40]))
        hits.append({"src": "text", "kw": kw, "credit": credit, "span": _window(text, m.start(), m.end()),
                     "locator": "text"})
    if target in x.get("brreg_links", []):
        mm = re.search(r"""href\s*=\s*["']([^"']*(?:brreg\.no|proff\.no|purehelp\.no|forvalt\.no)[^"']*?"""
                       + target + r"""[^"']*)["']""", html, re.I)
        span = raw_window(html, mm.group(0)) if mm else raw_window(html, target)
        hits.append({"src": "registry_link", "kw": True, "credit": False, "span": span or target,
                     "locator": "a[href*=brreg]"})
    if target in ids:
        hits.append({"src": "jsonld", "kw": True, "credit": False,
                     "span": raw_window(html, target) or target, "locator": 'script[type="application/ld+json"]'})
    if not hits and trx.search(html):
        mm = trx.search(html)
        hits.append({"src": "html", "kw": bool(X.ORG_KEYWORD_RX.search(html[max(0, mm.start() - 60):mm.start()])),
                     "credit": False, "span": _clip(html[max(0, mm.start() - 120):mm.end() + 60]), "locator": "html"})
    others = (set(x.get("other_orgnrs_kw", [])) | set(x.get("other_orgnrs_text", [])) |
              set(x.get("brreg_links", [])) | ids) - {target}
    block = X.orgnr_block(html, target) if any(h["src"] == "text" for h in hits) else None
    return PageInfo(idx=idx, page=page, x=x, text=text, title=title, meta=meta, lang=lang,
                    blocking=_blocking(x, page, title, text), hits=hits, others=others, block=block, orgs=orgs,
                    feeds=feeds, links=links, h1=h1)


# --------------------------------------------------------------------------- site identity helpers
def site_base(start_url: str, final_url: str) -> tuple[str, bool]:
    """('scheme://host/' or 'scheme://host/sub/path', is_subpath). A site is a sub-path of a shared host when
    the filed URL itself has a non-trivial path, or it redirects to a non-home path on another domain."""
    sp = urlsplit(final_url)
    host = (sp.hostname or "").lower()
    scheme = sp.scheme if sp.scheme in ("http", "https") else "https"
    path = sp.path or "/"
    st = urlsplit(start_url or final_url)
    start_path = st.path or "/"
    start_dom = X.reg_domain(st.hostname or "")
    final_dom = X.reg_domain(host)
    start_sub = not HOMEISH_PATH_RX.match(start_path)
    final_sub = not HOMEISH_PATH_RX.match(path)
    subpath = start_sub or (final_sub and start_dom != final_dom)
    if not subpath:
        return f"{scheme}://{host}/", False
    base = path
    if start_sub and (st.hostname or "").lower().removeprefix("www.") == host.removeprefix("www.") \
            and path.lower().startswith(start_path.rstrip("/").lower()):
        base = start_path
    return f"{scheme}://{host}{base}", True


def under_base(url: str, site_url: str, subpath: bool, exclusive: bool) -> bool:
    """Is `url` part of the verified site? (same registrable domain; under the base path for sub-path sites)."""
    u = urlsplit(url)
    s = urlsplit(site_url)
    uh = (u.hostname or "").lower().removeprefix("www.")
    sh = (s.hostname or "").lower().removeprefix("www.")
    if subpath:
        return uh == sh and (u.path or "/").lower().startswith((s.path or "/").rstrip("/").lower())
    if uh == sh:
        return True
    return exclusive and X.reg_domain(uh) == X.reg_domain(sh)


def co_names(rec: dict) -> list[str]:
    out = []
    for k in ("forretningsadresse", "postadresse"):
        for line in (rec.get(k) or {}).get("adresse") or []:
            m = re.match(r"\s*c/o\s+(.+)", line or "", re.I)
            if m:
                out.append(m.group(1).strip())
    return out


def _person_like(s: str) -> bool:
    words = re.findall(r"[A-Za-zÆØÅæøåÄÖÜäöüÉé.\-]+", s)
    return 2 <= len(words) <= 4 and not COMPANY_WORD_RX.search(s)


def _owner_entities(info: PageInfo) -> list[str]:
    """Legal entities a page names as its owner: first 'X AS' in each copyright line, JSON-LD legalName."""
    out = []
    for seg in info.x.get("copyright", []) or []:
        seg = re.sub(r"^(?:[\s\d\-–,.:|©]+|copyright\b|opphavsrett\b|\(c\)|&copy;|by\b|av\b)+", "", seg, flags=re.I)
        m = re.match(r"([A-ZÆØÅ0-9][\w&'.\-]*(?:\s+[\w&'.\-]+){0,5}?)\s+" + ORGFORM_WORD_RX.pattern, seg)
        if m and not X.CREDIT_RX.search(m.group(0)):
            out.append(m.group(0).strip())
    for o in info.orgs:
        ln = o.get("legalName")
        if isinstance(ln, str) and ORGFORM_WORD_RX.search(ln):
            out.append(ln.strip())
    return out


def _entity_key(name: str) -> str:
    n = fold(name).replace("&", " og ").replace("+", " og ")
    return squash("".join(t for t in X.name_core_tokens(n) if t not in ("og", "and")))


def _entity_matches(entity: str, prof: NameProfile, former: Iterable[str] = ()) -> bool:
    """A legal entity named as site owner ('© 2026 X AS', JSON-LD legalName) must be *this* entity: same
    name apart from case, punctuation, legal form and '&'/'og'. 'Habberstad AS' is not
    'Habberstad Rekruttering og Utvikling AS' (it is the parent)."""
    key = _entity_key(entity)
    if not key:
        return True
    names = [prof.name, *former]
    if any(key == _entity_key(n) for n in names if n):
        return True
    # tolerate a dropped person part of ENK-style names ('Koolart' for 'KOOLART John Martin Sanvik')
    return bool(prof.optional) and key == squash("".join(prof.required))


def org_matches(o: dict, target: str, prof: NameProfile) -> bool:
    ids = _jsonld_ids(o)
    if ids:
        return target in ids
    return any(isinstance(o.get(k), str) and name_matches(o[k], prof) for k in ("legalName", "name"))


# --------------------------------------------------------------------------- registry corroboration
def _nat8(raw: str | None) -> str | None:
    d = re.sub(r"\D", "", raw or "")
    if d.startswith("0047"):
        d = d[4:]
    elif d.startswith("47") and len(d) == 10:
        d = d[2:]
    return d if len(d) == 8 else None


def phone_in_text(p8: str, text: str) -> re.Match | None:
    rx = r"(?<![\d])(?:(?:\+|00)\s?47[\s.\-]?|\(\+47\)\s?)?" + r"[\s.\-  ]?".join(p8) + r"(?![\d])"
    return re.search(rx, text)


def registry_phones(rec: dict) -> list[str]:
    return sorted({p for p in (_nat8(rec.get("telefon")), _nat8(rec.get("mobil"))) if p})


def registry_email(rec: dict) -> str:
    return (rec.get("epostadresse") or "").strip().lower()


def _all_emails(info: PageInfo) -> set[str]:
    x = info.x
    out = set(x.get("mailto", [])) | set(x.get("emails_text", [])) | set(x.get("cf_emails", [])) | \
        set(x.get("rtl_emails", [])) | set(x.get("emails_obfuscated", []))
    for o in info.orgs:
        if isinstance(o.get("email"), str):
            out.add(o["email"].lower().removeprefix("mailto:").strip())
    return {e for e in out if X.EMAIL_RX.fullmatch(e)}


def corroborate(infos: list[PageInfo], rec: dict, site_domain: str) -> dict:
    text = " ".join(i.text for i in infos)
    tl = text.lower()
    tel_digits = [re.sub(r"\D", "", t) for i in infos for t in i.x.get("tel_links", [])]
    sig: dict[str, Any] = {}
    sig["phone_match"] = [p for p in registry_phones(rec)
                          if phone_in_text(p, text) or any(d.endswith(p) for d in tel_digits)]
    em = registry_email(rec)
    found = set().union(*(_all_emails(i) for i in infos)) if infos else set()
    sig["email_match"] = bool(em) and em in found
    dom = em.split("@", 1)[1] if "@" in em else ""
    sig["email_domain_match"] = bool(dom) and dom not in X.FREEMAIL and X.reg_domain(dom) not in X.FREEMAIL and \
        X.reg_domain(dom) == site_domain
    addr_hits = []
    for k in ("forretningsadresse", "postadresse"):
        a = rec.get(k) or {}
        pn = a.get("postnummer")
        lines = a.get("adresse") or []
        if not pn or not lines:
            continue
        street = re.findall(r"[a-zæøåäöü][a-zæøåäöü.\-]{3,}", (lines[0] or "").lower())
        if not street or re.match(r"(postboks|pb|boks|c/o)$", street[0]):
            continue
        for m in re.finditer(r"(?<!\d)" + re.escape(pn) + r"(?!\d)", text):
            win = tl[max(0, m.start() - 120): m.end() + 60]
            if street[0] in win:
                addr_hits.append(f"{k}:{pn}+{street[0]}")
                break
    sig["address_match"] = addr_hits
    name = _ws((rec.get("navn") or "").lower())
    toks = X.name_core_tokens(name)
    strong_name = bool(ORGFORM_WORD_RX.search(rec.get("navn") or "")) or len(toks) >= 3
    sig["legal_name_full"] = bool(name) and len(toks) >= 1 and strong_name and bool(re.search(
        r"(?<![a-z0-9æøå])" + re.escape(name).replace(r"\ ", r"[\s\-]+") + r"(?![a-z0-9æøå])", tl))
    sig["n"] = sum([bool(sig["phone_match"]), bool(sig["email_match"] or sig["email_domain_match"]),
                    bool(addr_hits), bool(sig["legal_name_full"])])
    sig["kinds"] = [k for k in ("phone_match", "email_match", "email_domain_match", "address_match", "legal_name_full")
                    if sig[k]]
    return sig


# --------------------------------------------------------------------------- verdict
@dataclass
class Verdict:
    level: str                                  # L1 | L2 | ambiguous | not_available | blocked
    reason: str
    lead_kind: str = ""
    site_url: str | None = None
    host: str | None = None
    domain: str | None = None
    subpath: bool = False
    exclusive: bool = True
    multi: bool = False
    dominant: bool = False
    proof: dict | None = None                   # {"page","url","span","method","locator"}
    other_orgnrs: list[str] = field(default_factory=list)
    shared_with: list[str] = field(default_factory=list)
    corroboration: dict | None = None
    infos: list[PageInfo] = field(default_factory=list)
    profile: NameProfile | None = None
    target: str = ""
    form: str | None = None
    foreign_owner: str | None = None            # L2 only: site names another legal entity as owner

    @property
    def published(self) -> bool:
        return self.level in ("L1", "L2")

    @property
    def confidence(self) -> float:
        return CONFIDENCE.get(self.level, 0.0)

    @property
    def restricted(self) -> bool:
        """Facts must come from the target's own org-number block (or equal the registry record)."""
        return self.multi or not self.exclusive or bool(self.foreign_owner)


_HIT_RANK = {("text", True): 0, ("registry_link", True): 1, ("jsonld", True): 2, ("text", False): 3,
             ("html", True): 4, ("html", False): 5}


def verify(pages: list[Page], rec: dict | None, target: str, *, lead_kind: str, start_url: str,
           shared_orgnrs: Iterable[str] = (), name: str | None = None, form: str | None = None,
           cache: dict | None = None) -> Verdict:
    """Identity verdict for the pages of one lead (pages[0] = home page after redirects)."""
    rec = rec or {}
    name = name if name is not None else (rec.get("navn") or "")
    if form is None and isinstance(rec.get("organisasjonsform"), dict):
        form = rec["organisasjonsform"].get("kode")
    prof = name_profile(name, form)
    if not pages:
        return Verdict("not_available", "no page could be fetched", lead_kind=lead_kind, profile=prof, target=target)
    home = pages[0]
    v = Verdict("ambiguous", "", lead_kind=lead_kind, profile=prof, target=target, form=form)
    v.host = (urlsplit(home.final_url).hostname or "").lower()
    v.domain = X.reg_domain(v.host)
    if home.status in (401, 403, 429):
        v.level, v.reason = "blocked", f"home page refused the request (HTTP {home.status}, access control or bot protection)"
        return v
    if not (200 <= home.status < 300):
        extra = " (redirect chain too long or looping)" if 300 <= home.status < 400 else ""
        v.level, v.reason = "not_available", f"home page returns HTTP {home.status} ({home.final_url}){extra}"
        return v
    if not home.is_html:
        v.level, v.reason = "not_available", f"home page is not an HTML page ({home.content_type or 'unknown type'})"
        return v
    infos = []
    for p in pages:
        if not (200 <= p.status < 300 and p.is_html):
            continue
        key = f"{p.final_url}|{p.sha256 or id(p)}|{target}"
        info = cache.get(key) if cache is not None else None
        if info is None:
            info = analyse_page(p, target, name, idx=0)
            if cache is not None:
                cache[key] = info
        info = PageInfo(**{**info.__dict__, "idx": len(infos)})
        infos.append(info)
    v.infos = infos
    hi = infos[0]
    if hi.blocking:
        sig = ", ".join(hi.blocking)
        if any(s.startswith("bot_wall") for s in hi.blocking):
            v.level, v.reason = "blocked", f"bot wall / challenge page at {home.final_url} ({sig})"
        elif any(s.startswith(("cjk_text", "spam_text")) for s in hi.blocking):
            v.level, v.reason = "not_available", f"hijacked or expired domain: unrelated foreign-language/spam content at {home.final_url}"
        else:
            v.level, v.reason = "not_available", f"parked, for-sale or placeholder page at {home.final_url} ({sig})"
        return v
    v.site_url, v.subpath = site_base(start_url, home.final_url)
    shared = sorted(set(shared_orgnrs) - {target})
    v.shared_with = shared
    v.exclusive = not v.subpath and not shared
    ok = [i for i in infos if not i.blocking]
    others = sorted(set().union(*(i.others for i in ok)))
    v.other_orgnrs = others
    v.multi = bool(others)
    v.dominant = any(name_matches(i.strong_text(), prof) for i in ok)
    found = [(i, h) for i in ok for h in i.hits]
    reg_lead = lead_kind == "registry_website"
    if found:
        i, h = min(found, key=lambda ih: (_HIT_RANK.get((ih[1]["src"], ih[1]["kw"]), 9), ih[0].idx))
        if all(hh["credit"] for _, hh in found) and not v.dominant:
            v.reason = (f"org number appears on {i.url} only in a web-agency/credit context, "
                        "and the site is not branded as the company")
            return v
        if v.restricted and not reg_lead and not v.dominant:
            ctx = f"other organisation numbers ({', '.join(others[:5])})" if others else \
                f"a host shared with other organisation numbers ({', '.join(shared[:5])})"
            v.reason = (f"org number printed on {i.url}, but the site also shows {ctx} and is not branded as "
                        f"{name or 'the company'}; it is not the company's own website")
            return v
        method = {"text": "orgnr_in_page_text", "registry_link": "orgnr_in_registry_link",
                  "jsonld": "orgnr_in_jsonld_identifier", "html": "orgnr_in_page_html"}[h["src"]]
        v.level = "L1"
        v.proof = {"page": i.idx, "url": i.url, "span": h["span"], "method": method, "locator": h["locator"]}
        note = []
        if others:
            note.append(f"site also shows other organisation numbers ({', '.join(others[:5])}); "
                        "contact facts limited to the company's own org-number block")
        if shared:
            note.append(f"{v.host} also hosts other organisation numbers' sites")
        if v.subpath:
            note.append(f"company section {v.site_url} of a shared host")
        v.reason = f"organisation number printed on {i.url}" + (" (" + "; ".join(note) + ")" if note else "")
        return v
    # ------------------------------------------------------------------ no org number: L2 or ambiguous
    if others:
        v.reason = (f"site shows other organisation number(s) {', '.join(others[:5])} but not {target}: "
                    "it belongs to another entity")
        return v
    if not reg_lead:
        v.reason = "candidate site does not print the organisation number"
        return v
    if v.subpath:
        v.reason = f"registered website {v.site_url} is a sub-page of a shared host; only a printed org number can verify it"
        return v
    if shared:
        v.reason = (f"domain {v.domain} is also the website of other organisation numbers ({', '.join(shared[:5])}); "
                    "only a printed org number can verify it")
        return v
    if hi.x.get("text_len", 0) < 200 and not hi.orgs:
        v.reason = "home page has no readable content (JavaScript or image-only); org number not found"
        return v
    if ADDRESS_NAME_RX.match(_ws(name).upper()):
        v.reason = "address-named entity (property SPV); the site must print the org number"
        return v
    strong_all = " | ".join(i.strong_text() for i in ok)
    for co in co_names(rec):
        if _person_like(co):
            continue
        words = re.findall(r"[a-zæøå0-9]{4,}", fold(strong_all))
        if any(w in fold(co) for w in words if w not in ("home", "hjem", "forside", "velkommen", "welcome")):
            v.reason = f"registry address is c/o {co}, whose brand the site carries: the manager's site, not the company's"
            return v
    st_dom = X.reg_domain(urlsplit(start_url).hostname or "")
    if st_dom and st_dom != v.domain and not v.dominant:
        v.reason = (f"registered website redirects to another domain ({v.host}) whose title/site name does not "
                    f"carry the company name")
        return v
    prom_pages = [i for i in ok if name_matches(i.prominent_text(), prof)]
    if not prom_pages:
        missing = [t for t in prof.required if not any(token_in(t, i.prominent_text()) for i in ok)]
        v.reason = f"company name not prominent on the site (missing: {', '.join(missing) or 'name tokens'})"
        return v
    former = [h.get("navn", "") for h in rec.get("historiskeNavn") or [] if isinstance(h, dict)]
    owner = next((ent for i in ok for ent in _owner_entities(i) if not _entity_matches(ent, prof, former)), None)
    corr = corroborate(ok, rec, v.domain or "")
    v.corroboration = corr
    if owner:
        # A sibling/parent named as owner: only an overwhelming registry match keeps the registry-filed site,
        # and then facts are restricted to values equal to the registry record.
        if not ((corr["phone_match"] or corr["email_match"]) and corr["n"] >= 3):
            v.reason = f"site names another legal entity as its owner ({owner})"
            return v
        v.foreign_owner = owner
    if corr["n"] < 1:
        v.reason = "registered website with the company name, but no registry corroboration (phone, e-mail, address, legal name)"
        return v
    if not prof.distinctive and corr["n"] < 2:
        v.reason = "generic company name: two registry corroborations needed"
        return v
    if corr["kinds"] == ["email_domain_match"] and not v.dominant:
        v.reason = "only the e-mail domain corroborates and the name is not in the site title/site name"
        return v
    core_all = [t for t in prof.core if not t.isdigit() and t not in prof.optional and t not in ("og", "i", "på", "of")]
    if len(core_all) > len(prof.required) and not corr["legal_name_full"] and corr["n"] < 2 and \
            not any(all(token_in(t, i.prominent_text()) for t in core_all) for i in ok):
        missing = [t for t in core_all if t not in prof.required]
        v.reason = (f"only the distinctive part of the name is prominent (not: {', '.join(missing)}) and only one "
                    "registry corroboration; possibly a parent/brand site")
        return v
    if st_dom and st_dom != v.domain and not corr["legal_name_full"] and corr["n"] < 2 and \
            not any(all(token_in(t, i.strong_text()) for t in core_all) for i in ok):
        v.reason = (f"registered website redirects to another domain ({v.host}) whose title/site name carries only "
                    "part of the company name, with one registry corroboration (parent or brand site)")
        return v
    strongish = any(token_in(t, i.strong_text() + " | " + i.h1) for i in ok for t in prof.required)
    if not (strongish or corr["legal_name_full"] or corr["n"] >= 2):
        v.reason = "company name appears only in the footer and only one registry corroboration"
        return v
    pi = prom_pages[0]
    v.level = "L2"
    span = _clip(" | ".join(p for p in (pi.title, pi.meta.get("og:site_name", "")) if p) or pi.prominent_text()[:300])
    v.proof = {"page": pi.idx, "url": pi.url, "span": span, "method": "registry_website+name+" + "+".join(corr["kinds"]),
               "locator": "title"}
    v.reason = "registry-filed website; company name prominent; corroborated by " + ", ".join(corr["kinds"])
    if v.foreign_owner:
        v.reason += f" (site names {v.foreign_owner} as owner: only registry-equal contact facts published)"
    return v


# --------------------------------------------------------------------------- facts
@dataclass
class Fact:
    field: str
    value: Any
    page: int                     # index into verdict.infos
    locator: str
    span: str
    qualifier: str | None = None

    def sort_key(self) -> tuple:
        return (self.field, self.qualifier or "", str(self.value))


def email_kind(addr: str, prof: NameProfile, label: str) -> str:
    local = addr.split("@", 1)[0].lower().split("+", 1)[0]
    segs = [s for s in re.split(r"[._\-]", local) if s]
    if local in INVOICE_LOCALS or any(s in INVOICE_LOCALS for s in segs) or \
            local.startswith(("faktura", "invoice", "noreply", "no-reply")):
        return "invoice"
    if local in ROLE_LOCALS or (segs and segs[0] in ROLE_LOCALS):
        return "role"
    fl = squash(local)
    names = {squash(t) for t in prof.core if len(t) >= 3} | {prof.joined, prof.joined_required}
    if label and len(label) >= 3:
        names.add(label)
    if fl and fl in names:
        return "name"
    if len(segs) >= 2 and all(s.isalpha() and len(s) >= 2 for s in segs):
        return "personal"
    return "other"


def normalize_phone(raw: str) -> str | None:
    s = (raw or "").strip()
    if not s:
        return None
    s = re.sub(r"^\(\+?(\d+)\)", r"+\1", s)
    if s.startswith("00"):
        s = "+" + s[2:]
    try:
        num = phonenumbers.parse(s, "NO")
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(num):
        return None
    return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)


def text_phones(text: str) -> list[dict]:
    """Phone numbers in visible text with context (keyword, fax, credit)."""
    out = []
    for m in X.PHONE_RX.finditer(text):
        digits = re.sub(r"\D", "", m.group("num"))
        if len(digits) != 8:
            continue
        before = text[max(0, m.start() - 30):m.start()]
        if re.search(r"(org|kr\.?|nok|konto|kontonr|bank|account|iban)\W{0,6}$", before[-14:], re.I):
            continue
        if text[m.end():m.end() + 2] == ",-":
            continue
        kws = list(X.PHONE_KW_RX.finditer(before))
        fax = bool(kws) and re.match(r"fa(x|ks)", kws[-1].group(0), re.I) is not None
        out.append({"digits": digits, "raw": m.group(0).strip(), "kw": bool(kws) and not fax, "fax": fax,
                    "credit": bool(X.CREDIT_RX.search(text[max(0, m.start() - 70):m.start()])),
                    "span": _window(text, m.start(), m.end(), 70, 40)})
    return out


SOCIAL_PLATFORMS = ("facebook", "instagram", "linkedin", "twitter", "youtube", "tiktok")
# brand + foreign country handle (enterpriseuk, brandsverige): an international sibling, not the Norwegian entity
FOREIGN_HANDLE_RX = re.compile(r"(?:uk|usa|us|de|dk|se|fi|nl|sverige|sweden|danmark|denmark|deutschland|germany|"
                               r"suomi|finland|france|spain|espana|italia|italy|poland|polska|global|international|"
                               r"intl|worldwide|europe)$")


def canonical_social(platform: str, url: str) -> tuple[str, str] | None:
    """(canonical_url, handle) or None for non-profile links."""
    sp = urlsplit(url.strip())
    host = (sp.hostname or "").lower()
    segs = [unquote(s) for s in (sp.path or "").split("/") if s]
    if platform == "facebook":
        if not segs:
            return None
        if segs[0].lower() == "profile.php":
            return None                                   # opaque numeric profile: not attributable by name
        if segs[0].lower() in ("p", "pg") and len(segs) >= 2:
            segs = segs[1:]
        if segs[0].lower() == "pages":
            return None
        h = segs[0]
        return f"https://www.facebook.com/{h}", h
    if platform == "instagram":
        return (f"https://www.instagram.com/{segs[0]}", segs[0]) if segs else None
    if platform == "linkedin":
        if len(segs) >= 2 and segs[0].lower() in ("company", "school", "showcase"):
            return f"https://www.linkedin.com/{segs[0].lower()}/{segs[1]}", segs[1]
        return None
    if platform == "twitter":
        if not segs:
            return None
        base = "x.com" if host.endswith("x.com") else "twitter.com"
        return f"https://{base}/{segs[0]}", segs[0]
    if platform == "youtube":
        if segs and segs[0].startswith("@"):
            return f"https://www.youtube.com/{segs[0]}", segs[0]
        if len(segs) >= 2 and segs[0].lower() in ("c", "user", "channel"):
            return f"https://www.youtube.com/{segs[0].lower()}/{segs[1]}", segs[1]
        return None
    if platform == "tiktok":
        return (f"https://www.tiktok.com/{segs[0]}", segs[0]) if segs and segs[0].startswith("@") else None
    return None


def social_matches(url: str, prof: NameProfile, label: str | None) -> bool:
    """Stricter than extract.social_matches_company: the matching name part must dominate the handle, so
    sponsor/umbrella handles that merely contain a place name (visitbergen) are rejected."""
    if X.PLATFORM_SOCIAL_RX.search(url):
        return False
    h = squash(X.social_handle(url))
    if len(h) < 3:
        return False
    cands: set[str] = {prof.joined, prof.joined_required}
    cands |= {squash(t) for t in prof.required if len(t) >= 4}
    if label and len(label) >= 4:
        cands.add(label)
    for c in cands:
        if not c or len(c) < 4:
            continue
        if c in h and len(c) >= 0.6 * len(h):
            return True
        if h in c and len(h) >= 5 and len(h) >= 0.6 * len(c):
            return True
    if label and len(label) == 3 and h.startswith(label) and len(h) >= 6:
        rest = h[3:]
        if any(squash(t) and squash(t) in rest for t in prof.required if len(t) >= 4):
            return True
    return False


ADDR_RX = re.compile(r"(?P<street>[A-ZÆØÅ][^\d,|:;\n()@]{2,40}?\s\d{1,4}\s?[A-Za-z]?)\s*,?\s+(?P<pc>\d{4})\s+"
                     r"(?P<place>[A-ZÆØÅ][A-Za-zÆØÅæøåéü\-]+(?:\s(?:i|på)\s[A-ZÆØÅ][A-Za-zæøåÆØÅ\-]+)?)")
ADDR_LABEL_RX = re.compile(r"^(?:(?:besøks|post|leverings|faktura|forretnings|kontor|vare)?-?adresse|address|"
                           r"visiting address|postal address|besøk|kontor|office|adr\.?)\s*:?\s*", re.I)


STREET_SUFFIX_RX = re.compile(
    r"(?:vei(?:en)?|veg(?:en)?|vn\.?|gat(?:a|e|en)|gt\.?|all[ée](?:en)?|plass(?:en)?|torg(?:et)?|bakken?|brygg(?:a|e|en)|"
    r"kai(?:a|en)?|stien|lia|haugen|tun(?:et)?|ringen|svingen|terrassen?|parken|senter(?:et)?|jordet|myra|moen|"
    r"holmen|neset|odden|stranda?|kroken|løkka|vika|road|street|avenue|lane|område)$", re.I)
STANDALONE_SUFFIX = {"vei", "veg", "vn", "vn.", "gate", "gata", "gt", "gt.", "allé", "alle", "plass", "torg", "road",
                     "street", "avenue", "lane", "brygge", "kai"}
LEGAL_FORM_WORDS = {"AS", "ASA", "ANS", "DA", "SA", "BA", "KS", "NUF", "ENK", "IKS", "KF", "STI", "BRL", "ESEK"}
STREET_MODIFIERS = {"øvre", "nedre", "gamle", "gml.", "store", "lille", "søndre", "nordre", "vestre", "østre", "nye",
                    "indre", "ytre"}


def _trim_street(street: str) -> str:
    """'Drammens Turnforening Danvikgata 27' -> 'Danvikgata 27'; 'Bygdøy Allé 47b' stays whole."""
    m = re.search(r"\s(\d{1,4}\s?[A-Za-z]?)$", street)
    if not m:
        return street
    words = street[:m.start()].split()
    if not words:
        return street
    k = None
    for j in range(len(words) - 1, -1, -1):
        w = words[j].strip(".,").lower()
        if w in ("postboks", "boks", "pb", "pb.") or STREET_SUFFIX_RX.search(w):
            k = j
            break
    if k is None:
        k = len(words) - 1
    start = k
    if words[k].strip(",").lower() in STANDALONE_SUFFIX:
        while start > 0 and k - start < 2 and words[start - 1][:1].isupper() and \
                words[start - 1].strip(".,").upper() not in LEGAL_FORM_WORDS and \
                not (words[start - 1].isupper() and len(words[start - 1]) <= 3):
            start -= 1
    if start > 0 and words[start - 1].lower() in STREET_MODIFIERS:
        start -= 1
    return " ".join(words[start:]) + " " + m.group(1)


def find_addresses(text: str) -> list[dict]:
    """Street + postcode + place lines, best first (visiting > plain > postal/box; invoice addresses skipped)."""
    out = []
    for m in ADDR_RX.finditer(text):
        street = _trim_street(ADDR_LABEL_RX.sub("", m.group("street")).strip())
        if len(street) < 4 or not re.search(r"[A-Za-zÆØÅæøå]{3,}", street):
            continue
        pc = m.group("pc")
        if pc == "0000":
            continue
        place = m.group("place")
        if place.lower() in ("telefon", "tlf", "postadresse", "e-post", "epost", "org", "mobil", "faks", "email"):
            continue
        off = m.group("street").rfind(street.split()[0])
        start = m.start("street") + max(0, off)
        before = text[max(0, start - 60):start].lower()
        if re.search(r"faktura|invoice|billing", before):
            continue
        box = bool(re.match(r"(postboks|boks|pb\.?)\b", street, re.I))
        rank = 0 if re.search(r"besøk|visit|kontor|beliggenhet|lokaler|finn oss", before) else 2 if box else \
            1.5 if "post" in before else 1
        value = _ws(text[start:m.end("place")])
        out.append({"value": value, "postcode": pc, "box": box, "rank": rank,
                    "span": _window(text, start, m.end("place"), 60, 40)})
    out.sort(key=lambda a: a["rank"])
    return out


def _jsonld_address(o: dict) -> str | None:
    a = o.get("address")
    if isinstance(a, list):
        a = a[0] if a else None
    if not isinstance(a, dict):
        return None
    street = a.get("streetAddress")
    pc = a.get("postalCode")
    loc = a.get("addressLocality")
    if not (isinstance(street, str) and street.strip() and isinstance(pc, (str, int)) and re.fullmatch(r"\d{4}", str(pc).strip())):
        return None
    return _ws(f"{street.strip()}, {str(pc).strip()} {loc.strip() if isinstance(loc, str) else ''}")


def _logo_of(o: dict, base: str) -> str | None:
    lg = o.get("logo")
    if isinstance(lg, list):
        lg = lg[0] if lg else None
    if isinstance(lg, dict):
        lg = lg.get("url") or lg.get("contentUrl")
    if isinstance(lg, str) and lg.strip():
        u = urljoin(base, lg.strip())
        if u.startswith(("http://", "https://")):
            return u
    return None


def collect_facts(v: Verdict, rec: dict | None) -> list[Fact]:
    if not v.published or not v.infos:
        return []
    rec = rec or {}
    prof = v.profile or name_profile(rec.get("navn") or "")
    target = v.target
    ok = [i for i in v.infos if not i.blocking]
    home = ok[0]
    proof_i = v.infos[v.proof["page"]] if v.proof else home
    restricted = v.restricted
    rich = (not restricted) or v.dominant           # site-level descriptive facts allowed
    label = squash((v.domain or "").split(".")[0]) if v.exclusive else None
    reg_em = registry_email(rec)
    reg_em_dom = X.reg_domain(reg_em.split("@", 1)[1]) if "@" in reg_em else ""
    reg_ph = set(registry_phones(rec))
    facts: list[Fact] = []

    # ---- descriptive (home page, else proof page)
    t_info = home if home.title else proof_i
    title = t_info.title
    if rich and title and title.strip().lower() not in USELESS_TITLES and len(title.strip()) >= 3 and \
            not _spammy(title, v.domain):
        facts.append(Fact("website_title", title.strip(), t_info.idx, "title", _clip(title)))
    if rich:
        for i in (home, proof_i):
            for key, loc in (("description", 'meta[name="description"]'), ("og:description", 'meta[property="og:description"]')):
                d = i.meta.get(key)
                if d and len(d) >= 15 and d.strip().lower() != (i.title or "").strip().lower() and \
                        not PLACEHOLDER_DESC_RX.search(d) and not _spammy(d, v.domain):
                    facts.append(Fact("website_description", d.strip(), i.idx, loc, _clip(d)))
                    break
            if any(f.field == "website_description" for f in facts):
                break
    lang = home.lang
    if lang and re.fullmatch(r"[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})*", lang):
        facts.append(Fact("website_language", lang, home.idx, "html[lang]", f'<html lang="{lang}">'))
    matched_orgs = [(i, o) for i in ok for o in i.orgs if org_matches(o, target, prof)]
    if rich or v.dominant:
        brand = None
        for i in (home, proof_i):
            sn = i.meta.get("og:site_name")
            if sn and name_matches(sn, prof):
                brand = Fact("brand_name", sn.strip(), i.idx, 'meta[property="og:site_name"]', _clip(sn))
                break
        if brand is None:
            for i, o in matched_orgs:
                nm = o.get("name")
                if isinstance(nm, str) and nm.strip() and name_matches(nm, prof):
                    brand = Fact("brand_name", nm.strip(), i.idx, 'script[type="application/ld+json"]',
                                 raw_window(i.page.html, nm, 40, 60) or _clip(nm))
                    break
        if brand:
            facts.append(brand)
        logo = None
        for i, o in matched_orgs:
            u = _logo_of(o, i.url)
            if u:
                logo = Fact("logo_url", u, i.idx, 'script[type="application/ld+json"] logo',
                            raw_window(i.page.html, str(o.get("logo"))[:60].strip("{}'\" ") or "logo", 40, 120) or u)
                break
        if logo is None:
            name_keys = {k for k in (prof.joined_required, label or squash((v.domain or "").split(".")[0]))
                         if k and len(k) >= 4} | {squash(t) for t in prof.required if len(squash(t)) >= 4}
            for i in (home, proof_i):
                og = i.meta.get("og:image")
                if og and re.search(r"logo", urlsplit(og).path, re.I) and \
                        any(k in squash(unquote(urlsplit(og).path)) for k in name_keys):
                    u = urljoin(i.url, og)
                    if u.startswith(("http://", "https://")):
                        logo = Fact("logo_url", u, i.idx, 'meta[property="og:image"]', _clip(og))
                        break
        if logo:
            facts.append(logo)

    # ---- address
    addr = None
    blocks = [(i, i.block) for i in ok if i.block and i.block.get("isolated")]
    if v.level == "L1":
        for i, b in blocks:
            found = find_addresses(b.get("block_text", ""))
            if found:
                addr = Fact("website_address", found[0]["value"], i.idx, "text (org-number block)", found[0]["span"])
                break
    else:
        reg_pcs = {(rec.get(k) or {}).get("postnummer") for k in ("forretningsadresse", "postadresse")} - {None}
        for i in ok:
            found = [a for a in find_addresses(i.text) if a["postcode"] in reg_pcs]
            if found:
                addr = Fact("website_address", found[0]["value"], i.idx, "text", found[0]["span"])
                break
    if addr is None and (not restricted or v.dominant):
        for i, o in matched_orgs:
            a = _jsonld_address(o)
            if a:
                addr = Fact("website_address", a, i.idx, 'script[type="application/ld+json"] address',
                            raw_window(i.page.html, str((o.get("address") or {}).get("streetAddress", "")) if isinstance(o.get("address"), dict) else "address", 40, 160) or a)
                break
    if addr:
        facts.append(addr)

    # ---- e-mail
    def email_span(i: PageInfo, e: str) -> tuple[str, str]:
        if e in i.x.get("mailto", []):
            return 'a[href^="mailto:"]', raw_window(i.page.html, "mailto:" + e, 20, 80) or e
        if e in i.x.get("cf_emails", []):
            return "[data-cfemail] (decoded)", e
        if e in i.x.get("rtl_emails", []):
            return '[style*="direction:rtl"] (reversed)', raw_window(i.page.html, e[::-1], 80, 20) or e
        return "text", text_window(i.text, e) or raw_window(i.page.html, e) or e

    block_emails = {(e, i.idx) for i, b in blocks for e in b.get("emails", [])}
    seen: set[str] = set()
    cand_emails: list[tuple[str, PageInfo, bool]] = []
    for i in ok:
        for e in sorted(_all_emails(i)):
            cand_emails.append((e, i, (e, i.idx) in block_emails))
    for i, b in blocks:
        for e in b.get("emails", []):
            cand_emails.append((e, i, True))
    for e, i, in_block in cand_emails:
        if e in seen or X.PLACEHOLDER_EMAIL_RX.search(e) or not X.EMAIL_RX.fullmatch(e):
            continue
        kind = email_kind(e, prof, label or squash((v.domain or "").split(".")[0]))
        if kind in ("invoice",):
            continue
        dom = X.reg_domain(e.split("@", 1)[1])
        equal_reg = bool(reg_em) and e == reg_em
        if restricted and not (in_block or equal_reg):
            continue
        if equal_reg:
            ok_e = kind != "personal"
        else:
            if kind not in ("role", "name"):
                continue
            site_dom = (dom == v.domain) and (v.exclusive or in_block)
            ok_e = site_dom or (reg_em_dom and dom == reg_em_dom and reg_em_dom not in X.FREEMAIL) or \
                (in_block and dom not in X.FREEMAIL)
            if v.level == "L2":
                ok_e = dom == v.domain or (reg_em_dom and dom == reg_em_dom and reg_em_dom not in X.FREEMAIL)
        if not ok_e:
            continue
        loc, span = email_span(i, e)
        if in_block and loc == "text":
            b = i.block or {}
            loc, span = "text (org-number block)", text_window(b.get("block_text", ""), e) or span
        facts.append(Fact("contact_email", e, i.idx, loc, span, qualifier=e))
        seen.add(e)

    # ---- phone: registry-equal > org-number block > tel: link > JSON-LD > keyword-labelled text; max 3
    ph: dict[str, tuple] = {}

    def add_phone(prio: int, raw: str, i: PageInfo, loc: str, span: str) -> None:
        e164 = normalize_phone(raw)
        if e164 and not e164.startswith("+47") and prio != 0 and v.form != "NUF":
            return                                     # foreign office / sister-company numbers
        if e164 and (e164 not in ph or prio < ph[e164][0]):
            ph[e164] = (prio, i.idx, loc, span)

    def tel_span(i: PageInfo, t: str) -> str:
        return raw_window(i.page.html, "tel:" + t, 20, 60) or raw_window(i.page.html, t, 40, 40) or t

    def best_text_span(text: str, p8: str) -> str:
        spans = []
        rx = r"(?<![\d])(?:(?:\+|00)\s?47[\s.\-]?|\(\+47\)\s?)?" + r"[\s.\-  ]?".join(p8) + r"(?![\d])"
        for m in re.finditer(rx, text):
            spans.append(_window(text, m.start(), m.end(), 60, 30))
        def personal_mails(w: str) -> int:
            return sum(1 for e in X.EMAIL_RX.findall(w)
                       if re.split(r"[._\-+]", e.split("@", 1)[0].lower())[0] not in ROLE_LOCALS)
        spans.sort(key=lambda w: (personal_mails(w), len(w)))
        return spans[0] if spans else p8

    def staff_context(text: str, p8: str) -> bool:
        """Number listed next to a person (personal e-mail within 45 chars): a staff line, not a company line."""
        rx = r"(?<![\d])" + r"[\s.\-  ]?".join(p8) + r"(?![\d])"
        for m in re.finditer(rx, text):
            win = text[max(0, m.start() - 45): m.end() + 45]
            for e in X.EMAIL_RX.findall(win):
                e = e.lower()
                own = X.reg_domain(e.split("@", 1)[1]) in {v.domain, reg_em_dom} - {"", None}
                if own and e != reg_em and email_kind(e, prof, label or "") in ("personal", "other"):
                    return True
        return False

    def mobile(p8: str | None) -> bool:
        return bool(p8) and p8[0] in "49"

    block_ph = {(re.sub(r"\D", "", p)[-8:], i.idx) for i, b in blocks for p in b.get("phones", []) + b.get("tel_links", [])}
    for i, b in blocks:
        btxt = b.get("block_text", "")
        for p in b.get("tel_links", []):
            n8 = _nat8(p)
            if n8 not in reg_ph and n8 and staff_context(btxt, n8):
                continue
            add_phone(0 if n8 in reg_ph else 1, p, i, 'a[href^="tel:"] (org-number block)', tel_span(i, p))
        for p in b.get("phones", []):
            if p not in reg_ph and staff_context(btxt, p):
                continue
            add_phone(0 if p in reg_ph else 1, p, i, "text (org-number block)", best_text_span(btxt, p))
    for i in ok:
        tels = i.x.get("tel_links", [])
        directory = len({_nat8(t) or t for t in tels}) > 3          # staff directory page
        for t in tels:
            n8 = _nat8(t)
            equal_reg = bool(n8) and n8 in reg_ph
            if equal_reg:
                add_phone(0, t, i, 'a[href^="tel:"]', tel_span(i, t))
                continue
            if restricted and (n8, i.idx) not in block_ph:
                continue
            if v.level == "L2" and i.page.kind != "contact":
                continue
            if (n8, i.idx) in block_ph:
                continue                                   # already taken from the block
            if directory or mobile(n8) or (n8 and staff_context(i.text, n8)):
                continue
            add_phone(2, t, i, 'a[href^="tel:"]', tel_span(i, t))
        for tp in text_phones(i.text):
            if tp["fax"] or tp["credit"]:
                continue
            equal_reg = tp["digits"] in reg_ph
            if equal_reg:
                add_phone(0, tp["raw"], i, "text", best_text_span(i.text, tp["digits"]))
                continue
            if restricted or not tp["kw"]:
                continue
            if v.level == "L2" and i.page.kind != "contact":
                continue
            if mobile(tp["digits"]) or staff_context(i.text, tp["digits"]):
                continue                                   # staff mobile / named-person lines are not company lines
            add_phone(4, tp["raw"], i, "text", tp["span"])
        if not restricted:
            for o in i.orgs:
                if org_matches(o, target, prof) and isinstance(o.get("telephone"), str):
                    tel = o["telephone"]
                    n8 = _nat8(tel)
                    if v.level == "L2" and n8 not in reg_ph:
                        continue
                    add_phone(0 if n8 in reg_ph else 3, tel, i, 'script[type="application/ld+json"] telephone',
                              raw_window(i.page.html, tel, 30, 30) or tel)
    for e164, (_prio, idx, loc, span) in sorted(ph.items(), key=lambda kv: (kv[1][0], kv[0]))[:3]:
        facts.append(Fact("contact_phone", e164, idx, loc, span, qualifier=e164))

    # ---- social profiles
    so_seen: set[str] = set()
    block_socials: set[tuple[str, int]] = set()
    for i, b in blocks:
        for h in b.get("links", []):
            hu = urljoin(i.url, h)
            for platform in SOCIAL_PLATFORMS:
                m = X.SOCIAL_RX[platform].search(hu)
                can = canonical_social(platform, m.group(0)) if m else None
                if can:
                    block_socials.add((can[0], i.idx))
    for i in ok:
        for platform in SOCIAL_PLATFORMS:
            for u in i.x.get("social", {}).get(platform, []):
                can = canonical_social(platform, u)
                if not can or can[0] in so_seen:
                    continue
                in_block = (can[0], i.idx) in block_socials
                if restricted and not v.dominant and not in_block:
                    continue
                if not social_matches(can[0], prof, label if not restricted else None):
                    continue
                if v.form != "NUF" and FOREIGN_HANDLE_RX.search(squash(can[1])):
                    continue
                so_seen.add(can[0])
                facts.append(Fact("social_profile", {"platform": platform, "url": can[0], "handle": can[1]}, i.idx,
                                  "a[href]", raw_window(i.page.html, u.split("://", 1)[-1], 30, 60) or u,
                                  qualifier=f"{platform}|{can[0]}"))
    if not restricted or v.dominant:
        for i, o in matched_orgs:
            same = o.get("sameAs")
            for u in (same if isinstance(same, list) else [same]):
                if not isinstance(u, str):
                    continue
                for platform in SOCIAL_PLATFORMS:
                    m = X.SOCIAL_RX[platform].search(u)
                    if not m:
                        continue
                    can = canonical_social(platform, m.group(0))
                    if can and can[0] not in so_seen and not X.PLATFORM_SOCIAL_RX.search(can[0]) and \
                            social_matches(can[0], prof, label) and \
                            (v.form == "NUF" or not FOREIGN_HANDLE_RX.search(squash(can[1]))):
                        so_seen.add(can[0])
                        facts.append(Fact("social_profile", {"platform": platform, "url": can[0], "handle": can[1]},
                                          i.idx, 'script[type="application/ld+json"] sameAs',
                                          raw_window(i.page.html, u.split("://", 1)[-1], 30, 60) or u,
                                          qualifier=f"{platform}|{can[0]}"))

    # ---- careers page
    if v.site_url:
        best = None
        for i in ok:
            for u, txt in i.links:
                if not u.startswith(("http://", "https://")):
                    continue
                if not under_base(u, v.site_url, v.subpath, v.exclusive):
                    continue
                if restricted and not v.dominant and not v.subpath:
                    continue
                sp = urlsplit(u)
                if X.SKIP_EXT_RX.search(sp.path) or sp.path in ("", "/"):
                    continue
                short_txt = txt if len(txt) <= 40 else ""
                hay = unquote(sp.path) + " " + short_txt
                if CAREERS_RX.search(hay):
                    score = (0 if CAREERS_RX.search(short_txt) else 1, sp.path.count("/"), bool(sp.query), len(u), u)
                    if best is None or score < best[0]:
                        best = (score, u, i, txt)
        if best:
            _, u, i, txt = best
            href_span = raw_window(i.page.html, urlsplit(u).path or u, 40, 80) or f"{txt} -> {u}"
            facts.append(Fact("careers_page", u, i.idx, "a[href]", href_span))
    facts.sort(key=Fact.sort_key)
    return facts


def feed_url(v: Verdict) -> str | None:
    """RSS/Atom feed linked from the verified home page (comment feeds skipped)."""
    if not v.published or not v.infos or not v.site_url:
        return None
    if v.restricted and not v.dominant and not v.subpath:
        return None
    for u, t in v.infos[0].feeds:
        if re.search(r"comment|kommentar", u + " " + t, re.I):
            continue
        if u.startswith(("http://", "https://")) and under_base(u, v.site_url, v.subpath, v.exclusive):
            return u
    return None
