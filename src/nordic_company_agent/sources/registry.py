"""Enhetsregisteret identity resolution and core legal facts (batch module).

Flow per batch:
  1. MOD11-check every input locally (invalid numbers cost no request).
  2. Optional evaluator-supplied bulk snapshot (`config.registry_snapshot`): rows for the batch are
     loaded once per run (0 requests) and used only as a fallback when the live register could not
     be checked; the live record is always preferred as current.
  3. Live batch lookup `GET /enheter?organisasjonsnummer=<<=1000 numbers>&size=n` (tier "registry").
  4. Numbers missing from the batch (the batch silently drops deleted, removed and sub-unit numbers)
     get one `GET /enheter/{orgnr}`: 200 SlettetEnhet -> deleted, 410 -> removed for legal reasons
     (publish nothing), 404 -> `GET /underenheter/{orgnr}` -> sub-unit, else not found.
  5. Echo check: a record is only used when its `organisasjonsnummer` equals the input.

Evidence hashing (content_sha256) choice
----------------------------------------
For records taken from the multi-id batch call, `content_sha256` is the SHA-256 of the record's own
raw bytes, sliced verbatim out of the batch body (the JSON text of that array element, not a
re-serialisation). Verified live on 2026-10-13: these bytes are byte-identical to the body of
`GET /enheter/{orgnr}` (the evidence `source_url`), and Brreg serialises records with sorted keys,
compact separators and unescaped UTF-8, i.e. exactly the canonical form
`json.dumps(rec, sort_keys=True, ensure_ascii=False, separators=(",", ":"))` with numbers kept as
written. Each per-record body is stored as its own snapshot, so every claim_span is a verbatim
substring of the snapshot named by its evidence and can be re-checked by fetching `source_url`.
The batch body itself is also stored (its URL is the evidence's `retrieved_via`).
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from json.decoder import scanstring
from pathlib import Path
from typing import Any

from .. import geo, registry_csv
from ..context import CURRENT_COMPANY, RunContext
from ..envelope import (NLOD, OFFICIAL_REGISTRY, OFFICIAL_REGISTRY_BULK, Claim, CompanyResult, Evidence,
                        ModuleOutcome, utc_iso)
from ..orgnr import formatted, is_valid

MODULE = "registry"
VERSION = "1"
METHOD = f"{MODULE}@{VERSION}"
TIER = "registry"

BASE = "https://data.brreg.no/enhetsregisteret/api"
SOURCE_NAME = "Brønnøysundregistrene, Enhetsregisteret open data API (data.brreg.no)"
BULK_SOURCE_NAME = "Brønnøysundregistrene, Enhetsregisteret bulk snapshot (supplied file)"
ACCEPT_JSON = {"Accept": "application/json"}
BATCH_SIZE = 1000
BATCH_MAX_BYTES = 24_000_000
SPAN_MAX = 300
BUDGET_NOTE = "not checked: request budget reserved for higher-priority sources"
REMOVED_NOTE = "removed from the register for legal reasons (HTTP 410)"
CAPITAL_FORMS = {"AS", "ASA"}            # share capital is mandatory for these forms
PLACEHOLDER_DATES = {"1900-01-01"}
PLACEHOLDER_INDUSTRY = {"00.000"}


def enhet_url(orgnr: str) -> str:
    return f"{BASE}/enheter/{orgnr}"


def underenhet_url(orgnr: str) -> str:
    return f"{BASE}/underenheter/{orgnr}"


def enhet_page(orgnr: str) -> str:
    return f"https://virksomhet.brreg.no/nb/oppslag/enheter/{orgnr}"


def underenhet_page(orgnr: str) -> str:
    return f"https://virksomhet.brreg.no/nb/oppslag/underenheter/{orgnr}"


# =========================================================================== raw JSON spans
# Claim spans must be verbatim fragments of the stored body. These helpers locate the raw text of
# a JSON-pointer target without re-serialising anything.
_DEC = json.JSONDecoder()
_DEC_DECIMAL = json.JSONDecoder(parse_float=Decimal)
_WS = " \t\n\r"


def _skip(s: str, i: int) -> int:
    n = len(s)
    while i < n and s[i] in _WS:
        i += 1
    return i


def pointer(*parts: Any) -> str:
    """JSON pointer from path parts: pointer('a', 0, 'b') -> '/a/0/b'."""
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in parts)


def _tokens(ptr: str) -> list[str]:
    if ptr in ("", "/"):
        return []
    return [t.replace("~1", "/").replace("~0", "~") for t in ptr.split("/")[1:]]


def locate(text: str, ptr: str) -> tuple[int, int, int] | None:
    """Return (fragment_start, value_start, value_end) for the node at `ptr` in raw JSON `text`.

    For an object member `fragment_start` is the opening quote of its key, so
    `text[fragment_start:value_end]` is the verbatim `"key":value` member."""
    try:
        i = _skip(text, 0)
        frag = i
        for tok in _tokens(ptr):
            i = _skip(text, i)
            c = text[i]
            if c == "{":
                i += 1
                while True:
                    i = _skip(text, i)
                    if text[i] != '"':
                        return None
                    kstart = i
                    key, i = scanstring(text, i + 1)
                    i = _skip(text, i)
                    if text[i] != ":":
                        return None
                    i = _skip(text, i + 1)
                    if key == tok:
                        frag = kstart
                        break
                    _, i = _DEC.raw_decode(text, i)
                    i = _skip(text, i)
                    if text[i] == ",":
                        i += 1
                        continue
                    return None
            elif c == "[":
                if not tok.isdigit():
                    return None
                want, k = int(tok), 0
                i += 1
                while True:
                    i = _skip(text, i)
                    if text[i] == "]":
                        return None
                    if k == want:
                        frag = i
                        break
                    _, i = _DEC.raw_decode(text, i)
                    i = _skip(text, i)
                    if text[i] == ",":
                        i += 1
                        k += 1
                        continue
                    return None
            else:
                return None
        i = _skip(text, i)
        _, end = _DEC.raw_decode(text, i)
        return frag, i, end
    except (ValueError, IndexError):
        return None


def span_at(text: str | None, ptr: str, max_len: int = SPAN_MAX) -> str | None:
    """Verbatim raw fragment for `ptr` (the `"key":value` member for object members), truncated."""
    if not text:
        return None
    loc = locate(text, ptr)
    if loc is None:
        return None
    return text[loc[0]:loc[2]][:max_len]


def members_span(text: str | None, parent_ptr: str, keys: list[str], max_len: int = SPAN_MAX) -> str | None:
    """Verbatim raw fragment covering several sibling members of the object at `parent_ptr`.

    Tries all keys, then progressively drops leading keys until the covering range fits
    `max_len`; finally falls back to the (truncated) member of the first present key."""
    if not text:
        return None
    locs = []
    for k in keys:
        loc = locate(text, parent_ptr + pointer(k))
        if loc is not None:
            locs.append(loc)
    if not locs:
        return None
    for i in range(len(locs)):
        sub = locs[i:]
        start, end = min(l[0] for l in sub), max(l[2] for l in sub)
        if end - start <= max_len:
            return text[start:end]
    return text[locs[0][0]:locs[0][2]][:max_len]


def array_elements(text: str, ptr: str) -> list[tuple[int, int]]:
    """(start, end) offsets of each element of the array at `ptr` (empty if absent/not an array)."""
    loc = locate(text, ptr)
    if loc is None:
        return []
    i = loc[1]
    if text[i] != "[":
        return []
    out: list[tuple[int, int]] = []
    i += 1
    while True:
        i = _skip(text, i)
        if i >= len(text) or text[i] == "]":
            return out
        start = i
        _, i = _DEC.raw_decode(text, i)
        out.append((start, i))
        i = _skip(text, i)
        if i < len(text) and text[i] == ",":
            i += 1


def decode_text(body: bytes) -> str:
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("utf-8", errors="replace")


def loads_decimal(text: str) -> Any:
    return _DEC_DECIMAL.decode(text)


# =========================================================================== personal-data redaction
_BIRTH_KEYS = ("fodselsdato", "fødselsdato", "foedselsdato", "fodselsnummer", "fødselsnummer", "foedselsnummer",
               "birthDate", "birthdate", "dateOfBirth", "date_of_birth", "birth_date")
_BIRTH_KEYS_LOWER = {k.lower() for k in _BIRTH_KEYS}
_KEYALT = "|".join(re.escape(k) for k in _BIRTH_KEYS)
_VAL = r'(?:"(?:[^"\\]|\\.)*"|-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?|null|true|false)'
_RX_BEFORE_COMMA = re.compile(rf'"(?:{_KEYALT})"\s*:\s*{_VAL}\s*,\s*')
_RX_AFTER_COMMA = re.compile(rf'\s*,\s*"(?:{_KEYALT})"\s*:\s*{_VAL}')
_RX_ALONE = re.compile(rf'"(?:{_KEYALT})"\s*:\s*{_VAL}')


def _has_birth_keys(obj: Any) -> bool:
    if isinstance(obj, dict):
        return any(str(k).lower() in _BIRTH_KEYS_LOWER or _has_birth_keys(v) for k, v in obj.items())
    if isinstance(obj, list):
        return any(_has_birth_keys(v) for v in obj)
    return False


def _strip_birth_keys(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _strip_birth_keys(v) for k, v in obj.items() if str(k).lower() not in _BIRTH_KEYS_LOWER}
    if isinstance(obj, list):
        return [_strip_birth_keys(v) for v in obj]
    return obj


def redact_birth_dates(body: bytes) -> bytes:
    """Remove birth-date / national-id members from a JSON body before it is stored.

    Members are cut out textually so every other byte (and so every claim span) stays verbatim;
    if the result does not parse cleanly the body is re-serialised without those keys."""
    text = decode_text(body)
    red = _RX_BEFORE_COMMA.sub("", text)
    red = _RX_AFTER_COMMA.sub("", red)
    red = _RX_ALONE.sub("", red)
    try:
        if not _has_birth_keys(json.loads(red)):
            return red.encode("utf-8")
    except ValueError:
        pass
    try:
        clean = _strip_birth_keys(json.loads(text))
    except ValueError:
        return b""  # unparseable: store nothing rather than risk storing personal data
    return json.dumps(clean, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


# =========================================================================== small helpers
def check(name: str, passed: bool | None, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": passed, "detail": detail}


def outcome_state_for_status(status: int) -> str:
    return "blocked" if status in (401, 403, 429) else "failed"


def slug(name: str) -> str:
    """Normalised name for stable claim qualifiers: 'Haakon Stephen Bruun-Hanssen' -> 'haakon-stephen-bruun-hanssen'."""
    s = unicodedata.normalize("NFKC", name or "").lower()
    s = re.sub(r"[^\w]+", "-", s, flags=re.UNICODE).strip("-_")
    return re.sub(r"[-_]+", "-", s)


def to_decimal(v: Any) -> Decimal | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, Decimal):
        return v
    if isinstance(v, int):
        return Decimal(v)
    if isinstance(v, float):
        return Decimal(repr(v))
    try:
        return Decimal(str(v).strip())
    except (InvalidOperation, ValueError):
        return None


def money_display(amount: Decimal, currency: str | None) -> str:
    if amount == amount.to_integral_value():
        s = f"{int(amount):,}".replace(",", " ")
    else:
        s = f"{amount:,.2f}".replace(",", " ")
    return f"{currency} {s}" if currency else s


def address_value(a: dict[str, Any]) -> dict[str, Any]:
    return {
        "lines": [str(x) for x in (a.get("adresse") or []) if str(x).strip()],
        "postal_code": a.get("postnummer"),
        "city": a.get("poststed"),
        "municipality": a.get("kommune"),
        "municipality_number": a.get("kommunenummer"),
        "country": a.get("land"),
        "country_code": a.get("landkode"),
    }


def address_display(a: dict[str, Any]) -> str:
    parts = [str(x).strip() for x in (a.get("adresse") or []) if str(x).strip()]
    pc = " ".join(str(x) for x in (a.get("postnummer"), a.get("poststed")) if x)
    if pc:
        parts.append(pc)
    if a.get("landkode") and a.get("landkode") != "NO" and a.get("land"):
        parts.append(str(a["land"]))
    return ", ".join(parts)


def _has_address(a: Any) -> bool:
    return isinstance(a, dict) and any(a.get(k) for k in ("adresse", "postnummer", "poststed", "kommunenummer", "land"))


def joined_lines(v: Any) -> str | None:
    if isinstance(v, list):
        s = " ".join(str(x).strip() for x in v if str(x).strip())
    elif isinstance(v, str):
        s = " ".join(v.split())
    else:
        return None
    return s or None


def _date_part(v: Any) -> str | None:
    return str(v)[:10] if v else None


# =========================================================================== evidence source
_CSV_COL = {v: k for k, v in registry_csv._KEY_FIX.items()}
_REPRESENTATIVE_LEAF = ("adresse", "kode", "belop", "postnummer", "poststed", "tekst")


@dataclass
class RecordSource:
    """Builds Evidence for JSON-pointer locations of one registry record (live JSON or bulk snapshot)."""
    orgnr: str
    kind: str                         # "live" | "csv" | "json_snapshot"
    text: str | None                  # raw JSON of the record (live) / compact JSON (json_snapshot)
    source_url: str
    source_class: str
    source_name: str
    retrieved_at: str
    sha: str | None
    page_url: str | None
    http_status: int | None = None
    retrieved_via: str | None = None
    rec: dict[str, Any] | None = None
    method: str = METHOD

    def ev(self, ptr: str, *, keys: list[str] | None = None, effective_at: str | None = None) -> Evidence:
        if self.kind == "csv":
            locator, span = self._csv(ptr, keys)
        else:
            locator = ptr or "/"
            span = members_span(self.text, ptr, keys) if keys else span_at(self.text, ptr)
        return Evidence(source_url=self.source_url, source_class=self.source_class, source_name=self.source_name,
                        retrieved_at=self.retrieved_at, content_sha256=self.sha, claim_span=span, locator=locator,
                        extraction_method=self.method, page_url=self.page_url, effective_at=effective_at,
                        http_status=self.http_status, licence=NLOD, retrieved_via=self.retrieved_via)

    def _csv(self, ptr: str, keys: list[str] | None) -> tuple[str, str | None]:
        toks = [t for t in _tokens(ptr) if not t.isdigit()]
        node: Any = self.rec or {}
        for t in toks:
            node = node.get(t) if isinstance(node, dict) else None
        if isinstance(node, dict):
            for leaf in (keys or []) + list(_REPRESENTATIVE_LEAF):
                if node.get(leaf) not in (None, "", []):
                    toks, node = toks + [leaf], node[leaf]
                    break
        col = ".".join(toks)
        col = _CSV_COL.get(col, col)
        if isinstance(node, list):
            span = "\n".join(str(x.get("tekst", x)) if isinstance(x, dict) else str(x) for x in node)
        elif isinstance(node, bool):
            span = "true" if node else "false"
        elif node is None or isinstance(node, dict):
            span = None
        else:
            span = str(node)
        return f"csv:organisasjonsnummer={self.orgnr};column={col}", (span[:SPAN_MAX] if span else None)


def registry_evidence(company: CompanyResult, ptr: str, *, keys: list[str] | None = None,
                      effective_at: str | None = None) -> Evidence | None:
    """Evidence for another location of this company's registry record (for other modules, e.g. the
    website module citing `/hjemmeside`). Returns None when no registry record was resolved."""
    base = company.data.get("registry_evidence")
    if not isinstance(base, Evidence):
        return None
    src: RecordSource | None = company.data.get("_registry_source")
    if isinstance(src, RecordSource):
        return src.ev(ptr, keys=keys, effective_at=effective_at)
    return dataclasses.replace(base, locator=ptr, claim_span=None, effective_at=effective_at)


# =========================================================================== claim builders
def _claim(field_name: str, value: Any, ev: Evidence | list[Evidence], *, qualifier: str | None = None,
           display: str | None = None, effective_at: str | None = None, relation: str = "self",
           confidence: float = 1.0, method: str = METHOD, match_basis: str = "organisation_number_exact",
           note: str | None = None, availability: str = "available") -> Claim:
    evs = ev if isinstance(ev, list) else [ev]
    return Claim(field=field_name, value=value, evidence=[e for e in evs if e is not None], availability=availability,
                 confidence=confidence, qualifier=qualifier, display=display, effective_at=effective_at,
                 relation=relation, method=method, match_basis=match_basis, note=note)


_FORCED_REASONS = (
    ("tvangsopplostPgaManglendeDagligLederDato", "missing general manager (daglig leder)"),
    ("tvangsopplostPgaManglendeRevisorDato", "missing auditor"),
    ("tvangsopplostPgaManglendeRegnskapDato", "missing annual accounts"),
    ("tvangsopplostPgaMangelfulltStyreDato", "deficient board"),
    ("tvangsavvikletPgaManglendeSlettingDato", "not deleted after dissolution (forced liquidation)"),
)
_REGISTRATIONS = (
    ("business_register_registration", "registrertIForetaksregisteret", "registreringsdatoForetaksregisteret",
     "Foretaksregisteret"),
    ("vat_registration", "registrertIMvaregisteret", "registreringsdatoMerverdiavgiftsregisteret",
     "Merverdiavgiftsregisteret"),
    ("voluntary_register_registration", "registrertIFrivillighetsregisteret",
     "registreringsdatoFrivillighetsregisteret", "Frivillighetsregisteret"),
    ("foundation_register_registration", "registrertIStiftelsesregisteret", None, "Stiftelsesregisteret"),
)
_CONTACTS = (("registry_website", "hjemmeside"), ("registry_email", "epostadresse"),
             ("registry_phone", "telefon"), ("registry_mobile", "mobil"))


def _identity_claims(rec: dict[str, Any], src: RecordSource, relation: str) -> list[Claim]:
    out: list[Claim] = []
    orgnr = str(rec.get("organisasjonsnummer") or src.orgnr)
    out.append(_claim("organisation_number", orgnr, src.ev("/organisasjonsnummer"), display=formatted(orgnr),
                      relation=relation))
    if rec.get("navn"):
        out.append(_claim("legal_name", rec["navn"], src.ev("/navn"), relation=relation))
    seen: set[str] = set()
    hist = [(i, h) for i, h in enumerate(rec.get("historiskeNavn") or []) if isinstance(h, dict) and h.get("navn")]
    for i, h in sorted(hist, key=lambda t: (str(t[1].get("tilDato") or t[1].get("fraDato") or ""), t[0])):
        to, frm = h.get("tilDato"), h.get("fraDato")
        q = _date_part(to) or _date_part(frm) or str(i)
        if q in seen:
            q = f"{q}|{i}"
        seen.add(q)
        out.append(_claim("former_name", {"name": h["navn"], "from": frm, "to": to},
                          src.ev(pointer("historiskeNavn", i)), qualifier=q, relation=relation,
                          display=f"{h['navn']} (until {_date_part(to)})" if to else h["navn"],
                          effective_at=_date_part(to)))
    of = rec.get("organisasjonsform") or {}
    if isinstance(of, dict) and of.get("kode"):
        out.append(_claim("legal_form", {"code": of["kode"], "description": of.get("beskrivelse")},
                          src.ev("/organisasjonsform", keys=["beskrivelse", "kode"]), relation=relation,
                          display=f"{of.get('beskrivelse')} ({of['kode']})" if of.get("beskrivelse") else of["kode"]))
    return out


def _status_claims(rec: dict[str, Any], src: RecordSource) -> list[Claim]:
    out: list[Claim] = []
    konkurs = rec.get("konkurs")
    avvikling = rec.get("underAvvikling")
    tvang = rec.get("underTvangsavviklingEllerTvangsopplosning")
    forced = [(k, reason, rec[k]) for k, reason in _FORCED_REASONS if rec.get(k)]
    if konkurs is True:
        status, ev = "bankrupt", [src.ev("/konkurs"), src.ev("/konkursdato") if rec.get("konkursdato") else None]
        eff = rec.get("konkursdato")
    elif tvang is True:
        status = "forced_dissolution"
        ev = [src.ev("/underTvangsavviklingEllerTvangsopplosning")] + [src.ev(pointer(k)) for k, _r, _d in forced]
        eff = max((d for _k, _r, d in forced), default=None)
    elif avvikling is True:
        status, ev = "under_liquidation", [src.ev("/underAvvikling"),
                                           src.ev("/underAvviklingDato") if rec.get("underAvviklingDato") else None]
        eff = rec.get("underAvviklingDato")
    elif konkurs is False and avvikling is False and tvang is False:
        status, eff = "active", None
        ev = [src.ev("/konkurs"), src.ev("/underAvvikling"), src.ev("/underTvangsavviklingEllerTvangsopplosning")]
    else:
        status = None
    if status:
        out.append(_claim("registry_status", status, ev, effective_at=eff,
                          display=status.replace("_", " ")))
    if konkurs is True:
        out.append(_claim("bankruptcy", {"date": rec.get("konkursdato")},
                          [src.ev("/konkursdato") if rec.get("konkursdato") else src.ev("/konkurs")],
                          effective_at=rec.get("konkursdato"),
                          display=f"bankrupt since {rec['konkursdato']}" if rec.get("konkursdato") else "bankrupt"))
    if avvikling is True:
        d = rec.get("underAvviklingDato")
        out.append(_claim("liquidation", {"date": d},
                          [src.ev("/underAvviklingDato") if d else src.ev("/underAvvikling")], effective_at=d,
                          display=f"under liquidation since {d}" if d else "under liquidation"))
    if tvang is True:
        if forced:
            _key, reason, d = max(forced, key=lambda t: (str(t[2]), t[0]))
            ev = [src.ev(pointer(fk)) for fk, _r, _d in sorted(forced)]
        else:
            reason, d = None, None
            ev = [src.ev("/underTvangsavviklingEllerTvangsopplosning")]
        out.append(_claim("forced_dissolution", {"date": d, "reason": reason}, ev, effective_at=d,
                          display=", ".join(x for x in (reason, d) if x) or "under forced dissolution"))
    return out


def enhet_claims(rec: dict[str, Any], src: RecordSource) -> list[Claim]:
    """All self claims for an active (`respons_klasse: Enhet`) record. `rec` should be parsed with
    Decimal floats (live) or come from `registry_csv` (snapshot)."""
    out = _identity_claims(rec, src, "self")
    out += _status_claims(rec, src)
    of = rec.get("organisasjonsform")
    form = (of.get("kode") or "") if isinstance(of, dict) else ""

    for fld, key in (("business_address", "forretningsadresse"), ("postal_address", "postadresse")):
        a = rec.get(key)
        if _has_address(a):
            out.append(_claim(fld, address_value(a), src.ev(pointer(key)), display=address_display(a)))
    fa = rec.get("forretningsadresse") if isinstance(rec.get("forretningsadresse"), dict) else {}
    knr = fa.get("kommunenummer")
    if knr:
        mev = src.ev("/forretningsadresse", keys=["kommune", "kommunenummer"])
        out.append(_claim("municipality", {"name": fa.get("kommune"), "number": knr}, mev,
                          display=f"{fa.get('kommune')} ({knr})" if fa.get("kommune") else knr))
        county = geo.county_for_municipality(knr)
        if county:
            out.append(_claim("county", county, src.ev("/forretningsadresse/kommunenummer"), display=county["name"],
                              match_basis="derived", method=geo.METHOD, confidence=0.99,
                              note=f"derived from municipality number {knr}: first two digits = 2024 county number"))
    if fa.get("landkode") or fa.get("land"):
        out.append(_claim("country", {"code": fa.get("landkode"), "name": fa.get("land")},
                          src.ev("/forretningsadresse", keys=["land", "landkode"]),
                          display=f"{fa.get('land')} ({fa.get('landkode')})" if fa.get("land") else fa.get("landkode")))

    for rank in (1, 2, 3):
        n = rec.get(f"naeringskode{rank}")
        if isinstance(n, dict) and n.get("kode") and n["kode"] not in PLACEHOLDER_INDUSTRY:
            out.append(_claim("industry", {"code": n["kode"], "description": n.get("beskrivelse"), "rank": rank},
                              src.ev(pointer(f"naeringskode{rank}"), keys=["beskrivelse", "kode"]), qualifier=str(rank),
                              display=f"{n['kode']} {n.get('beskrivelse') or ''}".strip()))
    sek = rec.get("institusjonellSektorkode")
    if isinstance(sek, dict) and sek.get("kode"):
        out.append(_claim("sector", {"code": sek["kode"], "description": sek.get("beskrivelse")},
                          src.ev("/institusjonellSektorkode", keys=["beskrivelse", "kode"]),
                          display=f"{sek['kode']} {sek.get('beskrivelse') or ''}".strip()))
    sd = rec.get("stiftelsesdato")
    if sd and sd not in PLACEHOLDER_DATES:
        out.append(_claim("founding_date", sd, src.ev("/stiftelsesdato")))
    rd = rec.get("registreringsdatoEnhetsregisteret")
    if rd and rd not in PLACEHOLDER_DATES:
        out.append(_claim("registration_date", rd, src.ev("/registreringsdatoEnhetsregisteret")))

    for fld, flag, dkey, label in _REGISTRATIONS:
        if not isinstance(rec.get(flag), bool):
            continue
        reg = rec[flag]
        d = rec.get(dkey) if (dkey and reg) else None
        val: dict[str, Any] = {"registered": reg, "date": d}
        evs = [src.ev(pointer(flag)), src.ev(pointer(dkey)) if d else None]
        if fld == "vat_registration" and rec.get("frivilligMvaRegistrertBeskrivelser"):
            val["voluntary_registration"] = {
                "descriptions": list(rec["frivilligMvaRegistrertBeskrivelser"]),
                "date": rec.get("registreringsdatoFrivilligMerverdiavgiftsregisteret")}
            evs.append(src.ev("/frivilligMvaRegistrertBeskrivelser"))
        out.append(_claim(fld, val, evs, effective_at=d,
                          display=(f"registered in {label}" + (f" since {d}" if d else "")) if reg
                          else f"not registered in {label}"))

    out += _employee_claims(rec, src)

    kap = rec.get("kapital")
    amount = to_decimal(kap.get("belop")) if isinstance(kap, dict) else None
    if amount is not None:
        v: dict[str, Any] = {"amount": amount, "currency": kap.get("valuta"), "type": kap.get("type"),
                             "shares": kap.get("antallAksjer"), "since": kap.get("innfortDato")}
        for k_src, k_dst in (("innbetalt", "paid_in"), ("bundet", "bound")):
            if kap.get(k_src) is not None:
                v[k_dst] = to_decimal(kap[k_src])
        if isinstance(kap.get("fulltInnbetalt"), bool):
            v["fully_paid"] = kap["fulltInnbetalt"]
        out.append(_claim("share_capital", v, src.ev("/kapital"), effective_at=kap.get("innfortDato"),
                          display=money_display(amount, kap.get("valuta"))
                          + (f" ({kap.get('type')})" if kap.get("type") else "")))
    elif form in CAPITAL_FORMS:
        out.append(_claim("share_capital", None, src.ev("/organisasjonsform", keys=["beskrivelse", "kode"]),
                          availability="not_available", note="no registered capital (kapital) in the record"))

    for fld, key, date_key in (("statutory_purpose", "vedtektsfestetFormaal", "vedtektsdato"),
                               ("activity", "aktivitet", None)):
        txt = joined_lines(rec.get(key))
        if txt:
            eff = rec.get(date_key) if date_key else None
            out.append(_claim(fld, txt, src.ev(pointer(key), effective_at=eff), effective_at=eff))
    if rec.get("maalform"):
        out.append(_claim("language_form", rec["maalform"], src.ev("/maalform")))

    seen: set[str] = set()
    for i, p in enumerate(rec.get("paategninger") or []):
        if not isinstance(p, dict) or not (p.get("tekst") or p.get("infotype")):
            continue
        code = p.get("infotype")
        q = code or str(i)
        if q in seen:
            q = f"{q}|{i}"
        seen.add(q)
        d = p.get("innfoertDato")
        out.append(_claim("registry_annotation", {"code": code, "text": p.get("tekst"), "date": d},
                          src.ev(pointer("paategninger", i), effective_at=d), qualifier=q, effective_at=d,
                          display=f"{code}: {p.get('tekst')}" if code else p.get("tekst")))
    fr_d, fr_b = rec.get("fravalgRevisjonDato"), rec.get("fravalgRevisjonBeslutningsDato")
    if fr_d or fr_b:
        out.append(_claim("audit_exemption", {"date": fr_d, "decision_date": fr_b},
                          [src.ev("/fravalgRevisjonDato") if fr_d else None,
                           src.ev("/fravalgRevisjonBeslutningsDato") if fr_b else None],
                          effective_at=fr_d or fr_b, display=f"opted out of audit from {fr_d or fr_b}"))
    if isinstance(rec.get("erIKonsern"), bool):
        out.append(_claim("group_membership", rec["erIKonsern"], src.ev("/erIKonsern"),
                          display="part of a group (konsern)" if rec["erIKonsern"] else "not registered as part of a group"))
    ov = rec.get("overordnetEnhet")
    if ov:
        out.append(_claim("public_sector_parent", {"organisation_number": str(ov)}, src.ev("/overordnetEnhet"),
                          relation="parent", display=formatted(str(ov)) if len(str(ov)) == 9 else str(ov),
                          note="overordnetEnhet: parent unit in the public-sector hierarchy (not a corporate parent)"))
    for fld, key in _CONTACTS:
        val = rec.get(key)
        if isinstance(val, str) and val.strip():
            out.append(_claim(fld, val, src.ev(pointer(key))))
    sia = rec.get("sisteInnsendteAarsregnskap")
    if sia is not None and str(sia).strip().isdigit():
        out.append(_claim("latest_submitted_accounts_year", int(str(sia).strip()), src.ev("/sisteInnsendteAarsregnskap")))
    out += _foreign_claims(rec, src)
    return out


def _employee_claims(rec: dict[str, Any], src: RecordSource, relation: str = "self") -> list[Claim]:
    has = rec.get("harRegistrertAntallAnsatte")
    n = rec.get("antallAnsatte")
    as_of = rec.get("registreringsdatoAntallAnsatteNAVAaregisteret")
    as_of_ev = src.ev("/registreringsdatoAntallAnsatteNAVAaregisteret") if as_of else None
    if isinstance(n, int) and not isinstance(n, bool) and n > 0:
        return [_claim("employees", {"count": n, "as_of": as_of}, [src.ev("/antallAnsatte", effective_at=as_of), as_of_ev],
                       effective_at=as_of, relation=relation,
                       display=f"{n:,}".replace(",", " ") + " (NAV Aa-registeret" + (f", as of {as_of})" if as_of else ")"))]
    if has is True:
        return [_claim("employees", {"count": None, "range": "1-4", "as_of": as_of},
                       [src.ev("/harRegistrertAntallAnsatte"), as_of_ev], effective_at=as_of, relation=relation,
                       display="1-4 (exact count not published)",
                       note="harRegistrertAntallAnsatte=true without antallAnsatte: Brreg omits counts below 5")]
    ev = src.ev("/harRegistrertAntallAnsatte") if has is False else src.ev("/organisasjonsnummer")
    return [_claim("employees", None, ev, availability="not_available", relation=relation,
                   note="no employees registered in NAV Aa-registeret")]


def _foreign_claims(rec: dict[str, Any], src: RecordSource) -> list[Claim]:
    keys = ("registreringsnummerIHjemlandet", "utenlandskRegisterNavn", "utenlandskRegisterAdresse",
            "foretaksformIHjemlandet", "underlagtLovgivningLand", "underlagtLovgivningLandKode")
    present = [k for k in keys if rec.get(k)]
    if not present:
        return []
    v: dict[str, Any] = {}
    if rec.get("registreringsnummerIHjemlandet"):
        v["registration_number"] = rec["registreringsnummerIHjemlandet"]
    if rec.get("utenlandskRegisterNavn"):
        v["register_name"] = rec["utenlandskRegisterNavn"]
    ra = rec.get("utenlandskRegisterAdresse")
    if isinstance(ra, dict):
        v["register_address"] = {"lines": [str(x) for x in ra.get("adresse") or []], "city": ra.get("poststed"),
                                 "country": ra.get("land")}
    ff = rec.get("foretaksformIHjemlandet")
    if isinstance(ff, dict):
        v["home_legal_form"] = {"code": ff.get("kode"), "description": ff.get("beskrivelse"),
                                "description_nb": ff.get("beskrivelseBokmaal")}
    if rec.get("underlagtLovgivningLand"):
        v["governing_law_country"] = rec["underlagtLovgivningLand"]
    if rec.get("underlagtLovgivningLandKode"):
        v["governing_law_country_code"] = rec["underlagtLovgivningLandKode"]
    disp = " ".join(x for x in (v.get("registration_number"), f"in {v['register_name']}" if v.get("register_name") else None,
                                f"({v['governing_law_country_code']})" if v.get("governing_law_country_code") else None) if x)
    return [_claim("foreign_registration", v, [src.ev(pointer(k)) for k in present], display=disp or None)]


def deleted_claims(rec: dict[str, Any], src: RecordSource, relation: str = "self") -> list[Claim]:
    out = _identity_claims(rec, src, relation)
    d = rec.get("slettedato")
    ev = [src.ev("/respons_klasse") if rec.get("respons_klasse") else None, src.ev("/slettedato") if d else None]
    out.append(_claim("registry_status", "deleted", ev, effective_at=d, relation=relation,
                      display=f"deleted {d}" if d else "deleted"))
    val: dict[str, Any] = {"date": d}
    evs = [src.ev("/slettedato")] if d else ev
    if rec.get("nedleggelsesdato"):
        val["closed"] = rec["nedleggelsesdato"]
        evs = evs + [src.ev("/nedleggelsesdato")]
    out.append(_claim("deletion", val, evs, effective_at=d, relation=relation,
                      display=f"deleted from Enhetsregisteret {d}" if d else "deleted from Enhetsregisteret"))
    return out


def subunit_value(rec: dict[str, Any]) -> dict[str, Any]:
    """Shape used for sub-units (underenheter) both as input entity and as registered workplace."""
    a = rec.get("beliggenhetsadresse")
    n1 = rec.get("naeringskode1")
    emp = rec.get("antallAnsatte")
    v: dict[str, Any] = {
        "organisation_number": str(rec.get("organisasjonsnummer") or ""),
        "name": rec.get("navn"),
        "address": address_value(a) if _has_address(a) else None,
        "industry": ({"code": n1.get("kode"), "description": n1.get("beskrivelse")}
                     if isinstance(n1, dict) and n1.get("kode") and n1["kode"] not in PLACEHOLDER_INDUSTRY else None),
        "employees": emp if isinstance(emp, int) and not isinstance(emp, bool) and emp > 0 else None,
        "started": rec.get("oppstartsdato"),
    }
    if v["employees"] is None and rec.get("harRegistrertAntallAnsatte") is True:
        v["employees_range"] = "1-4"
    return v


def subunit_input_claims(rec: dict[str, Any], src: RecordSource) -> list[Claim]:
    """Claims when the *input* number is a sub-unit: all carry relation='subunit'."""
    rel = "subunit"
    out = _identity_claims(rec, src, rel)
    for fld, key in (("business_address", "beliggenhetsadresse"), ("postal_address", "postadresse")):
        a = rec.get(key)
        if _has_address(a):
            out.append(_claim(fld, address_value(a), src.ev(pointer(key)), display=address_display(a), relation=rel))
    for rank in (1, 2, 3):
        n = rec.get(f"naeringskode{rank}")
        if isinstance(n, dict) and n.get("kode") and n["kode"] not in PLACEHOLDER_INDUSTRY:
            out.append(_claim("industry", {"code": n["kode"], "description": n.get("beskrivelse"), "rank": rank},
                              src.ev(pointer(f"naeringskode{rank}"), keys=["beskrivelse", "kode"]), qualifier=str(rank),
                              relation=rel, display=f"{n['kode']} {n.get('beskrivelse') or ''}".strip()))
    if rec.get("oppstartsdato"):
        out.append(_claim("founding_date", rec["oppstartsdato"], src.ev("/oppstartsdato"), relation=rel,
                          note="oppstartsdato (start date of the sub-unit)"))
    if rec.get("registreringsdatoEnhetsregisteret"):
        out.append(_claim("registration_date", rec["registreringsdatoEnhetsregisteret"],
                          src.ev("/registreringsdatoEnhetsregisteret"), relation=rel))
    emp = [c for c in _employee_claims(rec, src, rel) if c.availability == "available"]
    out += emp
    parent = rec.get("overordnetEnhet")
    if parent:
        out.append(_claim("parent_company", {"organisation_number": str(parent), "relationship": "main_unit"},
                          src.ev("/overordnetEnhet"), relation=rel,
                          display=f"main unit {formatted(str(parent))}" if len(str(parent)) == 9 else str(parent),
                          note="the input number is a sub-unit (underenhet); this is its registered main unit "
                               "(overordnetEnhet), not a group parent"))
    return out


# =========================================================================== batch module
@dataclass
class _Live:
    orgnr: str
    text: str
    sha: str
    rec_dec: dict[str, Any]
    rec: dict[str, Any]
    retrieved_at: str
    via: str | None
    http_status: int


@dataclass
class _ChunkState:
    state: str                    # ok | budget | failed | blocked
    note: str | None = None
    url: str | None = None
    http_status: int | None = None


def _identity(orgnr: str, status: str, *, confidence: float, method: str, checks: list[dict[str, Any]],
              **extra: Any) -> dict[str, Any]:
    d: dict[str, Any] = {"organisation_number": orgnr, "organisation_number_formatted": formatted(orgnr),
                         "match_status": status, "confidence": confidence, "method": method, "checks": checks}
    d.update({k: v for k, v in extra.items() if v is not None})
    return d


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _snapshot_kind(path: Path) -> str:
    fh = registry_csv._open_text(Path(path))
    try:
        while True:
            ch = fh.read(1)
            if not ch or not ch.isspace():
                break
    finally:
        fh.close()
    return "json_snapshot" if ch in ("[", "{") else "csv"


async def _load_snapshot(ctx: RunContext, wanted: set[str]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    path = ctx.config.registry_snapshot
    if not path:
        return {}, {}
    cache = ctx.shared.setdefault("registry_snapshot", {})
    if cache.get("path") != str(path):
        cache.clear()
        cache.update(path=str(path), rows={}, checked=set(), sha256=None, kind=None, retrieved_at=None, error=None)
    try:
        missing = wanted - cache["checked"]
        if missing:
            rows = await asyncio.to_thread(registry_csv.load_snapshot, path, set(missing))
            cache["rows"].update(rows)
            cache["checked"] |= missing
        if cache["sha256"] is None:
            cache["sha256"] = await asyncio.to_thread(_file_sha256, Path(path))
            cache["kind"] = await asyncio.to_thread(_snapshot_kind, Path(path))
            mtime = Path(path).stat().st_mtime
            cache["retrieved_at"] = utc_iso(datetime.fromtimestamp(mtime, tz=timezone.utc))
            ctx.shared["registry_snapshot_sha256"] = cache["sha256"]
    except Exception as exc:  # unreadable snapshot: continue with the live register only
        cache["error"] = f"{type(exc).__name__}: {exc}"
        return {}, cache
    return {o: cache["rows"][o] for o in wanted if o in cache["rows"]}, cache


BATCH_RETRY_DELAYS = (2.0, 5.0, 10.0)   # tests set this to zeros


async def _live_batch(ctx: RunContext, orgnrs: list[str]) -> tuple[dict[str, _Live], dict[str, _ChunkState]]:
    live: dict[str, _Live] = {}
    states: dict[str, _ChunkState] = {}

    async def attempt(chunk: list[str]) -> "_ChunkState":
        url = f"{BASE}/enheter?organisasjonsnummer={','.join(chunk)}&size={len(chunk)}"
        if not ctx.allow(TIER, 1):
            return _ChunkState("budget", BUDGET_NOTE, url)
        try:
            res = await ctx.get(url, tier=TIER, source=MODULE, headers=ACCEPT_JSON, max_bytes=BATCH_MAX_BYTES,
                                timeout=40.0)
        except Exception as exc:
            return _ChunkState("failed", f"batch lookup failed: {type(exc).__name__}: {exc}"[:300], url)
        if res.status != 200 or res.truncated:
            state = outcome_state_for_status(res.status) if res.status != 200 else "failed"
            return _ChunkState("blocked" if state == "blocked" else "failed",
                               f"batch lookup returned HTTP {res.status}" + (" (truncated)" if res.truncated else ""),
                               url, res.status)
        retrieved_at = utc_iso(res.retrieved_at)
        ctx.snapshots.save_result(res, retrieved_at)
        text = decode_text(res.content)
        wanted = set(chunk)
        for s_, e_ in array_elements(text, "/_embedded/enheter"):
            rtext = text[s_:e_]
            rec = json.loads(rtext)
            o = str(rec.get("organisasjonsnummer") or "")
            if o not in wanted:
                continue  # never attach a record to a number we did not ask for
            rbytes = rtext.encode("utf-8")
            sha = ctx.snapshots.save(rbytes, url=enhet_url(o), retrieved_at=retrieved_at,
                                     content_type="application/json")
            live[o] = _Live(o, rtext, sha, loads_decimal(rtext), rec, retrieved_at, url, res.status)
        return _ChunkState("ok", None, url, res.status)

    async def one(chunk: list[str], depth: int = 0) -> None:
        # The batch lookup anchors identity for every company: retry with backoff, then split.
        st = await attempt(chunk)
        for delay in BATCH_RETRY_DELAYS:
            if st.state in ("ok", "budget", "blocked") or ctx.time_left() < delay + 120:
                break
            await asyncio.sleep(delay)
            st = await attempt(chunk)
        if st.state == "failed" and len(chunk) > 1 and depth < 3 and ctx.time_left() > 180:
            half = len(chunk) // 2
            await asyncio.gather(one(chunk[:half], depth + 1), one(chunk[half:], depth + 1))
            return
        for o in chunk:
            states[o] = st

    chunks = [orgnrs[i:i + BATCH_SIZE] for i in range(0, len(orgnrs), BATCH_SIZE)]
    await asyncio.gather(*(one(ch) for ch in chunks))
    return live, states


def _live_source(lv: _Live, *, via: str | None) -> RecordSource:
    return RecordSource(orgnr=lv.orgnr, kind="live", text=lv.text, source_url=enhet_url(lv.orgnr),
                        source_class=OFFICIAL_REGISTRY, source_name=SOURCE_NAME, retrieved_at=lv.retrieved_at,
                        sha=lv.sha, page_url=enhet_page(lv.orgnr), http_status=lv.http_status, retrieved_via=via)


def _snapshot_source(orgnr: str, rec: dict[str, Any], cache: dict[str, Any]) -> RecordSource:
    kind = cache.get("kind") or "csv"
    text = json.dumps(rec, ensure_ascii=False, separators=(",", ":")) if kind == "json_snapshot" else None
    return RecordSource(orgnr=orgnr, kind=kind, text=text, source_url=str(cache.get("path")),
                        source_class=OFFICIAL_REGISTRY_BULK, source_name=BULK_SOURCE_NAME,
                        retrieved_at=cache.get("retrieved_at") or utc_iso(), sha=cache.get("sha256"),
                        page_url=enhet_page(orgnr), rec=rec)


def _snapshot_consistency(live_rec: dict[str, Any], snap: dict[str, Any] | None) -> dict[str, Any] | None:
    if not snap:
        return None
    diffs = []
    for label, getter in (("navn", lambda r: r.get("navn")),
                          ("organisasjonsform", lambda r: (r.get("organisasjonsform") or {}).get("kode")),
                          ("forretningsadresse.postnummer", lambda r: (r.get("forretningsadresse") or {}).get("postnummer"))):
        a, b = getter(live_rec), getter(snap)
        if a != b:
            diffs.append(f"{label}: live={a!r} snapshot={b!r}")
    return check("snapshot_consistency", not diffs,
                 "supplied snapshot agrees with the live record" if not diffs
                 else "live record preferred; snapshot differs: " + "; ".join(diffs))


def _apply_exact(c: CompanyResult, rec_dec: dict[str, Any], rec: dict[str, Any], src: RecordSource,
                 checks: list[dict[str, Any]], method: str, confidence: float, note: str | None) -> None:
    claims = enhet_claims(rec_dec, src)
    if confidence < 1.0:
        for cl in claims:
            cl.confidence = min(cl.confidence, confidence)
    c.add(claims)
    c.data["registry"] = rec
    c.data["registry_evidence"] = src.ev("/organisasjonsnummer")
    c.data["_registry_source"] = src
    c.data["registry_source"] = "live" if src.kind == "live" else "snapshot"
    form = (rec.get("organisasjonsform") or {}).get("kode") if isinstance(rec.get("organisasjonsform"), dict) else None
    c.data["identity"] = _identity(c.organisation_number, "exact", confidence=confidence, method=method, checks=checks,
                                   name=rec.get("navn"), legal_form=form,
                                   source="live" if src.kind == "live" else "snapshot")
    c.outcome(ModuleOutcome(MODULE, "available", note=note, source_url=src.source_url, retrieved_at=src.retrieved_at,
                            http_status=src.http_status))


def _set_identity_only(c: CompanyResult, status: str, availability: str, note: str | None, checks: list[dict[str, Any]],
                       method: str, url: str | None = None, http_status: int | None = None,
                       retrieved_at: str | None = None, requests: int = 0, **extra: Any) -> None:
    c.data["identity"] = _identity(c.organisation_number, status, confidence=0.0, method=method, checks=checks,
                                   note=note, **extra)
    c.outcome(ModuleOutcome(MODULE, availability, note=note, source_url=url, retrieved_at=retrieved_at,
                            http_status=http_status, requests=requests))
    if availability == "failed":
        c.error(MODULE, "failed", note or "registry lookup failed", url)


async def _follow_up(ctx: RunContext, orgnr: str, comps: list[CompanyResult], base_checks: list[dict[str, Any]]) -> None:
    """Single lookups for a number the batch did not return (deleted / removed / sub-unit / unknown)."""
    token = CURRENT_COMPANY.set(comps[0])
    n_req = 0
    try:
        checks = list(base_checks)
        url = enhet_url(orgnr)
        if not ctx.allow(TIER, 1):
            for c in comps:
                _set_identity_only(c, "error", "failed", BUDGET_NOTE, checks + [check("single_lookup", None, BUDGET_NOTE)],
                                   "brreg_enhet_single", url)
            return
        try:
            res = await ctx.get(url, tier=TIER, source=MODULE, headers=ACCEPT_JSON)
            n_req += 1
        except Exception as exc:
            msg = f"single lookup failed: {type(exc).__name__}: {exc}"[:300]
            for c in comps:
                _set_identity_only(c, "error", "failed", msg, checks, "brreg_enhet_single", url, requests=n_req)
            return
        retrieved_at = utc_iso(res.retrieved_at)
        if res.status == 200:
            _handle_single_200(ctx, orgnr, comps, res, retrieved_at, checks, n_req, kind="enhet")
            return
        if res.status == 410:
            checks.append(check("single_lookup", False, "GET /enheter/{orgnr} returned HTTP 410 (Fjernet)"))
            for c in comps:
                _set_identity_only(c, "not_found", "not_available", REMOVED_NOTE, checks, "brreg_enhet_single", url,
                                   410, retrieved_at, n_req, removed=True)
            return
        if res.status != 404:
            msg = f"single lookup returned HTTP {res.status}"
            state = outcome_state_for_status(res.status)
            for c in comps:
                _set_identity_only(c, "error", state, msg, checks, "brreg_enhet_single", url, res.status, retrieved_at, n_req)
            return
        checks.append(check("single_lookup", False, "GET /enheter/{orgnr} returned HTTP 404 (not a main unit)"))
        # 404: maybe a sub-unit number
        uurl = underenhet_url(orgnr)
        if not ctx.allow(TIER, 1):
            for c in comps:
                _set_identity_only(c, "error", "failed", BUDGET_NOTE + " (sub-unit lookup)", checks,
                                   "brreg_underenhet_single", uurl, requests=n_req)
            return
        try:
            ures = await ctx.get(uurl, tier=TIER, source=MODULE, headers=ACCEPT_JSON)
            n_req += 1
        except Exception as exc:
            msg = f"sub-unit lookup failed: {type(exc).__name__}: {exc}"[:300]
            for c in comps:
                _set_identity_only(c, "error", "failed", msg, checks, "brreg_underenhet_single", uurl, requests=n_req)
            return
        uretrieved = utc_iso(ures.retrieved_at)
        if ures.status == 200:
            _handle_single_200(ctx, orgnr, comps, ures, uretrieved, checks, n_req, kind="underenhet")
        elif ures.status == 410:
            checks.append(check("subunit_lookup", False, "GET /underenheter/{orgnr} returned HTTP 410 (Fjernet)"))
            for c in comps:
                _set_identity_only(c, "not_found", "not_available", REMOVED_NOTE, checks, "brreg_underenhet_single",
                                   uurl, 410, uretrieved, n_req, removed=True)
        elif ures.status == 404:
            checks.append(check("subunit_lookup", False, "GET /underenheter/{orgnr} returned HTTP 404"))
            note = "no entity or sub-unit with this organisation number in Enhetsregisteret"
            for c in comps:
                _set_identity_only(c, "not_found", "not_available", note, checks, "brreg_enhet_single", url, 404,
                                   retrieved_at, n_req)
        else:
            msg = f"sub-unit lookup returned HTTP {ures.status}"
            for c in comps:
                _set_identity_only(c, "error", outcome_state_for_status(ures.status), msg, checks,
                                   "brreg_underenhet_single", uurl, ures.status, uretrieved, n_req)
    finally:
        CURRENT_COMPANY.reset(token)


def _handle_single_200(ctx: RunContext, orgnr: str, comps: list[CompanyResult], res, retrieved_at: str,
                       checks: list[dict[str, Any]], n_req: int, *, kind: str) -> None:
    url = enhet_url(orgnr) if kind == "enhet" else underenhet_url(orgnr)
    page = enhet_page(orgnr) if kind == "enhet" else underenhet_page(orgnr)
    method = "brreg_enhet_single" if kind == "enhet" else "brreg_underenhet_single"
    try:
        text = decode_text(res.content)
        rec_dec = loads_decimal(text)
        rec = json.loads(text)
        if not isinstance(rec, dict):
            raise ValueError("record is not a JSON object")
    except ValueError as exc:
        for c in comps:
            _set_identity_only(c, "error", "failed", f"unparseable registry response: {exc}"[:300], checks, method, url,
                               res.status, retrieved_at, n_req)
        return
    echo = str(rec.get("organisasjonsnummer") or "")
    checks = checks + [check("organisation_number_echo", echo == orgnr, f"record organisasjonsnummer={echo or 'missing'}")]
    if echo != orgnr:
        note = f"registry returned a record for {echo or 'an unknown number'}; nothing published"
        for c in comps:
            _set_identity_only(c, "error", "ambiguous", note, checks, method, url, res.status, retrieved_at, n_req)
        return
    sha = ctx.snapshots.save_result(res, retrieved_at)
    src = RecordSource(orgnr=orgnr, kind="live", text=text, source_url=url, source_class=OFFICIAL_REGISTRY,
                       source_name=SOURCE_NAME, retrieved_at=retrieved_at, sha=sha, page_url=page,
                       http_status=res.status)
    klass = rec.get("respons_klasse") or ""
    deleted = klass in ("SlettetEnhet", "SlettetUnderEnhet") or bool(rec.get("slettedato"))
    for c in comps:
        if deleted:
            relation = "subunit" if kind == "underenhet" else "self"
            c.add(deleted_claims(rec_dec, src, relation=relation))
            c.data["registry"] = rec
            c.data["registry_evidence"] = src.ev("/organisasjonsnummer")
            c.data["_registry_source"] = src
            c.data["registry_source"] = "live"
            ck = checks + [check("entity_class", True, f"respons_klasse={klass or 'n/a'}, slettedato={rec.get('slettedato')}")]
            c.data["identity"] = _identity(orgnr, "deleted", confidence=1.0, method=method, checks=ck,
                                           name=rec.get("navn"), deleted_date=rec.get("slettedato"),
                                           entity_type="subunit" if kind == "underenhet" else "enhet", source="live")
            c.outcome(ModuleOutcome(MODULE, "available", note=f"deleted from Enhetsregisteret {rec.get('slettedato') or ''}".strip(),
                                    source_url=url, retrieved_at=retrieved_at, http_status=res.status, requests=n_req))
        elif kind == "underenhet":
            parent = str(rec.get("overordnetEnhet") or "") or None
            c.add(subunit_input_claims(rec_dec, src))
            c.data["subunit_record"] = rec
            c.data["parent_organisation_number"] = parent
            c.data["registry_evidence"] = src.ev("/organisasjonsnummer")
            c.data["_registry_source"] = src
            ck = checks + [check("entity_class", True, f"respons_klasse={klass or 'n/a'} (sub-unit)")]
            c.data["identity"] = _identity(orgnr, "subunit", confidence=1.0, method=method, checks=ck,
                                           name=rec.get("navn"), parent_organisation_number=parent, source="live")
            c.outcome(ModuleOutcome(MODULE, "available", note=f"input is a sub-unit (underenhet) of {parent}",
                                    source_url=url, retrieved_at=retrieved_at, http_status=res.status, requests=n_req))
        else:
            ck = checks + [check("entity_class", True, f"respons_klasse={klass or 'n/a'}")]
            _apply_exact(c, rec_dec, rec, src, ck, method, 1.0, None)
            c.modules[MODULE].requests = n_req


async def _run(ctx: RunContext, companies: list[CompanyResult]) -> None:
    by_org: dict[str, list[CompanyResult]] = {}
    for c in companies:
        o = str(c.organisation_number)
        if not is_valid(o):
            note = "input is not a valid organisation number (9 digits with MOD11 check digit); not looked up"
            c.data["identity"] = {"organisation_number": o, "match_status": "invalid", "confidence": 0.0,
                                  "method": "mod11_check", "checks": [check("mod11_checksum", False, note)], "note": note}
            c.outcome(ModuleOutcome(MODULE, "not_applicable", note=note))
            continue
        by_org.setdefault(o, []).append(c)
    orgnrs = sorted(by_org)
    if not orgnrs:
        return
    snap_rows, snap_cache = await _load_snapshot(ctx, set(orgnrs))
    live, states = await _live_batch(ctx, orgnrs)
    follow: list = []
    for o in orgnrs:
        comps = by_org[o]
        st = states.get(o) or _ChunkState("failed", "not checked")
        checks = [check("mod11_checksum", True, "check digit valid")]
        if snap_cache.get("error"):
            checks.append(check("snapshot_load", False, f"supplied snapshot unreadable: {snap_cache['error']}"[:300]))
        lv = live.get(o)
        if lv is not None:
            checks.append(check("registry_lookup", True, "returned by the batch lookup /enheter?organisasjonsnummer="))
            echo = str(lv.rec.get("organisasjonsnummer") or "")
            checks.append(check("organisation_number_echo", echo == o, f"record organisasjonsnummer={echo}"))
            checks.append(check("entity_class", True, f"respons_klasse={lv.rec.get('respons_klasse') or 'n/a'}"))
            cons = _snapshot_consistency(lv.rec, snap_rows.get(o))
            if cons:
                checks.append(cons)
            for c in comps:
                _apply_exact(c, lv.rec_dec, lv.rec, _live_source(lv, via=lv.via), list(checks), "brreg_enheter_batch",
                             1.0, None)
        elif st.state == "ok":
            checks.append(check("registry_lookup", False, "not returned by the batch lookup (deleted, removed, "
                                                          "sub-unit or unknown); single lookup follows"))
            follow.append(_follow_up(ctx, o, comps, checks))
        elif o in snap_rows:
            checks.append(check("registry_lookup", None, f"live register not checked: {st.note}"))
            checks.append(check("snapshot_lookup", True, "found in the supplied Enhetsregisteret snapshot"))
            rec = snap_rows[o]
            note = f"live register not checked ({st.note}); facts from the supplied bulk snapshot"
            for c in comps:
                _apply_exact(c, rec, rec, _snapshot_source(o, rec, snap_cache), list(checks), "registry_snapshot",
                             0.95, note)
        else:
            checks.append(check("registry_lookup", None, st.note or "not checked"))
            state = "blocked" if st.state == "blocked" else "failed"
            for c in comps:
                _set_identity_only(c, "error", state, st.note or "registry not checked", checks, "brreg_enheter_batch",
                                   st.url, st.http_status)
    if follow:
        await asyncio.gather(*follow)


async def run_batch(ctx: RunContext, companies: list[CompanyResult]) -> None:
    """Resolve identity and publish core registry claims for every company (never raises)."""
    try:
        await _run(ctx, companies)
    except Exception as exc:  # last-resort safety net: every company still gets a terminal state
        for c in companies:
            if MODULE not in c.modules:
                c.error(MODULE, "failed", f"{type(exc).__name__}: {exc}")
                c.outcome(ModuleOutcome(MODULE, "failed", note=f"module error: {type(exc).__name__}"))
            c.data.setdefault("identity", _identity(c.organisation_number, "error", confidence=0.0, method="none",
                                                    checks=[]) if is_valid(str(c.organisation_number)) else
                              {"organisation_number": c.organisation_number, "match_status": "invalid",
                               "confidence": 0.0, "method": "mod11_check", "checks": []})
