#!/usr/bin/env bash
# One command for the interactive app: installs the pinned environment if needed, loads every profile in
# profiles/ and smoke/, and opens http://127.0.0.1:8765 in your browser.
#   ./ui.sh                       # default data
#   ./ui.sh --data my/envelopes.jsonl --port 9000
set -euo pipefail
cd "$(dirname "$0")"
if command -v uv >/dev/null 2>&1; then
  uv sync --frozen --no-dev --quiet
  exec uv run --frozen --no-dev nca ui "$@"
else
  if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv
    .venv/bin/pip install --quiet -r requirements.txt
    .venv/bin/pip install --quiet --no-deps -e .
  fi
  exec .venv/bin/nca ui "$@"
fi
