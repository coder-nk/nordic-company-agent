# Signalpost — participant brief

Status: Open. Round 1 runs from 23 August through 21 October 2026. Random daily evaluation begins 24 August.

## What to build

Build an agent that finds company information online.

Give it a Norwegian company number. It should search company websites, public registries and other permitted sources, then return a company profile with links to the facts it found. Check that each fact belongs to the right company, include its source and date, and clearly mark information you could not find. Run it again to keep the profile current.

## What to submit

Submit your agent's code, run instructions and a 100-company smoke-test result. You may use the public company list at any scale, but you do not submit precomputed profiles or a company manifest for scoring. The full submission checklist is below.

## How we test it

Builderr supplies an official company batch after the cutoff. Every eligible frozen entry receives the same current batch, including when the shared set grows from 1,000 to 1,100 companies. Your agent must handle company numbers it has not researched before.

## How you score

We combine independently checked findings from all submissions and Builderr's own crawlers into one reference collection. New verified findings update the collection, and every entrant is rescored against the same version. This is the checked information we have found, not a claim that we found everything online.

For each information type, 70% of its coverage score measures how many companies you covered and 30% measures how many individual facts you found. Example: the collection has 50 job postings across 20 companies. Finding 30 postings across 15 companies gives 60% of postings and 75% of companies: 70% × 75% + 30% × 60% = 70.5% for that information type. This feeds the 50 recall and coverage points; it is not the total score.

What to optimize: first make sure every fact belongs to the right company and has evidence. Then increase how much checked information you find. A high score cannot make up for a material wrong-company match.

The remaining points check whether each fact belongs to the right company and has a source (30), whether the profile gives useful supported explanations (12), and whether someone can find and verify the information (8). An entry qualifies with an official run and at least 65/100 overall. Coverage, recall and precision are scored dimensions, not separate qualification thresholds. Company-matching precision is separate from factual accuracy; fabricated financial values or a material wrong-company publication prevent the run from becoming official.

The exact requirements follow.

## What the company profile should show

The product should help someone understand a company before they apply, sell, partner or invest: what it does, who leads it, where it operates, how its latest filed numbers look, whether it appears to be hiring, and what dated public activity the checked sources reveal.

Your agent must find sources for the right company, show evidence for its facts and update the profile when the information changes.

## Universe and run format

- Universe: Norway-registered entities with observed official annual-account records.
- Public universe: all 411,160 eligible organisation numbers in the frozen 2025-filer snapshot.
- Local scale test: run your agent on 100 companies before submitting. You may test on 1,000 companies or more, but those outputs are not part of the score.
- Official evaluation: Builderr chooses the current shared company batch after the cutoff; every eligible frozen entry receives that same batch and must return one result per input.
- Completed runs add to a cumulative, locked checked reference set used when scores are updated.
- Daily schedule: the current shared company set per scheduled test day through 21 October; it is 1,000 companies now and may grow to 1,100.
- Builderr supplies organisation numbers, cutoff and output contract—not the companies' official sites or social identities.
- Every frozen submission receives the same batch and resource budget. A timeout or missing result is not scored; a Builderr infrastructure failure is rerun.
- Full public universe: [`signalpost-company-universe-2025.jsonl.gz`](../signalpost-company-universe-2025.jsonl.gz).
- Frozen eligible universe: 411,160 active entities whose latest submitted annual-account year was 2025. Uncompressed content SHA-256: `b82d6a3e7231d1759a958c282bc4366b80ec2fab8095053d8ed7fa9cd01bc838`. Download archive SHA-256: `1c89710e5b01f8617e86d09fbdff4a52f2f8dbbba297e74f7164b5984f5a0384`.

## Required company envelope

Every input must end in one terminal envelope, even when sources are missing or blocked.

Required sections:

1. Legal identity and public brand
2. Latest annual accounts and available history
3. Leadership and registered workplaces
4. Verified official website and company-owned profiles
5. Hiring and dated public activity from permitted sources
6. Claim-level evidence and availability state
7. Refresh metadata and material changes since the previous run

Use explicit states such as `available`, `not_available`, `blocked`, `not_applicable`, `ambiguous` and `failed`. Never turn absence into zero.

## Scoring — 100 points

