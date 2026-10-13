#!/usr/bin/env bash
# One evaluator command:
#   ./run.sh <organisations.jsonl|.json|.txt> [output_dir] [extra `nca run` flags]
# Example: ./run.sh smoke/smoke-100.jsonl out/smoke --max-requests 2000 --time-budget 40
# Writes <output_dir>/envelopes.jsonl (one terminal envelope per input, same order),
# run-report.json, changes.jsonl, snapshots/ and site/index.html.
set -euo pipefail
cd "$(dirname "$0")"
INPUT="${1:?usage: ./run.sh <organisations file> [output_dir] [flags]}"
OUT="${2:-out/run}"
shift $(( $# >= 2 ? 2 : 1 ))
if command -v uv >/dev/null 2>&1; then
  uv sync --frozen --no-dev --quiet
  exec uv run --frozen --no-dev nca run --input "$INPUT" --out "$OUT" "$@"
else
  # Fallback without uv: pinned requirements in a local venv.
  if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv
    .venv/bin/pip install --quiet -r requirements.txt
    .venv/bin/pip install --quiet --no-deps -e .
  fi
  exec .venv/bin/nca run --input "$INPUT" --out "$OUT" "$@"
fi
