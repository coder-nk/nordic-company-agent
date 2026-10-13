"""Signalpost envelope model: claims, evidence, availability states, stable keys.

Every module turns what it fetched into `Claim` objects that carry their own `Evidence`.
The `EnvelopeBuilder` deduplicates evidence, assigns deterministic ids (so re-running on the
same source snapshot yields byte-identical claims and no false changes), and serialises one
terminal envelope per organisation number.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterable

from . import __version__

AGENT_NAME = "nordic-company-agent"
ENVELOPE_SCHEMA = "nca.signalpost.envelope/1.0"

AVAILABILITY = ("available", "not_available", "blocked", "not_applicable", "ambiguous", "failed")

# Source classes (evidence.source_class)
OFFICIAL_REGISTRY = "official_registry"                # live Enhetsregisteret API
OFFICIAL_REGISTRY_BULK = "official_registry_bulk"      # supplied/downloaded Enhetsregisteret snapshot
OFFICIAL_REGISTRY_FEED = "official_registry_update_feed"
OFFICIAL_ROLES = "official_roles"
OFFICIAL_SUBUNITS = "official_subunits"
OFFICIAL_GROUP = "official_group_structure"
OFFICIAL_ACCOUNTS = "official_annual_accounts"
OFFICIAL_ACCOUNT_COPIES = "official_annual_account_copies"
OFFICIAL_AUTHORISATIONS = "official_signature_prokura"
OFFICIAL_STATE_AID = "official_state_aid_register"
OFFICIAL_JOBS_FEED = "official_public_jobs_feed"       # NAV arbeidsplassen feed
OFFICIAL_GRANTS = "official_public_grants"
COMPANY_OWNED = "company_owned"                         # verified official website
COMPANY_LINKED_PROFILE = "company_linked_profile"       # social profile URL linked from verified site
OPEN_KNOWLEDGE_BASE = "open_knowledge_base"             # Wikidata
OFFICIAL_LEI = "official_lei_registry"                  # GLEIF

NLOD = ("Contains data under the Norwegian licence for Open Government data (NLOD) 2.0 distributed by "
        "Brønnøysundregistrene (https://data.norge.no/nlod/en/2.0). Data has been normalised by nordic-company-agent.")
CC0_GLEIF = "GLEIF LEI data, CC0 1.0"
CC0_WIKIDATA = "Wikidata, CC0 1.0"
NAV_TERMS = "NAV arbeidsplassen job-ad feed, https://arbeidsplassen.nav.no/vilkar-api"
COMPANY_SITE = "Company-owned public web page; fetched once, robots.txt honoured; quoted as evidence only"


def utc_iso(dt: datetime | None = None) -> str:
    dt = dt or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def canonical(value: Any) -> str:
    """Stable JSON text for hashing/comparing values (Decimal-safe, key-sorted)."""
    return json.dumps(_jsonable(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        # Keep exact numeric text: integers stay integers, otherwise a plain decimal string.
        if value == value.to_integral_value():
            return int(value)
        f = float(value)
        # Use a JSON number only when it round-trips exactly; otherwise keep the exact decimal text.
        return f if Decimal(repr(f)) == value else str(value)
    if isinstance(value, (datetime,)):
        return utc_iso(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, set):
        return sorted(_jsonable(v) for v in value)
    return value


def short_hash(*parts: Any, n: int = 12) -> str:
    return hashlib.sha1("\x1f".join(canonical(p) for p in parts).encode("utf-8")).hexdigest()[:n]


@dataclass
class Evidence:
    source_url: str
    source_class: str
    source_name: str
    retrieved_at: str                      # ISO-8601 UTC 'Z'
    content_sha256: str | None             # SHA-256 of the raw response body the claim was read from
    claim_span: str | None = None          # verbatim fragment of the source containing the value
    locator: str | None = None             # JSON pointer, CSS-ish selector, or 'text@offset'
    extraction_method: str = ""            # e.g. 'brreg_enheter_json@1'
    page_url: str | None = None            # human-readable page to verify
    effective_at: str | None = None        # reporting/effective date asserted by the source
    http_status: int | None = None
    final_url: str | None = None
    licence: str | None = None
    retrieved_via: str | None = None       # batch URL when the record came from a multi-id request

    @property
    def id(self) -> str:
        return "ev-" + short_hash(self.source_url, self.content_sha256, self.locator, self.claim_span,
                                  self.extraction_method)

    def to_dict(self) -> dict[str, Any]:
        d = {"id": self.id}
        for k in ("source_url", "source_class", "source_name", "retrieved_at", "content_sha256", "claim_span",
                  "locator", "extraction_method", "page_url", "effective_at", "http_status", "final_url",
                  "licence", "retrieved_via"):
            v = getattr(self, k)
            if v is not None and v != "":
                d[k] = v
        return d


@dataclass
class Claim:
    field: str
    value: Any
    evidence: list[Evidence] = field(default_factory=list)
    availability: str = "available"
    confidence: float = 1.0
    qualifier: str | None = None           # disambiguates multi-valued fields in the stable key
    display: str | None = None             # human-readable rendering
    effective_at: str | None = None        # date the value is valid for (as-of / reporting date)
    period: dict[str, Any] | None = None   # {'start','end','year','scope'} for accounts
    relation: str = "self"                 # self | parent | ultimate_parent | group_member | subunit | related
    method: str = ""                       # extraction/verification method id
    match_basis: str = "organisation_number_exact"
    note: str | None = None

    def __post_init__(self):
        if self.availability not in AVAILABILITY:
            raise ValueError(f"invalid availability {self.availability!r} for {self.field}")
        if self.availability != "available":
            # Never encode absence as a value (in particular never as 0).
            self.value = None

    @property
    def key(self) -> str:
        return f"{self.field}|{self.qualifier}" if self.qualifier else self.field

    @property
    def id(self) -> str:
        return "cl-" + short_hash(self.key, self.value, self.availability, self.period, self.relation)

    def to_dict(self, evidence_ids: list[str]) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "key": self.key,
            "field": self.field,
            "family": field_family(self.field),
            "value": _jsonable(self.value),
            "availability": self.availability,
            "confidence": round(float(self.confidence), 3),
            "evidence_ids": evidence_ids,
        }
        for k in ("display", "effective_at", "period", "note"):
            v = getattr(self, k)
            if v is not None:
                d[k] = _jsonable(v)
        if self.relation != "self":
            d["relation"] = self.relation
        if self.method:
            d["method"] = self.method
        d["match_basis"] = self.match_basis
        return d


# --------------------------------------------------------------------------- field families
# Field -> family. Families mirror the seven required envelope sections plus external families
# used for recall scoring. Unknown fields fall back to 'other'.
FAMILIES: dict[str, tuple[str, ...]] = {
    "legal_identity": (
        "organisation_number", "legal_name", "former_name", "legal_form", "registry_status", "bankruptcy",
        "liquidation", "forced_dissolution", "deletion", "business_address", "postal_address", "municipality",
        "county", "country", "industry", "sector", "founding_date", "registration_date",
        "business_register_registration", "vat_registration", "voluntary_register_registration",
        "foundation_register_registration", "employees", "share_capital", "statutory_purpose", "activity",
        "language_form", "registry_annotation", "audit_exemption", "group_membership", "public_sector_parent",
        "registry_website", "registry_email", "registry_phone", "registry_mobile", "lei", "lei_status",
        "lei_next_renewal", "signature_rule", "prokura_rule", "foreign_registration",
    ),
    "corporate_structure": (
        "parent_company", "ultimate_parent", "group_member", "group_subsidiary", "lei_parent",
    ),
    "annual_accounts": (
        "accounts_period", "accounts_currency", "accounting_framework", "audit_status", "revenue",
        "sales_revenue", "operating_expenses", "payroll_expenses", "operating_profit", "net_financial_items",
        "financial_income", "financial_expenses", "profit_before_tax", "tax_expense", "net_profit",
        "total_comprehensive_income", "total_assets", "fixed_assets", "current_assets", "cash_and_bank",
        "inventories", "receivables", "goodwill", "equity", "paid_in_equity", "retained_earnings",
        "total_liabilities", "current_liabilities", "long_term_liabilities", "latest_submitted_accounts_year",
        "accounts_filing_years", "annual_report_copy", "equity_ratio", "operating_margin",
    ),
    "leadership": (
        "ceo", "chair", "deputy_chair", "board_member", "deputy_board_member", "observer", "auditor",
        "accountant", "owner", "partner", "general_partner", "contact_person", "business_manager",
        "norwegian_representative", "role_other", "website_leader",
    ),
    "workplaces": ("registered_workplace", "registered_workplace_count", "website_location"),
    "website": (
        "official_website", "website_verification", "website_title", "website_description", "brand_name",
        "logo_url", "website_language", "contact_email", "contact_phone", "website_address", "opening_hours",
        "careers_page", "website_org_number_statement",
    ),
    "social_profiles": (
        "social_profile",
    ),
    "hiring": ("job_posting", "active_job_count", "hiring_status"),
    "public_activity": (
        "news_item", "website_update", "registry_event", "role_change_event", "state_aid_grant",
        "research_grant", "tax_incentive_project", "encyclopedia_entry", "wikidata_item", "stock_listing",
    ),
}
_FIELD_TO_FAMILY = {f: fam for fam, fields in FAMILIES.items() for f in fields}


def field_family(field_name: str) -> str:
    return _FIELD_TO_FAMILY.get(field_name, "other")


# The seven required sections of the Signalpost brief, mapped to families.
SECTIONS: dict[str, tuple[str, ...]] = {
    "legal_identity_and_brand": ("legal_identity", "corporate_structure"),
    "annual_accounts": ("annual_accounts",),
    "leadership_and_workplaces": ("leadership", "workplaces"),
    "website_and_profiles": ("website", "social_profiles"),
    "hiring_and_public_activity": ("hiring", "public_activity"),
}


# --------------------------------------------------------------------------- module outcome
@dataclass
class ModuleOutcome:
    """What a source module reports about itself for one company (terminal state per module)."""
    module: str
    availability: str                      # one of AVAILABILITY
    note: str | None = None
    source_url: str | None = None
    retrieved_at: str | None = None
    http_status: int | None = None
    requests: int = 0

    def __post_init__(self):
        if self.availability not in AVAILABILITY:
            raise ValueError(f"invalid availability {self.availability!r} for module {self.module}")

    def to_dict(self) -> dict[str, Any]:
        d = {"module": self.module, "availability": self.availability}
        for k in ("note", "source_url", "retrieved_at", "http_status"):
            v = getattr(self, k)
            if v is not None:
                d[k] = v
        if self.requests:
            d["requests"] = self.requests
        return d


@dataclass
class CompanyResult:
    """Everything gathered for one organisation number during a run (pre-serialisation)."""
    organisation_number: str
    claims: list[Claim] = field(default_factory=list)
    modules: dict[str, ModuleOutcome] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)   # structured module data for synthesis/other modules
    requests: int = 0

    def add(self, claims: Iterable[Claim]) -> None:
        self.claims.extend(claims)

    def outcome(self, outcome: ModuleOutcome) -> None:
        self.modules[outcome.module] = outcome

    def error(self, module: str, state: str, message: str, url: str | None = None) -> None:
        e = {"module": module, "state": state, "message": message[:500]}
        if url:
            e["url"] = url
        self.errors.append(e)

    def get(self, field_name: str) -> list[Claim]:
        return [c for c in self.claims if c.field == field_name and c.availability == "available"]

    def first_value(self, field_name: str) -> Any:
        for c in self.claims:
            if c.field == field_name and c.availability == "available":
                return c.value
        return None


def dedupe_claims(claims: list[Claim]) -> list[Claim]:
    """Merge claims with the same key and value (keeping all evidence); drop non-available
    placeholders for a key that also has an available claim."""
    by_key: dict[tuple[str, str], Claim] = {}
    order: list[tuple[str, str]] = []
    for c in claims:
        k = (c.key, canonical(c.value) + "|" + c.availability + "|" + canonical(c.period) + "|" + c.relation)
        if k in by_key:
            existing = by_key[k]
            seen = {e.id for e in existing.evidence}
            existing.evidence.extend(e for e in c.evidence if e.id not in seen)
            existing.confidence = max(existing.confidence, c.confidence)
        else:
            by_key[k] = c
            order.append(k)
    out = [by_key[k] for k in order]
    available_keys = {c.key for c in out if c.availability == "available"}
    return [c for c in out if c.availability == "available" or c.key not in available_keys]
