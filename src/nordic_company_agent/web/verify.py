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
  Group / foreign sites (audit of the 1,000-company run): a cross-domain redirect keeps the fetched path as the
      base (never the wider host root); a shared host needs a visible labelled org number in an owner context
      (JSON-LD/HTML-only hits refused); a registry redirect onto a multi-entity host without the name in the title
      is refused; sibling entities (same distinctive token + legal form) without the exact registered name refuse
      L2; ANY group / foreign / sister / ownership / country-office / same-brand-foreign-domain / «vi»-definition /
      foreign-UI signal makes the site ambiguous - L1 included, with no rescue through the company's own block
      (audit 5); a domain filed by a chain whose name carries the domain label (otta.spenst.no), or a host co-filed
      by an entity sharing the company's distinctive token (sandia.no), is never published; registry phones /
      addresses shared with other organisations (cache `registry_shared_contacts`) never corroborate and such
      phones are never published; the legal name needs its upper-case form token; c/o-company registry addresses
      need two corroborations; place / region words never align a brand or a social handle on their own.
  L2e own-domain registry e-mail lead: exact registered name in title/og:site_name, >= 2 corroborations, exclusive
      host, no other-entity signals (confidence 0.8).
  Facts: generic role mailboxes only (never a person's mailbox, even the registry e-mail; never invoice-scanning
  or country/region-qualified mailboxes of foreign group entities); phones normalised to E.164, staff lines (named person / personal mailbox next to the number,
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
CONFIDENCE = {"L1": 0.97, "L2": 0.85, "L2e": 0.8}
PUBLISHED_LEVELS = ("L1", "L2", "L2e")
OWNER_PAGE_KINDS = ("contact", "about", "terms", "privacy", "impressum")
# Country/region-qualified role mailboxes (info-uk@, sales-au@, reception-wa@) belong to foreign group entities.
REGION_LOCAL_RX = re.compile(r"(?:[-_.](?:uk|gb|au|in|it|de|se|dk|fi|us|usa|wa|nsw|qld|vic|nz|pl|fr|es|nl|be|at|ch|cz|"
                             r"ee|lv|lt|is|ie|ca|br|mx|sg|hk|cn|jp|kr|za|ae|qa|tr|emea|apac|latam|na)$|"
                             r"^(?:uk|gb|au|in|it|de|se|dk|fi|us|usa|nz|pl|fr|es|nl|emea|apac)[-_.])")


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
# Property-manager / community / hosting platforms: a page there is hosted for the company, it is never the
# company's own official website.
PLATFORM_DOMAINS = {"vibbo.no", "styrerommet.no", "styreweb.com", "boligsamarbeid.no", "minside.no", "borettslag.net",
                    "spond.com", "klubbenonline.no", "idrettenonline.no", "mynewsdesk.com", "linktr.ee", "proff.no",
                    "gulesider.no", "1881.no", "finn.no", "facebook.com", "instagram.com", "linkedin.com"}
# Personal / builder hosts a filed domain may redirect to: publish the filed domain, not the builder URL.
HOSTING_DOMAINS = {"wixsite.com", "chatgpt.site", "webnode.page", "godaddysites.com", "squarespace.com", "weebly.com",
                   "jimdosite.com", "blogspot.com", "wordpress.com", "netlify.app", "vercel.app", "github.io",
                   "mykajabi.com", "carrd.co", "framer.website", "webflow.io", "one.com", "site123.me", "mozello.com"}
HOMEISH_PATH_RX = re.compile(r"^/(?:(?:no|nb|nn|en|se|sv|da|de|fi|nb-no|no-nb|nn-no|en-gb|en-us|nor|norsk|english)/?)?"
                             r"(?:(?:index|default|home|hjem|forside|framside|start|startside|main|velkommen|welcome)"
                             r"(?:\.[a-z]{2,5})?/?)?$", re.I)
ERROR_TITLE_RX = re.compile(r"fant\s+ikke\s+siden|siden\s+finnes\s+ikke|page\s+not\s+found|not\s+found|\b404\b|"
                            r"\berror\b|\bfeil\b", re.I)
USELESS_TITLES = {"hjem", "home", "forside", "framside", "startside", "start", "velkommen", "welcome", "new page 1",
                  "my wordpress site", "untitled", "index", "default", "hjemmeside", "homepage", "main", "page"}
# Injected SEO spam on compromised sites ("Our Partner casaapostas.org") must never become a company description.
SPAM_DESC_RX = re.compile(r"(?:casino|apostas|bet(?:ting)?\b|slot|poker|gambl|kasino|viagra|cialis|porn|escort|"
                          r"payday|loan offer|bitcoin|forex|our partner|sponsored by|backlink)", re.I)
DOMAIN_TOKEN_RX = re.compile(r"(?<![\w@.-])((?:[\w-]+\.)+(?:com|net|org|no|info|biz|io|xyz|top|site|online|club|bet|se|dk|eu))\b", re.I)
# agency credits and chain boilerplate are never a company description (policy F)
DESC_CREDIT_RX = re.compile(r"web\s?design|nettside(?:r)?\s+(?:av|fra|levert)|levert\s+av|utviklet\s+av|powered\s+by|"
                            r"designet\s+av|laget\s+av|design\s*(?:og|&)\s*utvikling|webbyrå|web\s+agency", re.I)
DESC_CHAIN_RX = re.compile(r"interflora|\ben\s+del\s+av\b|\bkjeden\b|\bkjede\w*|franchise|\bmedlem\s+av\b|"
                           r"\bpart\s+of\b|\bmember\s+of\b|allianse\w*|nettalliansen", re.I)


def _spammy(text: str, site_domain: str | None) -> bool:
    if SPAM_RX.search(text) or SPAM_DESC_RX.search(text):
        return True
    for m in DOMAIN_TOKEN_RX.finditer(text):
        if X.reg_domain(m.group(1)) != (site_domain or ""):
            return True
    return False


PLACEHOLDER_DESC_RX = re.compile(r"just another wordpress site|another wordpress site|lorem ipsum|hipster ipsum|"
                                 r"^description$|^beskrivelse$|my wordpress blog|site description|^test$|^home$|"
                                 # theme / page-builder demo copy (Avada, Elementor, Divi, ThemeIsle starter sites)
                                 r"\bavada\b|prebuilt website|pre-built website|website builder|\belementor\b|\bdivi\b|"
                                 r"themeisle|demosites|starter site|readymade godard|\bipsum\b", re.I)
CAREERS_RX = re.compile(r"(?<![a-zæøå])(karriere|careers?|jobb(?:e)?[- ]hos(?:[- ]oss)?|ledige[- ]stillinger|"
                        r"ledig[- ]stilling|stillinger|stilling|vacanc(?:y|ies)|jobs|rekruttering|join[- ]us|"
                        r"work[- ]with[- ]us)(?![a-zæøå])", re.I)
NOT_CAREERS_PATH_RX = re.compile(r"/(?:om-oss|om|about(?:-us)?|kontakt(?:-oss)?|contact)/?$", re.I)


def _brand_dash_other(d: str, prof: NameProfile) -> bool:
    """'Norefjell - Nettalliansen': a short '<brand> - <another name>' line, not a description."""
    if len(d) > 90 or re.search(r"[.!?]\s", d):
        return False
    parts = [p.strip() for p in re.split(r"\s[-–—|]\s|\s\|\s?", d) if p.strip()]
    if len(parts) != 2:
        return False
    for p in parts:
        ws = p.split()
        if 1 <= len(ws) <= 3 and all(w[:1].isupper() or w[:1].isdigit() for w in ws) and \
                not any(token_in(t, p) for t in prof.required):
            return True
    return False
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
    "frontdesk", "fotball", "handball", "haandball", "handballgruppa", "golf", "ski", "tennis", "idrett",
}
INVOICE_LOCALS = {"faktura", "fakturamottak", "fakturaer", "invoice", "invoices", "regning", "regninger", "bilag",
                  "ehf", "fakturering", "billing", "inngaaende", "inngaende", "scan", "skann", "kreditor",
                  "noreply", "no-reply", "donotreply", "do-not-reply", "postmaster", "webmaster", "abuse", "privacy",
                  "personvern", "gdpr", "dpo"}

COUNTRY_WORDS = {"norge", "norway", "norden", "nordic", "nordics", "scandinavia", "skandinavia", "scandinavian"}
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
    # country words distinguish a national company from its foreign group ("TODAY MOBILITY NORWAY AS" is not
    # "Today Mobility"): they are required name tokens
    for w in re.findall(r"[a-zæøå]+", (name or "").lower()):
        if w in COUNTRY_WORDS and w not in required and w not in optional:
            required.append(w)
            if w not in core:
                core.append(w)
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
    utf8: bool = True               # raw body is valid UTF-8 (else spans must be pure ASCII to stay byte-verbatim)

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
    entities: list[dict] = field(default_factory=list)       # legal entities named on the page
    owner_entities: list[dict] = field(default_factory=list) # ... in footer / copyright / contact / imprint text
    foreign_regs: list[str] = field(default_factory=list)    # foreign registration markers (UK company no, CVR ...)
    name_block: dict | None = None                           # block naming the exact legal name
    exact_named: bool = False                                # exact legal name (case-sensitive form) in the text
    owners: list[dict] = field(default_factory=list)         # entities named in owner statements (© lines, JSON-LD)
    named_orgnrs: list[tuple[str, str]] = field(default_factory=list)   # (name text before a labelled orgnr, orgnr)
    site_links: list[tuple[str, str]] = field(default_factory=list)     # "<X> nettside/website" anchors
    tel_anchors: list[tuple[str, str]] = field(default_factory=list)    # (tel: href number, visible anchor text)
    mailto_anchors: list[tuple[str, str]] = field(default_factory=list)  # (mailto: address, visible anchor text)
    ownership: list[str] = field(default_factory=list)                  # ownership / group statements
    country_list: bool = False                                          # list of country offices
    all_foreign_regs: list[str] = field(default_factory=list)           # foreign registration numbers, any text
    vis_others: list[str] = field(default_factory=list)                 # other valid org numbers in visible text
    third_party_orgnrs: list[str] = field(default_factory=list)         # ... in a third-party-service context
    acquisitions: list[str] = field(default_factory=list)               # acquisition / merger statements
    formless_owners: list[str] = field(default_factory=list)            # '© 2026 Questback'
    template: list[str] = field(default_factory=list)                   # empty-template / placeholder markers
    we_defs: list[tuple[str, list[str]]] = field(default_factory=list)  # «vi»/«oss» definitions: (snippet, names)

    def head_text(self) -> str:
        """Site-declared text outside the body: title, meta description/author, og:*, JSON-LD names/contacts."""
        parts = [self.title] + [self.meta.get(k, "") for k in ("description", "og:description", "og:site_name",
                                                               "og:title", "author", "twitter:description")]
        for o in self.orgs:
            for k in ("name", "legalName", "telephone", "email"):
                if isinstance(o.get(k), str):
                    parts.append(o[k])
        return " | ".join(p for p in parts if p)

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
    """VERBATIM fragment of the page source around `needle` (an org number also in its spaced / dotted
    renderings: '932 231 999'), at most 300 chars."""
    if not html or not needle:
        return None
    if re.fullmatch(r"\d{9}", needle):
        m = X.target_rx(needle).search(html)
        if not m:
            return None
        i, j = m.start(), m.end()
    else:
        i = html.lower().find(needle.lower())
        if i < 0:
            return None
        j = i + len(needle)
    a, b = max(0, i - before), min(len(html), j + after)
    if b - a > 300:
        return _raw_slice(html, i, j, 300)
    return html[a:b]


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
                 "twitter:description", "generator", "author") and k not in meta:
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


ERROR_URL_RX = re.compile(r"[?&](?:error=(?:true|1)|message=(?:login_required|access_denied|unauthori[sz]ed)|"
                          r"status=[45]\d\d|returnurl=|redirect_uri=)|/(?:login|logg-inn|signin|sign-in|error)(?:[/?#]|$)", re.I)


def _blocking(x: dict, page: Page, title: str, text: str) -> list[str]:
    out: list[str] = []
    tl = x.get("text_len", len(text))
    if ERROR_URL_RX.search(page.final_url or ""):
        out.append("error_or_login_page")
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


_LABELLED_RX = re.compile(r"(?:org(?:anisasjons)?[\s.\-]*(?:n(?:umme)?r|no|number|num)\b\.?|orgnr|organisasjonsnummer|"
                          r"organizational number|organisation number|organization number|reg(?:istration)?\.?\s*(?:no|nr|number)"
                          r"\b\.?|foretaksnr|mva(?:[- ]?nr)?|vat(?: no)?)[\s:./#]*(?:NO\s?)?", re.I)
SITE_LINK_RX = re.compile(r"\b(?:nettside|nettsider|hjemmeside|nettsted|website|web site|homepage)\b", re.I)


def _labelled_orgnrs(text: str) -> list[tuple[re.Match, str]]:
    """Valid org numbers printed with an org-number label (or NO ... MVA wrapper) in text."""
    out = []
    for m in X.ORGNR_RX.finditer(text):
        d = re.sub(r"\D", "", m.group("num"))
        if not is_valid(d):
            continue
        before = text[max(0, m.start() - 45):m.start()]
        if X.ORG_KEYWORD_RX.search(before) or m.group("pre") or m.group("mva") or re.search(r"\(\s*$", before[-3:]):
            out.append((m, d))
    return out


def _named_orgnrs(text: str) -> list[tuple[str, str]]:
    """(text between the previous org number and this labelled org number, orgnr): the name a page prints for
    each organisation number it shows (used to recognise a site titled with another organisation's name)."""
    out = []
    prev_end = 0
    for m, d in _labelled_orgnrs(text):
        lab = _LABELLED_RX.search(text, max(prev_end, m.start() - 160), m.start())
        cut = lab.start() if lab else m.start()
        out.append((text[max(prev_end, m.start() - 160):cut], d))
        prev_end = m.end()
    return out


ADDRESS_TAIL_RX = re.compile(r"[\s,;:–\-]*(?:[A-ZÆØÅ][\wæøåÆØÅ.\-]*(?:\s[\wæøåÆØÅ.\-]+){0,3}\s\d{1,4}\s?[A-Za-z]?[\s,]*)?"
                             r"(?:(?:NO-)?\d{4}\s+[A-ZÆØÅ][\wæøåÆØÅ\-]+(?:\s[A-ZÆØÅ][\wæøåÆØÅ\-]+)?)?[\s,;:–\-]*")
_COPY_RX = re.compile(r"©|&copy;|\(c\)|copyright|opphavsrett|alle rettigheter|all rights reserved|alt innhold", re.I)


