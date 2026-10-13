"""Annual accounts from Regnskapsregisteret's open API (data.brreg.no).

One GET per company (`/regnskapsregisteret/regnskap/{orgnr}`) returns a JSON array with up to the
three latest filed fiscal years, both the entity's own accounts (SELSKAP, scope "company") and
its consolidated group accounts (KONSERN, scope "group").

Exactness rules (a fabricated or mis-scaled figure is the worst possible failure):

* every figure is the Decimal parsed from its exact JSON path, in whole units of the record's
  own `valuta` (already scaled by the register; never rescaled, rounded or converted);
* the claim span is the literal `"key":value` text cut out of the raw response bytes by a
  position-tracking scanner (never re-serialised), and the Decimal parsed from that literal must
  equal the json.loads value or the figure is dropped;
* a missing key or an empty object is "not reported" and produces no claim, never 0;
* figures the source itself contradicts (balance sheet off by >10 %, net financial items with a
  flipped sign, a NUF filing in a foreign currency) are withheld as `ambiguous` with a note;
  smaller inconsistencies keep the value and add a note plus a lower confidence.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from ..envelope import (
    NLOD,
    OFFICIAL_ACCOUNT_COPIES,
    OFFICIAL_ACCOUNTS,
    Claim,
    CompanyResult,
    Evidence,
    ModuleOutcome,
    utc_iso,
)
from ..http import BudgetExceeded, DeadlineExceeded
from ..orgnr import is_valid

MODULE = "accounts"
COPIES_MODULE = "account_copies"
VERSION = 1
EXTRACTION = f"{MODULE}@{VERSION}"
COPIES_EXTRACTION = f"{COPIES_MODULE}@{VERSION}"
TIER = "accounts"
COPIES_TIER = "extras"
SOURCE_NAME = "Brønnøysundregistrene – Regnskapsregisteret (open annual-accounts API)"
COPIES_SOURCE_NAME = "Brønnøysundregistrene – Regnskapsregisteret (annual-accounts copy API)"
BASE = "https://data.brreg.no/regnskapsregisteret/regnskap"
PAGE_URL = "https://virksomhet.brreg.no/nb/oppslag/enheter/{orgnr}"
NOT_CHECKED_BUDGET = "not checked: request budget reserved for higher-priority sources"

# Organisation forms that normally never file annual accounts. They are skipped only when the
# registry record also lacks `sisteInnsendteAarsregnskap` (an ENK/FLI that files is still fetched).
NON_FILING_FORMS = frozenset({
    "ENK", "FLI", "ESEK", "KOMM", "FYLK", "STAT", "ORGL", "KIRK", "SAM", "ANNA", "ADOS", "OPMV",
    "PERS", "TVAM", "UTLA", "BO", "KBO", "KTRF", "IKJP", "EOFG", "VPFO",
})

SCOPES = {"SELSKAP": "company", "KONSERN": "group"}
FRAMEWORKS = {
    "regnskapslovenAlminneligRegler": "NGAAP",
    "IFRS": "IFRS",
    "forenkletAnvendelseIFRS": "simplified_IFRS",
}
NGAAP_CODE = "regnskapslovenAlminneligRegler"

# 500 body for banks/insurers/pension funds (deterministic: never retried).
_UNSUPPORTED_RE = re.compile(r"oppstillingsplan som ikke er st(?:o|ø|oe)ttet\s*\(([A-ZÆØÅ_]+)\)", re.I)

_R = "resultatregnskapResultat"
_DR = (_R, "driftsresultat")
_FR = (_R, "finansresultat")
_EK = ("egenkapitalGjeld", "egenkapital")
_GJ = ("egenkapitalGjeld", "gjeldOversikt")

# (field, JSON path under the record, statement). Paths are exactly as served, including the
# source's typos ('regnkapsprinsipper', 'sumInnskuttEgenkaptial').
FIELDS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("revenue", _DR + ("driftsinntekter", "sumDriftsinntekter"), "income"),
    ("sales_revenue", _DR + ("driftsinntekter", "salgsinntekter"), "income"),
    ("operating_expenses", _DR + ("driftskostnad", "sumDriftskostnad"), "income"),
    ("payroll_expenses", _DR + ("driftskostnad", "loennskostnad"), "income"),
    ("operating_profit", _DR + ("driftsresultat",), "income"),
    ("net_financial_items", _FR + ("nettoFinans",), "income"),
    ("financial_income", _FR + ("finansinntekt", "sumFinansinntekter"), "income"),
    ("financial_expenses", _FR + ("finanskostnad", "sumFinanskostnad"), "income"),
    ("profit_before_tax", (_R, "ordinaertResultatFoerSkattekostnad"), "income"),
    ("tax_expense", (_R, "ordinaertResultatSkattekostnad"), "income"),
    ("net_profit", (_R, "aarsresultat"), "income"),
    ("total_comprehensive_income", (_R, "totalresultat"), "income"),
    ("total_assets", ("eiendeler", "sumEiendeler"), "balance"),
    ("fixed_assets", ("eiendeler", "anleggsmidler", "sumAnleggsmidler"), "balance"),
    ("current_assets", ("eiendeler", "omloepsmidler", "sumOmloepsmidler"), "balance"),
    ("cash_and_bank", ("eiendeler", "sumBankinnskuddOgKontanter"), "balance"),
    ("inventories", ("eiendeler", "sumVarer"), "balance"),
    ("receivables", ("eiendeler", "sumFordringer"), "balance"),
    ("goodwill", ("eiendeler", "goodwill"), "balance"),
    ("equity", _EK + ("sumEgenkapital",), "balance"),
    ("paid_in_equity", _EK + ("innskuttEgenkapital", "sumInnskuttEgenkaptial"), "balance"),
    ("retained_earnings", _EK + ("opptjentEgenkapital", "sumOpptjentEgenkapital"), "balance"),
    ("total_liabilities", _GJ + ("sumGjeld",), "balance"),
    ("current_liabilities", _GJ + ("kortsiktigGjeld", "sumKortsiktigGjeld"), "balance"),
    ("long_term_liabilities", _GJ + ("langsiktigGjeld", "sumLangsiktigGjeld"), "balance"),
)
FIELD_PATHS = {f: p for f, p, _ in FIELDS}
BALANCE_FIELDS = frozenset(f for f, _, s in FIELDS if s == "balance")
SPLIT_FIELDS = ("paid_in_equity", "retained_earnings")
TOTAL_EQ_LIAB_PATH = ("egenkapitalGjeld", "sumEgenkapitalGjeld")

# Record header parts cited as evidence for accounts_period.
PERIOD_EVIDENCE_PATHS = (("regnskapsperiode",), ("valuta",), ("regnkapsprinsipper",), ("revisjon",),
                         ("avviklingsregnskap",))

ABS_TOL = Decimal(2)            # whole-unit rounding slack used in reconciliation checks
REL_TOL = Decimal("0.001")      # 0.1 %
WITHHOLD_REL = Decimal("0.10")  # balance sheet withheld beyond 10 % mismatch
STALE_DAYS = 548                # ~18 months
COPY_YEARS_PUBLISHED = 5        # annual_report_copy claims for the most recent N listed years
COPY_RATE_FLOOR = 5             # stop calling the copy API when x-rate-limit-remaining < 5
COPY_RATE_WINDOW_S = 65.0       # the copy API's fixed rate window is ~60 s

_MISSING = object()


# --------------------------------------------------------------------------- raw-span scanner
_WS = " \t\n\r"
_NUM_RE = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")


def _ptr_escape(key: str) -> str:
    return key.replace("~", "~0").replace("/", "~1")


def json_pointer(*parts: Any) -> str:
    return "".join("/" + _ptr_escape(str(p)) for p in parts)


def index_spans(text: str) -> dict[str, tuple[int, int, int]]:
    """Map every JSON pointer in `text` to (span_start, value_start, value_end) offsets.

    `span_start` is the opening quote of the member's key (so `text[span_start:value_end]` is the
    verbatim `"key":value` fragment) or the value start for array elements and the root.
    Raises ValueError on malformed JSON.
    """
    spans: dict[str, tuple[int, int, int]] = {}
    n = len(text)

    def ws(i: int) -> int:
        while i < n and text[i] in _WS:
            i += 1
        return i

    def string_end(i: int) -> int:
        j = i + 1
        while j < n:
            c = text[j]
            if c == "\\":
                j += 2
                continue
            if c == '"':
                return j + 1
            j += 1
        raise ValueError("unterminated string")

    def value(i: int, ptr: str, span_start: int | None, depth: int) -> int:
        if depth > 64:
            raise ValueError("nesting too deep")
        i = ws(i)
        if i >= n:
            raise ValueError("unexpected end of JSON")
        start = i
        c = text[i]
        if c == "{":
            i = ws(i + 1)
            if i < n and text[i] == "}":
                end = i + 1
            else:
                while True:
                    i = ws(i)
                    if i >= n or text[i] != '"':
                        raise ValueError(f"expected key at {i}")
                    key_end = string_end(i)
                    key = json.loads(text[i:key_end])
                    key_start = i
                    i = ws(key_end)
                    if i >= n or text[i] != ":":
                        raise ValueError(f"expected ':' at {i}")
                    i = ws(value(i + 1, ptr + "/" + _ptr_escape(key), key_start, depth + 1))
                    if i < n and text[i] == ",":
                        i += 1
                        continue
                    if i < n and text[i] == "}":
                        end = i + 1
                        break
                    raise ValueError(f"expected ',' or '}}' at {i}")
        elif c == "[":
            i = ws(i + 1)
            if i < n and text[i] == "]":
                end = i + 1
            else:
                k = 0
                while True:
                    i = ws(value(i, f"{ptr}/{k}", None, depth + 1))
                    k += 1
                    if i < n and text[i] == ",":
                        i += 1
                        continue
                    if i < n and text[i] == "]":
                        end = i + 1
                        break
                    raise ValueError(f"expected ',' or ']' at {i}")
        elif c == '"':
            end = string_end(i)
        else:
            m = _NUM_RE.match(text, i)
            if m and m.end() > i:
                end = m.end()
            else:
                for lit in ("true", "false", "null"):
                    if text.startswith(lit, i):
                        end = i + len(lit)
                        break
                else:
                    raise ValueError(f"unexpected character at {i}")
        spans[ptr] = (start if span_start is None else span_start, start, end)
        return end

    end = ws(value(0, "", None, 0))
    if end != n:
        raise ValueError("trailing data after JSON value")
    return spans


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-finite JSON number {name}")


def parse_json_exact(text: str) -> Any:
    """json.loads with every number as an exact Decimal (no float ever touches a figure)."""
    return json.loads(text, parse_float=Decimal, parse_int=Decimal, parse_constant=_reject_constant)


# --------------------------------------------------------------------------- formatting
def _group3(digits: str) -> str:
    out = []
    while len(digits) > 3:
        out.append(digits[-3:])
        digits = digits[:-3]
    out.append(digits)
    return " ".join(reversed(out))


def format_amount(value: Decimal, currency: str | None) -> str:
    """'NOK 790 400 865' (plain spaces as thousands separators). Never rounds: a non-integral
    value keeps all of its source decimals ('NOK 1 234.50')."""
    neg = value < 0
    a = -value if neg else value
    if a == a.to_integral_value():
        int_part, frac = format(a.to_integral_value(), "f"), ""
    else:
        s = format(a, "f")
        int_part, frac = s.split(".", 1)
        frac = "." + frac
    txt = ("-" if neg else "") + _group3(int_part) + frac
    return f"{currency} {txt}" if currency else txt


def format_ratio(value: Decimal) -> str:
    return f"{(value * 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_EVEN)} %"


# --------------------------------------------------------------------------- helpers
def _resolve(obj: Any, path: tuple[str, ...]) -> Any:
    cur = obj
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return _MISSING
        cur = cur[p]
    return cur


def _num(v: Any) -> Decimal | None:
    if isinstance(v, Decimal) and v.is_finite():
        return v
    return None


def _iso_date(v: Any) -> date | None:
    if not isinstance(v, str) or len(v) != 10:
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        return None


def _bool(v: Any) -> bool | None:
    return v if isinstance(v, bool) else None


def _pct(diff: Decimal, base: Decimal) -> str:
    return f"{(diff / base * 100).quantize(Decimal('0.1'), rounding=ROUND_HALF_EVEN)} %"


def _tol(*values: Decimal) -> Decimal:
    return max(ABS_TOL, max(abs(v) for v in values) * REL_TOL)


def _join(notes: list[str]) -> str | None:
    seen: list[str] = []
    for n in notes:
        if n and n not in seen:
            seen.append(n)
    return "; ".join(seen) if seen else None


@dataclass
class _Fetch:
    orgnr: str
    list_url: str
    text: str
    spans: dict[str, tuple[int, int, int]]
    sha256: str
    retrieved_at: str
    http_status: int
    final_url: str | None


@dataclass
class _Rec:
    index: int
    data: dict[str, Any]
    record_id: int
    scope: str
    start: str
    end: str
    start_d: date
    end_d: date
    year: int
    days: int
    currency: str | None
    form: str | None
    framework_code: str | None
    framework: str | None
    small_company: bool | None
    audited: bool | None
    audit_opt_out: bool | None
    liquidation: bool | None
    qualifier: str = ""
    flags: list[str] = field(default_factory=list)
    notes_all: list[str] = field(default_factory=list)      # every claim of the record
    notes_money: list[str] = field(default_factory=list)    # monetary claims
    notes_balance: list[str] = field(default_factory=list)  # balance-sheet claims
    notes_period: list[str] = field(default_factory=list)   # accounts_period only
    field_notes: dict[str, list[str]] = field(default_factory=dict)
    field_caps: dict[str, float] = field(default_factory=dict)
    withheld: dict[str, str] = field(default_factory=dict)  # field -> reason (ambiguous claim)
    suppressed: dict[str, str] = field(default_factory=dict)  # field -> reason (no claim at all)
    cap_all: float = 1.0
    cap_balance: float = 1.0
    money_ambiguous: str | None = None                      # reason when all figures are withheld
    balance_withheld: str | None = None

    @property
    def url(self) -> str:
        return f"{BASE}/{self.data['virksomhet']['organisasjonsnummer']}/{self.record_id}"

    @property
    def period(self) -> dict[str, Any]:
        return {"start": self.start, "end": self.end, "year": self.year, "scope": self.scope}

    def value(self, fld: str) -> Decimal | None:
        return _num(_resolve(self.data, FIELD_PATHS[fld]))

    def flag(self, code: str) -> None:
        if code not in self.flags:
            self.flags.append(code)

    def field_note(self, fld: str, note: str, cap: float | None = None) -> None:
        self.field_notes.setdefault(fld, []).append(note)
        if cap is not None:
            self.field_caps[fld] = min(self.field_caps.get(fld, 1.0), cap)


def _parse_records(fx: _Fetch, body: list[Any], errors: list[str]) -> list[_Rec]:
    recs: list[_Rec] = []
    for i, r in enumerate(body):
        if not isinstance(r, dict):
            errors.append(f"record {i}: not an object")
            continue
        virk = r.get("virksomhet") if isinstance(r.get("virksomhet"), dict) else {}
        if virk.get("organisasjonsnummer") != fx.orgnr:
            errors.append(f"record {i}: organisation number {virk.get('organisasjonsnummer')!r} "
                          f"!= {fx.orgnr}; discarded")
            continue
        scope = SCOPES.get(r.get("regnskapstype"))
        rid = r.get("id")
        per = r.get("regnskapsperiode") if isinstance(r.get("regnskapsperiode"), dict) else {}
        start_d, end_d = _iso_date(per.get("fraDato")), _iso_date(per.get("tilDato"))
        if scope is None or not isinstance(rid, Decimal) or rid != rid.to_integral_value() or rid <= 0 \
                or start_d is None or end_d is None or end_d < start_d:
            errors.append(f"record {i}: missing/invalid regnskapstype, id or period; discarded")
            continue
        cur = r.get("valuta")
        cur = cur if isinstance(cur, str) and re.fullmatch(r"[A-Z]{3}", cur) else None
        prin = r.get("regnkapsprinsipper") if isinstance(r.get("regnkapsprinsipper"), dict) else {}
        rev = r.get("revisjon") if isinstance(r.get("revisjon"), dict) else {}
        fw_code = prin.get("regnskapsregler") if isinstance(prin.get("regnskapsregler"), str) else None
        not_audited, opt_out = _bool(rev.get("ikkeRevidertAarsregnskap")), _bool(rev.get("fravalgRevisjon"))
        # Accounts are unaudited when flagged as such OR when the company opted out of audit.
        if not_audited is True or opt_out is True:
            audited: bool | None = False
        elif not_audited is False:
            audited = True
        else:
            audited = None
        recs.append(_Rec(
            index=i, data=r, record_id=int(rid), scope=scope, start=per["fraDato"], end=per["tilDato"],
            start_d=start_d, end_d=end_d, year=end_d.year, days=(end_d - start_d).days + 1, currency=cur,
            form=virk.get("organisasjonsform") if isinstance(virk.get("organisasjonsform"), str) else None,
            framework_code=fw_code, framework=FRAMEWORKS.get(fw_code, fw_code) if fw_code else None,
            small_company=_bool(prin.get("smaaForetak")), audited=audited, audit_opt_out=opt_out,
            liquidation=_bool(r.get("avviklingsregnskap")),
        ))
    recs.sort(key=lambda x: (0 if x.scope == "company" else 1, x.end, x.start, x.record_id))
    # Stable qualifiers: "{year}|{scope}"; on a (rare) collision the record with the latest
    # period end keeps the plain key and the others get the end date appended.
    by_key: dict[tuple[int, str], list[_Rec]] = {}
    for x in recs:
        by_key.setdefault((x.year, x.scope), []).append(x)
    for (year, scope), group in by_key.items():
        group.sort(key=lambda x: (x.end, x.start, x.record_id))
        for j, x in enumerate(group):
            x.qualifier = f"{year}|{scope}" if j == len(group) - 1 else f"{year}|{scope}|{x.end}"
            if j < len(group) - 1 and sum(1 for y in group if y.end == x.end) > 1:
                x.qualifier += f"|{x.record_id}"
    return recs


# --------------------------------------------------------------------------- quality checks
def _assess(recs: list[_Rec], registry: dict[str, Any], retrieved: date) -> dict[str, Any]:
    reg_form = ((registry.get("organisasjonsform") or {}).get("kode")
                if isinstance(registry.get("organisasjonsform"), dict) else None)
    for r in recs:
        if r.currency is None:
            r.money_ambiguous = "figures withheld: currency missing in the source record"
            r.flag("currency_missing")
        elif r.currency != "NOK":
            r.flag("currency_not_nok")
            r.notes_money.append(f"amounts in {r.currency} as filed (whole {r.currency} units), not NOK")
        if not 300 <= r.days <= 400:
            r.flag("period_length")
            r.notes_all.append(f"{r.days}-day accounting period {r.start} to {r.end}; "
                               "figures cover this period only (not annualised)")
            r.cap_all = min(r.cap_all, 0.95)
        if r.liquidation:
            r.flag("liquidation_accounts")
            r.notes_all.append("liquidation accounts (avviklingsregnskap)")
            r.cap_all = min(r.cap_all, 0.9)

        # Balance sheet: total assets must equal total equity and liabilities.
        se, seg = r.value("total_assets"), _num(_resolve(r.data, TOTAL_EQ_LIAB_PATH))
        if se is not None and seg is not None:
            diff, base = abs(se - seg), max(abs(se), abs(seg))
            if base == 0:
                r.flag("zero_balance_sheet")
                r.notes_balance.append("balance sheet filed with total assets 0")
                r.cap_balance = min(r.cap_balance, 0.8)
            elif diff > _tol(se, seg):
                r.flag("balance_mismatch")
                msg = (f"total assets {format_amount(se, r.currency)} vs total equity and liabilities "
                       f"{format_amount(seg, r.currency)} in the source ({_pct(diff, base)} apart)")
                if diff > base * WITHHOLD_REL or se == 0:
                    r.flag("balance_withheld")
                    r.balance_withheld = f"balance sheet withheld: {msg}"
                else:
                    r.notes_balance.append(f"balance check failed: {msg}")
                    r.cap_balance = min(r.cap_balance, 0.7)

        for fld in ("revenue", "sales_revenue"):
            v = r.value(fld)
            if v is not None and v < 0:
                r.flag("negative_revenue")
                r.field_note(fld, "negative operating revenue as filed", 0.6)

        # Net financial items must equal profit before tax minus operating profit.
        nf, ebit, ebt = r.value("net_financial_items"), r.value("operating_profit"), r.value("profit_before_tax")
        if nf is not None and ebit is not None and ebt is not None:
            expected = ebt - ebit
            tol = _tol(nf, expected)
            if abs(nf - expected) > tol:
                if nf != 0 and abs(nf + expected) <= tol:
                    r.flag("net_financial_sign")
                    r.withheld["net_financial_items"] = (
                        "net financial items withheld: the source value has the opposite sign of "
                        "profit before tax minus operating profit")
                else:
                    r.flag("net_financial_mismatch")
                    r.field_note("net_financial_items", "net financial items do not reconcile with profit "
                                 "before tax minus operating profit in the source", 0.7)

        # Equity split: only company-scope NGAAP records whose parts reconcile to total equity.
        parts = {f: r.value(f) for f in SPLIT_FIELDS}
        present = {f: v for f, v in parts.items() if v is not None}
        if present:
            eq = r.value("equity")
            if r.scope != "company" or r.framework_code != NGAAP_CODE:
                reason = "equity split not published for group/IFRS accounts (mis-split in the open API)"
            elif eq is None or abs(sum(present.values(), Decimal(0)) - eq) > _tol(eq, *present.values()):
                reason = "equity split not published: paid-in + retained does not reconcile to total equity"
            else:
                reason = ""
            if reason:
                for f in present:
                    r.suppressed[f] = reason

    # NUF (Norwegian branch of a foreign company): foreign-currency filings and scale jumps.
    currencies = sorted({r.currency for r in recs if r.currency})
    timeline = ", ".join(f"FY{r.year} {r.scope} {r.currency}" for r in
                         sorted(recs, key=lambda x: (x.end, x.scope)) if r.currency)
    for r in recs:
        is_nuf = r.form == "NUF" or (r.form is None and reg_form == "NUF")
        if is_nuf and r.currency and r.currency != "NOK" and not r.money_ambiguous:
            r.flag("nuf_foreign_currency")
            reason = (f"NUF filing in {r.currency}: figures withheld because they may be the foreign head "
                      "office's accounts rather than the Norwegian branch's")
            if len(currencies) > 1:
                r.flag("currency_change")
                reason += f" (currency changed between filings: {timeline})"
            r.money_ambiguous = reason
    for scope in ("company", "group"):
        seq = [r for r in recs if r.scope == scope]
        for prev, r in zip(seq, seq[1:]):
            if prev.currency and r.currency and prev.currency != r.currency:
                r.flag("currency_change")
                r.notes_money.append(f"currency changed from {prev.currency} (FY{prev.year}) to {r.currency}; "
                                     "do not compare across years")
                continue
            is_nuf = r.form == "NUF" or (r.form is None and reg_form == "NUF")
            if not is_nuf:
                continue
            for fld in ("revenue", "total_assets"):
                a, b = prev.value(fld), r.value(fld)
                if a is not None and b is not None and a > 0 and b > 0:
                    ratio = max(a, b) / min(a, b)
                    if ratio >= 5:
                        r.flag("nuf_scale_jump")
                        r.notes_all.append(
                            f"NUF {fld.replace('_', ' ')} changed {ratio.quantize(Decimal('0.1'))}x from "
                            f"FY{prev.year}: the figures may include the foreign head office's accounts")
                        r.cap_all = min(r.cap_all, 0.5)
                        break
        # An isolated framework label between two identical neighbours is likely a source error.
        for prev, r, nxt in zip(seq, seq[1:], seq[2:]):
            if prev.framework_code and prev.framework_code == nxt.framework_code \
                    and r.framework_code and r.framework_code != prev.framework_code:
                r.flag("framework_outlier")
                r.notes_period.append(f"accounting framework {r.framework} differs from FY{prev.year} and "
                                      f"FY{nxt.year} ({prev.framework}); the label may be wrong in the source")

    info: dict[str, Any] = {"latest_year": None, "registry_latest_year": None, "pending_year": None,
                            "stale": False}
    if not recs:
        return info
    latest_year = max(r.year for r in recs)
    info["latest_year"] = latest_year
    raw_reg = registry.get("sisteInnsendteAarsregnskap")
    reg_year = int(raw_reg) if isinstance(raw_reg, (str, int)) and str(raw_reg).isdigit() else None
    info["registry_latest_year"] = reg_year
    latest_end = max(r.end_d for r in recs)
    for r in recs:
        if r.year != latest_year:
            continue
        if reg_year is not None and reg_year > latest_year:
            r.flag("newer_accounts_pending")
            r.notes_all.append(f"newer accounts (FY{reg_year}) are filed with the register but not yet "
                               "served by the open API; these are the latest figures available")
        if (retrieved - latest_end).days > STALE_DAYS:
            r.flag("stale")
            r.notes_all.append(f"stale: the latest accounts in the open API end {latest_end.isoformat()}")
            r.cap_all = min(r.cap_all, 0.9)
    if reg_year is not None and reg_year > latest_year:
        info["pending_year"] = reg_year
    info["stale"] = (retrieved - latest_end).days > STALE_DAYS
    comp = [r for r in recs if r.scope == "company"]
    grp = [r for r in recs if r.scope == "group"]
    if comp and grp and grp[-1].end_d < comp[-1].end_d:
        g = grp[-1]
        g.flag("group_lag")
        g.notes_all.append(f"latest available group accounts (FY{g.year}); company accounts for "
                           f"FY{comp[-1].year} are newer")
    return info


# --------------------------------------------------------------------------- claim building
def _evidence(fx: _Fetch, rec: _Rec, path: tuple[str, ...]) -> Evidence | None:
    """Evidence for the value at `path` of `rec`, with the verbatim span cut from the raw body."""
    sp = fx.spans.get(json_pointer(rec.index, *path))
    if sp is None:
        return None
    span = fx.text[sp[0]:sp[2]]
    if len(span) > 300:
        return None
    return Evidence(
        source_url=rec.url,
        source_class=OFFICIAL_ACCOUNTS,
        source_name=SOURCE_NAME,
        retrieved_at=fx.retrieved_at,
        content_sha256=fx.sha256,
        claim_span=span,
        locator=json_pointer(*path),          # relative to source_url (the single-record document)
        extraction_method=EXTRACTION,
        page_url=PAGE_URL.format(orgnr=fx.orgnr),
        effective_at=rec.end,
        http_status=fx.http_status,
        final_url=fx.final_url,
        licence=NLOD,
        retrieved_via=fx.list_url,
    )


def _raw_literal_ok(fx: _Fetch, rec: _Rec, path: tuple[str, ...], value: Decimal) -> bool:
    sp = fx.spans.get(json_pointer(rec.index, *path))
    if sp is None:
        return False
    try:
        return Decimal(fx.text[sp[1]:sp[2]]) == value
    except Exception:
        return False


def _period_value(rec: _Rec) -> dict[str, Any]:
    return {
        "start": rec.start, "end": rec.end, "year": rec.year, "scope": rec.scope, "days": rec.days,
        "currency": rec.currency, "framework": rec.framework, "framework_code": rec.framework_code,
        "audited": rec.audited, "audit_opt_out": rec.audit_opt_out, "small_company": rec.small_company,
        "liquidation_accounts": rec.liquidation, "record_id": rec.record_id,
    }


def _period_display(rec: _Rec) -> str:
    bits = [f"FY{rec.year} {rec.scope} accounts", f"{rec.start} to {rec.end}"]
    if rec.currency:
        bits.append(rec.currency)
    if rec.framework:
        bits.append(rec.framework)
    if rec.audited is False:
        bits.append("unaudited" + (" (audit opt-out)" if rec.audit_opt_out else ""))
    if rec.liquidation:
        bits.append("liquidation accounts")
    return ", ".join(bits)


def _record_claims(fx: _Fetch, rec: _Rec, errors: list[str]) -> tuple[list[Claim], dict[str, Claim]]:
    claims: list[Claim] = []
    by_field: dict[str, Claim] = {}
    evs = [e for e in (_evidence(fx, rec, p) for p in PERIOD_EVIDENCE_PATHS) if e is not None]
    # The filing itself (period, currency) is a fact about this entity even when its figures are
    # withheld; say why the figures are missing.
    header_notes = ([rec.money_ambiguous] if rec.money_ambiguous else []) + rec.notes_all
    claims.append(Claim(
        field="accounts_period", value=_period_value(rec), evidence=evs, confidence=rec.cap_all,
        qualifier=rec.qualifier, display=_period_display(rec), effective_at=rec.end, period=rec.period,
        method="verbatim_json", note=_join(header_notes + rec.notes_period),
    ))
    if rec.currency:
        ev = _evidence(fx, rec, ("valuta",))
        claims.append(Claim(
            field="accounts_currency", value=rec.currency, evidence=[ev] if ev else [], confidence=rec.cap_all,
            qualifier=rec.qualifier, display=rec.currency, effective_at=rec.end, period=rec.period,
            method="verbatim_json", note=_join(header_notes),
        ))
    for fld, path, statement in FIELDS:
        if fld in rec.suppressed:
            continue
        value = rec.value(fld)
        if value is None:
            continue                       # key absent, {} or non-numeric: not reported, never 0
        if not _raw_literal_ok(fx, rec, path, value):
            errors.append(f"record {rec.record_id} {fld}: raw literal does not match parsed value; dropped")
            continue
        ev = _evidence(fx, rec, path)
        if ev is None:
            errors.append(f"record {rec.record_id} {fld}: no verbatim span; dropped")
            continue
        notes = list(rec.notes_all) + list(rec.notes_money)
        conf = rec.cap_all
        withheld = rec.money_ambiguous
        if statement == "balance":
            notes += rec.notes_balance
            conf = min(conf, rec.cap_balance)
            withheld = withheld or rec.balance_withheld
        notes += rec.field_notes.get(fld, [])
        conf = min(conf, rec.field_caps.get(fld, 1.0))
        withheld = withheld or rec.withheld.get(fld)
        if withheld:
            notes.insert(0, withheld)
        c = Claim(
            field=fld, value=value, evidence=[ev], availability="ambiguous" if withheld else "available",
            confidence=min(conf, 0.5) if withheld else conf, qualifier=rec.qualifier,
            display=None if withheld else format_amount(value, rec.currency), effective_at=rec.end,
            period=rec.period, method="verbatim_json", note=_join(notes),
        )
        claims.append(c)
        by_field[fld] = c
    return claims, by_field


def _derived(rec: _Rec, by_field: dict[str, Claim]) -> list[Claim]:
    """Equity ratio and operating margin for the latest company-scope record (marked derived)."""
    out: list[Claim] = []
    specs = (
        ("equity_ratio", "equity", "total_assets", "equity/total_assets", "equity / total assets"),
        ("operating_margin", "operating_profit", "revenue", "operating_profit/revenue",
         "operating profit / operating revenue"),
    )
    for fld, num_f, den_f, method, label in specs:
        num_c, den_c = by_field.get(num_f), by_field.get(den_f)
        if not num_c or not den_c or num_c.availability != "available" or den_c.availability != "available":
            continue
        if fld == "equity_ratio" and ("balance_mismatch" in rec.flags or "zero_balance_sheet" in rec.flags):
            continue
        den = den_c.value
        if den is None or den <= 0:
            continue
        value = (num_c.value / den).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)
        if abs(value) > 5:
            continue  # tiny denominator: a ratio beyond ±500 % carries no decision value
        seen: set[str] = set()
        evs = [e for e in num_c.evidence + den_c.evidence if not (e.id in seen or seen.add(e.id))]
        out.append(Claim(
            field=fld, value=value, evidence=evs, confidence=min(num_c.confidence, den_c.confidence),
            qualifier=rec.qualifier, display=format_ratio(value), effective_at=rec.end, period=rec.period,
            method=method, match_basis="derived",
            note=f"derived: {label} from the filed FY{rec.year} {rec.scope} accounts "
                 f"({num_c.display} / {den_c.display}), rounded to 4 decimals; not a filed figure",
        ))
    return out


def _summary(rec: _Rec, by_field: dict[str, Claim]) -> dict[str, Any]:
    values = {f: c.value for f, c in by_field.items() if c.availability == "available"}
    withheld = {f: (c.note or "") for f, c in by_field.items() if c.availability != "available"}
    withheld.update(rec.suppressed)
    return {
        **_period_value(rec),
        "qualifier": rec.qualifier,
        "source_url": rec.url,
        "organisation_form": rec.form,
        "availability": "ambiguous" if rec.money_ambiguous else "available",
        "flags": list(rec.flags),
        "values": values,
        "withheld": withheld,
        "display": {f: c.display for f, c in by_field.items() if c.availability == "available"},
    }


# --------------------------------------------------------------------------- module entry points
def _skip_reason(company: CompanyResult) -> str | None:
    identity = company.data.get("identity") or {}
    if identity.get("match_status") != "exact":
        return "identity not exact: annual accounts not looked up"
    registry = company.data.get("registry") or {}
    form_obj = registry.get("organisasjonsform")
    form = form_obj.get("kode") if isinstance(form_obj, dict) else None
    if not registry.get("sisteInnsendteAarsregnskap") and form in NON_FILING_FORMS:
        return (f"organisation form {form} does not normally file annual accounts and the register lists "
                "no filed accounts (sisteInnsendteAarsregnskap absent)")
    return None


async def run(ctx: Any, company: CompanyResult) -> None:
    """Fetch and publish the filed annual accounts for `company` (tier "accounts"). Never raises."""
    orgnr = company.organisation_number
    url = f"{BASE}/{orgnr}"
    try:
        await _run(ctx, company, url)
    except (BudgetExceeded, DeadlineExceeded) as exc:
        company.error(MODULE, "failed", f"{type(exc).__name__}: {exc}", url)
        company.outcome(ModuleOutcome(MODULE, "failed", note=NOT_CHECKED_BUDGET, source_url=url))
        company.data[MODULE] = {"status": "failed", "note": NOT_CHECKED_BUDGET, "records": [], "latest": None}
    except Exception as exc:  # noqa: BLE001 - modules must never raise
        msg = f"{type(exc).__name__}: {exc}"
        company.error(MODULE, "failed", msg, url)
        company.outcome(ModuleOutcome(MODULE, "failed", note=f"accounts lookup failed ({msg[:200]})",
                                      source_url=url))
        company.data[MODULE] = {"status": "failed", "note": msg[:300], "records": [], "latest": None}
    if ctx.module_enabled(COPIES_MODULE):
        await run_copies(ctx, company)


def _set_data(company: CompanyResult, status: str, note: str | None, **extra: Any) -> None:
    company.data[MODULE] = {"status": status, "note": note, "records": [], "latest": None,
                            "latest_group": None, **extra}


async def _run(ctx: Any, company: CompanyResult, url: str) -> None:
    orgnr = company.organisation_number
    reason = _skip_reason(company)
    if reason:
        company.outcome(ModuleOutcome(MODULE, "not_applicable", note=reason))
        _set_data(company, "not_applicable", reason)
        return
    if not is_valid(orgnr):
        note = f"invalid organisation number {orgnr!r}"
        company.outcome(ModuleOutcome(MODULE, "failed", note=note))
        _set_data(company, "failed", note)
        return
    if not ctx.allow(TIER, 1):
        company.outcome(ModuleOutcome(MODULE, "failed", note=NOT_CHECKED_BUDGET, source_url=url))
        _set_data(company, "failed", NOT_CHECKED_BUDGET)
        return

    try:
        res = await ctx.get(url, tier=TIER, source=MODULE, headers={"Accept": "application/json"})
    except (BudgetExceeded, DeadlineExceeded):
        raise
    except Exception as exc:  # transport error after the client's own retry
        msg = f"{type(exc).__name__}: {exc}"
        company.error(MODULE, "failed", msg, url)
        company.outcome(ModuleOutcome(MODULE, "failed", note=f"request failed ({msg[:200]})", source_url=url,
                                      requests=1))
        _set_data(company, "failed", msg[:300])
        return
    retrieved_at = utc_iso(res.retrieved_at)
    try:
        sha = ctx.snapshots.save_result(res, retrieved_at)
    except Exception as exc:  # a snapshot-store problem must not lose the figures
        company.error(MODULE, "failed", f"snapshot not saved: {exc}", url)
        sha = res.sha256
    requests = 1 + len(res.redirects)
    registry = company.data.get("registry") or {}
    reg_year = registry.get("sisteInnsendteAarsregnskap")
    base_outcome = dict(source_url=url, retrieved_at=retrieved_at, http_status=res.status, requests=requests)

    if res.status == 404:
        note = "no accounts in Regnskapsregisteret's open API"
        if reg_year:
            note += (f" (Enhetsregisteret lists latest filed accounts for {reg_year}; the open API serves "
                     "only the latest three filings)")
        company.outcome(ModuleOutcome(MODULE, "not_available", note=note, **base_outcome))
        _set_data(company, "not_available", note, source_url=url, retrieved_at=retrieved_at,
                  content_sha256=sha, registry_latest_year=reg_year)
        return
    if res.status == 500:
        m = _UNSUPPORTED_RE.search(res.text)
        if m:
            code = m.group(1).upper()
            note = f"financial-institution layout not served by the open API (oppstillingsplan {code})"
            company.outcome(ModuleOutcome(MODULE, "not_available", note=note, **base_outcome))
            _set_data(company, "not_available", note, unsupported_layout=code, source_url=url,
                      retrieved_at=retrieved_at, content_sha256=sha, registry_latest_year=reg_year)
            return
    if res.status in (401, 403, 429):
        note = f"source refused the request (HTTP {res.status})"
        company.outcome(ModuleOutcome(MODULE, "blocked", note=note, **base_outcome))
        _set_data(company, "blocked", note)
        return
    if res.status != 200:
        note = f"unexpected HTTP {res.status} from Regnskapsregisteret"
        company.error(MODULE, "failed", note, url)
        company.outcome(ModuleOutcome(MODULE, "failed", note=note, **base_outcome))
        _set_data(company, "failed", note)
        return
    if res.truncated:
        note = "response body exceeded the size cap; not parsed"
        company.error(MODULE, "failed", note, url)
        company.outcome(ModuleOutcome(MODULE, "failed", note=note, **base_outcome))
        _set_data(company, "failed", note)
        return

    text = res.content.decode("utf-8")
    if text.startswith("\ufeff"):
        text = text[1:]
    body = parse_json_exact(text)
    if not isinstance(body, list):
        raise ValueError("accounts response is not a JSON array")
    fx = _Fetch(orgnr=orgnr, list_url=url, text=text, spans=index_spans(text), sha256=sha,
                retrieved_at=retrieved_at, http_status=res.status,
                final_url=res.final_url if res.final_url and res.final_url != url else None)
    errors: list[str] = []
    recs = _parse_records(fx, body, errors)
    if not recs:
        state = "ambiguous" if body and errors else "not_available"
        note = ("records in the response could not be tied to this organisation number"
                if state == "ambiguous" else "no accounts in Regnskapsregisteret's open API (empty list)")
        for e in errors:
            company.error(MODULE, state, e, url)
        company.outcome(ModuleOutcome(MODULE, state, note=note, **base_outcome))
        _set_data(company, state, note, source_url=url, retrieved_at=retrieved_at, content_sha256=sha)
        return

    info = _assess(recs, registry, res.retrieved_at.date())
    claims: list[Claim] = []
    summaries: list[dict[str, Any]] = []
    fields_by_rec: list[dict[str, Claim]] = []
    for rec in recs:
        rc, by_field = _record_claims(fx, rec, errors)
        claims.extend(rc)
        fields_by_rec.append(by_field)
        summaries.append(_summary(rec, by_field))

    derived: dict[str, Any] = {}
    company_idx = [i for i, r in enumerate(recs) if r.scope == "company"]
    if company_idx:
        i = max(company_idx, key=lambda k: (recs[k].end, recs[k].start, recs[k].record_id))
        if not recs[i].money_ambiguous:
            dc = _derived(recs[i], fields_by_rec[i])
            claims.extend(dc)
            derived = {c.field: c.value for c in dc}
            summaries[i]["derived"] = {c.field: {"value": c.value, "display": c.display} for c in dc}

    def latest(scope: str) -> dict[str, Any] | None:
        cand = [s for s, r in zip(summaries, recs) if r.scope == scope and s["availability"] == "available"]
        return cand[-1] if cand else None

    for e in errors:
        company.error(MODULE, "warning", e, url)
    company.add(claims)
    years = sorted({r.year for r in recs})
    scopes = sorted({r.scope for r in recs})
    note = (f"{len(recs)} filed record(s), FY{years[0]}" + (f"–FY{years[-1]}" if len(years) > 1 else "")
            + f", scope {'+'.join(scopes)}, currency {'/'.join(sorted({r.currency or '?' for r in recs}))}")
    if info.get("pending_year"):
        note += f"; newer accounts (FY{info['pending_year']}) filed but not yet in the open API"
    if any(r.money_ambiguous for r in recs):
        note += "; some records withheld as ambiguous (see claim notes)"
    company.outcome(ModuleOutcome(MODULE, "available", note=note, **base_outcome))
    company.data[MODULE] = {
        "status": "available",
        "note": note,
        "source_url": url,
        "retrieved_at": retrieved_at,
        "content_sha256": sha,
        "records": summaries,
        "latest": latest("company") or latest("group"),
        "latest_group": latest("group"),
        "derived": derived,
        **info,
    }


# --------------------------------------------------------------------------- copy API (extras)
def _copy_rate_state(ctx: Any) -> dict[str, Any]:
    st = ctx.shared.get("accounts.copy_rate")
    if st is None:
        st = ctx.shared["accounts.copy_rate"] = {"lock": asyncio.Lock(), "remaining": None, "at": 0.0}
    return st


async def run_copies(ctx: Any, company: CompanyResult) -> None:
    """Filed-years list from the annual-accounts copy API (tier "extras", module "account_copies").

    Returns immediately (no outcome) unless `ctx.allow("extras", 1)`. Rate limited to ~30
    requests/min shared across the run: calls are serialised and stop while the last seen
    `x-rate-limit-remaining` is below 5. Never raises."""
    if COPIES_MODULE in company.modules:
        return
    orgnr = company.organisation_number
    url = f"{BASE}/aarsregnskap/kopi/{orgnr}/aar"
    try:
        if (company.data.get("identity") or {}).get("match_status") != "exact" or not is_valid(orgnr):
            return
        acc = company.modules.get(MODULE)
        if acc is not None and acc.availability == "not_applicable":
            company.outcome(ModuleOutcome(COPIES_MODULE, "not_applicable", note=acc.note))
            return
        if not ctx.allow(COPIES_TIER, 1):
            return
        st = _copy_rate_state(ctx)
        async with st["lock"]:
            rem = st["remaining"]
            if rem is not None and rem < COPY_RATE_FLOOR and time.monotonic() - st["at"] < COPY_RATE_WINDOW_S:
                wait_s = COPY_RATE_WINDOW_S - (time.monotonic() - st["at"])
                if ctx.time_left() - wait_s > 600:
                    # Plenty of run time left: wait for the copy API's rate window to reset.
                    await asyncio.sleep(max(0.0, wait_s))
                    st["remaining"] = None
                else:
                    company.outcome(ModuleOutcome(COPIES_MODULE, "failed", source_url=url,
                                                  note=f"not checked: copy API rate limit nearly exhausted "
                                                       f"(x-rate-limit-remaining={rem}) and run time is short"))
                    return
            if not ctx.allow(COPIES_TIER, 1):
                return
            res = await ctx.get(url, tier=COPIES_TIER, source=COPIES_MODULE,
                                headers={"Accept": "application/json"}, retries=0)
            hdr = res.headers.get("x-rate-limit-remaining", "")
            if hdr.strip().lstrip("-").isdigit():
                st["remaining"], st["at"] = int(hdr), time.monotonic()
            elif res.status == 429:
                st["remaining"], st["at"] = 0, time.monotonic()
        _copies_result(ctx, company, url, res)
    except Exception as exc:  # noqa: BLE001 - modules must never raise
        msg = f"{type(exc).__name__}: {exc}"
        company.error(COPIES_MODULE, "failed", msg, url)
        note = NOT_CHECKED_BUDGET if isinstance(exc, (BudgetExceeded, DeadlineExceeded)) else msg[:200]
        company.outcome(ModuleOutcome(COPIES_MODULE, "failed", note=note, source_url=url))


def _copies_result(ctx: Any, company: CompanyResult, url: str, res: Any) -> None:
    orgnr = company.organisation_number
    retrieved_at = utc_iso(res.retrieved_at)
    sha = ctx.snapshots.save_result(res, retrieved_at)
    base = dict(source_url=url, retrieved_at=retrieved_at, http_status=res.status, requests=1 + len(res.redirects))
    if res.status in (403, 429):
        company.outcome(ModuleOutcome(COPIES_MODULE, "blocked", note=f"copy API refused (HTTP {res.status})", **base))
        return
    if res.status == 404:
        company.outcome(ModuleOutcome(COPIES_MODULE, "not_available", note="no annual-account copies listed", **base))
        return
    if res.status != 200:
        company.error(COPIES_MODULE, "failed", f"HTTP {res.status}", url)
        company.outcome(ModuleOutcome(COPIES_MODULE, "failed", note=f"unexpected HTTP {res.status}", **base))
        return
    text = res.content.decode("utf-8").lstrip("\ufeff")
    body = parse_json_exact(text)
    spans = index_spans(text)
    if not isinstance(body, list):
        raise ValueError("copy-year response is not a JSON array")
    years: dict[int, int] = {}
    for i, y in enumerate(body):
        s = str(y) if isinstance(y, (str, Decimal)) else ""
        if re.fullmatch(r"(19|20)[0-9]{2}", s):
            years.setdefault(int(s), i)
    if not years:
        company.outcome(ModuleOutcome(COPIES_MODULE, "not_available", note="no annual-account copies listed", **base))
        company.data[COPIES_MODULE] = {"years": [], "source_url": url}
        return

    def ev(locator: str, span: str) -> Evidence:
        return Evidence(source_url=url, source_class=OFFICIAL_ACCOUNT_COPIES, source_name=COPIES_SOURCE_NAME,
                        retrieved_at=retrieved_at, content_sha256=sha, claim_span=span, locator=locator,
                        extraction_method=COPIES_EXTRACTION, page_url=PAGE_URL.format(orgnr=orgnr),
                        http_status=res.status, licence=NLOD)

    ordered = sorted(years)
    claims = [Claim(field="accounts_filing_years", value=ordered, evidence=[ev("", text[:300])],
                    display=", ".join(str(y) for y in ordered), method="verbatim_json",
                    note="years with an annual-accounts copy (scanned PDF) in Regnskapsregisteret")]
    for y in ordered[-COPY_YEARS_PUBLISHED:]:
        sp = spans.get(f"/{years[y]}")
        pdf = f"{BASE}/aarsregnskap/kopi/{orgnr}/{y}"
        claims.append(Claim(
            field="annual_report_copy", value=pdf,
            evidence=[ev(f"/{years[y]}", text[sp[0]:sp[2]] if sp else str(y))], qualifier=str(y),
            display=f"Annual accounts {y} (scanned PDF copy)", method="copy_year_list_url",
            note="PDF URL of the listed filing year (serves application/pdf; not fetched by the agent)",
        ))
    company.add(claims)
    company.outcome(ModuleOutcome(COPIES_MODULE, "available", note=f"{len(ordered)} filing year(s) listed", **base))
    company.data[COPIES_MODULE] = {"years": ordered, "source_url": url,
                                   "copies": {y: f"{BASE}/aarsregnskap/kopi/{orgnr}/{y}" for y in ordered}}
