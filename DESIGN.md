# nordic-company-agent — design and module contract

Deterministic (no LLM, no paid API, $0) Signalpost agent: organisation number in → one terminal
**envelope** out, with claim-level evidence, availability states, refresh diffs and a supported summary.
Research notes live in `research/*.md` (read the one for your module) and raw API samples in
`research/samples/`.

## Layout

```
src/nordic_company_agent/
  orgnr.py          MOD11 validation / normalisation / org-number regexes          (done)
  http.py           BudgetedClient: counts EVERY request (redirect hops, retries,
                    robots.txt) against a hard cap; per-host gates; SSRF guard
                    (public_only=True); robots.txt (respect_robots=True)           (done)
  envelope.py       Evidence, Claim, ModuleOutcome, CompanyResult, field families  (done)
  context.py        RunConfig, RunContext (ctx.get/ctx.post/ctx.allow, snapshots,
                    caches, planner tiers)                                          (done)
  registry_csv.py   Brreg bulk CSV/JSON snapshot -> API-shaped dicts               (done)
  web/extract.py    page signals: org numbers, contacts, socials, JSON-LD, parked  (done)
  web/candidates.py candidate domains (registry site, e-mail domain, name slugs)  (done)
  sources/          one file per source module (contract below)
  pipeline.py       batch orchestration, planner reservations, envelope assembly
  refresh.py        diff vs previous envelopes (stable claim keys, idempotent)
  synthesis.py      templated, evidence-cited summary
  report.py / cli.py / viewer.py / server.py
tools/              offline declared-cache builders (site discovery, NAV jobs, ...)
cache/              declared caches shipped with the repo (see CACHES.md)
```

## Module contract (sources/*.py)

Per-company module:
```python
async def run(ctx: RunContext, company: CompanyResult) -> None
```
Batch module (one call for the whole batch, for multi-id endpoints):
```python
async def run_batch(ctx: RunContext, companies: list[CompanyResult]) -> None
```
Rules:
1. **Never raise.** Catch everything; on failure call `company.error(module, "failed", msg, url)` and set
   `company.outcome(ModuleOutcome(module, "failed", note=...))`.
2. **Budget:** before each request call `ctx.allow(tier, n)`; if False do not fetch, set outcome
   `failed` with note `"not checked: request budget reserved for higher-priority sources"`.
   Fetch only through `await ctx.get(url, tier=..., source=<module>, headers=...)` / `ctx.post(...)`.
   Company-controlled URLs (websites) must pass `public_only=True, respect_robots=True`.
3. **Inputs:** `company.data["registry"]` is the API-shaped Enhetsregisteret record (dict, same shape as
   `GET /enheter/{orgnr}`), present whenever `company.data["identity"]["match_status"] == "exact"`.
   `company.data["registry_evidence"]` is the `Evidence` for that record. Other modules publish their
   parsed data in `company.data[<module>]` (e.g. `company.data["subunits"] = [...]`).
4. **Outputs:** append `Claim`s with `company.add([...])` and set exactly one terminal
   `ModuleOutcome` per module: `available` (found data), `not_available` (looked, nothing there),
   `not_applicable` (question does not apply, e.g. accounts for an entity type that never files),
   `blocked` (source refused: 403/429/robots), `ambiguous` (could not tie to this exact entity),
   `failed` (error / not checked).
5. **Evidence:** every available claim carries ≥1 `Evidence` with: exact `source_url` fetched (for
   multi-id batch calls set `source_url` to the canonical single-entity URL that returns the same
   record and put the real request in `retrieved_via`), `page_url` (human page, e.g.
   `https://virksomhet.brreg.no/nb/oppslag/enheter/{orgnr}`), `retrieved_at` (ISO UTC 'Z'),
   `content_sha256` (sha256 of the raw body; get it from `ctx.snapshots.save_result(res, retrieved_at)`),
   `locator` (JSON pointer like `/forretningsadresse/adresse/0`, or `css:footer`/`text` for HTML),
   `claim_span` (verbatim fragment of the source containing the value, ≤300 chars; for JSON a compact
   snippet like `"navn":"EQUINOR ASA"`), `extraction_method` (`<module>@<version>`),
   `effective_at` when the source dates the value, `licence`.