def _owner_statements(text: str, footer: str, orgs: list[dict]) -> list[dict]:
    """Legal entities a page names as its owner: '© 2026 X AS', 'X AS © 2025', 'Copyright X AS', JSON-LD
    legalName - each with the org number printed right after it (if any)."""
    out: list[dict] = []
    for src in (footer or "", text or ""):
        all_ents = [e for e in X.legal_entities(src, prefix_forms=True) if not PROVIDER_RX.search(e["key"])
                    and not X.CREDIT_RX.search(src[max(0, e["start"] - 30):e["start"]])]
        for cm in _COPY_RX.finditer(src):
            ents = [e for e in all_ents if cm.start() - 70 <= e["end"] and e["start"] <= cm.end() + 90 and
                    # same footer segment as the (c) sign: '© Prioriss Privacy Policy | Mint Media AS' names no owner
                    not re.search(r"\s[|•]\s|\s\|", src[min(e["end"], cm.end()):max(e["start"], cm.start())])]
            if not ents:
                continue
            e = min(ents, key=lambda e: min(abs(e["start"] - cm.start()), abs(e["end"] - cm.start())))
            after = src[e["end"]: e["end"] + 60]
            om = next((d for mm, d in _labelled_orgnrs(after)), None)
            if not any(o["key"] == e["key"] for o in out):
                out.append({"name": e["name"], "key": e["key"], "words": e["words"], "foreign": e["foreign"], "orgnr": om})
    for o in orgs:
        ln = o.get("legalName")
        if isinstance(ln, str):
            ln = re.split(r"\s*[,(]\s*(?:org|\d)|\s+(?:org\.?\s*nr|\d{3}\s?\d{3}\s?\d{3})", ln, maxsplit=1, flags=re.I)[0]
        if isinstance(ln, str) and ORGFORM_WORD_RX.search(ln):
            k = X.entity_key(ln)
            if k and not any(x["key"] == k for x in out):
                ids = _jsonld_ids(o)
                out.append({"name": ln.strip(), "key": k, "words": X.entity_words(ln), "foreign": X.legal_form_is_foreign(ln),
                            "orgnr": next(iter(ids)) if len(ids) == 1 else None})
    return out[:6]


# --------------------------------------------------------------------------- group / foreign-parent signals
_GROUP_WORD = (r"(?:Group|GROUP|Gruppen|GRUPPEN|Groupe|Gruppe|konsern\w*|Konsern\w*|Holding|HOLDING|Industries|"
               r"Industrier|kjede\w*|Kjede\w*|familien|AB|ASA|AS|Oy|Oyj|ApS|A/S|GmbH|Ltd|Inc|LLC|plc|B\.?V\.?|N\.?V\.?)")
ACQUISITION_RX = re.compile(
    r"\b(?:er\s+blitt|har\s+blitt|nå|er\s+nå|blitt)\s+(?:en\s+)?del\s+av\b|\bkjøpt\s+opp\s+av\b|\bovertatt\s+av\b|"
    r"\bhar\s+slått\s+seg\s+sammen\s+med\b|\bslått\s+sammen\s+med\b|\bfusjonert\s+med\b|\bfusjonerer\s+med\b|"
    r"\b(?:heleid|eid|eies)\s+av\b|\bsiden\s+(?:driftes|drives|eies)\s+av\b|\bacquired\s+by\b|\bmerged\s+with\b|"
    r"\bis\s+now\s+part\s+of\b|\b(?<!er\s)(?<!vært\s)en\s+del\s+av\b", re.I)
# subjects of "eies av" that are not the company ("Programmet eies av Kommunal- og distriktsdepartementet")
_NOT_COMPANY_SUBJECTS = {"programmet", "prosjektet", "nettverket", "initiativet", "ordningen", "konseptet", "kurset",
                         "arrangementet", "programmene", "prosjektene", "tiltaket", "satsingen"}
_NOT_OWNER_OBJECTS = {"norge", "norway", "norden", "europa", "europe", "skandinavia", "scandinavia", "verden", "world",
                      "landet", "byen", "oss", "vår", "vårt", "våre", "deg", "dere", "hverdagen", "livet", "teamet",
                      "familien", "laget", "prosessen", "prosjektet", "løsningen", "oslo", "bergen", "trondheim",
                      "stavanger", "tromsø", "kristiansand", "drammen", "fredrikstad", "the", "a", "an", "our", "your"}


def _acquisition_phrases(text: str, prof: NameProfile) -> list[str]:
    """Acquisition / merger / ownership statements naming another organisation ("En del av Questback",
    "er blitt en del av X", "kjøpt opp av X", "har slått seg sammen med X"); persons as owners do not count."""
    out = []
    for m in ACQUISITION_RX.finditer(text or ""):
        obj = text[m.end():m.end() + 60].strip(" :,-–")
        mo = re.match(r"(?:nå\s+)?([A-ZÆØÅ][\wÆØÅæøå&'.\-]*)((?:\s+[A-ZÆØÅ0-9][\wÆØÅæøå&'.\-]*){0,4})", obj)
        if not mo:
            continue
        words = (mo.group(1) + mo.group(2)).split()
        if words[0].lower() in _NOT_OWNER_OBJECTS:
            continue
        if prof.required and all(token_in(t, " ".join(words)) for t in prof.required):
            continue                                   # the object is this company
        own = set(X.entity_words(prof.name)) if getattr(prof, "name", None) else set(prof.required)
        core = re.sub(r"-?familien$|-?familie$", "", " ".join(words), flags=re.I)
        cw = [w for w in re.findall(r"[a-z0-9]+", fold(core))]
        if cw and own and set(cw) <= own:
            continue                                   # "Som en del av Os Caravan-familien": the company's own brand
        before = re.findall(r"[\wÆØÅæøå]+", text[max(0, m.start() - 25):m.start()])
        if before and before[-1].lower() in _NOT_COMPANY_SUBJECTS and re.search(r"eies|eid|drives|driftes", m.group(0),
                                                                                  re.I):
            continue
        person = 2 <= len(words) <= 4 and not COMPANY_WORD_RX.search(" ".join(words)) and \
            not re.search(_GROUP_WORD, " ".join(words)) and \
            all((w[:1].isupper() and w[1:].islower()) or re.fullmatch(r"[A-ZÆØÅ]\.?", w) for w in words) and \
            sum(1 for w in words if len(w.strip(".")) > 1) >= 2
        if person and re.search(r"eies|eid|drives|driftes", m.group(0), re.I):
            continue                                   # "eies og drives av Susanne J Kvalvik" - owners are persons
        out.append(_ws(text[max(0, m.start() - 40):m.end() + 60]))
    return out[:6]


_COPY_OWNER_RX = re.compile(r"(?:(?:©|&copy;|\([cC]\)|[Cc]opyright|COPYRIGHT)\s*(?:\d{4}\s*(?:[-–]\s*\d{4}\s*)?)?)+"
                            r"[,.:\-–]?\s*([A-ZÆØÅ][\wÆØÅæøå&'.\-]+(?:\s+[A-ZÆØÅ0-9][\wÆØÅæøå&'.\-]*){0,3})")
_COPY_STOP = {"alle", "all", "rights", "rettigheter", "reservert", "reserved", "copyright", "kopirett", "opphavsrett",
              "made", "with", "med", "site", "website", "nettsiden", "siden",
              "by", "av", "design", "utviklet", "levert", "nettside", "nettsted", "webdesign", "powered", "laget",
              "innhold", "innholdet", "alt", "content", "personvern", "personvernerklæring", "personvernerklaering",
              "cookies", "cookie", "vilkår", "privacy", "policy", "terms", "gdpr", "tilgjengelighetserklæring",
              "tilgjengelighet", "org", "org.nr", "org.nr.", "orgnr", "organisasjonsnummer", "denne", "dette", "disse",
              "web", "nettløsning", "nettbutikk", "effektiv", "bottom", "top", "page", "lisensavtale", "kontakt",
              "contact", "home", "hjem", "om", "about", "meny", "menu", "sitemap", "nyhetsbrev", "newsletter", "følg",
              "follow", "telefon", "tlf", "e-post", "epost", "email", "adresse", "address", "the", "this", "our",
              "vår", "vårt", "våre", "er", "is", "og", "and", "for", "til", "to", "drevet", "driftet", "levert",
              "kundeservice", "kundesenter", "sosiale", "medier", "social", "media", "customer", "service", "support",
              "act", "law", "lov", "loven", "åndsverkloven", "notice", "info", "information"}
# a © line crediting a product, photographer or theme names no site owner ("© 2026 ArcHub Theme")
_COPY_CREDIT = {"theme", "tema", "template", "mal", "wordpress", "shopify", "wix", "squarespace", "webflow",
                "photography", "photo", "photos", "foto", "fotograf", "fotografi", "bilder", "images", "illustrasjon",
                "bisnode", "soliditet", "creditsafe", "experian", "trustpilot", "proff", "gaselle"}   # rating badges


def _formless_owners(text: str) -> list[str]:
    """'© 2026 Questback' - a © owner written without a legal form."""
    out = []
    for m in _COPY_OWNER_RX.finditer(text or ""):
        if re.search(r"\b(?:the|a|this|our|any|of)\s*$", text[max(0, m.start() - 6):m.start()], re.I):
            continue                                   # "the Copyright Act" is prose, not a © line
        raw_words = m.group(1).split()
        if not raw_words or raw_words[0].lower().strip(".,:") in _COPY_STOP:
            continue                                   # '© All rights reserved ...' names no owner
        if any(w.lower().strip(".,:") in _COPY_CREDIT for w in raw_words):
            continue
        words = []
        for w in raw_words:
            if w.lower().strip(".,:") in _COPY_STOP:
                break
            words.append(w)
            if re.fullmatch(r"(?:Group|GROUP|Gruppen|GRUPPEN|Groupe|Gruppe|Konsern\w*|Holding|HOLDING)[.,]?", w):
                break                                  # '© 2026 PHM Group Ventilasjonsrens ...' (menu glued on)
        if not words or ORGFORM_WORD_RX.search(" ".join(raw_words)) or \
                X.CREDIT_RX.search(text[max(0, m.start() - 30):m.end()]):
            continue
        out.append(" ".join(words))
    return sorted(set(out))[:4]


# who "vi"/"oss" is: '(Sandia Megling AS, Sandia Holding AS, ...) «Sandia», «vi» eller «oss»',
# 'eSmiley A/S er heretter benevnt «eSmiley», «vi»', 'X AS (hereinafter "we")'
WE_DEF_RX = re.compile(r"[«\"“'‘]\s*(?:vi|oss|vår|vårt|våre|we|us|our)\s*[»\"”'’]|\bheretter\s+(?:kalt|benevnt|omtalt\s+som)\b|"
                       r"\bhereinafter\s+(?:referred\s+to\s+as|called)\b|\bheretter\s*[«\"“]", re.I)
_DEF_ENTITY_RX = re.compile(r"(?<![\wÆØÅæøå])([A-ZÆØÅa-zæøå][\wÆØÅæøå&.\-]*(?:\s+[A-ZÆØÅ0-9][\wÆØÅæøå&.\-]*){0,4})\s+"
                            r"(AS|ASA|A/S|AB|ApS|Oy|Oyj|GmbH|Ltd\.?|Limited|SA|BA|ANS|DA|NUF|IKS|KF|AG|B\.V\.|BV|N\.V\.|"
                            r"Inc\.?|LLC|plc|SE)(?![\wÆØÅæøå/])")


# Swedish / Danish interface words that a Norwegian company's own site does not use ("Sök i nyheter", "Läs mer")
FOREIGN_UI_RX = re.compile(r"(?<![\wÆØÅæøåÄÖäö])(?:Sök|SÖK|Läs(?:\s+mer)?|LÄS|Kontakta\s+oss|Startsida|Varukorg|Kassan|"
                           r"Logga\s+in|Integritetspolicy|Om\s+företaget|Förbrukning|Våra\s+\w+|Søg|Læs(?:\s+mere?)?|"
                           r"Kontakt\s+os|Om\s+os|Privatlivspolitik)(?![\wÆØÅæøåÄÖäö])")


def _we_definitions(text: str) -> list[tuple[str, list[str]]]:
    """Definitions of «vi» / «oss» / 'heretter kalt': the legal entities named in the same sentence just before."""
    out: list[tuple[str, list[str]]] = []
    seen: set[int] = set()
    for m in WE_DEF_RX.finditer(text or ""):
        win = text[max(0, m.start() - 260):m.start()]
        cut = max(win.rfind(". "), win.rfind("\n"), win.rfind(" | "))
        sent = win[cut + 1:] if cut >= 0 else win
        start = m.start() - len(sent)
        if start in seen:
            continue
        seen.add(start)
        names = []
        for e in _DEF_ENTITY_RX.finditer(sent):
            body = e.group(1).split()
            while body and not any(c.isupper() for c in body[0]):
                body = body[1:]                        # 'er Ekom AS' -> 'Ekom AS' (eSmiley keeps its capital S)
            nm = _ws(" ".join(body) + " " + e.group(2)) if body else ""
            if nm and nm not in names:
                names.append(nm)
        if names:
            out.append((_ws(text[max(0, start):m.end() + 40])[:240], names))
    return out[:4]


THIRD_PARTY_RX = re.compile(r"databehandler|data\s*processor|underleverandør|leverandør\s+av|levert\s+av|utviklet\s+av|"
                            r"design(?:et)?\s+av|webdesign|web\s*design|hosting|regnskapsfører|revisor|inkasso|"
                            r"betalingsløsning|vipps|klarna|\bnets\b|svea|bambora|qliro|\bdibs\b", re.I)
TEMPLATE_RX = re.compile(
    r"T[ée]l[ée]phone\s*:\s*\+\s*0\b|Adresse\s+du\s+si[èe]ge\s*:\s*(?=$|\s*[A-ZÉ][\w ']{0,25}:)|"
    r"\b(?:E-?mail|E-post|Email)\s*:\s*(?=$|\|\s|\s*(?:T[ée]l[ée]?phone|Telefon|Adresse|Address)\s*:)|lorem\s+ipsum|"
    r"(?:registreringsdato|stiftelsesdato|registration\s+date|date\s+of\s+incorporation)\s*:\s*"
    r"(?:0?[1-9]|1[0-2])/(?:1[3-9]|2\d|3[01])/\d{4}", re.I)


OWNERSHIP_RX = re.compile(
    r"(?P<phrase>\b(?:eies|eid|drives|driftes|ledes)\s+(?:og\s+drives\s+)?av\b|"
    r"\ber\s+(?:en\s+)?(?:del|medlem)\s+av\b|\bdel\s+av\s+konsernet\b|\bi\s+[\wÆØÅæøå-]+-?konsernet\b|"
    r"\bdatterselskap\s+(?:av|til|under|i)\b|\b(?:wholly[- ]owned\s+)?subsidiary\s+of\b|\bmember\s+of\b|"
    r"\bpart\s+of\b|\btilhører\s+(?:nå\s+)?|\bheleid\s+av\b|\bowned\s+by\b|\boperated\s+by\b|"
    r"\bgjennom\s+sin\s+norske\s+filial\b|\bnorsk\s+filial\s+av\b|\bfilial\s+av\b|"
    r"\bsiden\s+(?:driftes|drives|eies)\s+av\b|\bet\s+selskap\s+i\b)", re.I)
