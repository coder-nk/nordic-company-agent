# Evaluation

All runs use random samples of the public 411,160-company Signalpost universe (`nca sample`, fixed seeds):
`batches/smoke-100.jsonl` (seed 20261013), `batches/profiles-1000.jsonl` (seed 20261014),
`batches/dev-50.jsonl` (seed 7). Development used `dev-50`; `smoke-100` and `profiles-1000` were held out
for measurement.

## Contract and robustness (automated)
* `nca validate` checks: exactly one envelope per input, in input order; unique ids; terminal status; every
  available claim has resolvable evidence with a source URL and retrieval time; missing values are never
  encoded; every financial claim has a reporting period.
* `uv run pytest -q`: 277 offline tests (module parsers on recorded responses, the identity gate on 18 hand-labelled
  website fixtures, refresh idempotency, carry-forward, bulk paths, input parsing).
* Failure drills: SIGTERM mid-run still writes all envelopes, with unfinished modules labelled
  `failed: not checked: run interrupted`. A 30-second budget still yields valid envelopes. A tarpit host is cut off by
  the per-request wall-clock cap. Placeholder envelopes are written before research, so even a hard kill leaves N lines.
* Clean-clone check: `git clone` → `./run.sh` works with only `uv` installed.

## Precision audits (independent reviewers, adversarial)
* 100-company smoke run, mechanical checks over all claims:
  * 10,414 available claims, all with resolving evidence.
  * All 5,700 accounts values equal the raw JSON number exactly (Decimal), with the right period, scope, currency and org-number echo.
  * Live re-check of 15 random companies against Brreg: 349/350 comparisons matched (the remaining one is an intentionally
    unpublished "00.000 unspecified" NACE code).
  * No birth dates in envelopes or snapshots.
  * Every published website independently confirmed to print the company's org number.
* Findings fixed after the audit:
  * stale LEI parents from lapsed LEI records (now not published);
  * the summary headline now uses the latest filed period and names its scope;
  * 225 evidence spans were rebuilt rather than copied (now all cut verbatim from the stored source);
  * meaningless growth figures (now suppressed for partial first years and sign changes).

## Website identity-gate audits (independent, adversarial, live re-checks)
| round | sample | published sites | wrong-company websites | wrong contact/social facts | action |
|---|---|---|---|---|---|
| 1 | 60 sites from profiles-1000 (first gate) | 60 | 3 (+3 doubtful) | several (regional/foreign mailboxes) | gate rules: keep locale base path after cross-domain redirects; sister/foreign-entity and shared-host rules; case-sensitive legal names; shared registry contacts don't corroborate; generic mailboxes only. L2 refused on any group site. |
| 2 | 250 fresh companies with discovery leads | 240 (all L1) | 3 (+2 doubtful) | 7 | org numbers inside group-company lists and misnamed hits no longer prove identity; parent © footers; foreign HQ blocks; foreign phone prefixes; authority and invoice mailboxes; truncated handles |
| 3 | 300 fresh (150 registry-site-only, 150 with leads) | 172 (150 L1, 22 L2) | 5 (+3 doubtful) | 4 (+9 doubtful) | structural policies: L1 needs a visible labelled proof; no sections of foreign or group roots, chains or platforms; foreign and group signals on all pages; L2 needs an exact registry contact, a visible full legal name and a privacy/terms controller check; conservative phones and e-mails (no shared switchboards, postboxes or sister contacts); description filters; stricter handles |
| 4 | 300 fresh (same mix) | 130 (127 L1, 3 L2) | 2 (+2 doubtful) | 4 | decisive policies: exclusivity (no other org numbers on any fetched page except in data-processor or agency context), brand alignment, acquisition and ownership phrases, the controller check also for L1, impostor/template pages, contacts only from the company's own block or equal to the registry, logos on own domain only, every span must contain its value |
| 5 | 500 fresh | *pending* | | | |

## Refresh
* Re-running 50 companies against their previous envelopes: **0 changes** (idempotent). Sources disabled or
  failed in the second run were carried forward as `stale`, never reported as removed.
* Synthetic change test (CEO and revenue modified): typed `modified` changes with old and new values, and
  evidence for both sides preserved.

## Coverage and cost (measured)
| run | companies | requests | wall clock | verified websites | notes |
|---|---|---|---|---|---|
| smoke-100 (cap 2,000) | 100 | 969 | 4.4 min | 8 (6 L1, 2 L2) | all modules ran; accounts 100/100, roles 100/100, workplaces 73, state aid 14, LEI 12 |
| profiles-1000 (cap 2,000) | 1,000 | 2,000 | 8.7 min | 60 (37 L1, 23 L2) | bulk mode for roles/workplaces (2 requests); accounts 996; extras budget-starved and labelled |

Third-party cost: $0.00 in every run.

Website coverage depends mostly on the declared discovery cache: registry-filed sites alone cover about
11% of the universe, and only some of those print the org number. The overnight crawl adds leads from
e-mail domains and legal-name domains. The table will be updated from the final runs.
