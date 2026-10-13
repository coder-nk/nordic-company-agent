"""Current roles (board, CEO, auditor, ...) from `GET /enhetsregisteret/api/enheter/{orgnr}/roller`.

Personal data: only holder name, role, role group and the group's last-changed date are published.
Birth dates (`fodselsdato`) are cut out of the body before it is stored; the stored snapshot is
flagged `redacted` and indexed under the SHA-256 of the original bytes (which is also the evidence
`content_sha256`). Deregistered holders (`avregistrert: true`) are not current and are skipped;
the `erDoed` flag is never output.
"""

from __future__ import annotations

import json
import unicodedata
from typing import Any

from ..context import RunContext
from ..envelope import NLOD, OFFICIAL_ROLES, Claim, CompanyResult, Evidence, ModuleOutcome, utc_iso
from ..orgnr import formatted
from .registry import (ACCEPT_JSON, BASE, BUDGET_NOTE, decode_text, enhet_page, outcome_state_for_status, pointer,
                       redact_birth_dates, slug, span_at)

MODULE = "roles"
VERSION = "1"
METHOD = f"{MODULE}@{VERSION}"
TIER = "roles"
SOURCE_NAME = "Brønnøysundregistrene, Enhetsregisteret roles API (roller)"

# Role type code (rolletyper code list, verified against research/samples codelist) -> claim field.
ROLE_FIELD = {
    "DAGL": "ceo",
    "LEDE": "chair",
    "NEST": "deputy_chair",
    "MEDL": "board_member",
    "VARA": "deputy_board_member",
    "OBS": "observer",
    "REVI": "auditor",
    "REGN": "accountant",
    "INNH": "owner",
    "DTPR": "partner",
    "DTSO": "partner",
    "KOMP": "general_partner",
    "KONT": "contact_person",
    "FFØR": "business_manager",
    "REPR": "norwegian_representative",
}
MULTI_FIELDS = {"board_member", "deputy_board_member", "observer", "partner", "role_other"}
FIELD_ORDER = ["ceo", "chair", "deputy_chair", "board_member", "deputy_board_member", "observer", "owner", "partner",
               "general_partner", "auditor", "accountant", "contact_person", "business_manager",
               "norwegian_representative", "role_other"]
NO_ROLES_FORMS = {"UTLA"}   # the roles endpoint returns 404 for foreign entities


def roles_url(orgnr: str) -> str:
    return f"{BASE}/enheter/{orgnr}/roller"


def _nfc(s: Any) -> str:
    return unicodedata.normalize("NFC", str(s or "")).strip()


def _person_name(navn: Any) -> str | None:
    if isinstance(navn, dict):
        parts = [_nfc(navn.get(k)) for k in ("fornavn", "mellomnavn", "etternavn")]
        s = " ".join(p for p in parts if p)
        return s or None
    if isinstance(navn, str):
        return " ".join(navn.split()) or None
    return None


def _entity_name(navn: Any) -> str | None:
    if isinstance(navn, list):
        return " ".join(_nfc(x) for x in navn if _nfc(x)) or None
    if isinstance(navn, str):
        return " ".join(navn.split()) or None
    return None