_STRONG_PHRASE_RX = re.compile(r"filial|siden\s+(?:driftes|drives|eies)|eies\s+av|heleid|owned\s+by|subsidiary|"
                               r"datterselskap|operated\s+by", re.I)
COUNTRY_RX = re.compile(r"\b(Norge|Norway|Sverige|Sweden|Danmark|Denmark|Finland|Suomi|Tyskland|Germany|Deutschland|"
                        r"Spania|Spain|España|Polen|Poland|Polska|Storbritannia|United Kingdom|England|USA|Nederland|"
                        r"Netherlands|Frankrike|France|Italia|Italy|Island|Iceland|Estland|Estonia|Litauen|Lithuania|"
                        r"Latvia|Latvija|Kina|China|Belgia|Belgium|Østerrike|Austria|Sveits|Switzerland)\b")
FOREIGN_CONTACT_RX = re.compile(r"(?:\+|00)\s?(?:46|45|49|34|358|48|44|1|31|33|39|354|372|370|371|86|32|43|41)[\s\-]?\d|"
                                r"@[\w.-]+\.(?:se|dk|de|fi|es|pl|uk|nl|fr|it|is|ee|lt|lv|cn|be|at|ch)\b", re.I)
FOREIGN_TLDS = {"se", "dk", "fi", "de", "uk", "pl", "nl", "fr", "es", "it", "ee", "lt", "lv", "is", "at", "ch", "be",
                "us", "cz", "hu", "pt", "ie", "eu"}
FOREIGN_LOCALES = {"sv", "se", "sv-se", "da", "dk", "da-dk", "fi", "fi-fi", "de", "de-de", "pl", "nl", "nl-nl", "nl-be",
                   "fr", "es", "it", "cs", "hu", "pt", "et", "lt", "lv", "is", "en-gb", "en-us", "uk", "us", "gb"}


def _ownership_phrases(text: str) -> list[str]:
    """Ownership statements naming a group/parent: 'Vi tilhører nå DENSIQ Group', 'er en del av Latour Industries AB',
    'eier merkenavnet ... gjennom sin norske filial', 'Siden driftes av X'."""
    out = []
    for m in OWNERSHIP_RX.finditer(text or ""):
        if re.search(r"(?:levert|utviklet|designet|laget|hostet|produsert)\s+(?:og\s+)?$|nettsiden\s+er\s+(?:levert|utviklet)",
                     text[max(0, m.start() - 40):m.start()], re.I):
            continue                                   # 'Denne nettsiden er levert og driftes av <web agency>' 
        obj = text[m.end():m.end() + 70]
        filial = bool(re.search(r"filial|siden\s+(?:driftes|drives|eies)", m.group("phrase"), re.I))
        if filial or re.search(r"^\s*(?:the\s+|nå\s+)?[A-ZÆØÅ][\wÆØÅæøå&'.\- ]{0,50}?\b" + _GROUP_WORD + r"\b", obj) or \
                re.search(r"konsernet", m.group("phrase"), re.I):
            out.append(_ws(text[max(0, m.start() - 40):m.end() + 70]))
    return out[:6]


def _country_office_list(text: str) -> bool:
    """>= 3 countries within 250 chars together with foreign phone numbers / e-mail domains: a list of country offices."""
    ms = list(COUNTRY_RX.finditer(text or ""))
    for j, m in enumerate(ms):
        win_end = m.start() + 250
        names = {mm.group(1).lower() for mm in ms[j:] if mm.start() <= win_end}
        if len(names) >= 3 and len(FOREIGN_CONTACT_RX.findall(text[m.start():win_end])) >= 2:
            return True
    return False


def _visible_text(html: str) -> str:
    """Visible text only: scripts, styles, templates, comments and hidden elements removed."""
    tree = LexborHTMLParser(html)
    for sel in ("script", "style", "noscript", "template", "svg", "[hidden]",
                "[style*='display:none']", "[style*='display: none']", "[style*='visibility:hidden']",
                "[style*='visibility: hidden']", ".sr-only", ".visually-hidden", ".screen-reader-text"):
        for n in tree.css(sel):
            n.decompose()
    body = tree.body or tree.root
    return re.sub(r"\s+", " ", body.text(separator=" ") if body else "").strip()


def analyse_page(page: Page, target: str, name: str = "", idx: int = 0) -> PageInfo:
    html = X.strip_invisible(page.html or "")          # zero-width / stega characters can hide an org-number list
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
    own_words = X.entity_words(name) if name else []
    own_core = {w for w in own_words if len(w) >= 3 and not w.isdigit()}
    own_foreign = X.legal_form_is_foreign(name) if name else False
    _nrx0 = X.legal_name_rx(name) if name else None
    # the exact legal name printed right before the number is its label ("Leo Bemanning AS · 934 354 710")
    name_label_rx = re.compile("(?:" + _nrx0.pattern + r")[\s,·•|–\-:(]{0,6}$") if _nrx0 else None
    vis = _visible_text(html)
    prof_p = name_profile(name or "")
    vis_others: set[str] = set()
    third_party: set[str] = set()
    for m_o in X.ORGNR_RX.finditer(vis):
        d_o = re.sub(r"\D", "", m_o.group("num"))
        if d_o == target or not is_valid(d_o):
            continue
        ctx_o = re.split(r"\n|\s\|\s", vis[max(0, m_o.start() - 80):m_o.start()])[-1]
        (third_party if THIRD_PARTY_RX.search(ctx_o) else vis_others).add(d_o)
    third_party -= vis_others                          # printed plainly elsewhere too: not only a third party
    labelled_others = [(m.start(), m.end(), d) for m, d in _labelled_orgnrs(vis) if d != target and d not in third_party]
    text_for_hits = vis
    foreign_pos = [(m_f.start(), m_f.end()) for m_f in X.FOREIGN_REG_RX.finditer(vis)]
    for m in trx.finditer(text_for_hits):
        text = text_for_hits                             # (span/flags computed on visible text)
        before = text[max(0, m.start() - 45):m.start()]
        after = text[m.end():m.end() + 6]
        kw = bool(X.ORG_KEYWORD_RX.search(before)) or bool(re.search(r"NO[\s.\-]?$", before)) or \
            bool(re.match(r"[\s.\-]?MVA", after, re.I)) or \
            bool(re.search(r"\b(?:org|org\.?\s*n[or]|reg|vat)\.?\s*[:#.]*\s*$", before, re.I)) or \
            bool(re.search(r"(?:selskaps)?registrerings?(?:nr|nummer)\.?\s*[:#.]*\s*$|\behf\s*[:.]?\s*(?:0192:|9908:)?\s*$|"
                           r"foretaksregisteret\s*[:#.]*\s*$", before, re.I)) or \
            bool(re.search(r"(?:organi[sz]ation|company|business|registration)\s+(?:reg(?:istration)?\.?\s+)?"
                           r"(?:number|no|nr)\.?\s*[:#.]*\s*$", before, re.I)) or \
            bool(name_label_rx and name_label_rx.search(text[max(0, m.start() - 90):m.start()]))
        phrase = re.split(r"\s[|·•]\s|\s[–—-]\s|\.\s|\n", text[max(0, m.start() - 80):m.start()])[-1]
        cm = list(X.CREDIT_RX.finditer(phrase))
        credit = bool(cm) and not (_nrx0 and _nrx0.search(phrase[cm[-1].end():]))
        tp = list(THIRD_PARTY_RX.finditer(text[max(0, m.start() - 80):m.start()]))
        if tp and not (_nrx0 and _nrx0.search(text[max(0, m.start() - 80):m.start()][tp[-1].end():])):
            credit = True                              # the number sits in a third-party-service context
        # `list`: another labelled org number within 150 chars (a list of group companies / processors)
        lst = any(abs(s0 - m.start()) <= 150 or abs(e0 - m.end()) <= 150 for s0, e0, _ in labelled_others)
        # ... or a foreign VAT / registration id within 150 chars (an invoicing list of group entities)
        lst = lst or any((m.start() - 150 <= e0 <= m.start()) or (m.end() <= s0 <= m.end() + 150)
                         for s0, e0 in foreign_pos)
        # `misnamed`: the legal name printed right before the number (within 80 chars) is another entity
        win0 = max(0, m.start() - 180)
        sp0 = text.find(" ", win0, m.start())
        win0 = sp0 + 1 if (win0 > 0 and sp0 != -1) else win0           # start at a word boundary
        seg = text[win0:m.start()]
        prev = [e for e in X.legal_entities(seg, prefix_forms=True) if e["end"] >= len(seg) - 80]
        misnamed = None
        if prev and own_words:
            e = max(prev, key=lambda e: e["end"])
            # another entity's name, sharing no name token with this company (name variants / typos are not)
            if not X.is_target_entity(e, own_words, own_foreign) and not (set(e["words"]) & set(own_core)):
                misnamed = e["name"]
        hits.append({"src": "text", "kw": kw, "credit": credit, "list": lst, "misnamed": misnamed,
                     "span": _window(text, m.start(), m.end()), "locator": "text",
                     "pre": text[max(0, m.start() - 220):m.end()]})
    text = X.plain_text(html)
    if target in x.get("brreg_links", []):
        mm = re.search(r"""href\s*=\s*["']([^"']*(?:brreg\.no|proff\.no|purehelp\.no|forvalt\.no)[^"']*?"""
                       + target + r"""[^"']*)["']""", html, re.I)
        span = raw_window(html, mm.group(0)) if mm else raw_window(html, target)
        hits.append({"src": "registry_link", "kw": True, "credit": False, "list": False, "misnamed": None,
                     "span": span or target,
                     "locator": "a[href*=brreg]"})
    if target in ids:
        hits.append({"src": "jsonld", "kw": True, "credit": False, "list": False, "misnamed": None,
                     "span": raw_window(html, target) or target, "locator": 'script[type="application/ld+json"]'})
    if not hits and trx.search(html):
        mm = trx.search(html)
        hits.append({"src": "html", "kw": bool(X.ORG_KEYWORD_RX.search(html[max(0, mm.start() - 60):mm.start()])),
                     "credit": False, "list": False, "misnamed": None, "span": _clip(html[max(0, mm.start() - 120):mm.end() + 60]), "locator": "html"})
    others = (set(x.get("other_orgnrs_kw", [])) | set(x.get("other_orgnrs_text", [])) |
              set(x.get("brreg_links", [])) | ids) - {target} - third_party
    block = X.orgnr_block(html, target, legal_name=name or None) if any(h["src"] == "text" for h in hits) else None
    entities = X.legal_entities(text)
    owner_text = " | ".join([x.get("footer_excerpt", ""), " | ".join(x.get("copyright", []) or [])] +
                            ([text] if page.kind in ("contact", "impressum", "about") else []))
    owner_entities = [e for e in X.legal_entities(owner_text) if not PROVIDER_RX.search(e["key"])]
    owners = _owner_statements(text, x.get("footer_excerpt", ""), orgs)
    named_orgnrs = _named_orgnrs(text)
    site_links = [(u, t) for u, t in links if SITE_LINK_RX.search(t)]
    nrx = X.legal_name_rx(name) if name else None
    exact_named = bool(nrx and (nrx.search(text) or nrx.search(title)))
    nblock = X.name_block(html, name, target) if (nrx and nrx.search(text)) else None
    return PageInfo(idx=idx, page=page, x=x, text=text, title=title, meta=meta, lang=lang,
                    blocking=_blocking(x, page, title, text), hits=hits, others=others, block=block, orgs=orgs,
                    feeds=feeds, links=links, h1=h1, entities=entities, owner_entities=owner_entities,
                    foreign_regs=X.foreign_registrations(owner_text),
                    name_block=nblock, exact_named=exact_named, owners=owners, named_orgnrs=named_orgnrs,
                    site_links=site_links,
                    tel_anchors=[tuple(t) for t in x.get("tel_anchors", [])],
                    mailto_anchors=[(unquote((a.attributes.get("href") or "")[7:].split("?")[0]).strip().lower(),
                                     _ws(a.text(separator=" "))) for a in tree.css('a[href^="mailto:"]')],
                    ownership=_ownership_phrases(text), country_list=_country_office_list(text),
                    all_foreign_regs=X.foreign_registrations(text), vis_others=sorted(vis_others),
                    third_party_orgnrs=sorted(third_party), acquisitions=_acquisition_phrases(vis, prof_p),
                    formless_owners=_formless_owners(vis),
                    template=sorted({_ws(m.group(0))[:40] for m in TEMPLATE_RX.finditer(vis)}),
                    we_defs=_we_definitions(vis))


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
    # After a redirect to another registrable domain the host root was never fetched (axflow.no ->
    # axflow.com/nb-no/: the root is the foreign group's site), so any non-root final path, locale prefixes
    # included, is the base.
    cross = bool(start_dom) and start_dom != final_dom
    if cross and path not in ("", "/") and not re.fullmatch(r"/(?:index|default)\.[a-z]{2,5}", path, re.I):
        final_sub = True
    subpath = start_sub or (final_sub and start_dom != final_dom)
    # never publish a URL wider than the filed one: a filed language path (todaymobility.com/no/) is kept
    if not subpath and start_path not in ("", "/") and not re.fullmatch(r"/(?:index|default)\.[a-z]{2,5}", start_path, re.I) \
            and (st.hostname or "").lower().removeprefix("www.") == host.removeprefix("www.") \
            and path.lower().startswith(start_path.rstrip("/").lower()):
        return f"{scheme}://{host}{start_path}", True
    if not subpath:
        return f"{scheme}://{host}/", False
    base = path
    if re.search(r"/[^/]+\.(?:html?|php|aspx?|jsp)$", base, re.I):
        base = base.rsplit("/", 1)[0] + "/"
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


def address_key(postcode: str | None, line: str | None) -> str | None:
    """Key of a registry street address ('0278|karenslyst allé 2'); None for post boxes / c/o lines."""
    if not postcode or not line or re.match(r"\s*(postboks|pb\.?|boks|c/o)\b", line, re.I):
        return None
    return f"{postcode}|{re.sub(r'\s+', ' ', line.strip().lower())}"


def _shared(shared_contacts: dict | None, kind: str, key: str | None, target: str) -> list[str]:
    """Sister organisations (same distinctive name token) that file the same registry phone/address as the
    target, from the declared cache `registry_shared_contacts`."""
    if not shared_contacts or not key:
        return []
    lst = set(map(str, (shared_contacts.get(kind) or {}).get(key) or []))
    if target and target not in lst:
        return []
    return sorted(lst - {target})


