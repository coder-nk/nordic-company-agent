"""Nightly Enhetsregisteret bulk files for large batches under a tight request budget.

When the planner cannot afford one roles call and one sub-unit call per company, the pipeline
downloads the official nightly bulk files instead (one request each):

  roles:     GET https://data.brreg.no/enhetsregisteret/api/roller/totalbestand   (~130 MB gzip JSON array)
  subunits:  GET https://data.brreg.no/enhetsregisteret/api/underenheter/lastned  (~90 MB gzip JSON array)

Only elements for the batch are kept. The roles/subunits modules then process those elements
exactly like API responses. Evidence cites the canonical per-entity API URL as `source_url`, the
bulk file as `retrieved_via`, and the file's Last-Modified as the effective date. Birth dates are
removed before anything is stored.
"""

from __future__ import annotations

import asyncio
import contextlib
import gzip
import json
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from ..envelope import CompanyResult, utc_iso

ROLES_URL = "https://data.brreg.no/enhetsregisteret/api/roller/totalbestand"
SUBUNITS_URL = "https://data.brreg.no/enhetsregisteret/api/underenheter/lastned"
MIN_TIME_LEFT_S = 15 * 60          # never start a bulk download with less run time than this


def _parse_roles(path: Path, wanted: set[str], stop: threading.Event | None = None) -> dict[str, bytes]:
    import ijson
    out: dict[str, bytes] = {}
    with gzip.open(path, "rb") as fh:
        for item in ijson.items(fh, "item", use_float=True):
            if stop is not None and stop.is_set():
                raise RuntimeError("cancelled")
            org = str(item.get("organisasjonsnummer") or "")
            if org in wanted:
                out[org] = json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                if len(out) == len(wanted):
                    break
    return out


def _parse_subunits(path: Path, wanted: set[str], stop: threading.Event | None = None) -> dict[str, list[bytes]]:
    import ijson
    out: dict[str, list[bytes]] = {o: [] for o in wanted}
    with gzip.open(path, "rb") as fh:
        for item in ijson.items(fh, "item", use_float=True):
            if stop is not None and stop.is_set():
                raise RuntimeError("cancelled")
            parent = str(item.get("overordnetEnhet") or "")
            if parent in out:
                out[parent].append(json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    return out


async def prepare(ctx: Any, companies: list[CompanyResult], kinds: set[str]) -> dict[str, Any]:
    """Download and index the requested bulk files ('roles', 'subunits'). Never raises."""
    report: dict[str, Any] = {}
    wanted = {c.organisation_number for c in companies
              if (c.data.get("identity") or {}).get("match_status") == "exact"}
    if not wanted:
        return report
    try:
        # Temporary location outside the output folder; always removed (the roles file holds birth
        # dates of every role holder in Norway and must never be left behind).
        bulk_dir = Path(tempfile.mkdtemp(prefix="nca-bulk-"))
    except OSError as exc:
        report["error"] = f"no temp dir: {exc}"
        return report
    stop = threading.Event()

    async def one(kind: str, url: str, parser, accept: str) -> None:
        t0 = time.monotonic()
        try:
            if ctx.time_left() < MIN_TIME_LEFT_S or not ctx.allow("registry", 1):
                report[kind] = {"status": "skipped", "reason": "time or budget"}
                return
            path = bulk_dir / f"{kind}.json.gz"
            meta = await ctx.client.download(url, path, source=f"bulk_{kind}", headers={"Accept": accept},
                                             max_seconds=max(60.0, min(14 * 60, ctx.time_left() - 18 * 60)))
            ctx.planner.charge("registry", 1)
            ctx.batch_requests[f"bulk_{kind}"] += 1
            if meta["status"] != 200:
                report[kind] = {"status": "failed", "http_status": meta["status"]}
                return
            try:
                data = await asyncio.to_thread(parser, path, wanted, stop)
            except asyncio.CancelledError:
                stop.set()
                raise
            info = {"url": url, "final_url": meta["final_url"], "retrieved_at": utc_iso(meta["retrieved_at"]),
                    "retrieved_dt": meta["retrieved_at"],
                    "last_modified": meta["headers"].get("last-modified"), "etag": meta["headers"].get("etag"),
                    "file_sha256": meta["sha256"], "bytes": meta["bytes"], "items": data}
            ctx.shared[f"bulk_{kind}"] = info
            report[kind] = {"status": "ok", "bytes": meta["bytes"], "matched": sum(1 for v in data.values() if v),
                            "seconds": round(time.monotonic() - t0, 1)}
        except Exception as exc:  # fall back to per-company API calls
            report[kind] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"[:200]}
        finally:
            with contextlib.suppress(OSError):
                (bulk_dir / f"{kind}.json.gz").unlink()

    tasks = []
    if "roles" in kinds:
        tasks.append(one("roles", ROLES_URL, _parse_roles, "application/json"))
    if "subunits" in kinds:
        # This path needs a gzip Accept header; with application/json it is routed to /underenheter/{orgnr}.
        tasks.append(one("subunits", SUBUNITS_URL, _parse_subunits, "application/gzip"))
    try:
        await asyncio.gather(*tasks)
    finally:
        stop.set()
        shutil.rmtree(bulk_dir, ignore_errors=True)
        ctx.shared["bulk_report"] = report
    return report
