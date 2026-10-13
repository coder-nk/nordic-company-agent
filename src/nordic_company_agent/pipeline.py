"""Batch orchestration: plan the request budget, run source modules in priority phases,
assemble one terminal envelope per input organisation number."""

from __future__ import annotations

import asyncio
import importlib
import json
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from . import __version__
from .context import CURRENT_COMPANY, CURRENT_MODULE, RunConfig, RunContext
from .envelope import CompanyResult, ModuleOutcome, utc_iso

# (module name, python module, kind, tier, phase). Phases run in order; modules within a phase run
# concurrently per company. Batch modules receive all companies at once.
MODULES: list[tuple[str, str, str, str, int]] = [
    ("registry", "registry", "batch", "registry", 0),
    ("registry_feed", "registry_feed", "batch", "batch_external", 1),
    ("gleif", "gleif", "batch", "batch_external", 1),
    ("wikidata", "wikidata", "batch", "batch_external", 1),
    ("accounts", "accounts", "company", "accounts", 2),
    ("subunits", "subunits", "company", "subunits", 3),
    ("roles", "roles", "company", "roles", 3),
    ("website", "website", "company", "website_primary", 4),
    ("group", "group", "company", "group", 5),
    ("jobs", "jobs", "batch", "batch_external", 6),
    ("authorisations", "authorisations", "company", "extras", 7),
    ("state_aid", "state_aid", "company", "extras", 7),
    ("account_copies", "accounts:run_copies", "company", "extras", 7),
]

# Modules whose absence of a positive result must still yield explicit claims (missing-data labels).
PRIMARY_FIELD = {
    "accounts": "revenue",
    "roles": "ceo",
    "subunits": "registered_workplace",
    "website": "official_website",
    "jobs": "job_posting",
    "gleif": "lei",
    "wikidata": "wikidata_item",
    "group": "parent_company",
    "state_aid": "state_aid_grant",
    "authorisations": "signature_rule",
}