REGION_WORDS = {"nordmore", "sunnmore", "romsdal", "hordaland", "rogaland", "agder", "trondelag", "vestland", "viken",
                "innlandet", "finnmark", "troms", "nordland", "telemark", "vestfold", "ostfold", "akershus", "buskerud",
                "oppland", "hedmark", "hallingdal", "valdres", "gudbrandsdal", "gudbrandsdalen", "setesdal", "jaeren",
                "ryfylke", "sunnhordland", "nordhordland", "midtre", "helgeland", "salten", "lofoten", "vesteralen",
                "ofoten", "namdal", "fosen", "orkdal", "gauldal", "nordfjord", "sunnfjord", "sogn", "hadeland",
                "romerike", "follo", "ringerike", "toten", "solor", "odal", "vestlandet", "sorlandet", "ostlandet",
                "nord", "sor", "vest", "ost", "midt", "nordre", "sondre", "vestre", "ostre", "indre", "ytre"}


def place_tokens(shared_contacts: dict | None, rec: dict | None = None) -> set[str]:
    """Folded Norwegian place / region tokens (cache `registry_shared_contacts` "places", region words and the
    company's own registry post town / municipality)."""
    out = set(REGION_WORDS)
    if shared_contacts and isinstance(shared_contacts.get("places"), list):
        cached = shared_contacts.get("_place_set")
        if not isinstance(cached, (set, frozenset)):
            cached = frozenset(shared_contacts["places"])
            shared_contacts["_place_set"] = cached
        out |= cached
    for k in ("forretningsadresse", "postadresse"):
        a = (rec or {}).get(k) or {}
        for pk in ("poststed", "kommune"):
            out |= {t for t in fold(a.get(pk) or "").replace("-", " ").split() if len(t) >= 3}
    return out


def domain_filers(shared_contacts: dict | None, domain: str | None) -> list[tuple[str, str]]:
    """[(orgnr, name)] of every organisation number that files `domain` (or a host under it) as its website."""
    if not shared_contacts or not domain:
        return []
    rows = (shared_contacts.get("domains") or {}).get(domain) or []
    return [(str(r[0]), str(r[1])) for r in rows if isinstance(r, (list, tuple)) and len(r) >= 2]


def corroborate(infos: list[PageInfo], rec: dict, site_domain: str, *, shared_contacts: dict | None = None,
                target: str = "") -> dict:
    """Registry corroboration. A phone or street address that the registry also lists for another organisation
    number (sister company, shared office, accountant) does not count; the legal name must carry its form token
    in upper case ('Yabimo as a supplier' is not 'YABIMO AS')."""
    text = " ".join(i.text + " | " + i.head_text() for i in infos)
    tl = text.lower()
    tel_digits = [re.sub(r"\D", "", t) for i in infos for t in i.x.get("tel_links", [])]
    sig: dict[str, Any] = {}
    sig["shared_discounted"] = []
    phones = []
    for p in registry_phones(rec):
        others = _shared(shared_contacts, "phones", p, target)
        if others:
            sig["shared_discounted"].append(f"phone {p} also filed by {', '.join(others[:3])}")
            continue
        phones.append(p)
    sig["phone_match"] = [p for p in phones if phone_in_text(p, text) or any(d.endswith(p) for d in tel_digits)]
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
        others = _shared(shared_contacts, "addresses", address_key(pn, lines[0]), target)
        if others:
            sig["shared_discounted"].append(f"address {pn} {lines[0]} also filed by {', '.join(others[:3])}")
            continue
        for m in re.finditer(r"(?<!\d)" + re.escape(pn) + r"(?!\d)", text):
            win = tl[max(0, m.start() - 120): m.end() + 60]
            if street[0] in win:
                addr_hits.append(f"{k}:{pn}+{street[0]}")
                break
    sig["address_match"] = addr_hits
    name = _ws(rec.get("navn") or "")
    toks = X.name_core_tokens(name)
    strong_name = bool(ORGFORM_WORD_RX.search(name)) or len(toks) >= 3
    nrx = X.legal_name_rx(name)
    sig["legal_name_full"] = bool(name) and len(toks) >= 1 and strong_name and nrx is not None and \
        bool(nrx.search(text))
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
    misnamed: list[str] = field(default_factory=list)          # entities named right before the target's number
    hosted: bool = False                        # filed domain redirects to a site-builder host
    vis_others: list[str] = field(default_factory=list)        # other org numbers in visible text
    group_signals: list[str] = field(default_factory=list)     # foreign-parent / group / chain signals
    shared_reg_phones: list[str] = field(default_factory=list) # registry phones also filed by other org numbers
    shared_contacts: dict | None = field(default=None, repr=False, compare=False)   # registry_shared_contacts
    places: frozenset = field(default=frozenset(), repr=False, compare=False)     # folded place / region tokens
    site_links: list[str] = field(default_factory=list)        # "<company> nettside" links to another domain
    other_entities: list[str] = field(default_factory=list)   # sibling / foreign legal entities named on the site
    foreign_regs: list[str] = field(default_factory=list)
    exact_strong: bool = False                  # exact registered name in title / og:site_name / JSON-LD name
    strict_block: bool = False                  # facts only from the company's own legal-name block
    cross_domain: bool = False

    @property
    def published(self) -> bool:
        return self.level in PUBLISHED_LEVELS

    @property
    def confidence(self) -> float:
        return CONFIDENCE.get(self.level, 0.0)

    @property
    def restricted(self) -> bool:
        """Facts must come from the target's own org-number block (or equal the registry record)."""
        return self.multi or not self.exclusive or bool(self.foreign_owner) or bool(self.other_entities) or \
            self.strict_block


_HIT_RANK = {("text", True): 0, ("registry_link", True): 1, ("jsonld", True): 2, ("text", False): 3,
             ("html", True): 4, ("html", False): 5}


def _trim_entity(name: str, dist_keys: list[str]) -> str:
    """'Off Road Hydraulic Solutions HYDRECO HYDRAULICS LTD' -> 'HYDRECO HYDRAULICS LTD' (for notes)."""
    words = name.split()
    for j, w in enumerate(words):
        if any(squash(w).startswith(k) for k in dist_keys):
            return " ".join(words[j:])
    return name


def _owner_context_hit(i: PageInfo, h: dict, prof: NameProfile) -> bool:
    """A visible, keyword-labelled org number where a site states its owner: footer, contact/about/terms/
    privacy/imprint page, or next to the company name."""
    if h["src"] != "text" or not h["kw"] or h["credit"] or h.get("list") or h.get("misnamed"):
        return False
    return bool(i.x.get("target_in_footer")) or i.page.kind in OWNER_PAGE_KINDS or \
        (bool(prof.required) and all(token_in(t, h["span"]) for t in prof.required))


CONTROLLER_RX = re.compile(r"behandlingsansvarlig\w*|ansvarlig\s+for\s+behandlingen|kontrollør|data\s+controller|"
                           r"\bcontroller\b|\bselger\b|\bseller\b|eier\s+av\s+(?:nett)?(?:sted|side)\w*|"
                           r"(?:nettstedet|nettsiden|siden|websiden)\s+(?:eies|drives|driftes)\s+av|"
                           r"\bvarene\s+selges\s+av\b|\bavtalen\s+inngås\s+med\b", re.I)


def _title_segments(infos: list["PageInfo"]) -> list[str]:
    return sorted({sg.strip() for i0 in infos for t0 in (i0.title, i0.meta.get("og:site_name", ""))
                   for sg in re.split(r"\s+[–—|·•:\-]\s+|\s*\|\s*", t0 or "") if sg.strip()})


def _words_seq_in(words: list[str], text: str) -> bool:
    """Token-boundary match of a name's word sequence in text ('son' never matches inside 'personvern')."""
    if not words:
        return False
    toks = [t for t in re.findall(r"[a-z0-9]+", fold(text or "").replace("&", " og ")) if t not in ("og", "and")]
    n = len(words)
    joined = "".join(words)
    for j in range(len(toks)):
        if toks[j:j + n] == words:
            return True
        acc = ""
        for k in range(j, min(len(toks), j + n + 2)):
            acc += toks[k]
            if acc == joined:
                return True
            if len(acc) >= len(joined):
                break
    return False


def _edit_le2(a: str, b: str) -> bool:
    """Levenshtein distance <= 2 ('aktivepeople' ~ 'activepeople')."""
    if abs(len(a) - len(b)) > 2:
        return False
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = cur
    return prev[-1] <= 2


def _distinctive_tokens(prof: NameProfile) -> list[str]:
    return [t for t in prof.required if not t.isdigit() and (t not in X.GENERIC_TOKENS or t in COUNTRY_WORDS)]


def _controller_conflict(ok: list["PageInfo"], prof: NameProfile, own_words: list[str], own_foreign: bool,
                         is_target=None) -> str | None:
    """A fetched privacy/terms/imprint page naming another legal entity as controller / seller / owner."""
    is_target = is_target or X.is_target_entity
    for i in ok:
        if i.page.kind not in ("privacy", "terms", "impressum", "legal") and \
                not re.search(r"personvern|privacy|vilk[aå]r|terms|impressum", i.url, re.I):
            continue
        for m in CONTROLLER_RX.finditer(i.text):
            win = i.text[max(0, m.start() - 120):m.end() + 160]
            ents = [e for e in X.legal_entities(win, prefix_forms=True) if not PROVIDER_RX.search(e["key"])]
            if not ents:
                continue
            pos = m.start() - max(0, m.start() - 120)
            e = min(ents, key=lambda e: min(abs(e["start"] - pos), abs(e["end"] - pos)))
            if not is_target(e, own_words, own_foreign) and not _entity_matches(e["name"], prof):
                return f"the page {i.url} names {e['name']} as controller/seller/owner"
    return None


def _group_signals(ok: list["PageInfo"], v: "Verdict", prof: NameProfile, own_words: list[str],
                   own_core: set[str], own_foreign: bool, form: str | None, siblings: list[str],
                   is_target=None) -> list[str]:
    """Foreign-parent / group / chain signals over ALL fetched pages (policy C)."""
    is_target = is_target or X.is_target_entity
    if form == "NUF":                       # a Norwegian branch is the foreign company itself
        return []
    sig: list[str] = []
    label = squash((v.domain or "").split(".")[0])
    brand_keys = {k for k in own_core if len(k) >= 3} | ({label} if len(label) >= 3 else set())
    base_seg = (urlsplit(v.site_url or "").path or "/").strip("/").split("/")[0].lower()
    for i in ok:
        sig += ["foreign registration number: " + r for r in i.all_foreign_regs]
        owner_ctx = i.page.kind in OWNER_PAGE_KINDS
        footer = (i.x.get("footer_excerpt", "") or "") + " " + " ".join(i.x.get("copyright") or [])
        for e in i.entities:
            if not e["foreign"] or PROVIDER_RX.search(e["key"]) or is_target(e, own_words, own_foreign):
                continue
            related = any(k in e["key"] for k in brand_keys)
            if not related and e["name"]:
                pos = i.text.find(e["name"])
                win_e = i.text[max(0, pos - 120):pos + len(e["name"]) + 120] if pos >= 0 else ""
                if pos >= 0 and SUPPLIER_CTX_RX.search(win_e):
                    continue                    # an unrelated supplier / processor, not a parent
                if owner_ctx and not (e["name"] in footer) and not OWNER_CTX_RX.search(win_e):
                    continue                    # processors / brands listed on a privacy or about page
            if related or owner_ctx or (e["name"] and e["name"] in footer):
                sig.append("foreign legal entity: " + e["name"])
        for ph in i.ownership:
            obj = re.split(OWNERSHIP_RX.pattern, ph, maxsplit=1, flags=re.I)[-1] if OWNERSHIP_RX.search(ph) else ph
            if prof.required and all(token_in(t, obj) for t in prof.required) and \
                    not re.search(r"group|gruppen|konsern|holding", obj, re.I):
                continue                    # the object is this company itself
            sig.append("ownership statement: " + ph)
        if i.country_list:
            sig.append("list of country offices")
        sig += ["acquisition/ownership statement: " + a for a in i.acquisitions]
        dist = _distinctive_tokens(prof)
        own_sq = squash(" ".join(own_words))
        for fo in i.formless_owners:
            head = " ".join(fo.split()[:max(2, len(own_words))])   # menu words may be glued on after the owner
            fsq = squash(head)
            near = any(len(k) >= 5 and len(fsq) >= 5 and (k.startswith(fsq) or fsq.startswith(k) or
                                                          _edit_le2(k, fsq)) for k in (label, own_sq) if k)
            if not any(token_in(t, head) for t in dist) and not (label and label in fsq) and not near and \
                    not PROVIDER_RX.search(fsq):
                sig.append("© owner names " + fo)
        locales: set[str] = set()
        for u, _t in i.links:
            sp = urlsplit(u)
            h = (sp.hostname or "").lower()
            d = X.reg_domain(h)
            seg = (sp.path or "/").strip("/").split("/")[0].lower()
            if d and d != v.domain and label and len(label) >= 3 and squash(d.split(".")[0]) == label:
                tld = d.rsplit(".", 1)[-1]
                if tld in FOREIGN_TLDS or (tld == "com" and (seg in FOREIGN_LOCALES or seg == "en")):
                    sig.append("same brand on a foreign domain: " + d + ("/" + seg if seg else ""))
            elif d == v.domain and v.subpath and seg and seg != base_seg and \
                    (seg in FOREIGN_LOCALES or seg in ("en", "no", "nb", "nn")) and len(sp.path.strip("/").split("/")) <= 2:
                locales.add(seg)
        if any(lc in FOREIGN_LOCALES for lc in locales) or len(locales) >= 2:
            sig.append("country/language sections of a group site: /" + "/, /".join(sorted(locales)[:4]) + "/")
        for o in i.owners:
            if not is_target(o, own_words, own_foreign) and not _entity_matches(o["name"], prof) and \
                    not PROVIDER_RX.search(o["key"]):
                sig.append("owner statement names " + o["name"])
    ui = sorted({_ws(m0.group(0)).lower() for i in ok for m0 in FOREIGN_UI_RX.finditer(i.text)})
    if len(ui) >= 2:
        sig.append("foreign-language interface strings: " + ", ".join(ui[:4]))
    own_last = (prof.name or "").split()[-1:] or [""]
    own_form = own_last[0] if own_last[0].upper() in X.NO_FORMS else ""
    for i in ok:
        for snippet, names in i.we_defs:
            ents = []
            for nm in names:
                words = X.entity_words(nm)
                frm = nm.rsplit(" ", 1)[-1]
                same = bool(words) and X.is_target_entity({"words": words, "foreign": X.legal_form_is_foreign(nm),
                                                           "name": nm, "key": squash(nm)}, own_words, own_foreign) and \
                    (frm.upper().replace(".", "") == own_form.upper().replace(".", "") or not own_form)
                ents.append((nm, same))
            if len(ents) >= 2 or any(not same for _, same in ents):
                sig.append("definition of «vi»/«oss» names " + ", ".join(n for n, _ in ents[:4]))
    sig += ["sister entity: " + n for n in siblings]
    out: list[str] = []
    for x in sig:
        if x not in out:
            out.append(x)
    return out[:8]


