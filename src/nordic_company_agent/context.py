"""Run context shared by all source modules: config, budgeted HTTP client, request planner,
declared caches and the raw-snapshot store."""

from __future__ import annotations

import contextvars
import gzip
import json
import os
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .http import REQUEST_COUNTER, BudgetedClient, FetchResult

PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parents[1]
DEFAULT_CACHE_DIR = REPO_ROOT / "cache"          # declared, versioned caches shipped with the repo
DEFAULT_STATE_DIR = REPO_ROOT / "state"          # mutable local state (previous runs) – not required


@dataclass
class RunConfig:
    run_id: str
    out_dir: Path
    max_requests: int = 2000
    time_budget_s: float = 40 * 60
    cache_dir: Path = DEFAULT_CACHE_DIR
    concurrency: int = 24
    modules: set[str] = field(default_factory=lambda: set(ALL_MODULES))
    registry_snapshot: Path | None = None        # evaluator-supplied Brreg CSV/JSON snapshot
    previous_envelopes: Path | None = None
    website_max_pages: int = 6
    live_discovery: bool = True                  # try candidate domains at run time when not cached
    bulk_mode: str = "auto"                      # auto | always | never: nightly bulk files for roles/sub-units
    started_monotonic: float | None = None       # process start; the time budget is measured from here
    save_snapshots: bool = True

    @staticmethod
    def from_env(**overrides: Any) -> "RunConfig":
        env = os.environ
        if "max_requests" not in overrides and env.get("NCA_MAX_REQUESTS"):
            overrides["max_requests"] = int(env["NCA_MAX_REQUESTS"])
        if "time_budget_s" not in overrides and env.get("NCA_TIME_BUDGET_S"):
            overrides["time_budget_s"] = float(env["NCA_TIME_BUDGET_S"])
        return RunConfig(**overrides)


# Module names (also used in envelope `modules` states and in `--modules`).
ALL_MODULES = (
    "registry", "registry_feed", "roles", "subunits", "group", "accounts", "account_copies",
    "authorisations", "website", "social_profiles", "news", "jobs", "wikidata", "gleif", "state_aid",
    "grants",
)

# Spending tiers in priority order. The planner gives each tier a share of the request budget;
# lower tiers may only use what higher tiers leave unused.
TIERS = (
    "registry",          # batch identity lookups, deleted/removed follow-ups
    "batch_external",    # GLEIF / Wikidata / feeds / NAV feed pages (O(N/100))
    "accounts",          # one per company that files accounts
    "website_primary",   # robots + home + proof page of a known/verified site
    "roles",             # one per company
    "subunits",          # one per company
    "group",             # konsernstruktur when erIKonsern
    "website_secondary", # contact/about/news/careers/sitemap pages
    "discovery",         # live candidate-domain discovery for companies not in the cache
    "extras",            # signature/prokura, state aid, filing-year copies
)


class Planner:
    """Request-budget allocator.

    `allow(tier, n)` returns True when `n` more requests may be spent in `tier` without
    eating into the reservation of higher-priority tiers that have not finished yet.
    Tiers call `done(tier)` when they finish so their unused reservation is released.
    """

    def __init__(self, client: BudgetedClient, reservations: dict[str, int] | None = None):
        self.client = client
        self.reservations = dict(reservations or {})
        self.spent: dict[str, int] = {t: 0 for t in TIERS}
        self.finished: set[str] = set()

    def set_reservations(self, reservations: dict[str, int]) -> None:
        self.reservations = dict(reservations)

    def _reserved_above(self, tier: str) -> int:
        total = 0
        for t in TIERS:
            if t == tier:
                break
            if t in self.finished:
                continue
            total += max(0, self.reservations.get(t, 0) - self.spent.get(t, 0))
        return total

    def allow(self, tier: str, n: int = 1) -> bool:
        if self.client.remaining < n:
            return False
        if self.client.time_left() < 20:
            return False
        # A tier may spend only what no unfinished higher-priority tier still needs. (No "own share"
        # shortcut: earlier overspend by any tier must never come out of a higher tier's reservation.)
        return self.client.remaining - self._reserved_above(tier) - n >= 0

    def charge(self, tier: str, n: int = 1) -> None:
        self.spent[tier] = self.spent.get(tier, 0) + n

    def done(self, tier: str) -> None:
        self.finished.add(tier)

    def report(self) -> dict[str, Any]:
        return {"reservations": self.reservations, "spent": self.spent, "finished": sorted(self.finished)}


