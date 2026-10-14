# Evaluation

All runs use random samples of the public 411,160-company Signalpost universe (`nca sample`, fixed seeds):
`batches/smoke-100.jsonl` (seed 20261013), `batches/profiles-1000.jsonl` (seed 20261014),
`batches/dev-50.jsonl` (seed 7). Development used `dev-50`; `smoke-100` and `profiles-1000` were held out
for measurement.

## Contract and robustness (automated)
* `nca validate` checks: exactly one envelope per input, in input order; unique ids; terminal status; every
  available claim has resolvable evidence with a source URL and retrieval time; missing values are never
  encoded; every financial claim has a reporting period.
* `uv run pytest -q`: 386 offline tests (module parsers on recorded responses, the identity gate on 18 hand-labelled
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
| 5 | 500 fresh | 242 (238 L1, 4 L2) | 3 (+2 doubtful) | ~10 (theme demo text, shared phones) | removed the last "own block on a group site" rescue: any group, foreign, sister or ownership signal → ambiguous; subdomains inherit the registrable domain's co-filers; «vi/oss» definitions in privacy pages; co-filers sharing the brand token; theme/demo filters; shared phones never published |
| 6 | 600 fresh (3 auditors) | 358 | 1 (+8 doubtful) | ~27 (registry contacts shared by property managers/groups, theme and CMS text, non-registry addresses) | sub-paths need registrable-domain alignment; ownership/chain spelling variants; NUF (foreign branch) group signals; proofs only from fetched URLs; contacts only when filed in Enhetsregisteret by this company alone; website addresses only when equal to the registry address; CMS/theme/spam/demo filters; a whole RSS feed is rejected if any item fails; registry contacts shared with other org numbers published as `related` |
| 7 | final deliverables (`smoke/`, `profiles/`) | 65 | see below | | final check after the round-6 rules |

## Refresh
* Re-running 50 companies against their previous envelopes: **0 changes** (idempotent). Sources disabled or
  failed in the second run were carried forward as `stale`, never reported as removed.
* Synthetic change test (CEO and revenue modified): typed `modified` changes with old and new values, and
  evidence for both sides preserved.

## Coverage and cost (measured, final deliverables)
| run | companies | requests | wall clock | verified websites | notes |
|---|---|---|---|---|---|
| `smoke/` (smoke-100, cap 2,000) | 100 | 778 | 3.7 min | 5 (all L1) | all modules ran live, none budget-skipped; accounts, roles, workplaces 100/100; state aid 14 companies (81 grants, live search); LEI 12; food inspections 10; news items 6 |
| `profiles/` (profiles-1000, cap 2,000) | 1,000 | 2,000 | 7.2 min | 60 (59 L1, 1 L2) | bulk mode for roles and workplaces (2 requests, 220 MB streamed); accounts 997; state aid 690 grants, SkatteFUNN 95, Research Council 38, food inspections 84 (declared caches); LEI 123; 56 social profiles; 75 news items; optional extras skipped by budget and reported (`account_copies` 997, `authorisations` 810) |

Third-party cost: **$0.00** in every run (no LLM, no paid API, no keys).

Website coverage depends mostly on the declared discovery cache. Registry-filed sites cover about 11% of
the universe, and only some of them print the org number. The gate publishes a site only on proof:
76 of the 1,000 profiles have a candidate site that was found but withheld as `ambiguous` (group, chain,
platform or shared sites, or no visible proof), rather than risk a wrong-company publication.