def verify(pages: list[Page], rec: dict | None, target: str, *, lead_kind: str, start_url: str,
           shared_orgnrs: Iterable[str] = (), name: str | None = None, form: str | None = None,
           cache: dict | None = None, shared_contacts: dict | None = None) -> Verdict:
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
    if v.domain in PLATFORM_DOMAINS or (urlsplit(home.final_url).hostname or "").endswith("sites.google.com"):
        v.reason = (f"{home.final_url} is a page hosted on a property-manager/community/directory platform "
                    f"({v.domain}), not the company's own website")
        return v
    v.site_url, v.subpath = site_base(start_url, home.final_url)
    st_host = (urlsplit(start_url).hostname or "").lower() if start_url else ""
    if (v.domain in HOSTING_DOMAINS or v.host.endswith("sites.google.com")) and st_host and \
            X.reg_domain(st_host) != v.domain and X.reg_domain(st_host) not in HOSTING_DOMAINS:
        # filed domain redirects to a site-builder host: the company's address is the filed domain
        v.site_url, v.subpath, v.hosted = f"https://{st_host}/", False, True
    shared = sorted(set(shared_orgnrs) - {target})
    v.shared_with = shared
    v.exclusive = not v.subpath and not shared
    ok = [i for i in infos if not i.blocking]
    tmpl = sorted({t for i in ok for t in i.template})
    if tmpl:
        v.level, v.reason = "not_available", f"template or placeholder site ({'; '.join(tmpl[:3])})"
        return v
    others = sorted(set().union(*(i.others for i in ok)))
    v.other_orgnrs = others
    v.multi = bool(others)
    v.dominant = any(name_matches(i.strong_text(), prof) for i in ok)
    own_key = X.entity_key(name)
    v.exact_strong = bool(own_key) and len(own_key) >= 3 and \
        any(_words_seq_in(X.entity_words(name), i.strong_text()) for i in ok)
    places = place_tokens(shared_contacts, rec)
    # sister entities share a distinctive NON-place token ('Nes Miljøpark AS' is no sister of GREENLAB NES AS)
    dist_keys = [squash(t) for t in prof.required if len(squash(t)) >= 3 and not t.isdigit() and fold(t) not in places]
    own_foreign = form == "NUF" or X.legal_form_is_foreign(name)
    own_words = X.entity_words(name)
    own_core = {w for w in own_words if len(w) >= 3 and not w.isdigit()}
    # acronyms the site gives this company right after its name ("Haugaland kontrollutvalgssekretariat HKS IKS")
    body_words = [w for w in re.findall(r"[\wÆØÅæøå]+", name) if w.upper() not in X.NO_FORMS]
    aliases: set[str] = set()
    if body_words:
        arx = re.compile(r"(?i:" + r"[\s\-]+".join(map(re.escape, body_words)) + r")\s*\(?\s*([A-ZÆØÅ]{2,6})\b")
        for i0 in ok:
            aliases |= {m0.group(1).lower() for m0 in arx.finditer(i0.text)
                        if m0.group(1)[0].lower() == fold(body_words[0])[0] and len(body_words) >= 2}
    if aliases:
        _is_t = X.is_target_entity

        def _is_target_alias(e, ow, of):
            ws = e.get("words") or X.entity_words(e.get("name", ""))
            return _is_t(e, ow, of) or (bool(ws) and ws[-1] in aliases)
    else:
        _is_target_alias = X.is_target_entity
    seen_ent: dict[str, str] = {}
    for i in ok:
        for e in i.entities:                       # siblings: anywhere on the fetched pages
            if not _is_target_alias(e, own_words, own_foreign) and any(k in e["key"] for k in dist_keys):
                seen_ent.setdefault(e["key"] + ("|f" if e["foreign"] else ""), _trim_entity(e["name"], dist_keys))
        for e in i.owner_entities:                 # foreign entities: where a site states its owner
            if not _is_target_alias(e, own_words, own_foreign) and e["foreign"] and form != "NUF":
                seen_ent.setdefault(e["key"] + "|f", e["name"])
    v.other_entities = sorted(set(seen_ent.values()))[:10]
    v.foreign_regs = [] if form == "NUF" else sorted({r for i in ok for r in i.foreign_regs})[:5]
    siblings = sorted({n for k, n in seen_ent.items() if any(d in k.split("|")[0] for d in dist_keys)})
    exact_named = any(i.exact_named for i in ok)
    st_dom0 = X.reg_domain(urlsplit(start_url).hostname or "") if start_url else ""
    v.cross_domain = bool(st_dom0) and st_dom0 != v.domain
    all_found = [(i, h) for i in ok for h in i.hits]
    misnamed = sorted({h["misnamed"] for _, h in all_found if h.get("misnamed")})
    found = [(i, h) for i, h in all_found if not h.get("misnamed")]       # never L1 on a number printed under
    v.misnamed = misnamed                                                  # another entity's name
    v.vis_others = []
    v.site_links = sorted({u for i in ok for u, t in i.site_links
                           if any(token_in(tk, t) for tk in prof.required) and
                           X.reg_domain(urlsplit(u).hostname or "") not in ("", v.domain)})[:3]
    # parent/group owner: an entity in an owner statement (© line, JSON-LD legalName) that is not this company,
    # whose org number the site prints among the others or which files this host
    owner_parent = None
    former0 = [h0.get("navn", "") for h0 in rec.get("historiskeNavn") or [] if isinstance(h0, dict)]
    for i0 in ok:
        for o in i0.owners:
            if _is_target_alias(o, own_words, own_foreign) or _entity_matches(o["name"], prof, former0):
                continue
            related = bool(set(o.get("words") or []) & (own_core | {squash((v.domain or "").split(".")[0])}))
            if (o.get("orgnr") and o["orgnr"] != target and (o["orgnr"] in others or o["orgnr"] in shared)) or \
                    (shared and not o.get("orgnr") and related):
                owner_parent = o["name"] + (f" ({o['orgnr']})" if o.get("orgnr") else "")
                break
        if owner_parent:
            break
    # a site titled with the name of another organisation shown on it (sister/group site): a title / og:site_name
    # segment that is the full name the page prints for another org number (followed by nothing or an address)
    title_other = None
    segs = sorted({sg.strip() for i0 in ok for t0 in (i0.title, i0.meta.get("og:site_name", ""))
                   for sg in re.split(r"\s+[–—|·•:\-]\s+|\s*\|\s*", t0 or "") if sg.strip()})
    for sg in segs:
        k = X.entity_key(sg)
        if len(k) < 6 or _is_target_alias({"words": X.entity_words(sg), "foreign": own_foreign}, own_words, own_foreign):
            continue
        for i0 in ok:
            for before, d in i0.named_orgnrs:
                occ = [m0 for m0 in re.finditer(r"(?<![\w])" + re.escape(sg) + r"(?![\w])", before, re.I)]
                if d == target or not occ:
                    continue
                rest = before[occ[-1].end():]
                if ADDRESS_TAIL_RX.fullmatch(rest):
                    title_other = f"{sg} (org.nr {d})"
                    break
            if title_other:
                break
        if title_other:
            break
    # the site is branded as the legal entity its © line names, and that entity is not this company
    segs_all = _title_segments(ok)
    owner_brand = next((o["name"] for i0 in ok for o in i0.owners
                        if not _is_target_alias(o, own_words, own_foreign) and not PROVIDER_RX.search(o["key"])
                        and len(o["key"]) >= 5 and any(o["key"] == X.entity_key(sg) or o["key"] in squash(sg)
                                                       for sg in segs_all)), None)
    if owner_brand and not title_other:
        title_other = f"{owner_brand} (named as the site owner in its © line and in the site title)"
    reg_lead = lead_kind == "registry_website"
    email_lead = lead_kind == "email_domain" and "@" in registry_email(rec) and \
        X.reg_domain(registry_email(rec).split("@", 1)[1]) == v.domain
    v.shared_reg_phones = [p for p in registry_phones(rec) if _shared(shared_contacts, "phones", p, target)]
    v.shared_contacts = shared_contacts
    # ---- C: foreign / group / chain signals on all fetched pages
    v.group_signals = _group_signals(ok, v, prof, own_words, own_core, own_foreign, form, siblings,
                                     is_target=_is_target_alias)
    # ---- P: chain / franchise domains and group co-filers (registry filings of the site's domain / host)
    v.places = frozenset(places)
    if v.domain and v.domain not in HOSTING_DOMAINS and not v.hosted:
        dlabel = squash(v.domain.split(".")[0])
        cof = {o: n for o, n in domain_filers(shared_contacts, v.domain) if o != target}
        names_map = (shared_contacts or {}).get("names") or {}
        for o in shared:
            if o in names_map:
                cof.setdefault(o, str(names_map[o]))
        own_sq = squash(" ".join(own_words))
        former_sq = [squash(h0.get("navn", "")) for h0 in rec.get("historiskeNavn") or [] if isinstance(h0, dict)]
        carries = len(dlabel) >= 3 and (dlabel in own_sq or any(dlabel in f0 for f0 in former_sq) or
                                         any(len(squash(t)) >= 4 and squash(t) in dlabel and fold(t) not in places
                                             for t in prof.required if not t.isdigit()))
        chain = [f"{n} ({o})" for o, n in sorted(cof.items()) if len(dlabel) >= 3 and dlabel in squash(n)]
        if chain and not carries:
            v.reason = (f"chain/franchise domain: {v.domain} is filed in the registry by {', '.join(chain[:3])}, "
                        f"whose name carries '{dlabel}'; {name or 'the company'} does not")
            return v
        dist_g = [t for t in _distinctive_tokens(prof) if len(squash(t)) >= 3 and not t.isdigit() and
                  fold(t) not in places and fold(t) not in COUNTRY_WORDS]
        sis = [f"{n} ({o})" for o, n in sorted(cof.items()) if any(token_in(t, n) for t in dist_g)]
        if sis:
            v.reason = (f"group site: {v.host} is also filed by sister entit{'ies' if len(sis) > 1 else 'y'} "
                        f"{', '.join(sis[:3])}")
            return v
    # ---- B: a sub-path is publishable only on the company's own brand host
    if v.subpath and not v.hosted:
        suffixes = [sg for sg in segs_all]
        host_brand = any(len(squash(t)) >= 3 and squash(t) in squash((v.domain or "").split(".")[0])
                         for t in prof.required if not t.isdigit()) or \
            any(token_in(t, sg) for sg in suffixes for t in prof.required if len(t) >= 3 and not t.isdigit())
        if not host_brand or shared or v.group_signals:
            why = "the host is not the company's brand" if not host_brand else \
                (f"the host is shared with other organisation numbers ({', '.join(shared[:4])})" if shared else
                 "group/foreign signals: " + "; ".join(v.group_signals[:3]))
            v.reason = f"{v.site_url} is a section of another site ({why}); not the company's own website"
            return v
        if HOMEISH_PATH_RX.match(urlsplit(v.site_url).path or "/"):
            v.exclusive = True               # the company's own brand host, published at its language section
    # ---- A: L1 proof only from a visible, keyword-labelled, single-company hit or a name-matched JSON-LD id
    def _eligible(i0: PageInfo, h0: dict) -> bool:
        if h0.get("misnamed") or h0.get("credit"):
            return False
        if h0["src"] == "text":
            return bool(h0["kw"]) and not h0.get("list")
        if h0["src"] == "jsonld":
            return any(target in _jsonld_ids(o) and any(isinstance(o.get(k), str) and name_matches(o[k], prof)
                                                         for k in ("name", "legalName")) for o in i0.orgs)
        return False
    found = [(i0, h0) for i0, h0 in found if _eligible(i0, h0)]
    # ---- J: acquisition / merger / ownership statements and a (c) owner that is not this company -> ambiguous
    acq = [sg for sg in v.group_signals if sg.startswith(("acquisition/ownership statement", "© owner names"))]
    if acq:
        v.reason = "the site states another owner: " + "; ".join(acq[:2])
        return v
    # ---- H: exclusivity - no other entity's organisation number anywhere in the visible text
    vis_others = sorted({d for i0 in ok for d in i0.vis_others})
    if vis_others:
        v.other_orgnrs = sorted(set(v.other_orgnrs) | set(vis_others))
        v.reason = (f"site also prints organisation numbers of other entities ({', '.join(vis_others[:5])}); "
                    "not exclusively this company's website")
        return v
    # ---- I: brand alignment - a distinctive name token in the domain label or the site's title/name
    dist_t = _distinctive_tokens(prof)
    label_sq = squash((v.domain or "").split(".")[0])
    strong_all_t = " | ".join(i0.strong_text() for i0 in ok)
    # 'NR 1 CATERING AS' ~ nr1catering.no (all tokens generic or short): the whole name aligns the brand
    whole_ok = any(len(w) >= 5 and w in label_sq for w in {prof.joined, squash("".join(X.entity_words(name)))}) or \
        (len(prof.joined) >= 5 and _words_seq_in(X.entity_words(name), strong_all_t))
    label_hit = whole_ok and any(len(w) >= 5 and w in label_sq
                                 for w in {prof.joined, squash("".join(X.entity_words(name)))})
    label_hit = label_hit or any(len(squash(t)) >= 3 and squash(t) in label_sq for t in dist_t)
    # a place / region word ('Spenst Otta', 'Nordmøre') never aligns a site's title with the company on its own
    title_hit = any(token_in(t, strong_all_t) for t in dist_t if fold(t) not in places)
    if not whole_ok and not label_hit and not title_hit:
        v.reason = ("brand not aligned: no distinctive token of the legal name in the domain or in the site's "
                    "title / site name / JSON-LD name")
        return v
    if v.cross_domain and (v.host or "").removeprefix("www.") != v.domain and not v.hosted and not label_hit:
        v.reason = (f"the lead redirects onto {v.host}, a sub-domain of {v.domain}, whose name does not carry "
                    f"{name or 'the company name'}")
        return v
    # ---- controller / seller named on a privacy or terms page must be this company (L1 and L2)
    ctrl = _controller_conflict(ok, prof, own_words, own_foreign, is_target=_is_target_alias)
    if ctrl:
        v.reason = ctrl + f", not {name}"
        return v
    if v.group_signals:
        # any group / foreign / sister / ownership / country-office / same-brand-foreign-domain signal: never
        # published (a subsidiary's own name always precedes its own number on a group page - no rescue)
        v.reason = ("group/foreign site (" + "; ".join(v.group_signals[:3]) + "): a group or foreign site is not "
                    f"published as {name or 'the company'}'s own website")
        return v
    if owner_parent and (found or reg_lead):
        v.reason = (f"parent/group site: owner statement names {owner_parent}, which the site or the host's "
                    f"registry filings show as another organisation")
        return v
    if title_other:
        v.reason = f"sister/group site: the site is titled with the name of another organisation it shows: {title_other}"
        return v
    if misnamed and not found:
        v.reason = (f"org number printed on the site next to another entity's name ({', '.join(misnamed[:2])}): "
                    "not proof that the site is this company's")
        return v
    if found:
        i, h = min(found, key=lambda ih: (_HIT_RANK.get((ih[1]["src"], ih[1]["kw"]), 9), ih[0].idx))
        if all(hh["credit"] for _, hh in found) and not v.dominant:
            v.reason = (f"org number appears on {i.url} only in a web-agency/credit context, "
                        "and the site is not branded as the company")
            return v
        nrx_r = X.legal_name_rx(name)
        named_hit = bool(nrx_r) and any(hh["src"] == "text" and nrx_r.search(hh.get("pre") or hh["span"]) for _, hh in found)
        if v.restricted and not reg_lead and not v.dominant and not named_hit:
            ctx = f"other organisation numbers ({', '.join(others[:5])})" if others else \
                f"a host shared with other organisation numbers ({', '.join(shared[:5])})"
            v.reason = (f"org number printed on {i.url}, but the site also shows {ctx} and is not branded as "
                        f"{name or 'the company'}; it is not the company's own website")
            return v
        if (shared or v.subpath) and not any(_owner_context_hit(ii, hh, prof) for ii, hh in found):
            v.reason = (f"{v.host} is shared with other organisation numbers ({', '.join(shared[:5]) or 'sub-path site'}); "
                        f"the org number is not printed visibly with a label in an owner context (footer, contact, "
                        f"imprint, privacy) - only in {h['src']}")
            return v
        if reg_lead and v.cross_domain and (v.multi or shared) and not v.dominant:
            v.reason = (f"registered website redirects to {v.host}, a site that also shows/serves other organisation "
                        f"numbers ({', '.join((others or shared)[:5])}) and whose title/site name does not carry "
                        f"{name or 'the company name'}")
            return v
        method = {"text": "orgnr_in_page_text", "registry_link": "orgnr_in_registry_link",
                  "jsonld": "orgnr_in_jsonld_identifier", "html": "orgnr_in_page_html"}[h["src"]]
        if not v.hosted and not under_base(i.url, v.site_url, v.subpath, v.exclusive):
            # never publish a base wider than the page that proved identity
            v.site_url, v.subpath, v.exclusive = i.url.split("#")[0].split("?")[0], True, False
        v.level = "L1"
        v.proof = {"page": i.idx, "url": i.url, "span": h["span"], "method": method, "locator": h["locator"]}
        note = []
        if others:
            note.append(f"site also shows other organisation numbers ({', '.join(others[:5])}); "
                        "contact facts limited to the company's own org-number block")
        if shared:
            note.append(f"{v.host} also hosts other organisation numbers' sites")
        if v.subpath:
            note.append(f"published at its section {v.site_url}")
        v.reason = f"organisation number printed on {i.url}" + (" (" + "; ".join(note) + ")" if note else "")
        return v
    # ------------------------------------------------------------------ no org number: L2 or ambiguous
    if others:
        v.reason = (f"site shows other organisation number(s) {', '.join(others[:5])} but not {target}: "
                    "it belongs to another entity")
        return v
    if not (reg_lead or email_lead):
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
        # JS-rendered shell: accept only strong head evidence (registry phone/e-mail declared in the head or body
        # AND the full registered name in <title> or meta author / og:site_name)
        head = hi.head_text() + " | " + hi.text
        nrx = X.legal_name_rx(name)
        name_in_head = bool(nrx) and any(nrx.search(x) for x in (hi.title, hi.meta.get("author", ""),
                                                                 hi.meta.get("og:site_name", "")))
        contact_in_head = any(phone_in_text(p, head) for p in registry_phones(rec)) or \
            (bool(registry_email(rec)) and registry_email(rec) in head.lower())
        if not (name_in_head and contact_in_head):
            v.reason = "home page has no readable content (JavaScript or image-only); org number not found"
            return v
    if siblings and not exact_named:
        v.reason = (f"site of a sister/group entity: it names {', '.join(siblings[:4])} but never "
                    f"{name or 'the company'}")
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
    # A registry phone/address shared with sister organisations only corroborates when the site carries this
    # company's exact full name in its title / og:site_name / JSON-LD name (it is not a sibling's or the group's).
    corr = corroborate(ok, rec, v.domain or "", shared_contacts=shared_contacts, target=target)
    v.corroboration = corr
    if v.other_entities or v.foreign_regs or v.group_signals:
        # Precision first: a site that names other legal entities (sister/group companies, foreign registrations)
        # or carries group/foreign-parent signals and does not print this company's organisation number is a
        # group site, not this company's own official website. L2 is refused outright.
        what = ", ".join((v.other_entities + v.foreign_regs + v.group_signals)[:4])
        v.reason = (f"site names other legal entities ({what}) and does not print this organisation number: "
                    "treated as a group site, not the company's own website")
        return v
    # ---- D: L2 needs an exact registry contact, the full legal name in owner text and a checked legal page
    if not (corr["email_match"] or corr["phone_match"]):
        v.reason = ("no exact registry e-mail or (unshared) registry phone on the site; an e-mail domain match alone "
                    "does not identify the company")
        return v
    nrx_l = X.legal_name_rx(name)
    owner_texts = [i.x.get("footer_excerpt", "") or "" for i in ok] + \
        [i.text for i in ok if i.page.kind in OWNER_PAGE_KINDS + ("legal",)]
    if not (nrx_l and any(nrx_l.search(t) for t in owner_texts)):
        v.reason = (f"the full legal name {name!r} (with its legal form) is not in the site's footer, contact or "
                    "imprint text")
        return v
    legal_pages = [i for i in ok if i.page.kind in ("privacy", "terms", "impressum", "legal")]
    if not legal_pages:
        v.reason = "privacy/terms/imprint page not found or not checked; L2 needs it"
        return v
    for i in legal_pages:
        for m in CONTROLLER_RX.finditer(i.text):
            win = i.text[max(0, m.start() - 120):m.end() + 160]
            ents = [e for e in X.legal_entities(win, prefix_forms=True) if not PROVIDER_RX.search(e["key"])]
            if not ents:
                continue
            pos = m.start() - max(0, m.start() - 120)
            e = min(ents, key=lambda e: min(abs(e["start"] - pos), abs(e["end"] - pos)))
            if not _is_target_alias(e, own_words, own_foreign) and not _entity_matches(e["name"], prof):
                v.reason = f"the {i.page.kind} page names {e['name']} as controller/seller/owner, not {name}"
                return v
    if owner:
        # A sibling/parent named as owner: only an overwhelming registry match keeps the registry-filed site,
        # and then facts are restricted to values equal to the registry record.
        if not ((corr["phone_match"] or corr["email_match"]) and corr["n"] >= 3):
            v.reason = f"site names another legal entity as its owner ({owner})"
            return v
        v.foreign_owner = owner
    if corr["n"] < 1:
        v.reason = "registered website with the company name, but no registry corroboration (phone, e-mail, address, legal name)"
        if corr["shared_discounted"]:
            v.reason += " not shared with other organisation numbers (" + "; ".join(corr["shared_discounted"][:2]) + ")"
        return v
    co_company = [co for co in co_names(rec) if not _person_like(co)]
    if co_company and corr["n"] < 2:
        v.reason = (f"registry address is c/o {co_company[0]}; with one registry corroboration only an org number "
                    "printed on the site can verify it")
        return v
    if email_lead and not reg_lead:
        nrx = X.legal_name_rx(name)
        title_name = bool(nrx) and any(nrx.search(i.title) or nrx.search(i.meta.get("og:site_name", "")) for i in ok)
        if not (corr["n"] >= 2 and title_name and not v.cross_domain and not v.foreign_owner and
                not v.other_entities and not v.foreign_regs and not co_company):
            v.reason = ("own-domain registry e-mail lead: needs the full registered name in the title, two registry "
                        "corroborations and no other-entity signals")
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
    v.level = "L2" if reg_lead else "L2e"
    span = _clip(" | ".join(p for p in (pi.title, pi.meta.get("og:site_name", "")) if p) or pi.prominent_text()[:300])
    locator = "title"
    if v.strict_block:
        bi = next(i for i in ok if i.name_block and i.name_block.get("isolated"))
        pi, span, locator = bi, _clip(bi.name_block["block_text"]), "text (legal-name block)"
    method = ("registry_website" if reg_lead else "registry_email_domain") + "+name+" + "+".join(corr["kinds"])
    v.proof = {"page": pi.idx, "url": pi.url, "span": span, "method": method, "locator": locator}
    v.reason = ("registry-filed website" if reg_lead else "domain of the registry e-mail") + \
        "; company name prominent; corroborated by " + ", ".join(corr["kinds"])
    if v.strict_block:
        v.reason += f" (site also names {', '.join((v.other_entities + v.foreign_regs)[:3])}: facts only from the company's own block)"
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


