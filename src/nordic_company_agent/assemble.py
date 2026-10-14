"""Turn a CompanyResult into one terminal Signalpost envelope."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from . import __version__
from .context import RunContext
from .envelope import (AGENT_NAME, CC0_GLEIF, CC0_WIKIDATA, COMPANY_SITE, ENVELOPE_SCHEMA, NAV_TERMS, NLOD,
                       SECTIONS, Claim, CompanyResult, Evidence, dedupe_claims, field_family, short_hash)
from .orgnr import formatted
from .refresh import carry_first_seen, diff
from .synthesis import build_summary

PRIMARY_FIELD = {
    "accounts": "revenue", "roles": "ceo", "subunits": "registered_workplace", "website": "official_website",
    "jobs": "job_posting", "gleif": "lei", "wikidata": "wikidata_item", "group": "parent_company",
    "state_aid": "state_aid_grant", "authorisations": "signature_rule", "registry_feed": "registry_event",
}
# Fields that should always be labelled explicitly when the module looked and found nothing.
KEY_ROLE_FIELDS = ("ceo", "chair")
NO_CEO_FORMS = {"ENK", "ESEK", "SAM", "ANS", "DA", "KS", "BRL", "FLI", "STI"}


def _source_evidence(c: CompanyResult, module: str, ctx: RunContext) -> list[Evidence]:
    """Evidence for an explicit 'not available' label: the response that showed absence."""
    out = c.modules.get(module)
    if not out or not out.source_url:
        reg = c.data.get("registry_evidence")
        return [reg] if isinstance(reg, Evidence) else []
    snap = None
    for d, e in ctx.snapshots.index.items():
        if e.get("url") == out.source_url:
            snap = d
            break
    from .envelope import utc_iso
    return [Evidence(source_url=out.source_url, source_class="module_check", source_name=module,
                     retrieved_at=out.retrieved_at or utc_iso(), content_sha256=snap, http_status=out.http_status,
                     extraction_method=f"{module}@check", claim_span=None, locator=None)]


# Optional extras that the planner may skip by design on large batches. A planned budget skip is not a
# failure of the source, so it is reported in the run report (not as a per-company 'failed' state).
OPTIONAL_MODULES = {"account_copies", "authorisations"}


def planned_skip(module: str, out) -> bool:
    note = str(out.note or "") if out is not None else ""
    return (module in OPTIONAL_MODULES and out is not None and out.availability == "failed"
            and ("not checked: request budget" in note or note == "module produced no terminal state"))


def _missing_labels(c: CompanyResult, ctx: RunContext) -> list[Claim]:
    claims: list[Claim] = []
    have = {cl.field for cl in c.claims}
    for module, field in PRIMARY_FIELD.items():
        out = c.modules.get(module)
        if out is None or out.availability == "available" or field in have or planned_skip(module, out):
            continue
        cl = Claim(field=field, value=None, availability=out.availability, confidence=1.0,
                   note=out.note or f"{module}: {out.availability}", method=f"{module}@outcome",
                   evidence=_source_evidence(c, module, ctx))
        cl.module = module  # type: ignore[attr-defined]
        claims.append(cl)
    roles = c.modules.get("roles")
    form = ((c.data.get("registry") or {}).get("organisasjonsform") or {}).get("kode")
    if roles and roles.availability == "available":
        for f in KEY_ROLE_FIELDS:
            if f not in have:
                state = "not_applicable" if (form in NO_CEO_FORMS and f == "ceo") else "not_available"
                cl = Claim(field=f, value=None, availability=state, confidence=1.0, method="roles@absent",
                           note=f"no current '{f}' role registered in Enhetsregisteret",
                           evidence=_source_evidence(c, "roles", ctx))
                cl.module = "roles"  # type: ignore[attr-defined]
                claims.append(cl)
    return claims


def _sort_key(cl: Claim):
    order = {f: i for i, f in enumerate(_FIELD_ORDER)}
    return (order.get(cl.field, 999), cl.relation, cl.qualifier or "", str(cl.period or ""))


_FIELD_ORDER = [f for fam in ("legal_identity", "corporate_structure", "annual_accounts", "leadership", "workplaces",
                              "website", "social_profiles", "hiring", "public_activity")
                for f in __import__("nordic_company_agent.envelope", fromlist=["FAMILIES"]).FAMILIES[fam]]


def _merge_socials(claims: list[Claim]) -> list[Claim]:
    """One social_profile claim per canonical profile URL, whichever source found it (website link,
    registry hjemmeside, Wikidata). Keeps the highest-confidence claim and the union of evidence."""
    from .web.verify import canonical_social
    out: list[Claim] = []
    by: dict[str, Claim] = {}
    for cl in claims:
        if cl.field != "social_profile" or cl.availability != "available" or not isinstance(cl.value, dict):
            out.append(cl)
            continue
        plat = cl.value.get("platform") or ""
        can = canonical_social(plat, cl.value.get("url") or "")
        url = can[0] if can else (cl.value.get("url") or "")
        url = url.replace("https://twitter.com/", "https://x.com/")
        key = f"{plat}|{url.lower().rstrip('/')}"
        cl.value = dict(cl.value, url=url)
        cl.qualifier = key
        if key in by:
            keep = by[key]
            if cl.confidence > keep.confidence:
                cl.evidence = cl.evidence + [e for e in keep.evidence if e.id not in {x.id for x in cl.evidence}]
                out[out.index(keep)] = cl
                by[key] = cl
            else:
                keep.evidence.extend(e for e in cl.evidence if e.id not in {x.id for x in keep.evidence})
            continue
        by[key] = cl
        out.append(cl)
    return out


def assemble_envelope(ctx: RunContext, c: CompanyResult, *, run_id: str, started_at: str, completed_at: str,
                      previous: dict[str, Any] | None) -> dict[str, Any]:
    identity = dict(c.data.get("identity") or {"match_status": "error", "confidence": 0.0, "method": "none", "checks": []})
    identity["organisation_number_formatted"] = formatted(c.organisation_number)
    claims = dedupe_claims(_merge_socials(c.claims + _missing_labels(c, ctx)))
    claims.sort(key=_sort_key)

    evidence: dict[str, dict[str, Any]] = {}
    claim_dicts: list[dict[str, Any]] = []
    for cl in claims:
        ev_ids = []
        for e in cl.evidence:
            d = e.to_dict()
            evidence.setdefault(d["id"], d)
            ev_ids.append(d["id"])
        cd = cl.to_dict(ev_ids)
        mod = getattr(cl, "module", None)
        if mod:
            cd["module"] = mod
        claim_dicts.append(cd)

    modules = {}
    for name, out in sorted(c.modules.items()):
        if planned_skip(name, out):
            sk = ctx.shared.setdefault("planned_skips", {})
            sk[name] = sk.get(name, 0) + 1
            continue
        modules[name] = out.to_dict()
    reg_changed = (c.data.get("registry_feed") or {}).get("last_change_at")
    changes, carried = diff(previous, claim_dicts, modules, run_id=run_id, detected_at=completed_at,
                            registry_changed_at=reg_changed)
    if carried:
        claim_dicts.extend(carried)
        for cd in carried:
            for eid in cd.get("evidence_ids", []):
                pe = next((e for e in (previous or {}).get("evidence", []) if e.get("id") == eid), None)
                if pe:
                    evidence.setdefault(eid, pe)
    # Keep the previous side of every change verifiable.
    if changes and previous:
        prev_ev = {e["id"]: e for e in previous.get("evidence", [])}
        for ch in changes:
            for eid in ch.get("old_evidence_ids") or []:
                if eid in prev_ev:
                    evidence.setdefault(eid, prev_ev[eid])
    carry_first_seen(previous, claim_dicts, completed_at)

    # Snapshots referenced by evidence
    snaps = []
    seen = set()
    for e in evidence.values():
        d = e.get("content_sha256")
        if d and d not in seen:
            seen.add(d)
            ent = ctx.snapshots.entry(d)
            if ent:
                snaps.append(ent)
            elif previous:
                pe = next((s for s in previous.get("source_snapshots", []) if s.get("sha256") == d), None)
                if pe:
                    snaps.append(pe)

    sections = {}
    for sec, fams in SECTIONS.items():
        ids = [cd["id"] for cd in claim_dicts if cd["family"] in fams]
        avail = [cd for cd in claim_dicts if cd["family"] in fams and cd["availability"] == "available"]
        states = sorted({cd["availability"] for cd in claim_dicts if cd["family"] in fams})
        sections[sec] = {"availability": "available" if avail else (states[0] if states else "not_available"),
                         "claim_count": len(avail), "claim_ids": ids}
    sections["claim_level_evidence"] = {"availability": "available" if evidence else "not_available",
                                        "evidence_count": len(evidence)}
    sections["refresh_and_changes"] = {"availability": "available", "change_count": len(changes),
                                       "has_previous": bool(previous)}

    summary = build_summary(claim_dicts, modules, changes, identity)
    now = datetime.now(timezone.utc)
    has_jobs = any(cd["field"] == "job_posting" and cd["availability"] == "available" for cd in claim_dicts)
    refresh = {
        "checked_at": completed_at,
        "previous_run_id": (previous or {}).get("run", {}).get("run_id"),
        "previous_checked_at": (previous or {}).get("run", {}).get("completed_at"),
        "registry_last_changed_at": reg_changed,
        "change_count": len(changes),
        "material_change_count": sum(1 for ch in changes if ch.get("materiality") == "material"),
        "stale_claim_count": len(carried),
        "next_refresh_due": (now + timedelta(days=1 if has_jobs else 7)).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "policy": "registry & roles: daily via Enhetsregisteret update feeds; accounts: when "
                  "sisteInnsendteAarsregnskap changes; website: weekly; jobs: every run",
        "content_hash": short_hash([(cd["key"], cd.get("value"), cd["availability"]) for cd in claim_dicts
                                    if not cd.get("stale")], n=16),
    }
    by_mod = c.data.get("_requests_by_module", {})
    n_batch = max(1, len(ctx.shared.get("_batch_size_hint", [])) or 1)
    from .envelope import LICENCE_TEXTS
    lic_ids = sorted({e.get("licence") for e in evidence.values() if e.get("licence")})
    licences = {i: LICENCE_TEXTS.get(i, i) for i in lic_ids}
    status = "completed" if identity.get("match_status") in ("exact", "subunit", "deleted", "not_found", "invalid") else "failed"
    env = {
        "schema_version": ENVELOPE_SCHEMA,
        "organisation_number": c.organisation_number,
        "run": {"run_id": run_id, "started_at": started_at, "completed_at": completed_at, "terminal_status": status,
                "agent": {"name": AGENT_NAME, "version": __version__, "llm": None, "paid_apis": []}},
        "identity": identity,
        "legal_name": next((cd["value"] for cd in claim_dicts if cd["field"] == "legal_name"
                            and cd["availability"] == "available"), None),
        "summary": summary,
        "sections": sections,
        "modules": modules,
        "claims": claim_dicts,
        "evidence": sorted(evidence.values(), key=lambda e: e["id"]),
        "source_snapshots": sorted(snaps, key=lambda s: s["sha256"]),
        "changes": changes,
        "refresh": refresh,
        "errors": c.errors,
        "operations": {"requests": c.requests, "requests_by_module": by_mod,
                       "runtime_ms": sum((c.data.get("_module_ms") or {}).values()),
                       "third_party_cost_usd": 0.0},
        "licences": licences or {"NLOD-2.0": NLOD},
    }
    _ = (CC0_GLEIF, CC0_WIKIDATA, COMPANY_SITE, NAV_TERMS, n_batch, field_family)
    return env
