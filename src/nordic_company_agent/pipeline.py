"""Batch orchestration: plan the request budget, run source modules in priority phases,
assemble one terminal envelope per input organisation number."""

from __future__ import annotations

import asyncio
import importlib
import gzip
import json
import signal
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from . import __version__
from .context import CURRENT_COMPANY, CURRENT_MODULE, TIERS, RunConfig, RunContext
from .envelope import CompanyResult, ModuleOutcome, utc_iso
from .web.candidates import candidates as web_candidates

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
    ("public_records", "public_records", "batch", "batch_external", 6),
    ("authorisations", "authorisations", "company", "extras", 7),
    ("state_aid", "state_aid", "company", "extras", 7),
    ("account_copies", "accounts:run_copies", "company", "extras", 7),
]

# Per-company module wall-clock limits (seconds); a stuck host can never stall a whole phase.
MODULE_TIMEOUT_S = {"website": 150.0, "account_copies": 100.0, "accounts": 60.0, "roles": 60.0, "subunits": 60.0,
                    "group": 60.0, "authorisations": 60.0, "state_aid": 90.0}

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
    live = 0                      # companies without a lead whose live candidate domains were not crawled offline
    disc = ctx.caches.jsonl_index("site_discovery")
    for c in exact:
        rec = c.data.get("registry") or {}
        rows = disc.get(c.organisation_number) or []
        if rec.get("hjemmeside") or any(r.get("leads") for r in rows):
            leads += 1
        elif getattr(ctx.config, "live_discovery", False):
            checked = {k for r in rows for k in (r.get("checked") or [])}
            if any(k not in checked and k != "registry_website" for k, _d in web_candidates(rec)):
                live += 1
    needs = {
        "registry": len(bulk or ()),
        "batch_external": 3 + 2 * (n // 100 + 1) + max(10, ne // 8) + 25,
        "accounts": filers,
        "website_primary": 3 * leads,
        "roles": 0 if "roles" in (bulk or ()) else ne,
        "subunits": 0 if "subunits" in (bulk or ()) else ne,
        "group": in_group,
        "website_secondary": 2 * leads,
        "discovery": 3 * live,
        "extras": 2 * ne,
    }
    remaining = ctx.client.remaining
    reservations: dict[str, int] = {}
    # website tiers come before roles / subunits: those two have a complete nightly bulk fallback, a website
    # that is never checked has none
    for tier in TIERS:
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
            limit = min(MODULE_TIMEOUT_S.get(name, 90.0), max(1.0, ctx.time_left() - 15))
            try:
                await asyncio.wait_for(fn(ctx, company), timeout=limit)
            except (TimeoutError, asyncio.TimeoutError):
                if name not in company.modules:
                    company.outcome(ModuleOutcome(name, "failed", note=f"not checked: module timed out after {limit:.0f}s"))
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
        _progress(ctx, company, name)


def _progress(ctx: RunContext, company: CompanyResult, name: str) -> None:
    """Optional live-progress callback (the UI's research view): (orgnr, module, availability, note)."""
    cb = ctx.shared.get("progress")
    if cb is None:
        return
    out = company.modules.get(name)
    try:
        cb(company.organisation_number, name, getattr(out, "availability", None), getattr(out, "note", None))
    except Exception:
        pass


async def run_modules(ctx: RunContext, companies: list[CompanyResult]) -> dict[str, int]:
    timings: dict[str, int] = {}
    enabled = [m for m in MODULES if ctx.module_enabled(m[0])]
    phases = sorted({m[4] for m in enabled})
    sem = asyncio.Semaphore(ctx.config.concurrency)
    for ph in phases:
        if ph >= 3 and ctx.shared.get("_bulk_task") is not None:
            task = ctx.shared.pop("_bulk_task")
            cap = max(0.0, min(15 * 60, ctx.time_left() - 18 * 60))
            done, _ = await asyncio.wait({task}, timeout=cap)
            if not done:
                task.cancel()  # roles/sub-units fall back to per-company calls within the budget
                ctx.shared.setdefault("bulk_report", {})["timeout"] = f"bulk download not finished within {cap:.0f}s"
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
            try:
                from .contact_sharing import label_shared
                label_shared(ctx, companies)
            except Exception as exc:  # labelling is best-effort; never break the run
                ctx.shared["contact_sharing_error"] = f"{type(exc).__name__}: {exc}"
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
from .contact_sharing import label_by_website  # noqa: E402


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
            _progress(ctx, c, name)
        for c in companies:
            if name not in c.modules:
                c.outcome(ModuleOutcome(name, "failed", note="module produced no terminal state"))


async def run_batch(orgnrs: list[str], config: RunConfig, invalid: list[dict[str, Any]] | None = None,
                    duplicates: list[str] | None = None,
                    previous: dict[str, dict[str, Any]] | None = None,
                    progress: Callable[..., None] | None = None) -> BatchOutput:
    from .assemble import assemble_envelope  # local import keeps module import cheap

    started = utc_iso()
    ctx = RunContext(config)
    if progress is not None:
        ctx.shared["progress"] = progress
    companies = [CompanyResult(o) for o in orgnrs]
    previous = previous or {}
    times = [t for p in previous.values() if isinstance(p.get("run"), dict) and (t := p["run"].get("completed_at"))]
    if times:
        ctx.shared["previous_checked_at"] = min(times)
    # Hard wall-clock guard: whatever a module or host does, stop gathering with enough time left to
    # assemble and write one envelope per company. SIGTERM/SIGINT also only stop the research.
    reserve_s = min(180.0, 30.0 + 0.08 * len(companies))
    timings: dict[str, int] = {}
    loop = asyncio.get_running_loop()
    work = asyncio.ensure_future(run_modules(ctx, companies))
    installed = []
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, work.cancel)
            installed.append(sig)
        except (NotImplementedError, RuntimeError, ValueError):
            pass
    try:
        timings = await asyncio.wait_for(work, timeout=max(20.0, ctx.time_left() - reserve_s))
    except (TimeoutError, asyncio.TimeoutError):
        ctx.shared["timed_out"] = True
    except asyncio.CancelledError:
        cur = asyncio.current_task()
        if cur is not None and cur.cancelling():
            raise
        ctx.shared["interrupted"] = True
    except Exception as exc:  # never lose the batch to one bug
        ctx.shared["run_error"] = f"{type(exc).__name__}: {exc}"
    finally:
        for sig in installed:
            try:
                loop.remove_signal_handler(sig)
            except Exception:
                pass
        task = ctx.shared.pop("_bulk_task", None)
        if task is not None and not task.done():
            task.cancel()
        try:
            await asyncio.wait_for(ctx.client.aclose(), timeout=10)
        except Exception:
            pass
    why = ("run interrupted (signal)" if ctx.shared.get("interrupted") else
           f"run error: {ctx.shared['run_error']}" if ctx.shared.get("run_error") else "run time budget exhausted")
    for c in companies:
        for name, *_rest in MODULES:
            if ctx.module_enabled(name) and name not in c.modules and (
                    name == "registry" or c.data.get("identity", {}).get("match_status") == "exact"):
                c.outcome(ModuleOutcome(name, "failed", note=f"not checked: {why}"))
        if "identity" not in c.data:
            c.data["identity"] = {"match_status": "error", "confidence": 0.0, "method": "none",
                                  "checks": [{"name": "registry_lookup", "passed": False,
                                              "detail": "registry lookup did not complete within the time budget"}]}
    completed = utc_iso()
    envelopes = []
    for c in companies:
        try:
            label_by_website(c)
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
        "licences": {},
    }


