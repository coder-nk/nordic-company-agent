# Signalpost submission checklist

Deadlines (from the challenge pages): register the project link on Unstop by **17 Oct 2026, 23:59 IST**.
Builderr accepts code revisions until **18 Oct** (version 1 + up to 4 revised commit hashes).

## What to send to submit@builderr.ai

| item | value |
|---|---|
| Agent name | nordic-company-agent |
| Repository URL | *(push this repo to GitHub/GitLab and paste the URL; it is currently a local git repo)* |
| Exact commit hash | `git rev-parse HEAD` after the final commit |
| One run command | `./run.sh <organisations.jsonl> <output_dir>`, which wraps `uv run nca run --input <file> --out <dir>` |
| Reference-harness-compatible command | `python scripts/run_competition_batch.py --organisations batch.jsonl --bulk brreg-enheter.csv --output out/envelopes.jsonl --report out/run-report.json --run-id <id> --expected-count <n>` |
| 100-company smoke test | `smoke/` (envelopes.jsonl, run-report.json, changes.jsonl, site/) |
| Models / APIs | **No LLM.** Keyless official/open APIs only: Brønnøysundregistrene Enhetsregisteret + Regnskapsregisteret + update feeds + signature/prokura + Støtteregisteret (NLOD 2.0), GLEIF (CC0), Wikidata (CC0), NAV Arbeidsplassen job feed (public token, NAV API terms), company-owned websites (robots.txt honoured). See README "Models, APIs and licences". |
| Expected cost per official run | **$0.00** third-party cost. Requests: about 8–10 per company for 100 companies; the default hard cap is 2,000 per run (see below). Runtime: about 5 min per 100 companies, and about 9 min for 1,000 companies at a 2,000-request cap. |
| Secrets | none (no API keys) |
| Declared caches | `cache/` (see CACHES.md): site-discovery leads, NAV jobs, Wikidata P2333 index, registry shared-website hosts, public records |
| Contact for results | *(your e-mail)* |

## Budget flags

The only published numbers are 45 minutes and 2,000 outbound requests per daily batch, so the defaults are
`--max-requests 2000 --time-budget 40`. If Builderr confirms a larger request budget for the
1,000–1,100-company batch, raise it with `--max-requests N` or the env var `NCA_MAX_REQUESTS=N` in the run
command. More budget adds website/news/state-aid/signature checks for more companies.

**Ask Builderr** (inquiries@builderr.ai / Discord) before 17 Oct:
1. Is the 2,000-request cap per 100 companies or per official batch (1,000–1,100)? Do Brønnøysund registry
   calls count?
2. Do they pass the frozen registry snapshot to the command (`--bulk`)? What is the exact invocation and output path?

## Before submitting
```bash
uv run pytest -q                                  # all tests pass
./run.sh batches/smoke-100.jsonl smoke            # regenerate the smoke test
uv run nca validate smoke/envelopes.jsonl --input batches/smoke-100.jsonl
git add -A && git commit -m "Signalpost submission" && git rev-parse HEAD
```
Clean-machine check (what Builderr does): clone at the commit into a new folder, then run `./run.sh batches/smoke-100.jsonl /tmp/check`.