# Mailboxes that are never a company's contact address: public authorities (privacy notices cite them) and
# invoice-intake processors.
AUTHORITY_MAIL_DOMAINS = {"datatilsynet.no", "brreg.no", "forbrukertilsynet.no", "forbrukerradet.no", "nav.no",
                          "skatteetaten.no", "altinn.no", "regjeringen.no", "politiet.no", "arbeidstilsynet.no",
                          "finanstilsynet.no", "lovdata.no", "digdir.no", "norid.no", "konkurransetilsynet.no",
                          "mattilsynet.no", "kfir.no", "lottstift.no", "statsforvalteren.no", "sivilombudet.no",
                          "dsb.no", "nkom.no", "medietilsynet.no", "helsedirektoratet.no", "udir.no"}
INVOICE_MAIL_DOMAINS = {"poweroffice.net", "ebilag.com", "xledger.net", "esker.net", "compello.com", "tripletex.no",
                        "fiken.no", "visma.com", "vismaonline.com", "unimicro.no", "24sevenoffice.com", "procountor.com",
                        "invoicecloud.com", "basware.com", "pagero.com", "inexchange.se", "ehfmail.no"}
INVOICE_CONTEXT_RX = re.compile(r"faktura|fakturer|ehf|invoice|billing|bilag", re.I)


def _denied_mail_domain(host: str, form: str | None) -> bool:
    h = (host or "").lower()
    dom = X.reg_domain(h)
    if dom in AUTHORITY_MAIL_DOMAINS or dom in INVOICE_MAIL_DOMAINS:
        return True
    if re.match(r"(?:faktura|invoice|invoices|ehf|scan|bilag|inbound|efaktura)\.", h):
        return True
    return h.endswith(".kommune.no") and form not in ("KOMM", "KF", "IKS", "FYLK")


def _invoice_context(i: "PageInfo", e: str) -> bool:
    """The address is only ever labelled as an invoice-intake mailbox ('Faktura: EHF eller e-post x@...')."""
    occ = list(re.finditer(re.escape(e), i.text, re.I))
    return bool(occ) and all(INVOICE_CONTEXT_RX.search(i.text[max(0, m.start() - 40):m.start()]) for m in occ)


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
        if not m.group("cc") and X.FOREIGN_CC_BEFORE_RX.search(text[max(0, m.start() - 14):m.start()]):
            continue                                   # '+45 7020 3455' is Danish: never read it as +47
        kws = list(X.PHONE_KW_RX.finditer(before))
        fax = (bool(kws) and re.match(r"fa(x|ks)", kws[-1].group(0), re.I) is not None) or \
            bool(X.FAX_BEFORE_RX.search(text[max(0, m.start() - 22):m.start()]))
        out.append({"digits": digits, "raw": m.group(0).strip(), "kw": bool(kws) and not fax, "fax": fax,
                    "credit": bool(X.CREDIT_RX.search(text[max(0, m.start() - 70):m.start()])),
                    "span": _window(text, m.start(), m.end(), 70, 40)})
    return out


# Service providers named in footers (payments, platforms, analytics): never the site owner.
PROVIDER_RX = re.compile(r"klarna|vipps|mobilepay|nets|paypal|stripe|adyen|svea|resurs|qliro|dibs|bambora|worldline|"
                         r"google|facebook|meta|wix|shopify|squarespace|apple|microsoft|trustpilot|cybot|cookiebot|"
                         r"mailchimp|hubspot|automattic|wordpress|webflow|godaddy|onecom|amazon|cloudflare|elementor|"
                         r"vimeo|hetzner|youtube|zendesk|intercom|hotjar|salesforce|adobe|dropbox|twilio|sendgrid|matomo|"
                         r"usercentrics|onetrust|digitalocean|akamai|fastly|vercel|netlify|heroku|oracle|zoom|slack|"
                         r"atlassian|mapbox|calendly|typeform|jotform|pinterest|snapchat|tiktok|bytedance|linkedin|"
                         r"twitter|criteo|sentry|mixpanel|klaviyo|pipedrive|quicksystems|webshop|mystore|domeneshop|"
                         r"proisp|leadinfo|leadfeeder|dealfront")
# an owner / controller / parent context around an entity name
OWNER_CTX_RX = re.compile(r"behandlingsansvarlig|controller|\beier\b|\beies\b|\beid\s+av|owned|\bowner|parent|"
                          r"morselskap|datterselskap|subsidiary|konsern|gruppen|\bgroup\b|selger|seller|avtalepart|"
                          r"kontraktspart|operat|drives\s+av|driftes\s+av|tilhører|del\s+av|part\s+of|hovedkontor|"
                          r"headquarter|\bHQ\b|registered\s+office|fakturaadress|faktura|invoice|org\.?\s*nr|"
                          r"organisasjonsnummer|organisationsnummer|vat|mva|adresse|address", re.I)
# a supplier / processor named on a page ("Hetzner Online GmbH leverer linjer", "Eksempel: Vimeo, Inc.")
SUPPLIER_CTX_RX = re.compile(r"leverer|leverandør|levert|underleverandør|databehandler|processor|provider|supplier|"
                             r"powered|hosting|colocation|plattform|platform|eksempel|example|visning\s+av\s+video|"
                             r"innebygd|embedded|analyse|analytics|informasjonskapsl|cookies?\b|samarbeidspartner|"
                             r"partnere?\b|forhandler|distribut", re.I)
SOCIAL_PLATFORMS = ("facebook", "instagram", "linkedin", "twitter", "youtube", "tiktok")
# brand + foreign country handle (enterpriseuk, brandsverige): an international sibling, not the Norwegian entity
# brand + foreign legal form as a trailing token (balco-ab, scankab_aps): the foreign group company's profile
FOREIGN_FORM_HANDLE_RX = re.compile(r"[-_.](?:ab|oy|oyj|aps|gmbh|ag|bv|ltd|plc|inc|llc|sa)$", re.I)
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


# capitalised words that start labels, headings and menu items - not a person's first or last name
NOT_NAME_WORDS = {"contact", "us", "kontakt", "oss", "ring", "telefon", "tlf", "mobil", "send", "email", "epost",
                  "e-post", "adresse", "besøk", "besok", "kundeservice", "hovedkontor", "avdeling", "sentralbord",
                  "åpningstider", "apningstider", "call", "phone", "office", "kontor", "support", "salg", "sales",
                  "service", "post", "info", "booking", "bestilling", "vakt", "døgnvakt", "akutt", "faks", "fax",
                  "org", "nr", "mva", "visit", "find", "finn", "the", "our", "vår", "vårt", "våre", "ta", "spør",
                  "chat", "whatsapp", "sms", "kontaktinformasjon", "kontaktinfo", "telefonnummer", "nummer"}
