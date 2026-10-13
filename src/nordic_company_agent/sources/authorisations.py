"""Signature and prokura rules from the open Brreg fullmakt API.

`GET https://data.brreg.no/fullmakt/enheter/{orgnr}/signatur` and `/prokura` (tier "extras"), only for
entities registered in Foretaksregisteret. The rule text is `signaturProkuraFritekst` when filed as
free text; otherwise (typically prokura) it is the role description(s) of the named holders
(e.g. "Prokura hver for seg"). Holder names are published, birth dates are removed before the body
is stored (snapshot indexed under the SHA-256 of the original bytes, flagged redacted).
"""

from __future__ import annotations

import json
from typing import Any

from ..context import RunContext
from ..envelope import NLOD, OFFICIAL_AUTHORISATIONS, Claim, CompanyResult, Evidence, ModuleOutcome, utc_iso
from .registry import BUDGET_NOTE, decode_text, enhet_page, outcome_state_for_status, redact_birth_dates, span_at

MODULE = "authorisations"
VERSION = "1"
METHOD = f"{MODULE}@{VERSION}"
TIER = "extras"
SOURCE_NAME = "Brønnøysundregistrene, fullmakt API (signatur/prokura)"
KINDS = (("signatur", "signature_rule"), ("prokura", "prokura_rule"))


def fullmakt_url(orgnr: str, kind: str) -> str:
    return f"https://data.brreg.no/fullmakt/enheter/{orgnr}/{kind}"


def _names(rows: Any) -> list[str]:
    out: list[str] = []
    for r in rows or []:
        if isinstance(r, dict) and isinstance(r.get("navn"), str):
            n = " ".join(r["navn"].split())
            if n and n not in out:
                out.append(n)
    return out


def _role_texts(rows: Any) -> list[str]:
    out: list[str] = []
    for r in rows or []:
        rolle = r.get("rolle") if isinstance(r, dict) else None
        t = (rolle or {}).get("tekstforklaring") if isinstance(rolle, dict) else None
        if t and t not in out:
            out.append(t)
    return out


def parse_rule(doc: dict[str, Any]) -> dict[str, Any] | None:
    """{'text', 'holders', 'basis', '_text_ptr', '_holders_ptr'} or None when no rule is published.

    Text priority: filed free text (`signaturProkuraFritekst`) -> role descriptions of named holders
    (prokura: "Prokura hver for seg") -> the service's signing combinations (`signeringsKombinasjon`,
    e.g. "Styret i fellesskap" when no specific rule is registered)."""
    sg = doc.get("signeringsGrunnlag")
    if not isinstance(sg, dict) or sg.get("kode") == "SI":
        return None
    spr = sg.get("signaturProkuraRoller") if isinstance(sg.get("signaturProkuraRoller"), dict) else {}
    mulige = sg.get("muligeSigneringsRoller") if isinstance(sg.get("muligeSigneringsRoller"), dict) else {}
    named = spr.get("personRolleGrunnlag")
    text = spr.get("signaturProkuraFritekst")
    if isinstance(text, str) and text.strip():
        basis, text_ptr = "registered_text", "/signeringsGrunnlag/signaturProkuraRoller/signaturProkuraFritekst"
    elif _role_texts(named):
        basis, text_ptr = "named_holders", "/signeringsGrunnlag/signaturProkuraRoller/personRolleGrunnlag"
        text = "; ".join(_role_texts(named))
    else:
        komb = (doc.get("signeringsKombinasjon") or {}).get("kombinasjon") if isinstance(
            doc.get("signeringsKombinasjon"), dict) else None
        texts: list[str] = []
        for k in komb or []:
            t = k.get("tekstforklaring") if isinstance(k, dict) else None
            if t and t not in texts:
                texts.append(t)
        if not texts:
            return None
        basis, text_ptr, text = "combinations", "/signeringsKombinasjon/kombinasjon", "; ".join(texts)
    if named:
        holders, holders_ptr = _names(named), "/signeringsGrunnlag/signaturProkuraRoller/personRolleGrunnlag"
    else:
        holders, holders_ptr = _names(mulige.get("personRolleGrunnlag")), \
            "/signeringsGrunnlag/muligeSigneringsRoller/personRolleGrunnlag"
    return {"text": " ".join(text.split()), "holders": holders, "basis": basis, "_text_ptr": text_ptr,
            "_holders_ptr": holders_ptr if holders else None}


async def run(ctx: RunContext, company: CompanyResult) -> None:
    try:
        await _run(ctx, company)
    except Exception as exc:
        company.error(MODULE, "failed", f"{type(exc).__name__}: {exc}")
        company.outcome(ModuleOutcome(MODULE, "failed", note=f"module error: {type(exc).__name__}"))


