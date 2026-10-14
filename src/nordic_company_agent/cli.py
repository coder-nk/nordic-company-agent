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
    """Read .json / .jsonl / text lists of organisation numbers (UTF-8, BOM tolerated).

    Returns (valid unique numbers in input order, invalid entries, duplicates). Unparseable lines and
    entries without a recognisable number become invalid entries (they still get an envelope).
    `input_order(path)` gives the envelope order (valid and invalid, duplicates removed)."""
    p = Path(path)
    raw = gzip.open(p, "rb").read() if p.suffix == ".gz" else p.read_bytes()
    text = raw.decode("utf-8-sig", errors="replace")
    values: list[Any] = []
    stripped = text.lstrip()
    if stripped.startswith("[") or (stripped.startswith("{") and p.suffix == ".json"):
        try:
            body = json.loads(text)
            values = body if isinstance(body, list) else (body.get("organisation_numbers") or
                                                          body.get("organisations") or [])
        except json.JSONDecodeError:
            values = [_loads_or_raw(line) for line in text.splitlines() if line.strip()]
    elif ".jsonl" in p.name or stripped.startswith("{"):
        values = [_loads_or_raw(line) for line in text.splitlines() if line.strip()]
    else:
        values = [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    out: list[str] = []
    invalid: list[dict[str, Any]] = []
    dups: list[str] = []
    seen: set[str] = set()
    order: list[str] = []
    for v in values:
        if isinstance(v, dict):
            raw_v = v.get("organisation_number") or v.get("orgnr") or v.get("organisasjonsnummer") or v.get("org")
            if raw_v is None:
                raw_v = json.dumps(v, ensure_ascii=False, sort_keys=True)[:80]
        else:
            raw_v = v
        try:
            org = normalize(str(raw_v))
        except InvalidOrgNumber as exc:
            digits = "".join(ch for ch in str(raw_v) if ch.isdigit()) or str(raw_v)
            if digits in seen:
                dups.append(digits)
                continue
            seen.add(digits)
            invalid.append({"input": raw_v, "organisation_number": digits, "reason": str(exc)})
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


def _loads_or_raw(line: str) -> Any:
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return line.strip()


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
    if a.output and a.out == "out/run":
        a.out = str(Path(a.output).parent if Path(a.output).suffix else Path(a.output))
    if a.workers:
        a.concurrency = a.workers
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
    REF_MODULES = {"registry_live": "registry", "accounting_obligation": "registry", "financials": "accounts",
                   "locations": "subunits", "financial_history": "account_copies"}
    if a.modules:
        req = {REF_MODULES.get(m.strip(), m.strip()) for m in a.modules.split(",") if m.strip()}
        unknown = req - set(ALL_MODULES)
        if unknown:
            print(f"warning: ignoring unknown modules {sorted(unknown)}", file=sys.stderr)
        modules = (req & set(ALL_MODULES)) | {"registry"}
        if "website" in modules:
            modules |= {"social_profiles", "news"}
    else:
        modules = set(ALL_MODULES)
    if a.skip_modules:
        modules -= {m.strip() for m in a.skip_modules.split(",")}
    # Placeholder envelopes first: even a hard kill (SIGKILL/OOM) leaves exactly one line per input.
    from .pipeline import failed_envelope
    ts0 = utc_iso()
    skel = {e["organisation_number"]: invalid_envelope(e, run_id, ts0) for e in invalid}
    skel.update({o: failed_envelope(o, run_id, ts0, ts0, "run did not complete (placeholder written at start)")
                 for o in orgnrs})
    write_jsonl(out_dir / "envelopes.jsonl", [skel[o] for o in all_inputs if o in skel])
    config = RunConfig.from_env(
        run_id=run_id, out_dir=out_dir, started_monotonic=t_start,
        **({"max_requests": a.max_requests} if a.max_requests else {}),
        **({"time_budget_s": a.time_budget * 60} if a.time_budget else {}),
        modules=modules, concurrency=a.concurrency,
        registry_snapshot=Path(a.bulk) if a.bulk else None,
        previous_envelopes=prev_path if previous else None,
        live_discovery=not a.no_live_discovery, save_snapshots=not a.no_snapshots, bulk_mode=a.bulk_mode,
        **({"cache_dir": Path(a.cache_dir)} if a.cache_dir else {}),
    )
    from concurrent.futures import ThreadPoolExecutor
    loop = asyncio.new_event_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=64, thread_name_prefix="nca-io"))
    try:
        result = loop.run_until_complete(run_batch(orgnrs, config, invalid=invalid, duplicates=dups,
                                                   previous=previous))
    except KeyboardInterrupt:
        return 130
    by_org = {e["organisation_number"]: e for e in result.envelopes}
    ts = utc_iso()
    for entry in invalid:
        by_org.setdefault(entry["organisation_number"], invalid_envelope(entry, run_id, ts))
    envelopes = [by_org[o] for o in all_inputs if o in by_org]
    expected = [o for o in all_inputs]
    write_jsonl(out_dir / "envelopes.jsonl", envelopes)
    validation = validate_envelopes(envelopes, expected)
    for extra in (a.output, a.profiles_output):
        if extra and Path(extra).resolve() != (out_dir / "envelopes.jsonl").resolve():
            write_jsonl(Path(extra), envelopes)
    changes = [dict(ch, organisation_number=e["organisation_number"]) for e in envelopes for ch in e.get("changes", [])]
    write_jsonl(out_dir / "changes.jsonl", changes)
    try:
        report = build_report(result, expected, validation, config, invalid, dups)
        report["operations"]["wall_clock_s"] = round(time.monotonic() - t_start, 1)
        for flag in ("timed_out", "interrupted", "run_error"):
            if result.ctx.shared.get(flag):
                report[flag] = result.ctx.shared[flag]
        report["bulk"] = result.ctx.shared.get("bulk_report")
        report["optional_modules_skipped_by_budget"] = result.ctx.shared.get("planned_skips", {})
        (out_dir / "run-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n")
        if a.report:
            Path(a.report).parent.mkdir(parents=True, exist_ok=True)
            Path(a.report).write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n")
        if not a.no_viewer:
            from .ui_server import export_static
            from .viewer import build_site
            export_static(envelopes, out_dir / "site", dataset=out_dir.name or "run", report=report,
                          snapshot_dir=out_dir / "snapshots")
            build_site(envelopes, out_dir / "site" / "classic", report=report)
        print(json.dumps({k: report[k] for k in ("run_id", "expected_count", "emitted_envelopes", "identity")}
                         | {"requests": report["operations"]["requests"],
                            "wall_clock_s": report["operations"]["wall_clock_s"],
                            "validation_passed": validation["passed"], "out": str(out_dir)}, indent=2))
    except Exception as exc:  # the envelopes are already written; reporting problems must not fail the run
        print(f"warning: report/viewer generation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
    code = 0 if validation["passed"] else 1
    _finish(loop, code)
    return code


def _finish(loop, code: int) -> None:
    """Outputs are on disk. Do not let stray worker threads (e.g. a hung DNS lookup) delay exit."""
    import os
    import threading
    try:
        loop.close()
    except Exception:
        pass
    sys.stdout.flush()
    sys.stderr.flush()
    if any(t.is_alive() and not t.daemon for t in threading.enumerate() if t is not threading.main_thread()):
        os._exit(code)


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


def cmd_bench(a: argparse.Namespace) -> int:
    from .bench import bench, load_envelopes, render
    ref = json.loads(Path(a.reference).read_text(encoding="utf-8"))
    res = bench(load_envelopes(a.envelopes), ref if isinstance(ref, list) else ref.get("profiles", []))
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    print(render(res, show=a.show))
    return 0


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


def cmd_ui(a: argparse.Namespace) -> int:
    from .ui_server import default_sources, serve_ui
    root = Path.cwd()
    sources = [Path(x) for x in a.data] if a.data else default_sources(root)
    missing = [str(x) for x in sources if not x.exists()]
    if missing:
        print(f"error: not found: {', '.join(missing)}", file=sys.stderr)
        return 2
    if not sources:
        print("no envelopes found (expected profiles/ or smoke/); companies can still be researched from the app",
              file=sys.stderr)
    serve_ui(sources, Path(a.store), host=a.host, port=a.port, open_browser=not a.no_open)
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
    r.add_argument("--max-requests", type=int, help="hard cap on outbound HTTP requests (default 3000 or $NCA_MAX_REQUESTS)")
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
    # Aliases accepted for drop-in compatibility with the Builderr reference harness
    # (scripts/run_competition_batch.py --organisations --bulk --output --profiles-output --report --run-id ...).
    r.add_argument("--output", help="envelope JSONL path (reference-harness alias; implies --out <its folder>)")
    r.add_argument("--profiles-output", help="also write the envelopes to this JSONL path")
    r.add_argument("--report", help="also write the run report to this path")
    r.add_argument("--workers", type=int, help="alias for --concurrency")
    r.add_argument("--checkpoint-every", type=int, help=argparse.SUPPRESS)
    r.add_argument("--resume", action="store_true", help=argparse.SUPPRESS)
    r.add_argument("--bulk-mode", choices=["auto", "always", "never"], default="auto",
                   help="use Brreg nightly bulk files for roles/sub-units when the request budget is too small "
                        "for per-company calls (auto), always, or never")
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

    b = sub.add_parser("bench", help="score a run against Builderr's public reference profiles (recall proxy)")
    b.add_argument("envelopes")
    b.add_argument("--reference", default="research/samples/builderr_public_sample_100_profiles.json")
    b.add_argument("--json", help="write the full comparison (all misses, conflicts, extras) here")
    b.add_argument("--show", type=int, default=10, help="items listed per section")
    b.set_defaults(fn=cmd_bench)

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

    u = sub.add_parser("ui", help="interactive company-intelligence app (opens the browser)")
    u.add_argument("--data", nargs="*", help="envelope files (.jsonl or .jsonl.gz); default: profiles/ and smoke/")
    u.add_argument("--store", default="out/ui", help="where researched companies and caches are kept")
    u.add_argument("--host", default="127.0.0.1")
    u.add_argument("--port", type=int, default=8765)
    u.add_argument("--no-open", action="store_true", help="do not open a browser window")
    u.set_defaults(fn=cmd_ui)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