Scoring version 2 applies to every Round 1 entrant from 26 August 2026. All active submissions are rescored under the same rubric and current pooled-evidence version.

- 50 — Recall and coverage: how much information did you find? For each information type, 70% measures company coverage and 30% measures individual facts found.
- 30 — Is the information correct? We check that each fact belongs to the right company and has a valid source and date.
- 12 — Is the summary useful? It should explain the company, changes and unknowns without making unsupported claims.
- 8 — Is it easy to use and verify? A user should be able to find, compare and verify the information on desktop and mobile.

For each external field family, its coverage score is 70% company recall and 30% individual-claim recall against the independently verified union of discoveries from every submitted crawler and Builderr’s own crawlers. The union grows when any agent contributes a new verified claim; each new version is hashed and every entrant is rescored against it. The final union freezes after final-submission verification.

Qualification requires an official run and 65/100. Coverage, recall and precision contribute to the score; they are not separate qualification thresholds. If the verified pool has fewer than 15 positive company-field opportunities across at least three external field families, recall is reported as not measured for that batch rather than as 0%. Final ranking uses the mean across every scheduled daily batch while your frozen version is active. An entrant-caused failed or missed batch scores zero after a clean reproduction; a Builderr harness, infrastructure or shared-source failure is void and rerun with the same code.

## Official run requirements

- Builderr supplies the current shared input set and a fixed time and resource budget for each official run.
- Return exactly one terminal envelope for every supplied company.
- If your agent times out or drops results, that run is not scored. A Builderr infrastructure failure is rerun.
- Your first submission is version 1; you may send up to four revised exact commit hashes by 18 October, for five versions total

## Official-run checks

- No fabricated financial values or material wrong-company publication
- Submitted public artifact includes a 100-company smoke-test result or report
- Exactly one terminal envelope for every company in each official batch
- Claim-level source, retrieval time and reporting period where relevant
- Honest availability states
- Idempotent refresh with prior snapshots preserved
- Reproducible setup, pinned dependencies and one evaluator command
- Declared source rights, server-side secrets and safe URL handling

Your score and qualification are separate. A small factual mistake lowers accuracy. A material wrong-company match can contaminate an entire profile, so the score remains visible but the run cannot become official until that match is corrected. It is better to miss some information than publish it under the wrong company; return `ambiguous` or `not_available` when the company match is uncertain.

## Rewards

- $2,000 main final pool: $1,200 / $500 / $300
- $500 JBOX bonus pool: $250 / $150 / $100
- Four separate $100 community-vote awards on 6 September, 20 September, 4 October and 18 October

Public voting does not alter the technical ranking. Only technically qualified agents enter the hosted gallery.

## Beyond the prize

The winning builder gets the opportunity to partner with [Håvard Liltved Dalen](https://www.linkedin.com/in/liltved/) to launch Signalpost in Norway. Håvard is a Norwegian serial entrepreneur, CPO at Fronted and co-founder of JBOX.

## Start here

Try one saved example first. Requires Python 3.12+; no API key or company-data download is needed.

```bash
curl -LO https://builderr.ai/signalpost-starter-kit.tar.gz
tar -xzf signalpost-starter-kit.tar.gz
cd signalpost-starter-kit
python3 scripts/run_refresh_replay.py \
  --manifest tests/fixtures/refresh-snapshots.json \
  --output out/refresh-demo.json
```

Open `out/refresh-demo.json` to inspect the changes and source evidence. This uses saved responses and does not qualify a competition entry.

Then follow `README.md` to install dependencies and try 100 live companies. It also explains how to test at larger scale; Builderr supplies the companies for official scoring.

- [Download the runnable reference agent](../signalpost-starter-kit.tar.gz)
- [Download the full 411,160-company universe](../signalpost-company-universe-2025.jsonl.gz)
- [Agent playbook](./signalpost-agent-playbook.md)
- [Learning harness](./signalpost-learning-harness.md)
- [Source policy](./signalpost-sources.md)
- [Evaluation contract](../docs/signalpost-evaluation-harness.md)
- [100-company product sample](https://builderr.ai/signalpost)

## Submit

Email `submit@builderr.ai` with the repository URL, exact commit hash, 100-company smoke-test result or report, one run command, models/APIs/licences, expected cost per official batch, agent name and contact for results.