def read_previous(path: Path | None) -> dict[str, dict[str, Any]]:
    """Previous envelopes by organisation number. Unparseable lines are skipped; placeholder envelopes
    from an interrupted run ("run did not complete") fall back to the newest archived run in history/."""
    out: dict[str, dict[str, Any]] = {}
    if not path or not Path(path).exists():
        return out
    placeholders: set[str] = set()
    for env in _iter_envelopes(Path(path)):
        org = str(env.get("organisation_number"))
        if _is_placeholder(env):
            placeholders.add(org)
            continue
        out[org] = env
    if placeholders:
        hist = Path(path).parent / "history"
        for run_dir in sorted(hist.glob("*/"), key=lambda d: d.stat().st_mtime, reverse=True) if hist.exists() else []:
            for env in _iter_envelopes(run_dir / "envelopes.jsonl"):
                org = str(env.get("organisation_number"))
                if org in placeholders and org not in out and not _is_placeholder(env):
                    out[org] = env
    return out


def _is_placeholder(env: dict[str, Any]) -> bool:
    return (env.get("run") or {}).get("terminal_status") == "failed" and any(
        "run did not complete" in (e.get("message") or "") for e in env.get("errors") or [])


def _iter_envelopes(path: Path):
    if not path.exists():
        return
    opener = (lambda: gzip.open(path, "rt", encoding="utf-8", errors="replace")) if path.suffix == ".gz" \
        else (lambda: path.open(encoding="utf-8", errors="replace"))
    with opener() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                env = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(env, dict) and env.get("organisation_number"):
                yield env
