"""Støtteregisteret (state aid register, Brønnøysundregistrene) — per-company module, tier ``extras``.

``POST https://stottetiltak-registerinfo-api.app.brreg.no/api/v1/soek/stoettetildeling`` with the JSON
body ``[{"field":"STOETTEMOTTAKER_ORGNR","value":<orgnr>,"matchType":"EXACT_MATCH"}]`` (no key needed;
one organisation number per call). Publishes the 10 most recent grants whose recipient role carries
exactly this organisation number. Amounts are parsed as ``Decimal`` exactly as served (never scaled);
grants reported only as an amount interval carry ``amount: None`` plus ``amount_range``.

Code values (purpose, instrument, status) and scheme names are resolved once per run from the
register's own code lists / scheme search when the planner allows; otherwise the codes are kept.
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from typing import Any

from ..envelope import (NLOD, OFFICIAL_STATE_AID, Claim, CompanyResult, Evidence, ModuleOutcome, utc_iso)

MODULE = "state_aid"
VERSION = "state_aid@1"
TIER = "extras"
API = "https://stottetiltak-registerinfo-api.app.brreg.no/api/v1"
SEARCH_URL = (f"{API}/soek/stoettetildeling?pageSize=100&page=1&sortBy=MOTTATT_DATO&sortDirection=DESC"
              "&language=NOB")
SCHEMES_URL = f"{API}/soek/stoetteordning?pageSize=1000&page=1&language=NOB"
CODELISTS = ("formaalstype", "stoetteinstrumenttype")
SOURCE_NAME = "Støtteregisteret (Brønnøysundregistrene)"
MAX_GRANTS = 10
BUDGET_NOTE = "not checked: request budget reserved for higher-priority sources"
JSON_HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}


def grant_page(nr: str) -> str:
    return f"https://stotte.brreg.no/nb/oppslag/stoettetildeling/{nr}"


def request_body(orgnr: str) -> bytes:
    return json.dumps([{"field": "STOETTEMOTTAKER_ORGNR", "value": orgnr, "matchType": "EXACT_MATCH"}],
                      separators=(",", ":")).encode("utf-8")


def _eligible(company: CompanyResult) -> bool:
    ident = company.data.get("identity") or {}
    return ident.get("match_status") in (None, "exact")


def _span(**kv: Any) -> str:
    def enc(v: Any) -> Any:
        return str(v) if isinstance(v, Decimal) else v
    return ",".join(f"{json.dumps(k)}:{json.dumps(enc(v), ensure_ascii=False, separators=(',', ':'))}"
                    for k, v in kv.items())[:300]


def _fmt_amount(amount: Decimal, currency: str | None) -> str:
    q = amount.quantize(Decimal("0.01")) if amount != amount.to_integral_value() else amount.quantize(Decimal(1))
    whole, _, frac = f"{q:f}".partition(".")
    sign = "-" if whole.startswith("-") else ""
    whole = whole.lstrip("-")
    groups = []
    while whole:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    return f"{currency or ''} {sign}{' '.join(groups)}{(',' + frac) if frac else ''}".strip()


async def _lookups(ctx) -> dict[str, Any]:
    """Run-level code lists + scheme names, fetched once (shared across companies)."""
    shared = ctx.shared
    lock = shared.setdefault("state_aid_lookup_lock", asyncio.Lock())
    async with lock:
        if "state_aid_lookups" in shared:
            return shared["state_aid_lookups"]
        out: dict[str, Any] = {"codes": {}, "schemes": {}, "evidence": {}}
        for name in CODELISTS:
            url = f"{API}/kodeliste/{name}?language=NOB"
            if not ctx.allow(TIER, 1):
                break
            try:
                res = await ctx.get(url, tier=TIER, source=MODULE, headers={"Accept": "application/json"})
                if res.ok:
                    ra = utc_iso(res.retrieved_at)
                    sha = ctx.snapshots.save_result(res, ra)
                    for i, row in enumerate(res.json()):
                        if isinstance(row, dict) and row.get("identifikator"):
                            out["codes"][row["identifikator"]] = (row.get("navn"), url, sha, ra, i)
            except Exception:  # noqa: BLE001 - lookups are optional
                pass
        if ctx.allow(TIER, 1):
            try:
                res = await ctx.post(SCHEMES_URL, tier=TIER, source=MODULE, data=b"[]", headers=JSON_HEADERS)
                if res.ok:
                    ra = utc_iso(res.retrieved_at)
                    sha = ctx.snapshots.save_result(res, ra)
                    for i, s in enumerate(res.json().get("stoetteordning") or []):
                        names = s.get("navn") or []
                        nm = next((n.get("tekst") for n in names if n.get("spraak") == "NOR"), None) or \
                            next((n.get("tekst") for n in names), None)
                        if s.get("stoettetiltaksnummer") and nm:
                            out["schemes"][s["stoettetiltaksnummer"]] = (nm, SCHEMES_URL, sha, ra, i)
            except Exception:  # noqa: BLE001
                pass
        shared["state_aid_lookups"] = out
        return out


def _purpose_code(g: dict[str, Any]) -> str | None:
    eg = g.get("eoesRettsligGrunnlag") or {}
    for part in ("artikkel", "retningslinjeEsa", "primaerrettsligGrunnlag"):
        f = (eg.get(part) or {}).get("formaal")
        if isinstance(f, list):
            f = f[0] if f else None
        if f:
            return f
    return None


def _role(g: dict[str, Any], kind: str) -> dict[str, Any] | None:
    for r in g.get("rolle") or []:
        if r.get("type") == kind:
            return r.get("rolleinnehaverVirksomhet") or r.get("rolleinnehaverAnnenAktoer") or {}
    return None


async def run(ctx, company: CompanyResult) -> None:
    try:
        await _run(ctx, company)
    except Exception as exc:  # noqa: BLE001 - modules never raise
        company.error(MODULE, "failed", f"{type(exc).__name__}: {exc}", SEARCH_URL)
        company.outcome(ModuleOutcome(MODULE, "failed", note=f"internal error: {type(exc).__name__}"))


async def _run(ctx, company: CompanyResult) -> None:
    o = company.organisation_number
    if not _eligible(company):
        company.outcome(ModuleOutcome(MODULE, "not_applicable",
                                      note="identity not confirmed as an exact organisation-number match"))
        return
    if not ctx.allow(TIER, 1):
        company.outcome(ModuleOutcome(MODULE, "failed", note=BUDGET_NOTE))
        return
    body = request_body(o)
    via = f"POST {SEARCH_URL} body={body.decode()}"
    try:
        res = await ctx.post(SEARCH_URL, tier=TIER, source=MODULE, data=body, headers=JSON_HEADERS)
    except Exception as exc:  # noqa: BLE001
        msg = f"Støtteregisteret search failed: {type(exc).__name__}: {exc}"
        company.error(MODULE, "failed", msg, SEARCH_URL)
        company.outcome(ModuleOutcome(MODULE, "failed", note=msg, source_url=SEARCH_URL))
        return
    if res.status in (401, 403, 429):
        company.outcome(ModuleOutcome(MODULE, "blocked", note=f"Støtteregisteret refused the search (HTTP {res.status})",
                                      source_url=SEARCH_URL, http_status=res.status))
        return
    if not res.ok:
        msg = f"Støtteregisteret search HTTP {res.status}"
        company.error(MODULE, "failed", msg, SEARCH_URL)
        company.outcome(ModuleOutcome(MODULE, "failed", note=msg, source_url=SEARCH_URL, http_status=res.status))
        return
    retrieved_at = utc_iso(res.retrieved_at)
    sha = ctx.snapshots.save_result(res, retrieved_at)
    doc = json.loads(res.content, parse_float=Decimal)
    grants = [(i, g) for i, g in enumerate(doc.get("stoettetildeling") or [])
              if ((_role(g, "rolletype.stoettemottaker") or {}).get("organisasjonsnummer")) == o]
    if not grants:
        company.data["state_aid"] = {"grant_count": 0}
        company.outcome(ModuleOutcome(MODULE, "not_available", source_url=SEARCH_URL, retrieved_at=retrieved_at,
                                      http_status=res.status,
                                      note="no state aid grants registered with this organisation number as recipient"))
        return
    grants.sort(key=lambda t: (t[1].get("tildelingsdato") or "", t[1].get("stoettetiltaksnummer") or ""), reverse=True)
    lookups = await _lookups(ctx)
    codes, schemes = lookups["codes"], lookups["schemes"]
    claims: list[Claim] = []
    for i, g in grants[:MAX_GRANTS]:
        nr = g.get("stoettetiltaksnummer")
        amt = g.get("tildeltBeloep") or {}
        rng = g.get("beloepsintervall") or {}
        amount = amt.get("beloep") if isinstance(amt.get("beloep"), (Decimal, int)) else None
        if isinstance(amount, int):
            amount = Decimal(amount)
        currency = amt.get("valuta") or (rng.get("fraBeloep") or {}).get("valuta")
        giver = _role(g, "rolletype.stoettegiver") or {}
        scheme_id = g.get("tilknyttetStoetteordning")
        scheme_hit = schemes.get(scheme_id) if scheme_id else None
        purpose_code = _purpose_code(g)
        purpose_hit = codes.get(purpose_code) if purpose_code else None
        instr_hit = codes.get(g.get("stoetteinstrument") or "")
        status_code = g.get("status") or ""
        status_hit = codes.get(status_code)
        value: dict[str, Any] = {
            "date": g.get("tildelingsdato"), "amount": amount, "currency": currency,
            "grantor": giver.get("navn"), "grantor_organisation_number": giver.get("organisasjonsnummer"),
            "scheme": scheme_hit[0] if scheme_hit else scheme_id, "scheme_id": scheme_id,
            "purpose": purpose_hit[0] if purpose_hit else purpose_code,
            "instrument": instr_hit[0] if instr_hit else g.get("stoetteinstrument"),
            "status": status_hit[0] if status_hit else (status_code.split(".", 1)[-1] or None),
        }
        if amount is None and rng:
            lo = (rng.get("fraBeloep") or {}).get("beloep")
            hi = (rng.get("tilBeloep") or {}).get("beloep")
            value["amount_range"] = {"from": lo, "to": hi, "currency": currency}
        if amount is not None:
            disp_amt = _fmt_amount(amount, currency)
        elif rng:
            lo, hi = value["amount_range"]["from"], value["amount_range"]["to"]
            disp_amt = (f"{_fmt_amount(lo, currency)} – {_fmt_amount(hi, None)} (interval)"
                        if lo is not None and hi is not None else "amount interval")
        else:
            disp_amt = "amount not stated"
        span_kv: dict[str, Any] = {"stoettetiltaksnummer": nr, "tildelingsdato": g.get("tildelingsdato")}
        if amount is not None:
            span_kv["tildeltBeloep"] = {"beloep": str(amount), "valuta": currency}
        elif rng:
            span_kv["beloepsintervall"] = {k: str((rng.get(k) or {}).get("beloep")) for k in ("fraBeloep", "tilBeloep")}
        span_kv["stoettegiver"] = giver.get("navn")
        span = _span(**span_kv)
        evs = [Evidence(source_url=SEARCH_URL, source_class=OFFICIAL_STATE_AID, source_name=SOURCE_NAME,
                        retrieved_at=retrieved_at, content_sha256=sha, claim_span=span,
                        locator=f"/stoettetildeling/{i}", extraction_method=VERSION, page_url=grant_page(nr),
                        effective_at=g.get("tildelingsdato"), http_status=res.status, licence=NLOD, retrieved_via=via)]
        for hit, what in ((scheme_hit, "scheme name"), (purpose_hit, "purpose label")):
            if hit:
                name, url, lsha, lra, li = hit
                loc = f"/stoetteordning/{li}/navn" if what == "scheme name" else f"/{li}/navn"
                evs.append(Evidence(source_url=url, source_class=OFFICIAL_STATE_AID, source_name=SOURCE_NAME,
                                    retrieved_at=lra, content_sha256=lsha, claim_span=_span(navn=name)[:300],
                                    locator=loc, extraction_method=f"{VERSION}:{what.replace(' ', '_')}",
                                    licence=NLOD,
                                    retrieved_via=(f"POST {url} body=[]" if what == "scheme name" else None)))
        notes = []
        if amount is None and rng:
            notes.append("the register states only an amount interval for this grant")
        if g.get("status") and g.get("status") != "stoettetiltaksstatus.registrert":
            notes.append(f"register status: {value['status']}")
        claims.append(Claim(field="state_aid_grant", value=value, evidence=evs, qualifier=nr, confidence=0.97,
                            display=f"{g.get('tildelingsdato')}: {disp_amt} from {giver.get('navn') or 'n/a'}"
                                    f" ({value['scheme'] or 'no scheme'})",
                            effective_at=g.get("tildelingsdato"), method="stoetteregisteret_recipient_orgnr",
                            note="; ".join(notes) or None))
    company.add(claims)
    company.data["state_aid"] = {"grant_count": len(grants), "published": len(claims),
                                 "latest_date": grants[0][1].get("tildelingsdato"),
                                 "page_capped": len(doc.get("stoettetildeling") or []) >= 100}
    note = f"{len(grants)} grants registered with this organisation number as recipient"
    if len(grants) > MAX_GRANTS:
        note += f"; the {MAX_GRANTS} most recent are published"
    if len(doc.get("stoettetildeling") or []) >= 100:
        note += " (first 100 by received date checked)"
    company.outcome(ModuleOutcome(MODULE, "available", note=note, source_url=SEARCH_URL, retrieved_at=retrieved_at,
                                  http_status=res.status))
