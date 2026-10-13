"""Command line: `nca run` (evaluator batch command), `nca company`, `nca serve`, `nca viewer`,
`nca validate`, `nca sample`."""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import random
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .context import ALL_MODULES, RunConfig
from .envelope import utc_iso
from .orgnr import InvalidOrgNumber, is_valid, normalize


def read_inputs(path: str) -> tuple[list[str], list[dict[str, Any]], list[str]]:
    """Read .json / .jsonl / text lists of organisation numbers.

    Returns (valid unique numbers in input order, invalid entries, duplicates).
    `input_order(path)` gives the envelope order (valid and invalid, duplicates removed)."""
    p = Path(path)
    text = (gzip.open(p, "rt", encoding="utf-8").read() if p.suffix == ".gz" else p.read_text(encoding="utf-8"))
    values: list[Any]
    stripped = text.lstrip()
    if p.suffix in (".json",) or (stripped.startswith("[") or (stripped.startswith("{") and "\n{" not in stripped.strip())):
        try:
            body = json.loads(text)
            values = body if isinstance(body, list) else body.get("organisation_numbers") or body.get("organisations") or []
        except json.JSONDecodeError:
            values = [json.loads(line) for line in text.splitlines() if line.strip()]
    elif ".jsonl" in p.name or stripped.startswith("{"):
        values = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        values = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]
    out: list[str] = []
    invalid: list[dict[str, Any]] = []
    dups: list[str] = []
    seen: set[str] = set()
    order: list[str] = []
    for v in values:
        raw = v.get("organisation_number") or v.get("orgnr") or v.get("organisasjonsnummer") if isinstance(v, dict) else v
        try:
            org = normalize(str(raw))
        except InvalidOrgNumber as exc:
            digits = "".join(ch for ch in str(raw) if ch.isdigit()) or str(raw)
            if digits in seen:
                dups.append(digits)
                continue
            seen.add(digits)
            invalid.append({"input": raw, "organisation_number": digits, "reason": str(exc)})
            order.append(digits)
            continue
        if org in seen:
            dups.append(org)
            continue
        seen.add(org)
        out.append(org)
        order.append(org)
    _ORDER_CACHE[str(path)] = order
    return out, invalid, dups


_ORDER_CACHE: dict[str, list[str]] = {}


def input_order(path: str) -> list[str]:
    if str(path) not in _ORDER_CACHE:
        read_inputs(path)
    return _ORDER_CACHE[str(path)]


def invalid_envelope(entry: dict[str, Any], run_id: str, ts: str) -> dict[str, Any]:
    from .pipeline import failed_envelope
    env = failed_envelope(entry["organisation_number"], run_id, ts, ts, entry["reason"], match_status="invalid")
    env["run"]["terminal_status"] = "completed"
    env["identity"]["checks"] = [{"name": "mod11_checksum", "passed": False, "detail": entry["reason"]}]
    env["errors"] = []
    env["summary"]["text"] = f"Not a valid Norwegian organisation number: {entry['reason']}. No requests were made."
    return env


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")
    tmp.replace(path)


