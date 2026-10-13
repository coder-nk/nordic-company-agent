# Signalpost (Builderr) challenge: spec, target field list, source permissions, refresh

Researched 2026-10-13. All builderr.ai documents were downloaded verbatim and are saved under
`research/samples/spec_and_fields__*` (see section 9). The full starter kit is unpacked in `research/builderr_starter_kit/`
(minus uv.lock). The public 411,160-company universe is at `data/signalpost-company-universe-2025.jsonl.gz`
(12.6 MB; it is a large binary, so consider gitignoring `data/`).

---------------------------------------------------------------------------------------------------

## 0. TL;DR

* The challenge is **"Signalpost"** on builderr.ai. Main page: https://builderr.ai/challenges/signalpost.
  Machine spec: https://builderr.ai/api/challenge/signalpost. Unstop mirror (the text the user pasted):
  https://unstop.com/hackathons/signalpost-build-an-agent-that-finds-company-information-builderrai-1754883
  (JSON: https://unstop.com/api/public/competition/1754883).
* **Two spec versions conflict** (details in section 1.3):
  * Unstop and the top-level `scoring` string in builderr `tasks/signalpost.json` say: 35/30/20/10/5, **100 random companies per day**,
    **45 min / 2,000 outbound requests / $10**, plus a 21/35 coverage floor.
  * The current builderr.ai pages ("scoring version 2", effective 26 Aug 2026) say: **50 recall / 30 precision / 12 synthesis / 8 UX**.
    The official batch is **1,000 companies now and may grow to 1,100**. The budget is described only as "a fixed time and resource budget"
    with no numbers. There is **no separate coverage floor**; only 65/100 overall.
  * Design for the intersection: one batch command, budget-aware, that finishes 100 companies easily within 2,000 requests and 45 min.
    It must also degrade gracefully on 1,000–1,100 companies under an unknown cap (registry bulk files plus multi-ID batch endpoints).
    Make the caps CLI flags.
* **Dates.** Unstop registration and project link are due **17 Oct 2026, 23:59 IST**. Builderr accepts code revisions until **18 Oct**
  (version 1 plus up to 4 revised commit hashes). The competition runs **through 21 Oct**.
* **Leaderboard (reviewed 5 Oct 2026).** 37 submissions, 28 assessed, **0 qualified**, best score 60.80.
  * Almost everyone scores about 27/30 on evidence, 12/12 on synthesis and 8/8 on UX.
  * **Recall is the bottleneck: 5–17.45 out of 50.**
  * Recall is measured on **external field families** (website, profiles, jobs, activity, and so on) against a pooled checked collection.
  * To qualify we need recall of roughly 18–20/50 while keeping zero wrong-company publications.
* **Permitted sources:**
  * Brreg open data (NLOD 2.0).
  * The company's own verified website and the profiles it links to.
  * Official or licensed APIs.
  * Wikidata (CC0), GLEIF (CC0).
  * The NAV job feed, through a public rotating token; terms required.
* **Exclude** proff.no / forvalt.no, purehelp.no, 1881.no, gulesider.no, regnskapstall.no, Norid RDAP/whois, LinkedIn, Meta, Glassdoor and Indeed.
  Their terms forbid systematic collection; proff and gulesider also return 403 from here.
* **Person-data policy:** publish role holder name + role + role group + last-changed date for the target entity only.
  **Never publish birth dates.** Never build cross-company person profiles (enhetsregisterloven § 9-1(3)). Skip private-person enrichment.

---------------------------------------------------------------------------------------------------

## 1. Challenge spec (Signalpost, Builderr Round 1)

### 1.1 Where it is published

| Doc | URL | Saved as |
|---|---|---|
| Challenge page | https://builderr.ai/challenges/signalpost | `spec_and_fields__builderr_challenge_page_text.txt` |
| Participant brief | https://builderr.ai/starter-briefs/signalpost.md | `spec_and_fields__builderr_brief_signalpost.md` |
| Evaluation contract | https://builderr.ai/docs/signalpost-evaluation-harness.md | `spec_and_fields__builderr_evaluation_contract.md` |
| Source policy ("Permitted sources") | https://builderr.ai/starter-briefs/signalpost-sources.md | `spec_and_fields__builderr_source_policy.md` |
| Agent playbook | https://builderr.ai/starter-briefs/signalpost-agent-playbook.md | `spec_and_fields__builderr_agent_playbook.md` |
| Learning harness | https://builderr.ai/starter-briefs/signalpost-learning-harness.md | `spec_and_fields__builderr_learning_harness.md` |
| Machine spec | https://builderr.ai/api/challenge/signalpost | `spec_and_fields__builderr_api_challenge_signalpost.json` |
| Task feed | https://builderr.ai/tasks/signalpost.json | `spec_and_fields__builderr_tasks_signalpost.json` |
| Runnable starter kit | https://builderr.ai/signalpost-starter-kit.tar.gz | unpacked: `research/builderr_starter_kit/` |
| Public universe (411,160) | https://builderr.ai/signalpost-company-universe-2025.jsonl.gz | `data/signalpost-company-universe-2025.jsonl.gz` |
| 100-profile product sample (UX reference) | https://builderr.ai/signalpost | `spec_and_fields__builderr_public_sample_5profiles.json` (5 of 100 profiles from its `const DATA=`) |
| Unstop listing | unstop.com …-1754883 | `spec_and_fields__unstop_listing.json` |

builderr.ai/robots.txt is `Allow: /`. Hashes:
* Universe archive SHA-256 `1c89710e…0a5384`; uncompressed content `b82d6a3e…01bc838`. Both match the brief.

### 1.2 Verbatim key text

**Unstop (identical to the user's request):**
> "Every agent receives the same 100 random companies each day. Find useful information (35 points), match it correctly with evidence (30), update correctly (20), give useful explanations (10), and make it easy to use (5). … To qualify: at least 65/100 overall, 21/35 coverage, and all published accuracy, evidence and execution requirements. A material wrong-company publication or fabricated financial value fails the entry. … Daily limits: 45 minutes, 2,000 outbound requests and $10 in declared external API costs."
> What to Submit: "At least 1,000 company profiles / Your repository link / Exact commit hash / One command to run it / Model/API details / Expected run costs".
> Submission round: "Register and share your project link on Unstop by 17 October, 11:59 PM IST. Send the complete Signalpost entry to submit@builderr.ai. Builderr accepts code revisions until 18 October and runs the competition through 21 October."
> Prizes (Unstop): INR 120,000 / 50,000 / 30,000. Builderr: $1,200/$500/$300, plus a JBOX bonus of $250/$150/$100.

**builderr.ai challenge page (current):**
> "Scored on 50 recall · 30 precision · 12 synthesis · 8 UX"
> "Build a company research agent. Collect company details, people, locations, financial results, jobs and public activity where available. Check each fact belongs to the right company. Link it to its source and date. Clearly mark anything you could not find. Keep profiles current. Check sources again, show what changed and preserve the earlier evidence."
> "Your starting list: 411,160 eligible Norwegian companies with 2025 annual-account records. Your agent must work from a company number, including companies it has not researched before."
> "Submit your agent and a 100-company smoke test. … Your repository link, exact commit hash and one command to run the agent. The models, APIs and licences you use, expected cost per official run, and contact details. … You do not need to submit precomputed profiles or a company manifest. Builderr supplies the companies for every official run."
> "Each official run has a fixed time and resource budget. Finish the run and return one result for every supplied company. If your agent times out or drops results, that run is not scored."
> "We compare every agent with the same checked collection. 70% measures how many companies you covered and 30% measures how many checked facts you found."
> "It is better to miss some information than publish it under the wrong company. When the company match is uncertain, return ambiguous or not available instead."
> States: "available — you found it / not_available — you looked, there is nothing there / blocked — the source refused the request / not_applicable — the question does not apply to this company / ambiguous — you could not be sure it is the right company / failed — the run broke on this one"
> "Refresh must be idempotent: the same source snapshot must not create duplicate records or false changes."
> "Exact tiebreak order: fewer wrong-company publications, then higher weighted company recall, then lower declared third-party cost."
> "You think you cannot use an LLM. You can. Each run has a small external API budget, and if you need a model key for scoring, ask and we will supply one. What we cannot use is a credential tied to your own account on another service, because we cannot reproduce your run with it."

**Brief, required envelope sections:**
> "1. Legal identity and public brand 2. Latest annual accounts and available history 3. Leadership and registered workplaces 4. Verified official website and company-owned profiles 5. Hiring and dated public activity from permitted sources 6. Claim-level evidence and availability state 7. Refresh metadata and material changes since the previous run"
> "The product should help someone understand a company before they apply, sell, partner or invest: what it does, who leads it, where it operates, how its latest filed numbers look, whether it appears to be hiring, and what dated public activity the checked sources reveal."
> "Daily schedule: the current shared company set per scheduled test day through 21 October; it is 1,000 companies now and may grow to 1,100."
> "Builderr supplies organisation numbers, cutoff and output contract—not the companies' official sites or social identities."

**Evaluation contract:**
> "Output contract: Exactly one terminal envelope per input organisation number. Envelopes must contain legal identity, claims, evidence references, availability states, source snapshots, refresh metadata and errors."
> "For every external field family, coverage is 70% company recall and 30% individual-claim recall against the independently verified union of discoveries from every submitted crawler and Builderr's own crawlers."
> "If the verified pool has fewer than 15 positive company-field opportunities across at least three external field families, recall is reported as not measured for that batch rather than as 0%."
> "Builderr provides the frozen official registry snapshot used for identity anchoring. External caches must be declared. Cached public-universe material is allowed, but the official score still enforces source timestamps, refresh behavior and the same evidence cutoff for everyone."
> "server-side secrets supplied through documented environment variables only"
> "Measurement: Report exact-company precision, wrong-company publications, per-field precision/recall/coverage, evidence-span validity, crawl completion, refresh correctness, false-change rate, cost per company, request count, and p50/p95 runtime. Abstention is reported separately and cannot satisfy coverage."

**Machine spec (`api/challenge/signalpost`):**
> interface.preferred_routes: `"/companies/:organisation_number", "/research", "/refresh"`;
> output: "one completed company record with a source for each fact, explicit missing-data labels, and update timestamps".
> submit.include: repository_url, exact_commit_hash, smoke_test_report_100, agent_name, contact_for_results, models_or_apis, expected_cost_usd.

**Source policy (verbatim excerpts):**
> Preferred official records: "Brønnøysundregistrene Enhetsregisteret bulk data and entity API … Regnskapsregisteret API and annual-account copies … Official roles endpoints … Official subunit records". Company-owned: "Verified official website / Sitemap, news, investor, careers, location and contact pages / Structured data embedded by the company / Social or video profiles linked by the verified company site, subject to the destination platform's access terms". External: "Official or licensed platform APIs / Search APIs used to discover candidates / Public pages whose terms and robots policy permit the submitted access pattern / Licensed news, review, jobs, traffic or company-data feeds".
> "Search results generate candidates; they are not claim evidence." "Group, parent, subsidiary, franchise and public-brand relationships must be labelled, not collapsed." "Every claim records source URL or source identifier, retrieval time, effective/reporting date where relevant, content hash and extraction method." "Company-owned promotional copy may describe the business but cannot provide an independent sentiment claim."
> "Do not scrape a platform when its terms, robots policy or applicable law prohibit the submitted method. This includes treating unofficial LinkedIn, Meta, Glassdoor, Indeed or Google clients as automatically acceptable simply because code exists on GitHub."

**Starter README, submission contract:**
> "a 100-company smoke-test result or report; one documented command that accepts a JSONL batch of organisation numbers; exactly one terminal envelope per input; pinned dependencies and reproducible setup; a previous-snapshot input and material-change output; a machine-readable run report with runtime, request count and third-party cost; declared models, APIs, licences and source-rights assumptions."

### 1.3 Conflicts and how to resolve them

| Item | Unstop / tasks.json `scoring` | builderr.ai v2 (brief, contract, page, api spec) | Our design target |
|---|---|---|---|
| Score split | 35 info / 30 match+evidence / 20 updates / 10 explanations / 5 UX | 50 recall / 30 precision+evidence / 12 synthesis / 8 UX | Both reward the same things; recall is the lever. |
| Coverage floor | 21/35 coverage | "not separate qualification thresholds" | Ignore the floor but maximise external recall. |
| Batch | 100 random/day (home page also says "Tested on 100 random companies per day") | "1,000 companies now and may grow to 1,100" | Must scale from 100 to 1,100. |
| Budget | 45 min, 2,000 outbound requests, $10 | "fixed time and resource budget" (unpublished) | Defaults: `--time-budget 40m --max-requests 1950 --max-cost 0`. Allocate per company = budget / N, priority-ordered. |
| Precomputed profiles | "At least 1,000 company profiles" | "You do not need to submit precomputed profiles" | Commit a 1,000-profile run plus a 100 smoke report anyway (cheap insurance). |

Other entrants interpret the cap differently:
* Ryugi62 caps non-registry requests at 1,950 and does not cap registry calls.
* AnSa30-06 uses 30 × N (their latest 1,000-company run used 9,361 requests in 19.1 min).
* anur4ag hard-caps at 1,900 with a 38-minute soft deadline.

**Recommendation:** email inquiries@builderr.ai or ask on Discord (https://discord.gg/ytBsTqkkes) whether registry calls count and what the cap is for a 1,000-company batch. Until we hear back, count every request and stay under 2,000 for 100 companies.

### 1.4 Run format, as implied by the reference agent (`builderr_starter_kit/scripts/run_competition_batch.py`)

* Input: `--organisations` given as .json (list or `{"organisation_numbers": [...]}`), .jsonl (`{"organisation_number": "..."}` per line), or text with one ID per line.
  Duplicates are rejected and IDs must be 9 digits.
* `--bulk brreg-enheter.csv` is the frozen Brreg CSV snapshot that Builderr says it provides ("frozen official registry snapshot").
* Outputs: `--output envelopes.jsonl` (one terminal envelope per input), `--profiles-output`, `--report run-report.json`, `--run-id`, `--expected-count`.
  Also checkpoint/resume support.
* The minimal envelope (`OUTPUT_CONTRACT.md`) is shown below. `claims[]` items are {field, value, availability, confidence, evidence_ids}.
  `evidence[]` items are {id, source_url, source_class, retrieved_at, content_sha256, claim_span}.

```json
{"organisation_number":"123456789",
 "run":{"run_id":"…","started_at":"…","completed_at":"…","terminal_status":"completed"},
 "claims":[{"field":"official_website","value":"https://example.no/","availability":"available","confidence":0.99,"evidence_ids":["ev-1"]}],
 "evidence":[{"id":"ev-1","source_url":"https://example.no/","source_class":"company_owned","retrieved_at":"…","content_sha256":"…","claim_span":"Example AS, organisation number 123 456 789"}],
 "changes":[],"errors":[],
 "operations":{"requests":4,"runtime_ms":8120,"third_party_cost_usd":0}}
```

* The playbook also requires per-claim effective or reporting date, extraction method, parser version and a snapshot selector/span.
* Refresh (`src/norway_company_agent/refresh.py`): diff previous vs current on tracked fields.
  * Tracked fields: registry name, form, employees, municipality, website, latest_submitted_accounts; financials.records; financial_history.years; roles; locations; website title/description/social_links.
  * Each change records old/new value, source_url, retrieved_at, effective_at, and old/new content hash.
  * The fixture `tests/fixtures/refresh-snapshots.json` expects exactly two changes (employees 2→3, financial records) and no false changes on re-run.
* UX reference: builderr.ai/signalpost, a static single-page app with a searchable company list, a coverage pill per company ("x/5 data"), and sections in this order:
  Company record, Financials (latest + PDF years), People & locations, Company website, Public footprint. Each has a "View source" disclosure showing URL + retrieved date.
  * The 5 coverage dimensions in their DATA are `company`, `financials`, `people_places`, `website`, `footprint`.
  * In their curated 100: website 97/100, footprint (handles) 52/100, LinkedIn 27/100. That LinkedIn capture is marked "rightsStatus: experimental".

### 1.5 What the universe looks like (implications for random batches)

The file `data/signalpost-company-universe-2025.jsonl.gz` has 411,160 rows with fields organisation_number, name, legal_form, employees, bankrupt,
liquidating, municipality, municipality_number, industry_code, industry_label, website, latest_submitted_accounts.

* Legal forms: AS 376,956 (91.7%), BRL 9,812, ESEK 8,635, STI 5,358, NUF 2,606, FLI 1,844, SA 1,505, ENK 1,421, DA 912, ANS 683,
  VPFO 363, SAM 258, ASA 189, then IKS, KS, PRE, PK, SPA, ANNA, SÆR, BBL, GFS, BA, SF, KIRK, KF, SE.
* All rows are active: 0 bankrupt and 0 liquidating at freeze time. Status changes can still appear on refresh.
* Industries: **real estate (68.x) 21.9%**, **"00.000 Uoppgitt" 11.3%**, 97.001 borettslag/sameie 4.6%, 64.323 holding ("Andre egeninvesteringsselskaper") 3.3%.
  The codes are SN2025 (NACE Rev. 2.1); for example 97.001 means "Aktiviteter i borettslag og boligsameier".
* **Registry website present for only 10.9%** (AS 8.8%; FLI 61%; SA 42%; BRL 40%). Among companies with ≥5 employees it is 24.7%.
* **Employee count present for only 14.5%, and the minimum observed value is 5.** Counts of 1–4 are not published in the snapshot.
  Absence must be `not_available`, never 0.
* Consequences:
  * A typical random batch of 100 is about 92 AS, many of them holding or property companies with no website, jobs or news.
  * External recall opportunities are sparse, so website discovery beyond the registry `hjemmeside` is where recall is won or lost.
  * Under the evaluator's sparse-batch rule (<15 positive opportunities across ≥3 external families), recall is "not measured".
* The reference sampler stratifies by `legal_form|employee bin|NACE2|county|web` (`data/universe-metadata.json`). The Builderr dev split was 600/200/200.

### 1.6 What other entrants reveal (public GitHub repos)

* AnSa30-06/signalpost-norway:
  * Builderr reported "two wrong-company website publications" (score 74.67 under the old rubric). They then restricted website verification to three proofs:
    orgnr on the page, legal name + postal specifics, or the registry-filed domain.
  * Later, a wrong match for fjords.com.
  * Their last 1,000-company run: 108 verified sites, 128 ambiguous, 737 none.
* Common patterns:
  * Mod-11 orgnr check on input.
  * Accept a website only when a checksum-valid orgnr matching the target appears on the page (Norwegian businesses typically show orgnr in the footer).
  * Social links only when linked from the verified site.
  * NAV job ads matched only when the employer orgnr equals the company or one of its subunits.
  * Wikidata social IDs.
  * $0 cost, no LLM.

---------------------------------------------------------------------------------------------------

## 2. Design implications (concrete recommendations)

1. **One command:** `uv run nordic-agent run --input batch.jsonl --out out/ [--previous out_prev/envelopes.jsonl] [--registry-snapshot enheter.csv(.gz)] [--max-requests 1950] [--time-budget 40m]`.
   * It writes `envelopes.jsonl` (exactly N lines, same order as input), `changes.jsonl`, `run-report.json`
     (runtime, p50/p95, requests per host, cost=0, state counts) and a static `site/` (index + per-company pages, mobile-friendly).
   * Accept .json, .jsonl and .txt input like the reference. Validate mod-11; an invalid ID becomes an envelope with `failed`/`not_applicable` plus a reason, never a dropped row.
   * Wrap the whole run in a global try/except per company so a crash yields a `failed` envelope.
2. **Request economy** (counting every HTTP request, including robots.txt, redirects and retries):
   * **Identity/roles/subunits in bulk** (1 request each, about 430 MB total; files regenerated nightly around 04:00–04:30 CEST):
     * `GET https://data.brreg.no/enhetsregisteret/api/enheter/lastned` → 211 MB `enheter_alle.json.gz`
     * `/api/underenheter/lastned` → 89 MB
     * `/api/roller/totalbestand` → 130 MB
     * Alternatively use the evaluator-supplied snapshot (0 requests).
   * **Or per batch of 100** via multi-ID endpoints: `GET /enhetsregisteret/api/enheter?organisasjonsnummer=a,b,…&size=100` and
     `/api/oppdateringer/{enheter|underenheter|roller}?organisasjonsnummer=a,b,…` (verified working with 100 IDs per call; see section 7).
   * **Financials:** `GET https://data.brreg.no/regnskapsregisteret/regnskap/{org}` has no batch form, so it costs one request per company.
     * This is the biggest fixed cost: 100 requests for 100 companies, 1,000 for 1,000.
     * The filing-years endpoint `/regnskap/aarsregnskap/kopi/{org}/aar` is rate-limited to about 30/min (observed by the Builderr reference).
       Skip it at scale, or call it only when budget and time allow.
   * **Wikidata:** one SPARQL query per batch of IDs on P2333 (Norwegian organisation number).
   * **Website:** at most about 6–8 requests per company with a verified or candidate site (robots, home, 2–4 key pages). Spend these by expected yield (companies with ≥5 employees, FLI/SA, registry hjemmeside first).
   * **NAV jobs:** see section 3, D4. The feed is not queryable by orgnr; use a declared cache or bounded feed paging.
3. **Identity gate before publication:**
   * Accept a page only if (a) the target orgnr (with or without spaces, "NO 923 609 016 MVA") is on it,
     or (b) it is the registry `hjemmeside` domain plus corroboration (legal name tokens plus email domain, phone, or address match).
   * Anything else is `ambiguous`; never publish its facts.
   * Label parent, group, brand and franchise relations explicitly.
4. **Synthesis (12 pts):** deterministic templated paragraph where every sentence cites evidence IDs. Cover:
   what it does (vedtektsfestet formål / aktivitet / NACE / website description), who leads it, where it operates,
   latest filed numbers with period, hiring yes/no/unknown, what changed since the previous run, and an explicit "unknown / not found" list.
5. **UX (8 pts):** a static HTML viewer like builderr.ai/signalpost.
   * Search by name or orgnr, compare view, a coverage pill, and per-fact "source + retrieved + as-of" disclosure.
   * Responsive CSS, no JS frameworks needed.
   * Optionally a tiny stdlib HTTP server exposing `/companies/:orgnr`, `/research`, `/refresh` (the preferred routes in the machine spec).
6. **Refresh (scored on both versions):** stable claim keys, `first_seen`/`last_seen`, typed change events, idempotent re-run, and preservation of the last supported value on failure (section 7).

---------------------------------------------------------------------------------------------------

## 3. Ranked target field list (most likely to be in the checked collection first)

Ranking rationale:
* (1) The brief's 7 sections and "company details, people, locations, financial results, jobs and public activity".
* (2) Builderr's own sample DATA and starter modules.
* (3) What Brreg's virksomhet page, proff/purehelp (observed via public knowledge; not scraped), Wikipedia infoboxes, D&B and LinkedIn show.
* (4) The availability observed in the universe.

**Recall is computed on "external field families"**, so tiers D and E are where points are won. Tiers A–C are table stakes for precision,
synthesis and identity anchoring, and some may also be pooled.

### Tier A: legal identity (Enhetsregisteret, NLOD; ~100% available)

| # | Field | Best permitted source / JSON path | Notes |
|---|---|---|---|
| A1 | Organisation number | input + `organisasjonsnummer` | mod-11 check |
| A2 | Legal name | `navn`; `historiskeNavn[]` | names change; track |
| A3 | Legal form | `organisasjonsform.kode/beskrivelse` | |
| A4 | Status | `konkurs`, `underAvvikling`, `underTvangsavviklingEllerTvangsopplosning`, deleted (`slettedato`, HTTP 410 = removed for legal reasons; purge caches) | material change |
| A5 | Business address | `forretningsadresse{adresse[],postnummer,poststed,kommune,kommunenummer,land}`; `postadresse` | ENK address may be a home address |
| A6 | Industry | `naeringskode1..3{kode,beskrivelse}` (SN2025) | 11% "00.000 Uoppgitt" |
| A7 | Employees (registered) | `antallAnsatte`, `harRegistrertAntallAnsatte`, `registreringsdatoAntallAnsatteNAVAaregisteret` | updated around the 10th of each month; <5 not published |
| A8 | Founded / registered | `stiftelsesdato`, `registreringsdatoEnhetsregisteret`, `registreringsdatoForetaksregisteret` | |
| A9 | Registry contacts | `hjemmeside`, `epostadresse`, `telefon`, `mobil` | website 10.9% of universe |
| A10 | Purpose / activity text | `vedtektsfestetFormaal[]`, `aktivitet[]`, `vedtektsdato` | core input for "what it does" |
| A11 | Share capital | `kapital{belop,antallAksjer,type,valuta,innfortDato}` | |
| A12 | Register memberships | `registrertIMvaregisteret`, `registrertIForetaksregisteret`, `registrertIStiftelsesregisteret`, `registrertIFrivillighetsregisteret`, `registrertIPartiregisteret` | |
| A13 | Sector | `institusjonellSektorkode` | |
| A14 | Group | `erIKonsern` + `GET /enhetsregisteret/api/konsernstruktur/{org}` | new konsern registration rules from 2025-10-24 forskrift |
| A15 | Parent (public sector) | `overordnetEnhet` | |
| A16 | Language form | `maalform` | minor |

### Tier B: annual accounts (Regnskapsregisteret; ~97% of universe has the latest year)

| # | Field | Source / path (`GET https://data.brreg.no/regnskapsregisteret/regnskap/{org}`) | Notes |
|---|---|---|---|
| B1 | Reporting period + type | `regnskapsperiode.fraDato/tilDato`, `regnskapstype` (SELSKAP/KONSERN), `valuta` | **always attach the period**; ASA can return both company and group accounts |
| B2 | Revenue | `resultatregnskapResultat.driftsresultat.driftsinntekter.sumDriftsinntekter` | never fabricate; copy exact numbers |
| B3 | Operating result | `…driftsresultat.driftsresultat` | |
| B4 | Profit before tax | `resultatregnskapResultat.ordinaertResultatFoerSkattekostnad` | |
| B5 | Net result | `resultatregnskapResultat.aarsresultat` | |
| B6 | Total assets | `eiendeler.sumEiendeler` | |
| B7 | Equity | `egenkapitalGjeld.egenkapital.sumEgenkapital` | |
| B8 | Debt | `egenkapitalGjeld.gjeldOversikt.sumGjeld` | |
| B9 | Audit flags | `revisjon.ikkeRevidertAarsregnskap`, `fravalgRevisjon`; `regnkapsprinsipper.smaaForetak` | useful caveat in synthesis |
| B10 | Filing history + PDF links | `/regnskap/aarsregnskap/kopi/{org}/aar` → years; PDF `/kopi/{org}/{year}` | rate-limited (~30/min); optional |
| B11 | Derived ratios (equity ratio, operating margin) | computed | label as `derived`, cite inputs |

Gotchas:
* The Regnskapsregisteret repo is "preview, no guarantees" and was archived 2025-11-18. It serves only the latest year.
* A 404 means no record, not zero.
* A small slice (banks and insurers) returns source errors; use `failed`/`not_available` and never 0.

### Tier C: people and places (Brreg)

| # | Field | Source / path | Notes |
|---|---|---|---|
| C1 | CEO (daglig leder) | `GET /enhetsregisteret/api/enheter/{org}/roller` → `rollegrupper[].roller[]` with `type.kode=DAGL`, `person.navn{fornavn,mellomnavn,etternavn}` | **drop `person.fodselsdato`** |
| C2 | Board chair, members, deputies | LEDE, NEST, MEDL, VARA (group STYR); `rollegrupper[].sistEndret` | `avregistrert` = inactive |
| C3 | Auditor / accountant | REVI / REGN (an `enhet` with orgnr + name) | |
| C4 | Owner / partners / others | INNH (ENK), DTPR/DTSO (ANS/DA), FFØR, KONT (contact person), KOMP, REPR (NUF) and similar | |
| C5 | Signature / prokura | Brreg fullmakt API (see the `brreg_feeds_bulk__fullmakt_*` samples) | optional |
| C6 | Registered workplaces | `GET /enhetsregisteret/api/underenheter?overordnetEnhet={org}&size=1000` → `organisasjonsnummer`, `navn`, `beliggenhetsadresse`, `naeringskode1`, `antallAnsatte`, `oppstartsdato` | also in the subunit bulk file |

### Tier D: external field families (the recall differentiator, in priority order)

| # | Family / field | Best permitted source | Match gate |
|---|---|---|---|
| D1 | **Verified official website** (URL, final URL, registered domain) | registry `hjemmeside`; else domain candidates (normalised legal name + `.no`/`.com`, email domain from `epostadresse`, Wikidata P856, GLEIF) fetched directly, with **no search-engine scraping** | target orgnr on the page, or registry domain plus corroboration; otherwise `ambiguous` |
| D2 | **Company-owned social/video profiles** (LinkedIn, Facebook, Instagram, YouTube, X, TikTok URLs) | `<a href>` and JSON-LD `sameAs` on the verified site; Wikidata P4264 (LinkedIn), P2013 (FB), P2003 (IG), P2397 (YouTube), P2002 (X) | link must come from the verified site or a Wikidata item with P2333 = orgnr; never fetch the platform pages |
| D3 | **Website-described business/brand** (site title, meta description, og:site_name, JSON-LD Organization name/logo/description, "om oss" excerpt) | verified site: home + `/om-oss`, `/about` | labelled as company-reported |
| D4 | **Hiring / jobs** (title, location, published, deadline/expires, URL, active flag; count) | **NAV Job Vacancy Feed** `https://pam-stilling-feed.nav.no/api/v1/feed` (Bearer; public rotating token at `https://pam-stilling-feed.nav.no/api/publicToken`; terms at https://arbeidsplassen.nav.no/vilkar-api); verified-site `/karriere`, `/jobb`, `/ledige-stillinger` | employer orgnr equals the target or one of its subunits; otherwise do not publish |
| D5 | **Dated public activity** (news/press items with dates, events, blog posts) | verified site sitemap.xml `lastmod`, RSS/Atom feeds, `/nyheter`, `/presse`, `/news`; dated JSON-LD `NewsArticle`/`BlogPosting`; listed companies: Oslo Børs NewsWeb (check terms first) | item must sit on the verified domain or be issuer-matched |
| D6 | **Registry activity timeline** (dated official events: address change, new board, new filing, name change) | `/api/oppdateringer/enheter?organisasjonsnummer=…&includeChanges=true`, `/oppdateringer/roller`, `/oppdateringer/underenheter` | exact orgnr; true dated public activity |
| D7 | **Contacts from site** (generic emails info@/post@, switchboard phone, addresses, opening hours) | verified site contact page, JSON-LD `PostalAddress`/`telephone`, `mailto:`/`tel:` | skip personal-name emails |
| D8 | **Locations from site** (offices/stores list) | verified site `/kontakt`, `/avdelinger`, `/locations`, JSON-LD `LocalBusiness` | cross-check with subunits |
| D9 | **Leadership from site** (management team names + titles) | verified site `/ledelse`, `/om-oss/ledelse`, `/team` | officers only; no rank-and-file staff |
| D10 | **Encyclopedic identity** (Wikidata QID, Wikipedia URL, inception, HQ, logo, ISIN P946, ticker P249, LEI P1278) | Wikidata SPARQL (CC0), batched by P2333 | exact P2333 match only |
| D11 | **LEI and parent/ultimate parent** | GLEIF API (CC0), `filter[entity.registeredAs]=orgnr` | registeredAs = orgnr |
| D12 | Ratings/reviews | Google Places API (paid, key, attribution rules); Trustpilot/Glassdoor are restricted | likely `not_available` / `blocked` under our no-paid-API policy; never scrape |
| D13 | Follower counts / LinkedIn workforce | LinkedIn has no permitted free access | `blocked` (declare) |

### Tier E: smaller sources worth a probe (optional)

* Frivillighetsregisteret for FLI/STI (ICNPO category, grasrotandel). See the `brreg_feeds_bulk__frivillighetsregisteret_*` samples.
* Brreg "granted subsidies" open data (Tilskuddsregisteret).
* Doffin public procurement notices (check terms).
* Stiftelsestilsynet for STI.
* Kunngjøringer (w2.brreg.no): **robots-disallowed** (see `brreg_feeds_bulk__kunngjoring_*_ROBOTS_DISALLOWED.html`). Use the update feeds instead.

---------------------------------------------------------------------------------------------------

## 4. Source permissions

### 4.1 Permitted / recommended

| Source | Licence / terms | Key? | Notes |
|---|---|---|---|
| data.brreg.no Enhetsregisteret (entity, roles, subunits, konsernstruktur, updates, bulk) | **NLOD 2.0** ("Dataene i tjenesten er lisensiert med Norsk lisens for offentlige data (NLOD)"; "It is not necessary to register in order to use the data set.") | no | No documented rate limit; the documented limit is pagination: "(Page+1)*size cannot exceed 10,000" → HTTP 400. Brreg recommends download + update feeds for copies. data.brreg.no/robots.txt resets the connection (none served). |
| data.brreg.no Regnskapsregisteret | NLOD 2.0 | no | preview API (archived repo); latest year only; filing-years endpoint about 30 req/min |
| www.brreg.no pages | robots: `Crawl-delay: 10` | – | avoid; not needed |
| Company-owned websites | site robots.txt + terms | no | honour RFC 9309 robots; fetch robots once per host (it counts as a request); timeouts; size caps |
| Wikidata | CC0 | no | SPARQL endpoint; send a descriptive UA |
| GLEIF | CC0 | no | |
| NAV pam-stilling-feed | terms of use (https://arbeidsplassen.nav.no/vilkar-api): free; may republish ads; **must remove inactive ads and update changed ads promptly** | public rotating token (fetch at runtime, 1 request), or a free private token by email registration. **Flag:** a private token is tied to our account, and Builderr says it cannot use such credentials. | not queryable by orgnr; the feed is a continuous stream of all ads. Jobs need a declared cache or bounded paging. |
| Geonorge address API (Kartverket) | open (see the `third_party_sources__geonorge_*` sample) | no | address normalisation only |

### 4.2 Excluded (confirmed)

| Source | Evidence |
|---|---|
| **proff.no / forvalt.no** (Proff AS, Enento Group) | Footer on forvalt.no (fetched 2026-10-13), verbatim: "Innholdet er beskyttet etter åndsverkloven. Enhver form for regelmessig, systematisk eller kontinuerlig innhenting, lagring, indeksering, distribusjon eller annen sammenstilling av data er ikke tillatt uten eksplisitt skriftlig samtykke fra Proff AS, og vil kunne utgjøre et brudd på åndsverkloven." proff.no also returns **403 from CloudFront** ("configured to block access from your country") from this host. Paid "Proff Premium API" exists. |
| **purehelp.no** | Commercial database; its FAQ says "All offentlig informasjon leveres av Dun & Bradstreet" (third-party licensed data). robots.txt disallows `/api/` and `/ai`. No permissive reuse licence; database right (åndsverkloven § 24) applies. Exclude. |
| **1881.no** | https://min.1881.no/vilkaar-personvern/bruk-av-tjenestene: "Uten direkte avtale er enhver kopiering, lagring og tilgjengeliggjøring forbudt." / "Det er forbudt å samle, eller gjøre uttrekk av, informasjonen på disse sidene (gjentatt og systematisk kopiering av deler av innholdet)." / "…til personlig bruk. Informasjonen kan ikke videreselges i noen form." robots.txt blocks Scrapy, ClaudeBot, GPTBot and other bots. |
| **gulesider.no** (Gule Sider AS, ex-Eniro) | Terms forbid copying "by any technology" without written consent, and forbid repeated/systematic copying (database right). Cloudflare challenge; 403 to WebFetch. |
| **regnskapstall.no** | robots disallows `/firma/` and blocks AI bots and Scrapy |
| **Norid RDAP/whois** | RDAP does not expose the holder's orgnr (only registrar/tech); identity search returns 401. Notice: "Any commercial use of information from the service … is prohibited." Do not use. |
| **LinkedIn** | robots.txt: "The use of robots or other automated means to access LinkedIn without the express permission of LinkedIn is strictly prohibited." Builderr's own LinkedIn capture is marked "experimental". Record URLs only when the verified site links to them. |
| Meta/Instagram, Glassdoor, Indeed, Google SERP/Maps scraping, Google News RSS, JobSpy | explicitly called out by Builderr's source policy; need official, licensed access |
| finn.no jobs | robots disallows `/job*` patterns for many bots; ToS restrictive. Not needed (NAV covers most ads). |
| Search APIs (Brave/Serper/Tavily) | paid or self-service keys tied to our account. Brave's free tier needs a personal account and Builderr "cannot use a credential tied to your own account". Skip under the user's no-paid-API decision. |

### 4.3 robots.txt etiquette (implementation rules)

* User-Agent: `nordic-company-agent/0.1 (+https://github.com/<repo>)`. Use the repo URL as contact; **no personal email**.
* Fetch `/robots.txt` once per host per run and cache it. On 4xx treat it as allow-all; on 5xx or timeout treat it as disallow (RFC 9309). Honour `Crawl-delay`.
* Keep at most 2 requests per second per host and 1 concurrent request per host. Retry only on 5xx/429/timeouts, once, with backoff, and count the retry.
* Respect `noindex`/`nofollow`? Not required for fetching, but don't follow links into disallowed paths.
* Safe URL handling (required by Builderr):
  * http/https only.
  * Block private, loopback and link-local IPs after DNS resolution (SSRF).
  * Cap redirects (5), response size (2 MB) and content types.
  * Never send cookies or credentials.

---------------------------------------------------------------------------------------------------

## 5. NLOD 2.0 attribution and Brreg terms

* Licence: https://data.norge.no/nlod/en/2.0. Brreg's API docs state "License: Norwegian Licence for Open Government Data (NLOD) 2.0".
* **§5 Attribution, verbatim:** "The licensee shall attribute the licensor as specified by the licensor and include a reference to this licence. To the extent practically possible, the licensee shall provide a link to both this licence and the source of the information. If the licensor has not specified how attributions shall be made, the licensee shall normally state the following: «Contains data under the Norwegian licence for Open Government data (NLOD) distributed by [name of licensor]». … If the information has been changed, the licensee must clearly indicate that changes have been made by the licensee."
* **Use this exact text** in every envelope's `licences[]` field, the run report and the HTML footer:
  * EN: `Contains data under the Norwegian licence for Open Government data (NLOD) distributed by Brønnøysundregistrene (https://data.norge.no/nlod/en/2.0). Data has been normalised and combined by nordic-company-agent.`
  * NO: `Inneholder data under Norsk lisens for offentlige data (NLOD) tilgjengeliggjort av Brønnøysundregistrene.`
* **§3 Exemptions:** the licence does not cover "information which contains personal data covered by the Norwegian Personal Data Act unless there is a legitimate basis for the disclosure and further processing of the personal data". Role data therefore needs our own GDPR basis (section 6).
* **§6 Proper use:** "shall not use the information in a manner that appears misleading nor present the information in a distorted or incorrect manner. Neither the licensor's nor other contributors' names or trademarks must be used to support, recommend or market the licensee…" So do not imply Brreg endorsement.
* Brreg operational terms:
  * There is no published rate limit (none found in the docs, the web or the forum).
  * The only hard limit documented is pagination (≤10,000 results; size ≤100 on update endpoints).
  * Bulk files are produced nightly ("Files are produced every night, around 05:00 AM"; Last-Modified observed 04:03–04:30 CEST). Use the `etag`/`last-modified` headers.
  * HTTP **410** means removed for legal reasons: "This should also be considered a request that any copies/caches also remove the relevant entity."
* The data.norge.no dataset entry for Enhetsregisteret (https://data.norge.no/en/datasets/68d08f28-a16d-4fab-a953-ed4ab08ce2e2) states: "Antall ansatte oppdateres ca. den 10. hver måned. MVA oppdateres en gang i døgnet. Daglig oppdatering fra Folkeregisteret og Matrikkelen."
* Regnskap responses also carry `virksomhet.morselskap` (parent-company flag) and `regnkapsprinsipper.regnskapsregler`.

---------------------------------------------------------------------------------------------------

## 6. GDPR / person-data policy (recommended)

Facts:
* The open roles API returns `person.navn`, `person.fodselsdato` and `person.erDoed`. Brreg: "We can provide information about the name and the birth date on a role holder."
* Private actors get birth date only; fødselsnummer requires Maskinporten.
* Enhetsregisterloven 2025 (in force 1 Jan 2026) § 9-1(3): "Sammenstilling av opplysninger om en person kan bare gis når det gjelder personens registrerte tilknytning til næringsvirksomhet." The old § 22 was the same.
* NLOD §3 excludes personal data, so our processing must rest on GDPR Art. 6(1)(f) legitimate interest with data minimisation.

Policy:
1. **Publish:** full name + role code/description + role group + `sistEndret` (as-of) for **current** roles of the **input entity only**.
   Entity role holders (auditor, accountant, corporate board members) with orgnr and name.
2. **Never publish or store:** `fodselsdato` (not even the year), fødselsnummer, personal home addresses, `erDoed`-based inferences.
   Strip birth dates at ingestion, before writing snapshots. Keep the SHA-256 of the original response bytes for evidence integrity and store a redacted copy.
   Document this in `LIMITATIONS.md`.
3. **No person-centric views:** no "other roles of this person" across companies, and no person search endpoint.
   Bulk `roller/totalbestand` may be used only to look up the input orgnr's roles.
4. **ENK / sole proprietors:** show the registry facts as registered. Do not enrich the person: no social or personal profile lookups, no LinkedIn person pages.
5. **Website-sourced people:** management names + titles from company-owned pages only. Skip ordinary employees and personal emails or direct phone numbers (`ola.nordmann@…`); keep generic role addresses (post@, info@, salg@).
6. **Deceased flag** (`erDoed: true`): publish the role as registered without the flag, or omit the person name. Never infer.
7. **Retention / erasure:** honour 410 (purge). Keep snapshots only as long as needed for evidence and refresh. Provide a removal note in the README.

---------------------------------------------------------------------------------------------------

## 7. Keeping profiles current

### 7.1 Measured change rates (data.brreg.no update feeds, 2026-10-13)

Raw output: `samples/spec_and_fields__change_rate_sample500.json`.

| Window | `oppdateringer/enheter` totalElements | `oppdateringer/underenheter` |
|---|---|---|
| Mon 2026-10-12 (one day) | 8,739 | **63,952** (monthly NAV employee-count load) |
| Sun 2026-10-11 | 278 | 128 |
| 7 days to 10-13 | 19,176 | 68,242 |
| 30 days to 10-13 | 109,576 | 92,974 |

* Sample of 300 entity updates on 10-12 (`includeChanges=true`): endringstype was Endring 270, Ny 28, Sletting 2.
* Changed paths were dominated by `/registreringsdatoAntallAnsatte*` (200), `/antallAnsatte` (95), `/harRegistrertAntallAnsatte`,
  then business address (~34), NACE (16), aktivitet (10), navn (7).
* Subunits show the same pattern: employees first, then `beliggenhetsadresse`.

**500 random universe companies** (seed 20261013; 47 requests, using `organisasjonsnummer=<100 IDs>` filters):

| Window | ≥1 entity update | role-update event |
|---|---|---|
| 30 d | 59 (11.8%) | 44 (8.8%) |
| 90 d | 186 (37.2%) | 80 (16.0%) |
| 365 d | 500 (100%), including a mass re-publication touching all entities in Nov 2025 | 277 (55.4%) |

* For a 40-company slice over 365 days, the changed fields were `antallAnsatte` (monthly), `sisteInnsendteAarsregnskap`
  (Feb–Jul, filing season; the deadline for most is 31 July), and rarely `vedtektsdato`, `kapital`, addresses, NACE, `erIKonsern`, `epostadresse` or MVA.
* Day to day, about 1–2% of a batch will show any registry change. Roles change for about 0.3% per day.
* Financials for 2025 are already filed for the whole universe, so new-filing events in Oct–Dec are rare (corrections or late filings only).
* Websites, jobs and news change much faster: jobs change daily; ads expire.

### 7.2 Feed mechanics (verified)

* `GET /enhetsregisteret/api/oppdateringer/enheter?dato=<ISO>&updatedBefore=<ISO>&oppdateringsid=<id>&organisasjonsnummer=a,b,…&includeChanges=true&size≤100&page`
  → `_embedded.oppdaterteEnheter[]` with {oppdateringsid, dato, organisasjonsnummer, endringstype, endringer[{op,path,value}]}, plus `page.totalElements`.
* `/oppdateringer/underenheter` takes the same parameters and returns `oppdaterteUnderenheter`.
* `/oppdateringer/roller?afterTime=<ISO>|afterId=<id>&organisasjonsnummer=…&size≤100` returns a **CloudEvents** list
  `[{id, time, type:"no.brreg.enhetsregisteret.rolle.oppdatert", source:".../enheter/{org}/roller", data:{organisasjonsnummer}}]`.
  The events carry no diff, so re-fetch the roles.
* Brreg's recommended "Maintenance of copy" process: download the total inventory (entities, sub-entities, roles), check the
  etag/last-modified, then "Fetch new updates continuously" by ID and re-fetch changed objects. Start the follow from an early ID or time before the dump.

### 7.3 Refresh design (recommended)

1. **Inputs:** `--previous envelopes.jsonl` (and/or the local SQLite state). The run emits the current envelopes plus `changes[]` per company.
2. **Cheap change detection for registry data:** one batch call per 100 orgs to each update feed since the `last_checked_at` of the previous run.
   Only companies with events get registry re-fetches. The previous claim values stay valid otherwise; mark them `last_verified_at` = feed check time and cite the feed URL as evidence.
3. **Stable claim keys:** `orgnr|field|qualifier`, for example `role|DAGL|<normalised name>`, `subunit|<orgnr>`, `job|nav:<uuid>`, `fin|<period>|revenue`.
   Store the value hash plus `first_seen`, `last_seen`, `last_verified`.
   The same snapshot must produce identical output (no timestamp-only diffs counted as changes), which makes the run idempotent.
4. **Typed changes with materiality:**
   * Material: status (konkurs/avvikling/slettet/410), name, legal form, new filing, CEO/chair change, address/municipality, website change.
   * Minor: employee count, board member, new/closed subunit, new/closed job, changed description, new news item.
   * Each change carries old/new values plus evidence for both sides.
5. **Failure semantics:** a failed refresh keeps the last supported value with `stale=true` and records the failure state. Never erase, never convert to 0.
6. **Re-crawl cadence by volatility:**
   * Registry: daily via the feeds.
   * Financials: only on `sisteInnsendteAarsregnskap` change.
   * Website: conditional GET (ETag / If-Modified-Since; a 304 still costs a request).
   * Sitemap `lastmod`; jobs every run (ads expire; NAV terms require removing inactive ads).
7. **Prior art:**
   * Brreg's copy-maintenance pattern (dump + update feed).
   * GLEIF golden copy + delta files.
   * Commercial monitors (Proff "Varslinger", D&B monitoring) alert on roles, accounts and announcements. Our typed change events mirror this.

---------------------------------------------------------------------------------------------------

## 8. Open questions for Builderr (ask before 17–18 Oct)

1. Which budget applies to the official batch (1,000–1,100 companies): 45 min / 2,000 requests / $10, or something scaled? Do registry calls and bulk downloads count?
2. Is the frozen registry snapshot passed as a CSV file argument (like `--bulk`)? What is the exact invocation and output path the harness expects?
3. Which external field families are in the checked collection (website, profiles, jobs, activity, contacts, locations, leadership, reviews)?
4. Is the NAV public token acceptable as a "credential", and is a declared pre-built cache of the NAV feed allowed?

---------------------------------------------------------------------------------------------------

## 9. Files saved by this research

* `research/spec_and_fields.md` (this file)
* `research/samples/spec_and_fields__builderr_brief_signalpost.md`, `…__builderr_evaluation_contract.md`, `…__builderr_source_policy.md`,
  `…__builderr_agent_playbook.md`, `…__builderr_learning_harness.md`, `…__builderr_challenge_page_text.txt`
* `research/samples/spec_and_fields__builderr_api_challenge_signalpost.json`, `…__builderr_tasks_signalpost.json`, `…__unstop_listing.json`
* `research/samples/spec_and_fields__builderr_public_sample_5profiles.json`: Builderr's own profile shape (registry, accounting, financial, financialHistory, roles, locations, web incl. identity_assessment, external.handles/linkedin, coverage dims)
* `research/samples/spec_and_fields__change_rate_sample500.json`
* `research/builderr_starter_kit/`: reference agent (OUTPUT_CONTRACT.md, README.md, docs/, scripts/, src/, tests/fixtures incl. refresh-snapshots.json)
* `data/signalpost-company-universe-2025.jsonl.gz`: 411,160 eligible org numbers (use for sampling dev/val/test sets)
