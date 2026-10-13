#!/usr/bin/env python3
"""Drop-in replacement for the Builderr reference harness command.

    python scripts/run_competition_batch.py --organisations batch.jsonl --bulk brreg-enheter.csv \
        --output out/envelopes.jsonl --profiles-output out/profiles.jsonl --report out/run-report.json \
        --run-id official-001 --expected-count 1000

All flags are forwarded to `nca run` (see README.md).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nordic_company_agent.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["run", *sys.argv[1:]]))