class SnapshotStore:
    """Stores raw response bodies (gzip) under <out>/snapshots/<sha256>.gz and indexes them."""

    def __init__(self, root: Path | None):
        self.root = root
        self.index: dict[str, dict[str, Any]] = {}
        if root:
            root.mkdir(parents=True, exist_ok=True)

    def save(self, body: bytes, *, url: str, retrieved_at: str, content_type: str | None = None,
             sha256: str | None = None, redacted: bool = False, redaction_note: str | None = None) -> str:
        import hashlib
        digest = sha256 or hashlib.sha256(body).hexdigest()
        if digest not in self.index:
            entry = {"sha256": digest, "url": url, "retrieved_at": retrieved_at, "bytes": len(body)}
            if content_type:
                entry["content_type"] = content_type.split(";")[0]
            if redacted:
                entry["redacted"] = (redaction_note or "personal birth dates removed before storage") + \
                    "; sha256 is of the original bytes"
            if self.root:
                path = self.root / f"{digest}.gz"
                if not path.exists():
                    path.write_bytes(gzip.compress(body, compresslevel=6))
                entry["path"] = f"snapshots/{digest}.gz"
            self.index[digest] = entry
        return digest

    def save_result(self, res: FetchResult, retrieved_at: str, body: bytes | None = None,
                    redacted: bool = False, redaction_note: str | None = None) -> str:
        return self.save(body if body is not None else res.content, url=res.final_url or res.url,
                         retrieved_at=retrieved_at, content_type=res.headers.get("content-type"),
                         sha256=res.sha256, redacted=redacted, redaction_note=redaction_note)

    def entry(self, digest: str) -> dict[str, Any] | None:
        return self.index.get(digest)


class Caches:
    """Lazy loader for declared caches in `cache_dir` (see CACHES.md). Missing caches are empty."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self._loaded: dict[str, Any] = {}

    def jsonl_index(self, name: str, key: str = "organisation_number") -> dict[str, list[dict[str, Any]]]:
        if name in self._loaded:
            return self._loaded[name]
        idx: dict[str, list[dict[str, Any]]] = {}
        for suffix in (".jsonl.gz", ".jsonl"):
            path = self.cache_dir / f"{name}{suffix}"
            if path.exists():
                opener = gzip.open if suffix.endswith(".gz") else open
                with opener(path, "rt", encoding="utf-8") as fh:
                    for line in fh:
                        if line.strip():
                            row = json.loads(line)
                            idx.setdefault(str(row.get(key)), []).append(row)
                break
        self._loaded[name] = idx
        return idx

    def json(self, name: str) -> Any:
        if name in self._loaded:
            return self._loaded[name]
        val = None
        for suffix in (".json.gz", ".json"):
            path = self.cache_dir / f"{name}{suffix}"
            if path.exists():
                opener = gzip.open if suffix.endswith(".gz") else open
                with opener(path, "rt", encoding="utf-8") as fh:
                    val = json.load(fh)
                break
        self._loaded[name] = val
        return val

    def meta(self, name: str) -> dict[str, Any] | None:
        path = self.cache_dir / f"{name}.meta.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return None


# The company a per-company module is currently working for (None inside batch modules).
CURRENT_COMPANY: contextvars.ContextVar[Any] = contextvars.ContextVar("nca_current_company", default=None)
CURRENT_MODULE: contextvars.ContextVar[str | None] = contextvars.ContextVar("nca_current_module", default=None)


class RunContext:
    def __init__(self, config: RunConfig, client: BudgetedClient | None = None):
        self.config = config
        self.started = config.started_monotonic if config.started_monotonic is not None else time.monotonic()
        deadline = self.started + config.time_budget_s
        self.client = client or BudgetedClient(max_requests=config.max_requests, deadline=deadline)
        if client is not None and client.deadline is None:
            client.deadline = deadline
        self.planner = Planner(self.client)
        self.caches = Caches(config.cache_dir)
        self.snapshots = SnapshotStore(config.out_dir / "snapshots" if config.save_snapshots else None)
        self.shared: dict[str, Any] = {}   # run-level shared data (e.g. NAV token, feed pages)
        self.batch_requests: Counter[str] = Counter()  # requests made by batch modules, by module

    def module_enabled(self, name: str) -> bool:
        return name in self.config.modules

    def time_left(self) -> float:
        return self.started + self.config.time_budget_s - time.monotonic()

    def allow(self, tier: str, n: int = 1) -> bool:
        return self.planner.allow(tier, n)

    def _account(self, tier: str, source: str, n: int) -> None:
        if n <= 0:
            return
        self.planner.charge(tier, n)
        company = CURRENT_COMPANY.get()
        if company is not None:
            company.requests += n
            by = company.data.setdefault("_requests_by_module", {})
            by[source] = by.get(source, 0) + n
        else:
            self.batch_requests[source] += n

    async def get(self, url: str, *, tier: str, source: str, **kw: Any) -> FetchResult:
        """GET through the budgeted client and charge the tier (and the current company) with the
        requests actually used, including redirect hops, retries and robots.txt."""
        counter = [0]
        token = REQUEST_COUNTER.set(counter)
        try:
            return await self.client.get(url, source=source, **kw)
        finally:
            REQUEST_COUNTER.reset(token)
            self._account(tier, source, counter[0])

    async def post(self, url: str, *, tier: str, source: str, **kw: Any) -> FetchResult:
        counter = [0]
        token = REQUEST_COUNTER.set(counter)
        try:
            return await self.client.post(url, source=source, **kw)
        finally:
            REQUEST_COUNTER.reset(token)
            self._account(tier, source, counter[0])
