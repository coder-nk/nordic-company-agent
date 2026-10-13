#!/usr/bin/env bash
# Regenerate the submission artifacts:
#   smoke/     100-company smoke test (envelopes, run report, changes, viewer)  -- required by Builderr
#   profiles/  1,000-company profiles (compressed envelopes + report + viewer)  -- "at least 1,000 profiles"
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf smoke profiles
./run.sh batches/smoke-100.jsonl smoke --run-id smoke-100 --expected-count 100
uv run nca validate smoke/envelopes.jsonl --input batches/smoke-100.jsonl > smoke/validation.json
./run.sh batches/profiles-1000.jsonl profiles --run-id profiles-1000 --expected-count 1000 --max-requests 2000 --no-viewer
uv run nca validate profiles/envelopes.jsonl --input batches/profiles-1000.jsonl > profiles/validation.json
gzip -9 -f profiles/envelopes.jsonl
rm -rf profiles/snapshots   # raw bodies stay local (large); hashes and URLs remain in every envelope
echo "deliverables ready: smoke/ profiles/"
