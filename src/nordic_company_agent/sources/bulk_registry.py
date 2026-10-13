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
import gzip
import json
import time
from pathlib import Path
from typing import Any

from ..envelope import CompanyResult, utc_iso

ROLES_URL = "https://data.brreg.no/enhetsregisteret/api/roller/totalbestand"
SUBUNITS_URL = "https://data.brreg.no/enhetsregisteret/api/underenheter/lastned"
MIN_TIME_LEFT_S = 15 * 60          # never start a bulk download with less run time than this


def _parse_roles(path: Path, wanted: set[str]) -> dict[str, bytes]:
    import ijson
    out: dict[str, bytes] = {}
    with gzip.open(path, "rb") as fh:
        for item in ijson.items(fh, "item", use_float=True):
            org = str(item.get("organisasjonsnummer") or "")
            if org in wanted:
                out[org] = json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                if len(out) == len(wanted):
                    break
    return out


def _parse_subunits(path: Path, wanted: set[str]) -> dict[str, list[bytes]]:
    import ijson
    out: dict[str, list[bytes]] = {o: [] for o in wanted}
    with gzip.open(path, "rb") as fh:
        for item in ijson.items(fh, "item", use_float=True):
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
    bulk_dir = Path(ctx.config.out_dir) / "bulk"
    bulk_dir.mkdir(parents=True, exist_ok=True)

    async def one(kind: str, url: str, parser, accept: str) -> None:
        t0 = time.monotonic()
        try:
            if ctx.time_left() < MIN_TIME_LEFT_S or not ctx.allow("registry", 1):
                report[kind] = {"status": "skipped", "reason": "time or budget"}
                return
            path = bulk_dir / f"{kind}.json.gz"
            meta = await ctx.client.download(url, path, source=f"bulk_{kind}", headers={"Accept": accept})
            ctx.planner.charge("registry", 1)
            ctx.batch_requests[f"bulk_{kind}"] += 1
            if meta["status"] != 200:
                report[kind] = {"status": "failed", "http_status": meta["status"]}
                return
            data = await asyncio.to_thread(parser, path, wanted)
            info = {"url": url, "final_url": meta["final_url"], "retrieved_at": utc_iso(meta["retrieved_at"]),
                    "retrieved_dt": meta["retrieved_at"],
                    "last_modified": meta["headers"].get("last-modified"), "etag": meta["headers"].get("etag"),
                    "file_sha256": meta["sha256"], "bytes": meta["bytes"], "items": data}
            ctx.shared[f"bulk_{kind}"] = info
            report[kind] = {"status": "ok", "bytes": meta["bytes"], "matched": sum(1 for v in data.values() if v),
                            "seconds": round(time.monotonic() - t0, 1)}
            try:
                path.unlink()  # do not keep person data from other entities on disk
            except OSError:
                pass
        except Exception as exc:  # fall back to per-company API calls
            report[kind] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"[:200]}

    tasks = []
    if "roles" in kinds:
        tasks.append(one("roles", ROLES_URL, _parse_roles, "application/json"))
    if "subunits" in kinds:
        # This path needs a gzip Accept header; with application/json it is routed to /underenheter/{orgnr}.
        tasks.append(one("subunits", SUBUNITS_URL, _parse_subunits, "application/gzip"))
    await asyncio.gather(*tasks)
    ctx.shared["bulk_report"] = report
    return report