async def _run(ctx: RunContext, company: CompanyResult) -> None:
    orgnr = company.organisation_number
    status = (company.data.get("identity") or {}).get("match_status")
    rec = company.data.get("registry") or {}
    if status != "exact":
        company.outcome(ModuleOutcome(MODULE, "not_applicable", note=f"identity not an active main unit ({status})"))
        return
    if rec.get("registrertIForetaksregisteret") is not True:
        company.outcome(ModuleOutcome(MODULE, "not_applicable",
                                      note="not registered in Foretaksregisteret: no signature/prokura register"))
        return
    states: list[str] = []
    notes: list[str] = []
    data: dict[str, Any] = {}
    first: tuple[str, str, int] | None = None
    n_req = 0
    for kind, field in KINDS:
        url = fullmakt_url(orgnr, kind)
        if not ctx.allow(TIER, 1):
            states.append("failed")
            notes.append(f"{kind}: {BUDGET_NOTE}")
            continue
        try:
            res = await ctx.get(url, tier=TIER, source=MODULE, headers={"Accept": "application/json"})
            n_req += 1
        except Exception as exc:
            states.append("failed")
            notes.append(f"{kind}: {type(exc).__name__}")
            company.error(MODULE, "failed", f"{kind} lookup failed: {type(exc).__name__}: {exc}", url)
            continue
        retrieved_at = utc_iso(res.retrieved_at)
        if first is None:
            first = (url, retrieved_at, res.status)
        if res.status == 404:
            states.append("not_available")
            notes.append(f"{kind}: none registered (HTTP 404)")
            company.add([_absent(field, url, retrieved_at, None, 404, f"no {kind} registered (HTTP 404)", page=enhet_page(orgnr))])
            continue
        if res.status != 200:
            states.append(outcome_state_for_status(res.status))
            notes.append(f"{kind}: HTTP {res.status}")
            continue
        original_sha = res.sha256
        redacted = redact_birth_dates(res.content)
        ctx.snapshots.save(redacted, url=url, retrieved_at=retrieved_at, content_type=res.headers.get("content-type"),
                           sha256=original_sha, redacted=True)
        text = decode_text(redacted)
        try:
            doc = json.loads(text)
        except ValueError:
            states.append("failed")
            notes.append(f"{kind}: unparseable response")
            continue
        echo = str(((doc.get("enhet") or {}) if isinstance(doc, dict) else {}).get("organisasjonsnummer") or "")
        if echo and echo != orgnr:
            states.append("ambiguous")
            notes.append(f"{kind}: payload refers to {echo}; nothing published")
            continue
        rule = parse_rule(doc) if isinstance(doc, dict) else None
        if rule is None:
            sg = doc.get("signeringsGrunnlag") if isinstance(doc, dict) else None
            if isinstance(sg, dict) and sg.get("kode") == "SI":
                why, loc = f"{sg.get('tekstforklaring') or 'no basis registered'} ({sg.get('kode')})", "/signeringsGrunnlag"
                note = f"no {kind} registered: {why}"
            else:
                rs = ((doc.get("status") or {}).get("rutineStatus") or {}) if isinstance(doc, dict) else {}
                why = " ".join(x for x in (rs.get("tekstforklaring"), f"({rs.get('kode')})" if rs.get("kode") else None) if x)
                loc = "/status/rutineStatus"
                note = f"no machine-readable {kind} rule published" + (f": {why}" if why else "")
            span = span_at(text, loc)
            company.add([_absent(field, url, retrieved_at, original_sha, 200, note, span,
                                 loc if span else "/", page=enhet_page(orgnr))])
            states.append("not_available")
            notes.append(f"{kind}: {why or 'no rule'}")
            continue
        evs = [_ev(url, retrieved_at, original_sha, span_at(text, rule["_text_ptr"]), rule["_text_ptr"], enhet_page(orgnr))]
        if rule["_holders_ptr"] and rule["_holders_ptr"] != rule["_text_ptr"]:
            evs.append(_ev(url, retrieved_at, original_sha, span_at(text, rule["_holders_ptr"]), rule["_holders_ptr"],
                           enhet_page(orgnr)))
        value = {"text": rule["text"], "holders": rule["holders"]}
        data[field] = value
        disp = rule["text"] + (f" ({', '.join(rule['holders'])})" if rule["holders"] and field == "prokura_rule" else "")
        cnote = None
        if rule["basis"] == "combinations":
            cnote = ("no specific rule registered; text is the signing combination(s) returned by the fullmakt "
                     "service; holders are the persons whose roles allow signing")
        elif rule["basis"] == "registered_text" and field == "signature_rule":
            cnote = "holders are the persons whose current roles can sign under this rule"
        company.add([Claim(field=field, value=value, evidence=evs, display=disp, method=METHOD, note=cnote)])
        states.append("available")
        notes.append(f"{kind}: published")
    company.data["authorisations"] = data
    if "available" in states:
        final = "available"
    elif states and all(s == "not_available" for s in states):
        final = "not_available"
    else:
        final = next((s for s in ("blocked", "ambiguous", "failed", "not_available") if s in states), "failed")
    url, retrieved_at, http_status = first or (fullmakt_url(orgnr, "signatur"), None, None)
    company.outcome(ModuleOutcome(MODULE, final, note="; ".join(notes) or None, source_url=url,
                                  retrieved_at=retrieved_at, http_status=http_status, requests=n_req))


def _ev(url: str, retrieved_at: str, sha: str | None, span: str | None, locator: str, page: str | None) -> Evidence:
    return Evidence(source_url=url, source_class=OFFICIAL_AUTHORISATIONS, source_name=SOURCE_NAME,
                    retrieved_at=retrieved_at, content_sha256=sha, claim_span=span, locator=locator,
                    extraction_method=METHOD, page_url=page, http_status=200, licence=NLOD)


def _absent(field: str, url: str, retrieved_at: str, sha: str | None, status: int, note: str,
            span: str | None = None, locator: str | None = None, page: str | None = None) -> Claim:
    ev = Evidence(source_url=url, source_class=OFFICIAL_AUTHORISATIONS, source_name=SOURCE_NAME,
                  retrieved_at=retrieved_at, content_sha256=sha, claim_span=span, locator=locator,
                  extraction_method=METHOD, page_url=page, http_status=status, licence=NLOD)
    return Claim(field=field, value=None, availability="not_available", evidence=[ev], note=note, method=METHOD)