def plan_reservations(ctx: RunContext, companies: list[CompanyResult], bulk: set[str] | None = None) -> dict[str, int]:
    """Split the request budget across tiers in priority order (needs estimated from the batch)."""
    n = len(companies)
    exact = [c for c in companies if c.data.get("identity", {}).get("match_status") == "exact"]
    ne = len(exact)
    filers = sum(1 for c in exact if (c.data.get("registry") or {}).get("sisteInnsendteAarsregnskap")
                 or (c.data.get("registry") or {}).get("organisasjonsform", {}).get("kode") in ("AS", "ASA", "SA", "NUF"))
    in_group = sum(1 for c in exact if (c.data.get("registry") or {}).get("erIKonsern"))
    leads = 0
    disc = ctx.caches.jsonl_index("site_discovery")
    for c in exact:
        rec = c.data.get("registry") or {}
        rows = disc.get(c.organisation_number) or []
        if rec.get("hjemmeside") or any(r.get("leads") for r in rows):
            leads += 1
    needs = {
        "registry": 2 + max(1, n // 900) + (n - ne) * 2 + len(bulk or ()),
        "batch_external": 6 + 3 * (n // 100 + 1) + 8,
        "accounts": filers,
        "website_primary": 3 * leads,
        "roles": 0 if "roles" in (bulk or ()) else ne,
        "subunits": 0 if "subunits" in (bulk or ()) else ne,
        "group": in_group,
        "website_secondary": 2 * leads,
        "discovery": 3 * max(0, ne - leads),
        "extras": 2 * ne,
    }
    remaining = ctx.client.max_requests
    reservations: dict[str, int] = {}
    for tier in ("registry", "batch_external", "accounts", "website_primary", "roles", "subunits", "group",
                 "website_secondary", "discovery", "extras"):
        give = min(needs.get(tier, 0), max(0, remaining))
        reservations[tier] = give
        remaining -= give
    return reservations


@dataclass
class BatchOutput:
    run_id: str
    started_at: str
    completed_at: str
    companies: list[CompanyResult]
    envelopes: list[dict[str, Any]]
    ctx: RunContext
    invalid: list[dict[str, Any]] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    module_runtime_ms: dict[str, int] = field(default_factory=dict)


def _load_module(pymod: str):
    try:
        return importlib.import_module(f".sources.{pymod}", __package__)
    except ImportError as exc:  # pragma: no cover - only when a module file is missing
        return exc


async def _run_company_module(ctx: RunContext, name: str, fn: Callable[..., Awaitable[None]],
                              company: CompanyResult, sem: asyncio.Semaphore) -> None:
    async with sem:
        token_c = CURRENT_COMPANY.set(company)
        token_m = CURRENT_MODULE.set(name)
        t0 = time.monotonic()
        n_before = len(company.claims)
        try:
            if ctx.time_left() < 15:
                company.outcome(ModuleOutcome(name, "failed", note="not checked: run time budget exhausted"))
                return
            await fn(ctx, company)
            _tag(company, n_before, name)
        except Exception as exc:  # modules should not raise; this is the last safety net
            company.error(name, "failed", f"{type(exc).__name__}: {exc}")
            company.outcome(ModuleOutcome(name, "failed", note=f"module error: {type(exc).__name__}"))
            company.data.setdefault("_tracebacks", []).append(traceback.format_exc(limit=4))
        finally:
            company.data.setdefault("_module_ms", {})[name] = int((time.monotonic() - t0) * 1000)
            CURRENT_MODULE.reset(token_m)
            CURRENT_COMPANY.reset(token_c)
        if name not in company.modules:
            company.outcome(ModuleOutcome(name, "failed", note="module produced no terminal state"))


async def run_modules(ctx: RunContext, companies: list[CompanyResult]) -> dict[str, int]:
    timings: dict[str, int] = {}
    enabled = [m for m in MODULES if ctx.module_enabled(m[0])]
    phases = sorted({m[4] for m in enabled})
    sem = asyncio.Semaphore(ctx.config.concurrency)
    for ph in phases:
        if ph >= 3 and ctx.shared.get("_bulk_task") is not None:
            await ctx.shared.pop("_bulk_task")
        tasks = []
        names = []
        for name, pymod, kind, tier, phase in enabled:
            if phase != ph:
                continue
            pyname, _, fname = pymod.partition(":")
            mod = _load_module(pyname)
            targets = companies if name == "registry" else [
                c for c in companies if c.data.get("identity", {}).get("match_status") == "exact"]
            if isinstance(mod, Exception):
                for c in targets:
                    c.outcome(ModuleOutcome(name, "failed", note=f"module unavailable: {mod}"))
                continue
            names.append(name)
            if kind == "batch":
                fn = getattr(mod, fname or "run_batch")
                tasks.append(_run_batch_module(ctx, name, fn, targets))
            else:
                fn = getattr(mod, fname or "run")
                tasks.append(asyncio.gather(*(_run_company_module(ctx, name, fn, c, sem) for c in targets)))
        t0 = time.monotonic()
        await asyncio.gather(*tasks)
        for name in names:
            timings[name] = timings.get(name, 0) + int((time.monotonic() - t0) * 1000)
        if ph == 0:
            # Identity is known now: size the per-tier budget for this batch, and decide whether the
            # nightly bulk files are cheaper than per-company roles/sub-unit calls.
            res = plan_reservations(ctx, companies)
            kinds = decide_bulk(ctx, companies, res)
            if kinds:
                res = plan_reservations(ctx, companies, kinds)
                ctx.shared["bulk_kinds"] = sorted(kinds)
            ctx.planner.set_reservations(res)
            if kinds:
                from .sources import bulk_registry
                ctx.shared["_bulk_task"] = asyncio.create_task(bulk_registry.prepare(ctx, companies, kinds))
        for tier, last in _tier_last_phase(enabled).items():
            if last == ph:
                ctx.planner.done(tier)
    return timings


def decide_bulk(ctx: RunContext, companies: list[CompanyResult], res: dict[str, int]) -> set[str]:
    mode = getattr(ctx.config, "bulk_mode", "auto")
    if mode == "never":
        return set()
    exact = sum(1 for c in companies if c.data.get("identity", {}).get("match_status") == "exact")
    if mode == "always":
        return {k for k in ("roles", "subunits") if ctx.module_enabled(k)} if exact else set()
    kinds = set()
    if exact >= 50 and ctx.time_left() > 25 * 60:
        for k in ("roles", "subunits"):
            if ctx.module_enabled(k) and res.get(k, 0) < exact:
                kinds.add(k)
    return kinds


# Extra tiers a module may spend from besides its main tier.
SECONDARY_TIERS = {"website": ("website_secondary", "discovery"), "accounts": ("extras",)}


def _tier_last_phase(enabled) -> dict[str, int]:
    last: dict[str, int] = {}
    for name, _py, _kind, tier, phase in enabled:
        for t in (tier,) + SECONDARY_TIERS.get(name, ()):
            last[t] = max(last.get(t, -1), phase)
    return last


from .refresh import FIELD_MODULE  # noqa: E402


def _tag(company: CompanyResult, start: int, module: str) -> None:
    for c in company.claims[start:]:
        if not getattr(c, "module", None):
            c.module = FIELD_MODULE.get(c.field, module)  # type: ignore[attr-defined]


async def _run_batch_module(ctx: RunContext, name: str, fn, companies: list[CompanyResult]) -> None:
    token_m = CURRENT_MODULE.set(name)
    before = {id(c): len(c.claims) for c in companies}
    try:
        if not companies:
            return
        if ctx.time_left() < 20:
            for c in companies:
                c.outcome(ModuleOutcome(name, "failed", note="not checked: run time budget exhausted"))
            return
        await fn(ctx, companies)
        for c in companies:
            _tag(c, before[id(c)], name)
    except Exception as exc:
        for c in companies:
            if name not in c.modules:
                c.error(name, "failed", f"{type(exc).__name__}: {exc}")
                c.outcome(ModuleOutcome(name, "failed", note=f"module error: {type(exc).__name__}"))
    finally:
        CURRENT_MODULE.reset(token_m)
        for c in companies:
            if name not in c.modules:
                c.outcome(ModuleOutcome(name, "failed", note="module produced no terminal state"))


async def run_batch(orgnrs: list[str], config: RunConfig, invalid: list[dict[str, Any]] | None = None,
                    duplicates: list[str] | None = None,
                    previous: dict[str, dict[str, Any]] | None = None) -> BatchOutput:
    from .assemble import assemble_envelope  # local import keeps module import cheap

    started = utc_iso()
    ctx = RunContext(config)
    companies = [CompanyResult(o) for o in orgnrs]
    previous = previous or {}
    if previous:
        times = [p.get("run", {}).get("completed_at") for p in previous.values() if p.get("run")]
        if times:
            ctx.shared["previous_checked_at"] = min(t for t in times if t)
    try:
        timings = await run_modules(ctx, companies)
    finally:
        await ctx.client.aclose()
    completed = utc_iso()
    envelopes = []
    for c in companies:
        try:
            env = assemble_envelope(ctx, c, run_id=config.run_id, started_at=started, completed_at=completed,
                                    previous=previous.get(c.organisation_number))
        except Exception as exc:  # never drop a company
            env = failed_envelope(c.organisation_number, config.run_id, started, completed,
                                  f"envelope assembly failed: {type(exc).__name__}: {exc}")
        envelopes.append(env)
    return BatchOutput(config.run_id, started, completed, companies, envelopes, ctx,
                       invalid=invalid or [], duplicates=duplicates or [], module_runtime_ms=timings)


def failed_envelope(orgnr: str, run_id: str, started: str, completed: str, message: str,
                    match_status: str = "error") -> dict[str, Any]:
    from .envelope import AGENT_NAME, ENVELOPE_SCHEMA
    return {
        "schema_version": ENVELOPE_SCHEMA,
        "organisation_number": orgnr,
        "run": {"run_id": run_id, "started_at": started, "completed_at": completed,
                "terminal_status": "failed", "agent": {"name": AGENT_NAME, "version": __version__}},
        "identity": {"match_status": match_status, "confidence": 0.0, "method": "none", "checks": []},
        "summary": {"text": message, "sentences": [], "unknowns": []},
        "sections": {}, "modules": {}, "claims": [], "evidence": [], "source_snapshots": [], "changes": [],
        "refresh": {}, "errors": [{"module": "agent", "state": "failed", "message": message}],
        "operations": {"requests": 0, "runtime_ms": 0, "third_party_cost_usd": 0.0},
        "licences": [],
    }


def read_previous(path: Path | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not path or not Path(path).exists():
        return out
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                env = json.loads(line)
                out[str(env.get("organisation_number"))] = env
    return out