COMMON_NOUNS = {
    "gullsmed", "gullsmeden", "ridesenter", "ridesenteret", "rideskole", "bakeri", "bakeriet", "baker", "bakeren",
    "frisor", "frisoren", "frisorsalong", "salong", "salongen", "tannlege", "tannlegen", "tannklinikk", "legekontor",
    "legesenter", "fysioterapi", "fysioterapeut", "kafe", "kafeen", "cafe", "butikk", "butikken", "verksted",
    "verkstedet", "bilverksted", "snekker", "snekkeren", "rorlegger", "rorleggeren", "elektriker", "elektrikeren",
    "maler", "maleren", "blomster", "blomsterbutikk", "florist", "hotell", "hotellet", "restaurant", "restauranten",
    "pizzeria", "puben", "garden", "gardsbutikk", "skole", "skolen", "barnehage", "barnehagen", "kirke", "kirken",
    "menighet", "idrettslag", "klubb", "klubben", "trening", "treningssenter", "yoga", "dekk", "dekksenter",
    "bilforhandler", "eiendomsmegler", "megler", "regnskap", "regnskapskontor", "advokat", "advokatene", "arkitekt",
    "arkitektene", "byggmester", "entreprenor", "transport", "taxi", "renhold", "vaktmester", "apotek", "optiker",
    "optikeren", "klinikk", "klinikken", "spa", "massasje", "interior", "mote", "sko", "sport", "sportsbutikk",
    "camping", "hytte", "hytter", "marina", "brygge", "bryggen", "kunst", "galleri", "galleriet", "musikk", "studio",
    "foto", "fotograf", "media", "reklame", "trykk", "trykkeri", "catering", "kantine", "matbutikk", "dyreklinikk",
    "veterinar", "hundeskole", "hagesenter", "planteskole", "gartner", "gartneri", "fiske", "jakt", "varmepumpe",
    "solcelle", "elbil", "bil", "bilen", "bilutleie", "utleie", "hjem", "hjemmet", "huset", "norge", "norway",
}
FOREIGN_GLUED_FORMS = ("gmbh", "aps", "oyj", "ltd", "inc", "llc", "plc", "ab", "oy", "ag", "bv")


def social_handle_ok(handle: str, prof: NameProfile, label: str | None, form: str | None = None,
                     places: set[str] | frozenset[str] | None = None) -> bool:
    """Policy G: distinctive (non-generic) name tokens - or the company's own exclusive domain label when it carries
    a name token - cover >= 60 % of the handle; no glued foreign legal-form suffix on the brand (densiqab); not a
    generic word plus <= 2 characters (gullsmedm, ridesenteret)."""
    h = squash(unquote(handle or "")).lstrip("@")
    h = re.sub(r"\d{6,}$", "", h)
    if h.endswith("as") and len(h) > 6:
        h2 = h[:-2]
    else:
        h2 = h
    if len(h2) < 3:
        return False
    dist = [fold(t) for t in prof.required if t not in X.GENERIC_TOKENS and not t.isdigit()]
    dist = [squash(t) for t in dist if len(squash(t)) >= 2]
    brands = set(dist) | ({prof.joined_required} if len(prof.joined_required) >= 4 else set())
    lab = label if (label and len(label) >= 3 and any(t in label or label in t for t in dist if len(t) >= 3)) else None
    if lab:
        brands.add(lab)
    if form != "NUF":
        for suf in FOREIGN_GLUED_FORMS:
            if h2.endswith(suf) and h2[:-len(suf)] in brands | {squash(t) for t in prof.core}:
                return False
    for w in (X.GENERIC_TOKENS | COMMON_NOUNS):
        w = squash(w)
        if len(w) >= 4 and (h2.startswith(w) or h2.endswith(w)) and len(h2) - len(w) <= 2:
            return False
    # a place / region word (plus a legal-form residue: 'nordmoresa') never identifies the company: a distinctive
    # non-place name token (or the whole name) must be in the handle
    places = places if places is not None else REGION_WORDS
    non_place = [t for t in dist if len(t) >= 3 and t not in places]
    if non_place and not any(t in h2 for t in non_place) and not (len(prof.joined) >= 5 and prof.joined in h2):
        return False
    if not non_place and dist and not (len(prof.joined) >= 5 and prof.joined in h2):
        return False
    covered = [0] * len(h2)
    toks = sorted({t for t in dist if len(t) >= 2} | ({lab} if lab else set()), key=len, reverse=True)
    for t in toks:
        for cand in {t, t[:-1] if (len(t) >= 6 and t.endswith("s")) else t}:
            j = h2.find(cand)
            while j != -1:
                for k in range(j, j + len(cand)):
                    covered[k] = 1
                j = h2.find(cand, j + 1)
    return sum(covered) >= 0.6 * len(h2)


def social_matches(url: str, prof: NameProfile, label: str | None) -> bool:
    """Stricter than extract.social_matches_company: the matching name part must dominate the handle, so
    sponsor/umbrella handles that merely contain a place name (visitbergen) are rejected."""
    if X.PLATFORM_SOCIAL_RX.search(url):
        return False
    h = squash(X.social_handle(unquote(url)))
    h = re.sub(r"\d{6,}$", "", h)                     # Facebook vanity URLs end in the page id
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
                           r"visiting address|postal address|besøk|kontor|office|adr\.?)(?![A-Za-zÆØÅæøå])\s*:?\s*", re.I)


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
        if re.search(r"faktura|invoice|billing|retur|returns?\b|\bc/o\b|\bv/\s", before):
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
    # Site-level descriptive facts (title, description, brand, logo, socials, careers, news): only on the
    # company's own site, or where title/og:site_name/JSON-LD carry its exact registered name.
    rich = (not restricted) or v.exact_strong
    label = squash((v.domain or "").split(".")[0]) if v.exclusive else None
    reg_em = registry_email(rec)
    reg_em_dom = X.reg_domain(reg_em.split("@", 1)[1]) if "@" in reg_em else ""
    reg_ph = set(registry_phones(rec))
    facts: list[Fact] = []

    # ---- descriptive (home page, else proof page)
    t_info = home if home.title else proof_i
    title = t_info.title
    if rich and title and title.strip().lower() not in USELESS_TITLES and len(title.strip()) >= 3 and \
            not _spammy(title, v.domain) and not ERROR_TITLE_RX.search(title) and not PLACEHOLDER_DESC_RX.search(title):
        facts.append(Fact("website_title", title.strip(), t_info.idx, "title", _clip(title)))
    if rich:
        for i in (home, proof_i):
            for key, loc in (("description", 'meta[name="description"]'), ("og:description", 'meta[property="og:description"]')):
                d = i.meta.get(key)
                if d and len(d.strip()) >= 25 and d.strip().lower() != (i.title or "").strip().lower() and \
                        not PLACEHOLDER_DESC_RX.search(d) and not _spammy(d, v.domain) and \
                        not DESC_CREDIT_RX.search(d) and not DESC_CHAIN_RX.search(d) and not _brand_dash_other(d, prof):
                    dv = _ws(d)
                    if len(dv) > 280:                  # the published value must fit inside its <= 300-char span
                        cut = dv[:279].rsplit(" ", 1)[0].rstrip(" ,;:-–")
                        dv = cut + "…"
                    facts.append(Fact("website_description", dv, i.idx, loc, _clip(d)))
                    break
            if any(f.field == "website_description" for f in facts):
                break
    lang = home.lang if rich else None
    if lang and re.fullmatch(r"[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})*", lang):
        facts.append(Fact("website_language", lang, home.idx, "html[lang]", f'<html lang="{lang}">'))
    matched_orgs = [(i, o) for i in ok for o in i.orgs if org_matches(o, target, prof)]
    if rich:
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
        dist_l = [squash(t) for t in _distinctive_tokens(prof) if len(squash(t)) >= 3]

        def own_logo(u: str) -> bool:
            sp_l = urlsplit(u)
            fname = squash(unquote((sp_l.path or "").rsplit("/", 1)[-1]))
            return X.reg_domain(sp_l.hostname or "") == v.domain and any(t in fname for t in dist_l)
        for i, o in matched_orgs:
            u = _logo_of(o, i.url)
            if u and own_logo(u):
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
                    if u.startswith(("http://", "https://")) and own_logo(u):
                        logo = Fact("logo_url", u, i.idx, 'meta[property="og:image"]', _clip(og))
                        break
        if logo:
            facts.append(logo)

    # ---- address
    addr = None
    name_blocks = [(i, i.name_block) for i in ok if i.name_block and i.name_block.get("isolated")]
    if v.strict_block:
        nrx_b = X.legal_name_rx(rec.get("navn") or prof.name)
        blocks = name_blocks + [(i, i.block) for i in ok if i.block and i.block.get("isolated") and nrx_b and
                                nrx_b.search(i.block.get("block_text", ""))]
    else:
        blocks = [(i, i.block) for i in ok if i.block and i.block.get("isolated")]
        if restricted:
            blocks += name_blocks
    if v.level == "L1" or v.strict_block:
        for i, b in blocks:
            found = find_addresses(b.get("block_text", ""))
            if found:
                addr = Fact("website_address", found[0]["value"], i.idx,
                            "text (legal-name block)" if b.get("kind") == "legal_name" else "text (org-number block)",
                            found[0]["span"])
                break
    else:
        reg_pcs = {(rec.get(k) or {}).get("postnummer") for k in ("forretningsadresse", "postadresse")} - {None}
        for i in ok:
            found = [a for a in find_addresses(i.text) if a["postcode"] in reg_pcs]
            if found:
                addr = Fact("website_address", found[0]["value"], i.idx, "text", found[0]["span"])
                break
    if addr is None and rich and not v.strict_block:
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
    def href_mismatch(i: PageInfo, e: str) -> bool:
        """<a href="mailto:post@aspectomarine.no">post@aspecto.no</a>: href and visible address differ."""
        for href, txt in i.mailto_anchors:
            shown = [x.lower() for x in X.EMAIL_RX.findall(txt or "")]
            if shown and (href == e or e in shown) and href not in shown:
                return True
        return False

    own_words_e = X.entity_words(rec.get("navn") or prof.name)
    own_foreign_e = X.legal_form_is_foreign(rec.get("navn") or prof.name)

    def other_entity_near(i: PageInfo, e: str) -> bool:
        for m in re.finditer(re.escape(e), i.text, re.I):
            before = i.text[max(0, m.start() - 70):m.start()]
            for ent in X.legal_entities(before, prefix_forms=True):
                if not X.is_target_entity(ent, own_words_e, own_foreign_e) and ent["end"] > len(before) - 50:
                    return True
        return False

    def named_domain(dom: str) -> bool:
        lab = squash(dom.split(".")[0])
        return (len(prof.joined_required) >= 4 and prof.joined_required in lab) or \
            any(len(squash(t)) >= 4 and squash(t) in lab for t in prof.required if t not in X.GENERIC_TOKENS)

    for e, i, in_block in cand_emails:
        if e in seen or X.PLACEHOLDER_EMAIL_RX.search(e) or not X.EMAIL_RX.fullmatch(e):
            continue
        if href_mismatch(i, e) or other_entity_near(i, e):
            continue
        # Only generic role mailboxes are published (never a person's mailbox, even when it is the registry
        # e-mail: that one is already published from Brreg as registry_email).
        kind = email_kind(e, prof, label or squash((v.domain or "").split(".")[0]))
        local = e.split("@", 1)[0]
        if kind != "role":
            continue                                      # policy L: generic role mailboxes only
        if REGION_LOCAL_RX.search(local) and v.form != "NUF":
            continue                                      # info-uk@, sales-au@: a foreign group entity's mailbox
        host_e = e.split("@", 1)[1]
        dom = X.reg_domain(host_e)
        if _denied_mail_domain(host_e, v.form) or _invoice_context(i, e):
            continue                                      # authorities, invoice processors, "Faktura: ..." lines
        equal_reg = bool(reg_em) and e == reg_em
        if restricted and not (in_block or equal_reg):
            continue
        if equal_reg:
            ok_e = True                                   # the registry e-mail with a generic local part
        else:
            # the site's own registrable domain, whose label carries a distinctive name token
            ok_e = dom == v.domain and named_domain(dom) and (v.exclusive or in_block) and \
                (in_block or not v.strict_block)
        if not ok_e:
            continue
        loc, span = email_span(i, e)
        if in_block and loc == "text":
            b = next((bb for ii, bb in blocks if ii.idx == i.idx and e in bb.get("emails", [])), None) or i.block or {}
            loc = "text (legal-name block)" if b.get("kind") == "legal_name" else "text (org-number block)"
            span = text_window(b.get("block_text", ""), e) or span
        facts.append(Fact("contact_email", e, i.idx, loc, span, qualifier=e))
        seen.add(e)

    # ---- phone (policy E): only (1) tel: links whose visible text is the same number, (2) keyword-labelled
    # numbers inside the company's own block, (3) the registry phone - never postbox/postcode/invoice lines,
    # shared registry numbers outside the company's named block, numbers next to another entity or another
    # domain's e-mail, a person's number, fax, foreign numbers or stale tel: hrefs. Max 3.
    ph: dict[str, tuple] = {}
    nrx_f = X.legal_name_rx(rec.get("navn") or prof.name)
    shared_ph = set(v.shared_reg_phones)

    def add_phone(prio: int, raw: str, i: PageInfo, loc: str, span: str) -> None:
        e164 = normalize_phone(raw)
        if not e164 or not e164.startswith("+47"):
            return
        if e164 not in ph or prio < ph[e164][0]:
            ph[e164] = (prio, i.idx, loc, span)

    def tel_span(i: PageInfo, t: str) -> str:
        return raw_window(i.page.html, "tel:" + t, 20, 60) or raw_window(i.page.html, t, 40, 40) or t

    def occurrences(text: str, p8: str) -> list[re.Match]:
        rx = r"(?<![\d])(?:(?:\+|00)\s?47[\s.\-]?|\(\+47\)\s?)?" + r"[\s.\-  ]?".join(p8) + r"(?![\d])"
        return list(re.finditer(rx, text))

    own_words_f = X.entity_words(rec.get("navn") or prof.name)
    own_foreign_f = X.legal_form_is_foreign(rec.get("navn") or prof.name)

    def bad_context(text: str, p8: str) -> bool:
        """postbox/postcode, invoice line, fax, another entity's name or another domain's e-mail, a person."""
        for m in occurrences(text, p8):
            before = text[max(0, m.start() - 60):m.start()]
            after = text[m.end():m.end() + 60]
            if re.search(r"(?:postboks|post\s?box|boks|pb\.?)\s*$", before[-14:], re.I) or \
                    re.match(r"\d{4}\s\d{4}$", m.group(0).strip()) and re.match(r"\s+[A-ZÆØÅ][a-zæøå]", after):
                return True                                # "Postboks 3222 7439 Trondheim"
            if INVOICE_CONTEXT_RX.search(before[-40:]) or X.FAX_BEFORE_RX.search(before[-22:]):
                return True
            for e in X.legal_entities(before, prefix_forms=True):
                if not X.is_target_entity(e, own_words_f, own_foreign_f) and e["end"] > len(before) - 45:
                    return True                            # "Butikk – Tak og Ventilasjon AS ... Tlf"
            for em in X.EMAIL_RX.findall(after):
                d = X.reg_domain(em.split("@", 1)[1].lower())
                if d not in (v.domain, reg_em_dom):
                    return True                            # another entity's line (post@forskerforsikring.no)
            pm = re.search(r"(?:^|[\s,:–-])([A-ZÆØÅ][a-zæøå]+)(?:\s+[A-ZÆØÅ]\.?)?\s+([A-ZÆØÅ][a-zæøå]+)"
                           r"(?:[\s,–-]+[\wæøå ]{0,25})?[:\s]*$", before[-45:])
            if pm and not ({pm.group(1).lower(), pm.group(2).lower()} & NOT_NAME_WORDS) and \
                    not re.search(r"(?i)(telefon|tlf|sentralbord|kontor|hovednummer|kundeservice)[:\s]*$", before[-20:]):
                return True                                # "Ola Nordmann, vaktmester: 912 34 567"
        return False

    def staff_context(text: str, p8: str) -> bool:
        """Number listed next to a person (personal e-mail within 45 chars): a staff line, not a company line."""
        for m in occurrences(text, p8):
            win = text[max(0, m.start() - 45): m.end() + 45]
            for e in X.EMAIL_RX.findall(win):
                e = e.lower()
                own = X.reg_domain(e.split("@", 1)[1]) in {v.domain, reg_em_dom} - {"", None}
                if own and e != reg_em and email_kind(e, prof, label or "") in ("personal", "other"):
                    return True
        return False

    def mobile(p8: str | None) -> bool:
        return bool(p8) and p8[0] in "49"

    def foreign_nationals(i: PageInfo) -> set[str]:
        out = set()
        for t in i.x.get("tel_links", []):
            if re.match(r"\s*(?:\+|00)\s?(?!47)\d", t):
                out.add(re.sub(r"\D", "", t)[-8:])
        return out

    def visible_tel(i: PageInfo, t: str) -> bool:
        """<a href="tel:X">X</a>: the visible text shows the same number (no invisible/stale hrefs)."""
        hd = re.sub(r"\D", "", t)[-8:]
        anchors = [txt for href, txt in i.tel_anchors if href.strip() == t.strip()]
        return any(re.sub(r"\D", "", txt or "")[-8:] == hd for txt in anchors)

    def named_block(b: dict) -> bool:
        return bool(nrx_f) and bool(nrx_f.search(b.get("block_text", "")))

    def usable(p8: str, text: str, i: PageInfo, in_named_block: bool) -> bool:
        if not p8 or p8 in foreign_nationals(i):
            return False
        reg = p8 in reg_ph
        if reg and p8 in shared_ph:
            return False                                   # a switchboard other organisations also file
        if set(map(str, ((v.shared_contacts or {}).get("phones") or {}).get(p8) or [])) - {target}:
            return False                                   # any number the registry lists for another org number
        if not reg and mobile(p8):
            return False                                   # never a person's mobile
        return not bad_context(text, p8) and (reg or not staff_context(text, p8))

    proof_page = v.proof["page"] if (v.proof and v.level == "L1") else None
    for i, b in blocks:
        if b.get("kind") == "legal_name" or i.idx != proof_page or b is not i.block:
            continue                                   # policy L: only the company's own (proof) org-number block
        btxt = b.get("block_text", "")
        bname = "legal-name block" if b.get("kind") == "legal_name" else "org-number block"
        nb = named_block(b)
        for p in b.get("tel_links", []):
            n8 = _nat8(p)
            if n8 and visible_tel(i, p) and usable(n8, i.text, i, nb):
                add_phone(0 if n8 in reg_ph else 1, p, i, f'a[href^="tel:"] ({bname})', tel_span(i, p))
        for tp in text_phones(btxt):
            if (tp["kw"] or tp["digits"] in reg_ph) and usable(tp["digits"], btxt, i, nb) and \
                    usable(tp["digits"], i.text, i, nb):
                add_phone(0 if tp["digits"] in reg_ph else 1, tp["raw"], i, f"text ({bname})", tp["span"])
    if not v.strict_block:
        for i in ok:
            for t in i.x.get("tel_links", []):
                n8 = _nat8(t)
                if not n8 or not visible_tel(i, t) or n8 not in reg_ph:
                    continue                           # policy L: outside the proof block only the registry phone
                if usable(n8, i.text, i, False):
                    add_phone(0, t, i, 'a[href^="tel:"]', tel_span(i, t))
            for tp in text_phones(i.text):
                if tp["digits"] in reg_ph and not tp["fax"] and usable(tp["digits"], i.text, i, False):
                    add_phone(0, tp["raw"], i, "text", tp["span"])
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
                if restricted and not v.exact_strong and not in_block:
                    continue
                if not social_matches(can[0], prof, label if not restricted else None) or \
                        not social_handle_ok(can[1], prof, label if not restricted else None, v.form, v.places):
                    continue
                if v.form != "NUF" and (FOREIGN_HANDLE_RX.search(squash(can[1])) or
                                        (FOREIGN_FORM_HANDLE_RX.search(unquote(can[1])) and
                                         not (v.form == "SA" and unquote(can[1]).lower().endswith("sa")))):
                    continue
                so_seen.add(can[0])
                facts.append(Fact("social_profile", {"platform": platform, "url": can[0], "handle": can[1]}, i.idx,
                                  "a[href]", raw_window(i.page.html, u.split("://", 1)[-1], 30, 60) or u,
                                  qualifier=f"{platform}|{can[0]}"))
    if rich:
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
                            social_handle_ok(can[1], prof, label, v.form, v.places) and \
                            (v.form == "NUF" or not (FOREIGN_HANDLE_RX.search(squash(can[1])) or
                                                     FOREIGN_FORM_HANDLE_RX.search(unquote(can[1])))):
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
                if restricted and not v.exact_strong and not (v.subpath and not v.other_entities):
                    continue
                sp = urlsplit(u)
                if X.SKIP_EXT_RX.search(sp.path) or sp.path in ("", "/") or NOT_CAREERS_PATH_RX.search(sp.path):
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
    facts = anchor_spans(facts, v)
    facts.sort(key=Fact.sort_key)
    return facts


