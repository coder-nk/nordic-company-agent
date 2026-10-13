# Connectors, budgets and fallbacks

Every request goes through `http.BudgetedClient`. It counts every HTTP request, including redirect hops,
retries and robots.txt fetches, against `--max-requests` (default 2,000). It also enforces per-host
concurrency and spacing, timeouts, body caps, no cookies, and an SSRF guard for company-controlled URLs.
A global wall-clock guard stops gathering early enough to write every envelope within `--time-budget`
(default 40 min).

## Spending tiers (priority order)
`registry → batch_external → accounts → website_primary → roles → subunits → group → website_secondary →
discovery → extras`. Reservations are sized from the batch after identity is known. A lower tier can borrow
only what no unfinished higher tier still needs.

| module | requests | batching / fallback |
|---|---|---|
| registry | ⌈N/1000⌉ + follow-ups for numbers missing from the batch | `/enheter?organisasjonsnummer=<≤1000>`; optional `--bulk` snapshot (0 requests) |
| registry_feed | ~2 per 500 | update feeds with org-number lists (`includeChanges=true`) |
| gleif | ⌈N/100⌉ (+ parents) | both org-number spellings per request |
| wikidata | 0–⌈N/150⌉ | only for numbers present in `cache/wikidata_p2333` |
| jobs | 1 token + a few feed pages + ≤20 entries | declared cache + forward paging from its cursor |
| accounts | 1 per filer | 404 / financial-institution 500 → `not_available` (never retried) |
| roles, subunits | 1 each per company, **or** 2 for the whole batch | bulk mode (`--bulk-mode auto`): nightly `roller/totalbestand` and `underenheter/lastned` when the budget cannot cover per-company calls |
| group | 1 when `erIKonsern` | `konsernstruktur` |
| website | ~2–5 per company with a lead | discovery cache → registry site → Wikidata P856 → live candidates (DNS-checked) |
| authorisations, state_aid, account_copies | extras, only with leftover budget | copy API rate limit (~30/min) respected |

With 100 companies at the default budget everything runs (about 750 requests, about 3 minutes). With
1,000 companies at 2,000 requests, bulk mode, batch endpoints and tiers keep identity, accounts, roles,
workplaces and verified websites. Extras are dropped first, and each is reported as
`failed: not checked: request budget …` rather than silently missing.
