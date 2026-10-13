"""Candidate official-website domains for a registry record.

Candidates are *discovery leads only*. Nothing derived from a candidate is published unless the
fetched site passes the identity gate in `verify.py` (organisation number printed on the site,
or registry-filed website plus independent corroboration).
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlsplit

from .extract import FREEMAIL, reg_domain

SUFFIX_RX = re.compile(
    r"\b(asa|as|enk|nuf|sa|da|ans|ba|ks|iks|kf|sti|stiftelse|i likvidasjon|under avvikling|"
    r"under tvangsavvikling|konkursbo|norsk avdeling av utenlandsk foretak|filial)\b\.?", re.I)
# Words that rarely form the brand on their own (dropped only for the 'core' variant).
GENERIC_SUFFIX_WORDS = {"holding", "holdings", "invest", "investering", "eiendom", "eiendommer", "gruppen", "group",
                        "norge", "norway", "norden", "nordic", "scandinavia", "management", "consulting", "drift",
                        "utvikling", "eiendomsselskap", "eiendomsutvikling", "kapital", "capital", "ventures"}
# Hosts that are never a company's own site even if registered as hjemmeside.
NON_COMPANY_HOSTS = ("facebook.com", "instagram.com", "linkedin.com", "twitter.com", "x.com", "youtube.com",
                     "tiktok.com", "google.com", "goo.gl", "sites.google.com", "wixsite.com", "blogspot.com",
                     "proff.no", "purehelp.no", "gulesider.no", "1881.no", "finn.no", "brreg.no", "spond.com",
                     "vipps.no", "linktr.ee", "mittanbud.no", "bedriftsdatabasen.no")


def translit(s: str, variant: int = 0) -> str:
    m = [{"æ": "ae", "ø": "o", "å": "a", "ä": "a", "ö": "o", "ü": "u", "é": "e", "è": "e"},
         {"æ": "ae", "ø": "oe", "å": "aa", "ä": "ae", "ö": "oe", "ü": "u", "é": "e", "è": "e"}][variant]
    return "".join(m.get(c, c) for c in s)


def name_tokens(name: str) -> list[str]:
    n = unicodedata.normalize("NFKC", (name or "").lower())
    n = re.split(r"\bv/|\bavd\.?\b|\bc/o\b|\bavdeling\b", n)[0]
    n = SUFFIX_RX.sub(" ", n)
    n = n.replace("&", " og ").replace("+", " ")
    return [t for t in re.findall(r"[a-z0-9æøåäöüéè]+", n) if t]


def name_slugs(name: str) -> dict[str, str]:
    toks = name_tokens(name)
    if not toks:
        return {}
    t0 = [translit(t, 0) for t in toks]
    t1 = [translit(t, 1) for t in toks]
    out: dict[str, str | None] = {
        "joined": "".join(t0),
        "hyphen": "-".join(t0) if len(t0) > 1 else None,
        "joined_alt": "".join(t1) if t1 != t0 else None,
        "idn": None,
        "core": None,
    }
    if any(c in "".join(toks) for c in "æøåäöü"):
        try:
            out["idn"] = "".join(toks).encode("idna").decode("ascii")
        except Exception:
            pass
    core = [t for t in t0 if t not in GENERIC_SUFFIX_WORDS]
    if core and core != t0 and len("".join(core)) >= 4:
        out["core"] = "".join(core)
    return {k: v for k, v in out.items() if v and 2 <= len(v) <= 63 and not v.isdigit()}


def normalize_registry_website(hjemmeside: str | None) -> str | None:
    """'www.Firma.no/side' -> 'https://www.firma.no/side' ; None for social/directory hosts."""
    if not hjemmeside:
        return None
    h = hjemmeside.strip().strip("/")
    if not h or " " in h:
        return None
    if not re.match(r"https?://", h, re.I):
        h = "https://" + h
    sp = urlsplit(h)
    host = (sp.hostname or "").lower()
    if not host or "." not in host:
        return None
    if any(host == d or host.endswith("." + d) for d in NON_COMPANY_HOSTS):
        return None
    path = sp.path if sp.path not in ("", "/") else "/"
    return f"https://{host}{path}"


def email_domain(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    d = email.split("@", 1)[1].strip().lower().rstrip(".>")
    if not re.fullmatch(r"[a-z0-9æøå.-]+\.[a-z]{2,24}", d):
        return None
    if d in FREEMAIL or reg_domain(d) in FREEMAIL:
        return None
    return reg_domain(d)


def candidates(rec: dict, max_cands: int = 8) -> list[tuple[str, str]]:
    """[(kind, host_or_url)] in priority order. kind ∈ registry_website, email_domain, name_*."""
    out: list[tuple[str, str]] = []
    reg = normalize_registry_website(rec.get("hjemmeside"))
    if reg:
        out.append(("registry_website", reg))
    ed = email_domain(rec.get("epostadresse"))
    if ed:
        out.append(("email_domain", ed))
    sl = name_slugs(rec.get("navn") or "")
    for key, tld in (("joined", ".no"), ("hyphen", ".no"), ("idn", ".no"), ("joined_alt", ".no"),
                     ("core", ".no"), ("joined", ".com"), ("hyphen", ".com"), ("core", ".com")):
        if sl.get(key):
            out.append((f"name_{key}{tld.replace('.', '_')}", sl[key] + tld))
    seen: set[str] = set()
    res: list[tuple[str, str]] = []
    for kind, d in out:
        host = urlsplit(d).hostname if "://" in d else d
        k = reg_domain(host or d)
        if k in seen:
            continue
        seen.add(k)
        res.append((kind, d))
    return res[:max_cands]
