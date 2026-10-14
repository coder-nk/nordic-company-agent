#!/usr/bin/env bash
# Regenerate the submission artifacts:
#   smoke/     100-company smoke test (envelopes, run report, changes, viewer)  -- required by Builderr
#   profiles/  1,000-company profiles (compressed envelopes + report + changes)  -- "at least 1,000 profiles"
#
#   tools/make_deliverables.sh           fresh runs (no previous envelopes, empty changes.jsonl)
#   tools/make_deliverables.sh refresh   re-run both batches against the current envelopes: changes.jsonl holds
#                                        the typed, evidence-backed changes since the previous run
set -euo pipefail
cd "$(dirname "$0")/.."
MODE="${1:-fresh}"
DAY="$(date -u +%Y%m%d)"
if [ "$MODE" = "fresh" ]; then
  rm -rf smoke profiles
  SMOKE_ID=smoke-100; PROF_ID=profiles-1000; PREV=()
elif [ "$MODE" = "refresh" ]; then
  [ -f smoke/envelopes.jsonl ] && [ -f profiles/envelopes.jsonl.gz ] || { echo "nothing to refresh" >&2; exit 2; }
  SMOKE_ID="smoke-100-$DAY"; PROF_ID="profiles-1000-$DAY"
  cp profiles/envelopes.jsonl.gz /tmp/nca-previous-profiles.jsonl.gz
  PREV=(--previous /tmp/nca-previous-profiles.jsonl.gz)
  rm -rf smoke/history                     # keep only the run being refreshed against
else
  echo "usage: $0 [fresh|refresh]" >&2; exit 2
fi
./run.sh batches/smoke-100.jsonl smoke --run-id "$SMOKE_ID" --expected-count 100
uv run nca validate smoke/envelopes.jsonl --input batches/smoke-100.jsonl > smoke/validation.json
./run.sh batches/profiles-1000.jsonl profiles --run-id "$PROF_ID" --expected-count 1000 --max-requests 2000 \
    --no-viewer "${PREV[@]}"
uv run nca validate profiles/envelopes.jsonl --input batches/profiles-1000.jsonl > profiles/validation.json
gzip -9 -f profiles/envelopes.jsonl
rm -rf profiles/snapshots profiles/history   # raw bodies stay local (large); hashes and URLs remain in every envelope
echo "deliverables ready ($MODE): smoke/ profiles/"