def _norm_span(sp: str) -> str:
    """The auditor's normalisation: HTML entities and %-escapes decoded, whitespace collapsed, lower-case."""
    import html as _html
    return re.sub(r"\s+", " ", _html.unescape(unquote(sp or ""))).strip().lower()


def _span_has(f: "Fact", span: str) -> bool:
    """Does `span` contain the fact's value as the source shows it?"""
    n = _norm_span(span)
    if not n:
        return False
    val = f.value
    if f.field == "contact_phone":
        return str(val)[-8:] in re.sub(r"\D", "", n)
    if f.field == "contact_email":
        e = str(val).lower()
        return e in n or e[::-1] in n or ("cfemail" in (f.locator or "") and ("cfemail" in n or "email-protection" in n))
    if f.field == "social_profile":
        h = str(val.get("handle", "")).lower()
        return bool(h) and (_norm_span(h) in n or h in n)
    if f.field in ("logo_url", "careers_page", "news_page"):
        pth = unquote(urlsplit(str(val)).path).rstrip("/").lower()
        return _norm_span(str(val)) in n or bool(pth and pth in n)
    if f.field == "website_language":
        return str(val).lower() in n
    if f.field == "news_item":
        val = val if isinstance(val, dict) else {}
        t, u = _norm_span(val.get("title") or ""), _norm_span(val.get("url") or "")
        if t:
            return t[:40] in n                         # anchored on the item's own title
        pth = unquote(urlsplit(val.get("url") or "").path).lower().rstrip("/")
        return bool((u and u in n) or (pth and pth in n))
    v = _norm_span(str(val))
    if f.field == "website_description":
        return _norm_span(str(val).rstrip("…").strip()) in n      # the whole (possibly truncated) value
    return v[:60] in n or (len(n) >= 0.5 * len(v) and n in v)


def _raw_slice(raw: str, s0: int, e0: int, limit: int = 300) -> str:
    """Verbatim slice of `raw` of at most `limit` chars that contains raw[s0:e0], centred on it."""
    ln = e0 - s0
    if ln >= limit:
        return raw[s0:s0 + limit]
    extra = limit - ln
    a0 = max(0, s0 - extra // 2)
    b0 = min(len(raw), a0 + limit)
    a0 = max(0, min(a0, b0 - limit))
    return raw[a0:b0]


_SEP = r"(?:\s|&nbsp;|&#160;|&#xa0;|[.\-\u00a0\u202f])*"


def _raw_patterns(f: "Fact", raw: str) -> list[re.Pattern]:
    import html as _html
    val = f.value
    pats: list[str] = []
    if f.field == "contact_phone":
        d = str(val)[-8:]
        pats.append(r"(?<!\d)" + _SEP.join(d) + r"(?!\d)")
    elif f.field == "contact_email":
        e = str(val)
        pats += [re.escape(e), re.escape(_html.escape(e)), re.escape(e[::-1])]
        if "cfemail" in (f.locator or ""):
            for m in re.finditer(r'data-cfemail="([0-9a-fA-F]+)"|email-protection#([0-9a-fA-F]+)', raw):
                hx = m.group(1) or m.group(2)
                if X.decode_cfemail(hx) == e.lower():
                    pats.append(re.escape(m.group(0)))
    elif f.field == "social_profile":
        h = str(val.get("handle", ""))
        pats += [re.escape(h), re.escape(quote_path(h)), re.escape(quote_path(h).lower())]
    elif f.field in ("logo_url", "careers_page", "news_page"):
        u = str(val)
        sp = urlsplit(u)
        rel = (sp.path or "") + (("?" + sp.query) if sp.query else "")
        for cand in (u, _html.escape(u), "//" + (sp.netloc or "") + rel, rel, _html.escape(rel), sp.path or ""):
            if cand and len(cand) > 1:
                pats.append(re.escape(cand))
    elif f.field == "website_title":
        pats.append(r"<title[^>]*>[^<]*</title>")
    elif f.field == "website_description":
        trx = html_text_rx(str(val).rstrip("…").strip(), max_tokens=80)
        if trx is not None:
            pats.append(trx.pattern)
        pats.append(r"<meta[^>]+(?:name|property)=[\"'](?:description|og:description)[\"'][^>]*>")
        pats.append(r"<meta[^>]+content=[\"'][^\"']*[\"'][^>]+(?:name|property)=[\"'](?:description|og:description)[\"'][^>]*>")
    elif f.field == "brand_name":
        pats.append(r"<meta[^>]+property=[\"']og:site_name[\"'][^>]*>")
        pats.append(re.escape(str(val)))
        pats.append(re.escape(_html.escape(str(val))))
    elif f.field == "website_language":
        pats.append(r"<html\b[^>]*>")
    else:                                               # address and other plain strings: contiguous tokens only
        toks = [t for t in re.split(r"\s+", str(val).strip()) if t]
        if toks:
            pats.append(r"(?:\s|&nbsp;|&#160;)+".join(re.escape(_html.escape(t, quote=False)) if
                                                         _html.escape(t, quote=False) in raw else re.escape(t) for t in toks))
    out = []
    for ptn in pats:
        try:
            out.append(re.compile(ptn, re.I))
        except re.error:
            continue
    return out


def html_text_rx(text: str, max_tokens: int = 10) -> re.Pattern[str] | None:
    """Regex for `text` as it may be written in HTML source (entities for punctuation/letters, any whitespace)."""
    toks = [t for t in re.split(r"\s+", (text or "").strip()) if t][:max_tokens]
    if not toks:
        return None

    def ch(c: str) -> str:
        if c.isascii() and c.isalnum():
            return re.escape(c)
        return f"(?:{re.escape(c)}|&#0*{ord(c)};|&#[xX]0*{ord(c):x};|&[a-zA-Z]+;)"

    try:
        return re.compile(r"(?:\s|&nbsp;|&#160;)+".join("".join(ch(c) for c in t) for t in toks), re.I)
    except re.error:
        return None


def quote_path(s: str) -> str:
    from urllib.parse import quote
    return quote(s, safe="/@:+-._~!$&'()*,;=")


def _ascii_slice(raw: str, s0: int, e0: int, limit: int = 300) -> str | None:
    """Like _raw_slice, but only pure-ASCII text (byte-identical whatever the page's charset)."""
    if not raw[s0:e0].isascii() or e0 - s0 > limit:
        return None
    a0, b0 = s0, e0
    while a0 > 0 and raw[a0 - 1].isascii() and b0 - a0 < limit and s0 - a0 < limit // 2:
        a0 -= 1
    while b0 < len(raw) and raw[b0].isascii() and b0 - a0 < limit:
        b0 += 1
    while a0 > 0 and raw[a0 - 1].isascii() and b0 - a0 < limit:
        a0 -= 1
    return raw[a0:b0]


def span_for(page: Page, f: "Fact") -> str | None:
    """A verbatim fragment of `page`'s source (<= 300 chars) that contains f.value as the source shows it.
    Pages whose body is not UTF-8 (windows-1252, ...) only get pure-ASCII spans, so the span is a byte-exact
    substring of the stored snapshot under any decoding; values that need non-ASCII text are then dropped."""
    raw = page.html or ""
    ascii_only = not getattr(page, "utf8", True)

    def good(sp: str | None) -> bool:
        return bool(sp) and sp in raw and len(sp) <= 300 and _span_has(f, sp) and (not ascii_only or sp.isascii())

    if good(f.span):
        return f.span
    for rx in _raw_patterns(f, raw):
        for m in rx.finditer(raw):
            cand = _ascii_slice(raw, m.start(), m.end()) if ascii_only else _raw_slice(raw, m.start(), m.end())
            if good(cand):
                return cand
    return None


def anchor_spans(facts: list["Fact"], v: Verdict) -> list["Fact"]:
    """Policy N: every published fact's evidence span is a VERBATIM fragment of the stored page source that
    contains the value as the source shows it (phones by digit pattern, e-mails by address or cfemail token,
    logos by their raw URL form, ...). Facts without such a fragment are dropped."""
    out = []
    for f in facts:
        new = span_for(v.infos[f.page].page, f)
        if new:
            f.span = new
            out.append(f)
    return out


def feed_url(v: Verdict) -> str | None:
    """RSS/Atom feed linked from the verified home page (comment feeds skipped)."""
    if not v.published or not v.infos or not v.site_url:
        return None
    if v.restricted and not v.exact_strong and not (v.subpath and not v.other_entities):
        return None
    for u, t in v.infos[0].feeds:
        if re.search(r"comment|kommentar", u + " " + t, re.I):
            continue
        if u.startswith(("http://", "https://")) and under_base(u, v.site_url, v.subpath, v.exclusive):
            return u
    return None