def cmd_run(a: argparse.Namespace) -> int:
    from .pipeline import read_previous, run_batch
    from .report import build_report, validate_envelopes

    t_start = time.monotonic()
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    orgnrs, invalid, dups = read_inputs(a.input)
    all_inputs = input_order(a.input)  # input order, invalid included, duplicates dropped
    if a.expected_count is not None and len(all_inputs) != a.expected_count:
        print(f"warning: expected {a.expected_count} organisations, received {len(all_inputs)}", file=sys.stderr)
    run_id = a.run_id or ("run-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    prev_path = Path(a.previous) if a.previous else (out_dir / "envelopes.jsonl")
    previous = read_previous(prev_path) if prev_path.exists() else {}
    if previous and not a.previous:
        # Preserve the earlier run (evidence + snapshots stay available) before overwriting.
        prev_run = next(iter(previous.values())).get("run", {}).get("run_id", "previous")
        hist = out_dir / "history" / prev_run
        hist.mkdir(parents=True, exist_ok=True)
        shutil.copy2(prev_path, hist / "envelopes.jsonl")
        for extra in ("run-report.json", "changes.jsonl"):
            if (out_dir / extra).exists():
                shutil.copy2(out_dir / extra, hist / extra)
    modules = set(ALL_MODULES) if not a.modules else {m.strip() for m in a.modules.split(",") if m.strip()}
    if a.skip_modules:
        modules -= {m.strip() for m in a.skip_modules.split(",")}
    config = RunConfig.from_env(
        run_id=run_id, out_dir=out_dir,
        **({"max_requests": a.max_requests} if a.max_requests else {}),
        **({"time_budget_s": a.time_budget * 60} if a.time_budget else {}),
        modules=modules, concurrency=a.concurrency,
        registry_snapshot=Path(a.bulk) if a.bulk else None,
        previous_envelopes=prev_path if previous else None,
        live_discovery=not a.no_live_discovery, save_snapshots=not a.no_snapshots,
        **({"cache_dir": Path(a.cache_dir)} if a.cache_dir else {}),
    )
    result = asyncio.run(run_batch(orgnrs, config, invalid=invalid, duplicates=dups, previous=previous))
    by_org = {e["organisation_number"]: e for e in result.envelopes}
    ts = utc_iso()
    for entry in invalid:
        by_org.setdefault(entry["organisation_number"], invalid_envelope(entry, run_id, ts))
    envelopes = [by_org[o] for o in all_inputs if o in by_org]
    expected = [o for o in all_inputs]
    validation = validate_envelopes(envelopes, expected)
    write_jsonl(out_dir / "envelopes.jsonl", envelopes)
    changes = [dict(ch, organisation_number=e["organisation_number"]) for e in envelopes for ch in e.get("changes", [])]
    write_jsonl(out_dir / "changes.jsonl", changes)
    report = build_report(result, expected, validation, config, invalid, dups)
    report["operations"]["wall_clock_s"] = round(time.monotonic() - t_start, 1)
    (out_dir / "run-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n")
    if not a.no_viewer:
        from .viewer import build_site
        build_site(envelopes, out_dir / "site", report=report)
    print(json.dumps({k: report[k] for k in ("run_id", "expected_count", "emitted_envelopes", "identity")}
                     | {"requests": report["operations"]["requests"], "wall_clock_s": report["operations"]["wall_clock_s"],
                        "validation_passed": validation["passed"], "out": str(out_dir)}, indent=2))
    return 0 if validation["passed"] else 1


def cmd_company(a: argparse.Namespace) -> int:
    from .pipeline import read_previous, run_batch
    try:
        org = normalize(a.orgnr)
    except InvalidOrgNumber as exc:
        print(json.dumps({"organisation_number": a.orgnr, "error": str(exc)}))
        return 2
    out_dir = Path(a.out or f"out/company-{org}")
    prev = read_previous(out_dir / "envelopes.jsonl")
    config = RunConfig.from_env(run_id="company-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
                                out_dir=out_dir, max_requests=a.max_requests or 60, time_budget_s=300)
    result = asyncio.run(run_batch([org], config, previous=prev))
    env = result.envelopes[0]
    write_jsonl(out_dir / "envelopes.jsonl", [env])
    if a.summary:
        print(env["summary"]["text"])
    else:
        print(json.dumps(env, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_validate(a: argparse.Namespace) -> int:
    from .report import validate_envelopes
    envs = [json.loads(l) for l in Path(a.envelopes).read_text(encoding="utf-8").splitlines() if l.strip()]
    expected = [e["organisation_number"] for e in envs]
    if a.input:
        expected = input_order(a.input)
    res = validate_envelopes(envs, expected)
    print(json.dumps(res, indent=2))
    return 0 if res["passed"] else 1


def cmd_sample(a: argparse.Namespace) -> int:
    rows = []
    with gzip.open(a.universe, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line)["organisation_number"])
    rng = random.Random(a.seed)
    pick = rng.sample(rows, a.count)
    Path(a.output).parent.mkdir(parents=True, exist_ok=True)
    with open(a.output, "w", encoding="utf-8") as fh:
        for o in pick:
            fh.write(json.dumps({"organisation_number": o}) + "\n")
    print(f"wrote {len(pick)} organisation numbers to {a.output} (seed {a.seed})")
    return 0


def cmd_viewer(a: argparse.Namespace) -> int:
    from .viewer import build_site
    envs = [json.loads(l) for l in Path(a.envelopes).read_text(encoding="utf-8").splitlines() if l.strip()]
    rep_path = Path(a.envelopes).with_name("run-report.json")
    report = json.loads(rep_path.read_text()) if rep_path.exists() else None
    build_site(envs, Path(a.out), report=report)
    print(f"wrote viewer for {len(envs)} companies to {a.out}/index.html")
    return 0


def cmd_serve(a: argparse.Namespace) -> int:
    from .server import serve
    serve(Path(a.out), host=a.host, port=a.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="nca", description="nordic-company-agent: evidence-backed Norwegian company profiles")
    ap.add_argument("--version", action="version", version=f"nordic-company-agent {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="research a batch of organisation numbers (one terminal envelope per input)")
    r.add_argument("--input", "--organisations", required=True, help=".jsonl / .json / .txt list of organisation numbers")
    r.add_argument("--out", "--output-dir", default="out/run", help="output directory")
    r.add_argument("--bulk", "--registry-snapshot", help="optional Brreg enheter CSV/JSON snapshot (identity anchor)")
    r.add_argument("--previous", help="previous envelopes.jsonl (default: <out>/envelopes.jsonl if present)")
    r.add_argument("--max-requests", type=int, help="hard cap on outbound HTTP requests (default 2000 or $NCA_MAX_REQUESTS)")
    r.add_argument("--time-budget", type=float, help="minutes (default 40 or $NCA_TIME_BUDGET_S/60)")
    r.add_argument("--run-id")
    r.add_argument("--expected-count", type=int)
    r.add_argument("--modules", help="comma list (default: all): " + ",".join(ALL_MODULES))
    r.add_argument("--skip-modules", help="comma list of modules to disable")
    r.add_argument("--concurrency", type=int, default=24)
    r.add_argument("--cache-dir")
    r.add_argument("--no-live-discovery", action="store_true", help="only use cached/registry website leads")
    r.add_argument("--no-snapshots", action="store_true", help="do not store raw response snapshots")
    r.add_argument("--no-viewer", action="store_true")
    r.set_defaults(fn=cmd_run)

    c = sub.add_parser("company", help="research one organisation number and print its envelope")
    c.add_argument("orgnr")
    c.add_argument("--out")
    c.add_argument("--max-requests", type=int)
    c.add_argument("--summary", action="store_true", help="print only the summary text")
    c.set_defaults(fn=cmd_company)

    v = sub.add_parser("validate", help="check an envelopes.jsonl against the output contract")
    v.add_argument("envelopes")
    v.add_argument("--input")
    v.set_defaults(fn=cmd_validate)

    s = sub.add_parser("sample", help="draw a random batch from the public universe")
    s.add_argument("--universe", default="data/signalpost-company-universe-2025.jsonl.gz")
    s.add_argument("--count", type=int, default=100)
    s.add_argument("--seed", type=int, default=1)
    s.add_argument("--output", required=True)
    s.set_defaults(fn=cmd_sample)

    w = sub.add_parser("viewer", help="build the static HTML viewer from envelopes.jsonl")
    w.add_argument("envelopes")
    w.add_argument("--out", required=True)
    w.set_defaults(fn=cmd_viewer)

    sv = sub.add_parser("serve", help="HTTP API: /companies/:orgnr, /research, /refresh + viewer")
    sv.add_argument("--out", default="out/serve")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8080)
    sv.set_defaults(fn=cmd_serve)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