6. **Never fabricate.** Copy values exactly; parse money with `Decimal`; never rescale; a missing key is
   `not_available`, never 0. Derived values (county from municipality number, equity ratio) must set
   `match_basis="derived"`, `method=` the rule, and cite the input evidence.
7. **Exact entity only.** Claims about another entity (parent, auditor firm, subunit) carry
   `relation=` (`parent`, `ultimate_parent`, `subunit`, `related`) and are never presented as the
   company's own facts. Website facts require the identity gate (see website section).
8. **Personal data:** names + role + role group + last-changed date of the input entity's current
   roles only. Never output or store birth dates: remove `fodselsdato` from role payloads *before*
   `ctx.snapshots.save(...)` (pass `sha256=` of the original bytes and `redacted=True`). No
   person-centric lookups. No personal e-mails (`fornavn.etternavn@`) from websites.
9. **Determinism:** same source bytes ⇒ same claims (order, ids). Sort multi-valued claims.
10. **Tests:** each module ships pytest tests under `tests/` that run offline from fixtures in
    `tests/fixtures/<module>/` (copy what you need from `research/samples/`). Use `respx` or a fake
    `ctx` to avoid network. `uv run pytest -q` must pass.

## Claim fields and value shapes

Field names are fixed (see `envelope.FAMILIES`). Qualifier (stable-key suffix) in brackets.

