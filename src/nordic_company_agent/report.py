"""Machine-readable run report and envelope contract validation."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from .envelope import AVAILABILITY

FINANCIAL_FIELDS = {
    "revenue", "sales_revenue", "operating_expenses", "payroll_expenses", "operating_profit", "net_financial_items",
    "financial_income", "financial_expenses", "profit_before_tax", "tax_expense", "net_profit",
    "total_comprehensive_income", "total_assets", "fixed_assets", "current_assets", "cash_and_bank", "inventories",
    "receivables", "goodwill", "equity", "paid_in_equity", "retained_earnings", "total_liabilities",
    "current_liabilities", "long_term_liabilities",
}


def validate_envelopes(envelopes: list[dict[str, Any]], expected: list[str]) -> dict[str, Any]:
    orgs = [e.get("organisation_number") for e in envelopes]
    problems: list[str] = []
    claims_without_evidence = 0
    evidence_incomplete = 0
    zero_for_missing = 0
    financial_without_period = 0
    dangling = 0
    for env in envelopes:
        ev = {e["id"]: e for e in env.get("evidence", [])}
        for c in env.get("claims", []):
            if c.get("availability") not in AVAILABILITY:
                problems.append(f"{env['organisation_number']}: bad availability {c.get('availability')}")
            if c.get("availability") == "available":
                if not c.get("evidence_ids"):
                    claims_without_evidence += 1
                for eid in c.get("evidence_ids", []):
                    e = ev.get(eid)
                    if e is None:
                        dangling += 1
                    elif not (e.get("source_url") and e.get("retrieved_at")):
                        evidence_incomplete += 1
                if c["field"] in FINANCIAL_FIELDS and not (c.get("period") or {}).get("end"):
                    financial_without_period += 1
            elif c.get("value") not in (None, [], {}):
                zero_for_missing += 1
    checks = {
        "exact_expected_count": len(envelopes) == len(expected),
        "same_order_as_input": orgs == expected,
        "unique_organisation_numbers": len(orgs) == len(set(orgs)),
        "all_terminal_status": all((e.get("run") or {}).get("terminal_status") in ("completed", "failed") for e in envelopes),
        "every_available_claim_has_evidence": claims_without_evidence == 0,
        "no_dangling_evidence_ids": dangling == 0,
        "evidence_has_source_and_time": evidence_incomplete == 0,
        "missing_values_never_encoded": zero_for_missing == 0,
        "financial_claims_have_period": financial_without_period == 0,
        "no_invalid_availability": not problems,
    }
    return {"passed": all(checks.values()), "checks": checks,
            "counts": {"claims_without_evidence": claims_without_evidence, "dangling_evidence_ids": dangling,
                       "evidence_incomplete": evidence_incomplete, "non_null_missing_values": zero_for_missing,
                       "financial_without_period": financial_without_period},
            "problems": problems[:50]}


def _pct(xs: list[int], q: float) -> int | None:
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]


def build_report(out, expected: list[str], validation: dict[str, Any], config: Any,
                 invalid: list[dict], duplicates: list[str]) -> dict[str, Any]:
    envs = out.envelopes
    ctx = out.ctx
    match = Counter(e.get("identity", {}).get("match_status") for e in envs)
    status = Counter(e.get("run", {}).get("terminal_status") for e in envs)
    mod_states: dict[str, Counter] = defaultdict(Counter)
    field_avail: dict[str, Counter] = defaultdict(Counter)
    fam_companies: dict[str, int] = Counter()
    fam_claims: dict[str, int] = Counter()
    web_levels = Counter()
    changes = Counter()
    for e in envs:
        for m, st in (e.get("modules") or {}).items():
            mod_states[m][st.get("availability")] += 1
        fams = set()
        for c in e.get("claims", []):
            field_avail[c["field"]][c["availability"]] += 1
            if c["availability"] == "available":
                fams.add(c["family"])
                fam_claims[c["family"]] += 1
            if c["field"] == "website_verification" and c["availability"] == "available":
                web_levels[(c.get("value") or {}).get("level")] += 1
        for f in fams:
            fam_companies[f] += 1
        for ch in e.get("changes", []):
            changes[ch.get("change_type")] += 1
    per_company_req = [e.get("operations", {}).get("requests", 0) for e in envs]
    per_company_ms = [e.get("operations", {}).get("runtime_ms", 0) for e in envs]
    stats = ctx.client.stats()
    started, completed = out.started_at, out.completed_at
    return {
        "run_id": out.run_id,
        "started_at": started,
        "completed_at": completed,
        "agent": envs[0]["run"]["agent"] if envs else {},
        "expected_count": len(expected),
        "emitted_envelopes": len(envs),
        "invalid_inputs": invalid,
        "duplicate_inputs_dropped": duplicates,
        "validation": validation,
        "identity": dict(match),
        "terminal_status": dict(status),
        "operations": {
            "requests": stats["requests_used"],
            "request_limit": stats["requests_limit"],
            "requests_by_source": stats["by_source"],
            "requests_by_host_top25": stats["by_host"],
            "batch_module_requests": dict(ctx.batch_requests),
            "per_company_requests_p50": _pct(per_company_req, 0.5),
            "per_company_requests_p95": _pct(per_company_req, 0.95),
            "p50_ms": _pct(per_company_ms, 0.5),
            "p95_ms": _pct(per_company_ms, 0.95),
            "wall_clock_s": round(ctx.config.time_budget_s - ctx.time_left(), 1),
            "time_budget_s": ctx.config.time_budget_s,
            "module_runtime_ms": out.module_runtime_ms,
            "third_party_cost_usd": 0.0,
            "llm": None,
            "http_errors": stats["errors"],
        },
        "planner": ctx.planner.report(),
        "module_states": {m: dict(v) for m, v in sorted(mod_states.items())},
        "field_availability": {f: dict(v) for f, v in sorted(field_avail.items())},
        "family_company_coverage": {f: round(n / max(1, len(envs)), 4) for f, n in sorted(fam_companies.items())},
        "family_claim_counts": dict(sorted(fam_claims.items())),
        "website_verification_levels": dict(web_levels),
        "change_types": dict(changes),
        "caches": {name: ctx.caches.meta(name) for name in ("site_discovery", "nav_jobs", "wikidata_p2333")},
        "config": {"max_requests": config.max_requests, "time_budget_s": config.time_budget_s,
                   "modules": sorted(config.modules), "registry_snapshot": str(config.registry_snapshot or ""),
                   "previous_envelopes": str(config.previous_envelopes or ""), "concurrency": config.concurrency,
                   "live_discovery": config.live_discovery},
    }