def parse_roles(doc: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    """Current roles as dicts (with private `_ptr`, `_order` keys) and the number skipped as deregistered."""
    out: list[dict[str, Any]] = []
    skipped = 0
    for gi, g in enumerate(doc.get("rollegrupper") or []):
        if not isinstance(g, dict):
            continue
        gt = g.get("type") or {}
        for ri, r in enumerate(g.get("roller") or []):
            if not isinstance(r, dict):
                continue
            if r.get("avregistrert") is True:
                skipped += 1
                continue
            rt = r.get("type") or {}
            code = _nfc(rt.get("kode"))
            base = pointer("rollegrupper", gi, "roller", ri)
            entry: dict[str, Any] = {}
            if isinstance(r.get("person"), dict):
                name = _person_name(r["person"].get("navn"))
                entry["_ptr"] = base + "/person/navn"
                entry["_holder"] = "person"
            elif isinstance(r.get("enhet"), dict):
                name = _entity_name(r["enhet"].get("navn"))
                org = str(r["enhet"].get("organisasjonsnummer") or "") or None
                entry["organisation_number"] = org
                entry["_ptr"] = base + ("/enhet/organisasjonsnummer" if org else "/enhet/navn")
                entry["_holder"] = "entity"
            elif isinstance(r.get("bostyrer"), dict):
                name = _entity_name(r["bostyrer"].get("navn"))
                entry["_ptr"] = base + "/bostyrer/navn"
                entry["_holder"] = "estate_administrator"
            else:
                continue
            if not name:
                continue
            field = ROLE_FIELD.get(code, "role_other")
            entry.update({"field": field, "name": name, "role_code": code or None, "role": rt.get("beskrivelse"),
                          "group_code": _nfc(gt.get("kode")) or None, "group": gt.get("beskrivelse"),
                          "last_changed": g.get("sistEndret")})
            valgt = r.get("valgtAv")
            if isinstance(valgt, dict) and (valgt.get("beskrivelse") or valgt.get("kode")):
                entry["elected_by"] = valgt.get("beskrivelse") or valgt.get("kode")
            if r.get("ansvarsandel"):
                entry["liability_share"] = r["ansvarsandel"]
            rk = r.get("rekkefolge")
            entry["_order"] = (gi, rk if isinstance(rk, int) else ri, ri)
            out.append(entry)
    _assign_qualifiers(out)
    order = {f: i for i, f in enumerate(FIELD_ORDER)}
    out.sort(key=lambda e: (order.get(e["field"], 99), e["_order"], e.get("qualifier") or ""))
    return out, skipped


def _assign_qualifiers(entries: list[dict[str, Any]]) -> None:
    counts: dict[str, int] = {}
    for e in entries:
        counts[e["field"]] = counts.get(e["field"], 0) + 1
    used: set[str] = set()
    for e in sorted(entries, key=lambda x: x["_order"]):
        f = e["field"]
        ident = e.get("organisation_number") or slug(e["name"])
        if f == "role_other":
            q = f"{e.get('role_code') or 'UNKNOWN'}|{ident}"
        elif f in MULTI_FIELDS or counts[f] > 1:
            q = ident
        else:
            q = None
        if q is not None:
            cand = q
            if f"{f}|{cand}" in used:
                cand = f"{q}|{e.get('role_code')}"
            base, n = cand, 2
            while f"{f}|{cand}" in used:
                cand, n = f"{base}|{n}", n + 1
            used.add(f"{f}|{cand}")
            q = cand
        e["qualifier"] = q


def public_entry(e: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in e.items() if not k.startswith("_")}


def _value(e: dict[str, Any]) -> dict[str, Any]:
    v = {k: e.get(k) for k in ("name", "role_code", "role", "group_code", "group", "last_changed")}
    for k in ("elected_by", "organisation_number", "liability_share"):
        if e.get(k):
            v[k] = e[k]
    return v


def _display(e: dict[str, Any]) -> str:
    who = e["name"]
    if e.get("organisation_number") and len(e["organisation_number"]) == 9:
        who += f" ({formatted(e['organisation_number'])})"
    return f"{who}, {e.get('role') or e.get('role_code')}"


async def run(ctx: RunContext, company: CompanyResult) -> None:
    try:
        await _run(ctx, company)
    except Exception as exc:
        company.error(MODULE, "failed", f"{type(exc).__name__}: {exc}")
        company.outcome(ModuleOutcome(MODULE, "failed", note=f"module error: {type(exc).__name__}"))


async def _run(ctx: RunContext, company: CompanyResult) -> None:
    orgnr = company.organisation_number
    ident = company.data.get("identity") or {}
    status = ident.get("match_status")
    rec = company.data.get("registry") or {}
    form = (rec.get("organisasjonsform") or {}).get("kode") if isinstance(rec.get("organisasjonsform"), dict) else None
    if status == "deleted":
        company.outcome(ModuleOutcome(MODULE, "not_applicable", note="entity is deleted; no current roles"))
        return
    if status != "exact":
        company.outcome(ModuleOutcome(MODULE, "not_applicable", note=f"identity not resolved to an active main unit ({status})"))
        return
    if form in NO_ROLES_FORMS:
        company.outcome(ModuleOutcome(MODULE, "not_applicable",
                                      note="foreign entity (UTLA): Enhetsregisteret registers no roles"))
        return
    url = roles_url(orgnr)
    if not ctx.allow(TIER, 1):
        company.outcome(ModuleOutcome(MODULE, "failed", note=BUDGET_NOTE, source_url=url))
        return
    try:
        res = await ctx.get(url, tier=TIER, source=MODULE, headers=ACCEPT_JSON)
    except Exception as exc:
        msg = f"roles lookup failed: {type(exc).__name__}: {exc}"[:300]
        company.error(MODULE, "failed", msg, url)
        company.outcome(ModuleOutcome(MODULE, "failed", note=msg, source_url=url, requests=1))
        return
    retrieved_at = utc_iso(res.retrieved_at)
    if res.status == 404:
        company.outcome(ModuleOutcome(MODULE, "not_available", note="no roles registered (HTTP 404)", source_url=url,
                                      retrieved_at=retrieved_at, http_status=404, requests=1))
        company.data["roles"] = []
        return
    if res.status != 200:
        state = outcome_state_for_status(res.status)
        company.outcome(ModuleOutcome(MODULE, state, note=f"roles lookup returned HTTP {res.status}", source_url=url,
                                      retrieved_at=retrieved_at, http_status=res.status, requests=1))
        if state == "failed":
            company.error(MODULE, "failed", f"HTTP {res.status}", url)
        return
    original_sha = res.sha256
    redacted = redact_birth_dates(res.content)
    ctx.snapshots.save(redacted, url=url, retrieved_at=retrieved_at, content_type=res.headers.get("content-type"),
                       sha256=original_sha, redacted=True)
    text = decode_text(redacted)
    try:
        doc = json.loads(text)
    except ValueError as exc:
        company.outcome(ModuleOutcome(MODULE, "failed", note=f"unparseable roles response: {exc}"[:300], source_url=url,
                                      retrieved_at=retrieved_at, http_status=200, requests=1))
        return
    if not isinstance(doc, dict):
        company.outcome(ModuleOutcome(MODULE, "failed", note="roles response is not an object", source_url=url,
                                      retrieved_at=retrieved_at, http_status=200, requests=1))
        return
    echo = _echo(doc)
    if echo is not None and echo != orgnr:
        company.outcome(ModuleOutcome(MODULE, "ambiguous", note=f"roles payload refers to {echo}; nothing published",
                                      source_url=url, retrieved_at=retrieved_at, http_status=200, requests=1))
        return
    entries, skipped = parse_roles(doc)
    company.data["roles"] = [public_entry(e) for e in entries]
    company.data["roles_deregistered_skipped"] = skipped
    claims: list[Claim] = []
    for e in entries:
        span = span_at(text, e["_ptr"])
        ev = Evidence(source_url=url, source_class=OFFICIAL_ROLES, source_name=SOURCE_NAME, retrieved_at=retrieved_at,
                      content_sha256=original_sha, claim_span=span, locator=e["_ptr"], extraction_method=METHOD,
                      page_url=enhet_page(orgnr), effective_at=e.get("last_changed"), http_status=200, licence=NLOD)
        claims.append(Claim(field=e["field"], value=_value(e), evidence=[ev], qualifier=e.get("qualifier"),
                            display=_display(e), effective_at=e.get("last_changed"),
                            relation="related" if e["_holder"] == "entity" else "self", method=METHOD))
    company.add(claims)
    if claims:
        note = f"{len(claims)} current role(s)" + (f"; {skipped} deregistered (avregistrert) skipped" if skipped else "")
        company.outcome(ModuleOutcome(MODULE, "available", note=note, source_url=url, retrieved_at=retrieved_at,
                                      http_status=200, requests=1))
    else:
        note = ("all registered role holders are deregistered (avregistrert)" if skipped
                else "no current roles registered")
        company.outcome(ModuleOutcome(MODULE, "not_available", note=note, source_url=url, retrieved_at=retrieved_at,
                                      http_status=200, requests=1))


def _echo(doc: dict[str, Any]) -> str | None:
    if doc.get("organisasjonsnummer"):
        return str(doc["organisasjonsnummer"])
    links = doc.get("_links") or {}
    for key in ("enhet", "self"):
        href = ((links.get(key) or {}).get("href") or "") if isinstance(links.get(key), dict) else ""
        parts = [p for p in href.split("/") if p]
        if key == "enhet" and parts:
            return parts[-1]
        if key == "self" and len(parts) >= 2 and parts[-1] == "roller":
            return parts[-2]
    return None