| field | value | notes |
|---|---|---|
| organisation_number | "923609016" | |
| legal_name | str | `former_name` [date] from `historiskeNavn` |
| legal_form | {"code","description"} | |
| registry_status | "active" \| "bankrupt" \| "under_liquidation" \| "forced_dissolution" \| "deleted" | plus `bankruptcy` {"date"}, `liquidation` {"date"}, `forced_dissolution` {"date","reason"} |
| business_address / postal_address | {"lines":[..],"postal_code","city","municipality","municipality_number","country","country_code"} | display "Strandgata 21, 4307 SANDNES" |
| municipality | {"name","number"} | county: derived from municipality number (2024 county codes) |
| industry [1/2/3] | {"code","description","rank"} | SN2025 codes as served |
| sector | {"code","description"} | institusjonellSektorkode |
| founding_date / registration_date | "YYYY-MM-DD" | 1900-01-01 = placeholder → skip |
| business_register_registration / vat_registration / voluntary_register_registration / foundation_register_registration | {"registered": bool, "date"} | |
| employees | {"count": int, "as_of": date} | `harRegistrertAntallAnsatte` true + no count → {"count": null, "range": "1-4"}; false → not_available |
| share_capital | {"amount": Decimal, "currency", "type", "shares", "since"} | |
| statutory_purpose / activity | str (lines joined with spaces) | |
| registry_website / registry_email / registry_phone / registry_mobile | str | as filed |
| registry_annotation [code] | {"code","text"} | paategninger |
| audit_exemption | {"date","decision_date"} | fravalgRevisjon* |
| group_membership | bool | erIKonsern |
| parent_company / ultimate_parent (relation set) | {"organisation_number","name","ownership","since"} | konsernstruktur |
| group_subsidiary [orgnr] | same shape | direct children |
| signature_rule / prokura_rule | {"text", "holders":[names]} | fullmakt API |
| ceo, chair, deputy_chair, board_member [name], deputy_board_member [name], observer [name], auditor, accountant, owner, partner [name], general_partner, contact_person, business_manager, norwegian_representative, role_other [code|name] | {"name","role_code","role","group_code","group","last_changed","elected_by"?,"organisation_number"? (entity holder)} | current roles only (`avregistrert` excluded) |
| registered_workplace [orgnr] | {"organisation_number","name","address":{..},"industry":{..},"employees","started"} | relation="subunit"; plus `registered_workplace_count` int |
| accounts: revenue, sales_revenue, operating_expenses, payroll_expenses, operating_profit, net_financial_items, financial_income, financial_expenses, profit_before_tax, tax_expense, net_profit, total_comprehensive_income, total_assets, fixed_assets, current_assets, cash_and_bank, inventories, receivables, goodwill, equity, paid_in_equity, retained_earnings, total_liabilities, current_liabilities, long_term_liabilities [year\|scope] | Decimal | `period={"start","end","year","scope":"company"\|"group"}`, display "NOK 790 400 865"; currency in `accounts_currency` [year\|scope] and in display |
| accounts_period [year\|scope] | {"start","end","year","scope","currency","framework","audited","small_company","record_id"} | |
| latest_submitted_accounts_year | int | sisteInnsendteAarsregnskap |
| accounts_filing_years | [int] | optional copy API |
| official_website | "https://host/" | requires identity gate |
| website_verification | {"level","proof_url","proof_span","method"} | |
| website_title / website_description / brand_name / logo_url / website_language | str | from verified site only |
| contact_email [addr] / contact_phone [e164] / website_address | str | verified site; generic role mailboxes only |
| social_profile [platform\|url] | {"platform","url","handle"} | linked from verified site (or Wikidata P2333 match + site corroboration) |
| careers_page | url | verified site |
| news_item [url] | {"title","url","published"} | verified site RSS/Atom/sitemap |
| job_posting [nav:uuid] | {"title","url","published","expires","employer_name","employer_organisation_number","location","positions"} | NAV; employer orgnr == company or one of its subunits |
| active_job_count | int | only when the jobs source was actually checked |
| registry_event [update_id] | {"date","change_type","fields":[..]} | Enhetsregisteret update feed |
| role_change_event [event_id] | {"date","type"} | roles update feed |
| state_aid_grant [id] | {"date","amount","currency","grantor","scheme","purpose"} | Støtteregisteret |
| lei / lei_status / lei_parent | str / {"status","registration_status","next_renewal","managing_lou"} / {...} | GLEIF registeredAs == orgnr |
| wikidata_item / encyclopedia_entry [lang] / stock_listing [ticker] | {"id","url"} / {"language","url","title"} / {"ticker","exchange","isin"} | Wikidata P2333 == orgnr |

## Website identity gate (sources/website.py, web/verify.py)

- **L1 (publish, conf ≈0.97):** target org number printed on a fetched page of the site (text, HTML,
  or a link to its registry page), page live, not parked/bot-wall/hijacked (`extract.placeholder_signals`).
  If the site also prints *other* org numbers (group/agency/processor), publish contact facts only from
  the smallest DOM block that contains the target number and no other (`extract.orgnr_block`).
- **L2 (publish, conf 0.85):** registry-filed `hjemmeside` with no other company's org number on the site,
  every distinctive name token prominent (title/og:site_name/JSON-LD name/H1/footer), ≥1 registry
  corroboration (phone, e-mail, e-mail domain, postcode+street, full legal name) and not an
  address-named SPV / c/o manager site (see `research/scripts/web_policy.py`).
- Everything else: `official_website` claim with availability `ambiguous` (candidate seen, not proven)
  or `not_available` (nothing found) — never publish facts from it. A dead/parked registry site:
  `not_available` with a note; a 403/robots refusal: `blocked`.
- Lead sources: (1) `cache/site_discovery.jsonl.gz` leads (domains where the number was printed at
  crawl time — re-fetch and re-verify live), (2) registry `hjemmeside`, (3) live candidates
  (`web/candidates.py`) only when budget allows (`tier="discovery"`).

## Spending tiers (context.TIERS)

registry → batch_external → accounts → website_primary → roles → subunits → group →
website_secondary → discovery → extras. The pipeline sets reservations from the batch size and the
`--max-requests` budget; modules just ask `ctx.allow(tier, n)`.
