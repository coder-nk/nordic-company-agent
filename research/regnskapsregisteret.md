# Regnskapsregisteret open accounts API (data.brreg.no) – live research

Probed live on 2026-10-13 (IST, ~11:30-12:00 UTC) from this machine using UA `nordic-company-agent/0.1 (research prototype)`, at most 2 req/s.
About 260 requests in total across ~120 distinct org numbers. All raw samples are in `research/samples/regnskapsregisteret__*.{json,csv,xml,ttl}`.

## 0. TL;DR

* `GET https://data.brreg.no/regnskapsregisteret/regnskap/{orgnr}` returns a **JSON array** with **up to the 3 most recent filed fiscal years** (not only the latest, as older docs, forum posts and blogs claim). Both **SELSKAP** (the entity's own accounts) and **KONSERN** (group accounts) come back in the same response. One request per company covers everything.
* Values are in **whole units of `valuta`** (NOK, USD, EUR, DKK seen). They are already scaled; there is no "thousands" factor. Verified against Equinor's published USD figures and Telenor/Atea/AF Gruppen's published NOK figures.
* Filters: `?år=YYYY` (URL-encoded `%C3%A5r`, matches the year of **tilDato**) and `?regnskapstype=SELSKAP|KONSERN` (uppercase only; lowercase gives 400). `?aar=` is **silently ignored** on the JSON endpoint. It is only used on `/csv`.
* No accounts gives **404 with an empty body**. Banks, insurers, life/pension companies give **500** with `"Regnskapet inneholder en oppstillingsplan som ikke er stottet (BANK|SKADE|LIV|PENSJ)"`. This is deterministic, so do not retry.
* No ETag, Last-Modified or Cache-Control on the JSON endpoint, and no rate-limit headers on it. TLS setup is expensive from here (~0.9 s). With keep-alive a request takes ~0.3 s, so at 2 req/s, 100 companies take ~50 s.
* There is a new **free PDF-copy API** (`/aarsregnskap/kopi/{orgnr}/aar` and `/{aar}`). It lists filed years back to 2011 and serves scanned image PDFs with no text layer. It is **rate-limited to ~30 requests per ~60 s window** (`x-rate-limit-remaining`).
* `sisteInnsendteAarsregnskap` in Enhetsregisteret matched the max `tilDato` year in **92/92** active companies checked, including 30 companies whose value changed in the update feed the previous evening. Deleted entities (`respons_klasse: SlettetEnhet`) **lack** `sisteInnsendteAarsregnskap`, but their accounts are **still served**.
* License: NLOD 2.0 (Norwegian Licence for Open Government Data). Attribution is required ("Contains data under the NLOD licence from Brønnøysundregistrene"). The service is described as a "preview" with no QoS guarantee. The GitHub repo `brreg/regnskapsregister-api` was archived in Nov 2025, but the service still runs (OpenAPI version 1.1.16, swagger build 2026-10-01).

## 1. Endpoints (from the OpenAPI spec `GET /regnskapsregisteret/regnskap/v3/api-docs`, saved as `samples/regnskapsregisteret__openapi_v3_api-docs.json`)

| Method/path | Params | Returns | Notes |
|---|---|---|---|
| `/regnskapsregisteret/regnskap/{orgNummer}` | `år` (int), `regnskapstype` (SELSKAP/KONSERN) | JSON array of `Regnskap`. Also XML, JSON-LD, RDF/JSON, RDF/XML, Turtle via `Accept` | **Main endpoint.** `Accept: text/html` gives 406 |
| `/regnskapsregisteret/regnskap/{orgNummer}/{id}` | – | a single `Regnskap` object (not an array) | Canonical record URI. Good as a **source link** per fact |
| `/regnskapsregisteret/regnskap/{orgNummer}/csv` | `aar` (int), `regnskapstype` | `text/csv`, `;`-separated, quoted, UTF-8 **with BOM** | Needs `Accept: text/csv` or `*/*`. `Accept: application/json` gives **400**. Column names differ from JSON (see §6) |
| `/regnskapsregisteret/regnskap/log` | – | JSON list of daily mass-delivery file names `YYYYMMDDhhmmss-masse.xml` since 2018-01-02 (2,222 entries; last `20261012204150-masse.xml`) | Only usable as a "register last refreshed" timestamp. The files themselves are for the paid subscription |
| `/regnskapsregisteret/regnskap/aarsregnskap/kopi/{orgnr}/aar` | – | JSON array of year strings, e.g. `["2011",…,"2025"]`, `[]` if none | **Rate-limited** (see §4) |
| `/regnskapsregisteret/regnskap/aarsregnskap/kopi/{orgnr}/{aar}` | – | `application/pdf`, `content-disposition: attachment; filename=aarsregnskap-2025_917507899.pdf` | Scanned 1-bit images (1728×2312 px pages), **no text/fonts**, 0.25–4.2 MB, 2–5 s. Needs `Accept: application/pdf` or `*/*` (JSON Accept gives **406**). Missing year gives 404. Works for banks too |
| `/regnskapsregisteret/regnskap/aarsregnskap/mellombalanse/{orgnr}/aar` | – | JSON `[{year, id}]`, normally `[]` | Interim balance sheets (rare) |
| `/regnskapsregisteret/regnskap/aarsregnskap/mellombalanse/{orgnr}/{id}` | – | PDF | |
| `/statistikk/by-orgnr`, `/by-method`, `/by-ip` | fraDato, tilDato | usage statistics | Not useful; don't call |

`robots.txt` on data.brreg.no: the connection is reset (no robots file served).

## 2. JSON structure (exact paths; `$[i]` = array element)

```
$[i].id                                   int   system id, used in /{orgnr}/{id}
$[i].journalnr                            str   e.g. "2026635024"; first 4 digits = year the filing was registered (inferred)
$[i].regnskapstype                        "SELSKAP" | "KONSERN"
$[i].regnskapDokumenttype                 in schema, never present in 371 records
$[i].virksomhet.organisasjonsnummer       str   == requested orgnr in 371/371 records
$[i].virksomhet.organisasjonsform         str   form at filing (AS, ASA, ENK, NUF, BRL, ESEK, SA, STI, ANS, IKS, SF, FLI…)
$[i].virksomhet.morselskap                bool  parent company flag (true for every KONSERN record)
$[i].regnskapsperiode.fraDato / tilDato   "YYYY-MM-DD"
$[i].valuta                               "NOK" | "USD" | "EUR" | "DKK" ...
$[i].avviklingsregnskap                   bool  liquidation accounts (seen true on short 2026 periods of deleted AS)
$[i].oppstillingsplan                     always "store" in 371/371 records (even smaaForetak=true): USELESS
$[i].revisjon.ikkeRevidertAarsregnskap    bool  never true in sample
$[i].revisjon.fravalgRevisjon             bool  audit opted out (true in ~30% of small AS records)
$[i].regnkapsprinsipper.smaaForetak       bool  (note typo "regnkapsprinsipper")
$[i].regnkapsprinsipper.regnskapsregler   "regnskapslovenAlminneligRegler" | "IFRS" | "forenkletAnvendelseIFRS"
$[i].egenkapitalGjeld.sumEgenkapitalGjeld
$[i].egenkapitalGjeld.egenkapital.sumEgenkapital
$[i].egenkapitalGjeld.egenkapital.innskuttEgenkapital.sumInnskuttEgenkaptial   (typo "Egenkaptial"!)
$[i].egenkapitalGjeld.egenkapital.opptjentEgenkapital.sumOpptjentEgenkapital
$[i].egenkapitalGjeld.gjeldOversikt.sumGjeld
$[i].egenkapitalGjeld.gjeldOversikt.kortsiktigGjeld.sumKortsiktigGjeld
$[i].egenkapitalGjeld.gjeldOversikt.langsiktigGjeld.sumLangsiktigGjeld
$[i].eiendeler.sumEiendeler
$[i].eiendeler.anleggsmidler.sumAnleggsmidler
$[i].eiendeler.omloepsmidler.sumOmloepsmidler
$[i].eiendeler.goodwill | sumVarer | sumFordringer | sumInvesteringer | sumBankinnskuddOgKontanter
$[i].resultatregnskapResultat.driftsresultat.driftsinntekter.sumDriftsinntekter
$[i].resultatregnskapResultat.driftsresultat.driftsinntekter.salgsinntekter
$[i].resultatregnskapResultat.driftsresultat.driftskostnad.sumDriftskostnad
$[i].resultatregnskapResultat.driftsresultat.driftskostnad.loennskostnad
$[i].resultatregnskapResultat.driftsresultat.driftsresultat
$[i].resultatregnskapResultat.finansresultat.nettoFinans
$[i].resultatregnskapResultat.finansresultat.finansinntekt.sumFinansinntekter
$[i].resultatregnskapResultat.finansresultat.finanskostnad.sumFinanskostnad | rentekostnadSammeKonsern | annenRentekostnad
$[i].resultatregnskapResultat.ordinaertResultatFoerSkattekostnad
$[i].resultatregnskapResultat.ordinaertResultatSkattekostnad   (positive = tax expense; Equinor 37884-25980=11904)
$[i].resultatregnskapResultat.ekstraordinaerePoster | skattekostnadEkstraordinaertResultat   (rare)
$[i].resultatregnskapResultat.aarsresultat
$[i].resultatregnskapResultat.totalresultat   (present in ~50%; IFRS comprehensive income or = aarsresultat)
```

**Nullability.** Across 371 records the API **never returned `null`**. Absent data is either an **omitted key** or an **empty object `{}`** (e.g. `"opptjentEgenkapital":{}`, `"langsiktigGjeld":{}`, `"anleggsmidler":{}`, `"finansinntekt":{}`). Explicit `0.00` values do occur (e.g. `sumVarer: 0.00`, `ordinaertResultatSkattekostnad: 0.00`), so 0 means "filed as 0" and must not be confused with missing data.
Presence across the first 249 records: always present were sumEiendeler, sumEgenkapitalGjeld, sumEgenkapital, sumGjeld, sumOmloepsmidler, sumDriftsinntekter, sumDriftskostnad and aarsresultat. Usually present: driftsresultat, ordinaertResultatFoerSkattekostnad, nettoFinans, sumAnleggsmidler. Often missing: goodwill (~5%), sumInvesteringer (~4%), sumVarer (~5%), totalresultat (~50%), ordinaertResultatSkattekostnad (~20% missing).
Numbers are JSON decimals with 2 decimals (`143580000000.00`). Parse with `parse_float=Decimal`.

**Ordering.** The array is grouped by regnskapstype (KONSERN first, then SELSKAP), ascending by period within each group. Do not rely on this; sort explicitly.

## 3. Years available, filters, history

* Returns the **latest ≤3 filed fiscal years** per regnskapstype. Equinor has PDF copies for 2011–2025, but JSON gives only 2023–2025. Gaps are not back-filled: SA 913376064 filed only 2021 and 2023, and JSON returns exactly those two.
* Old accounts drop off. ESEK 820358562's only filing is 2018 and JSON still returns 2018. FLI 975820645 (latest filing 2011) and KF 970541012 (latest 2000) give **404**, even though Enhetsregisteret has `sisteInnsendteAarsregnskap` set. Data before ~2017/2018 is apparently not in the JSON store (the log starts 2018-01-02).
* `?år=YYYY` filters on the **year of `tilDato`**: `930408476?år=2026` gives the period 2025-04-01..2026-03-31. Outside the window (Equinor `?år=2019`) or in the future gives 404.
* `?regnskapstype=SELSKAP|KONSERN` works. Combined with år it gives one record. Lowercase gives 400.
* KONSERN can lag SELSKAP: 979497539 has KONSERN only for 2023 but SELSKAP for 2023–2025. Kraft Finans ASA has KONSERN 2023–2024 and SELSKAP 2023–2025. **Never take "latest KONSERN" as current without checking its period.**
* Non-calendar fiscal years are common (`-03-31`, `-04-30`, `-06-30`, `-08-31`). First and liquidation periods can be short (37–333 days) or long (400, 480 days). Never annualise.
* There is no endpoint for historical figures beyond the 3 filings. Older history exists only as image PDFs via `kopi`.
* Brreg forum (May 2025): staff "hope to offer 3 years back" and an annual-accounts copy API "during 2025". Both are now live. Most third-party docs, blogs, the data.norge.no description and the archived README still say "latest year only". They are outdated.

## 4. Status codes, headers, limits

| Case | Status | Body |
|---|---|---|
| Normal | 200 | JSON array (1–10 KB) |
| ENK/FLI/NUF/ESEK without accounts, new AS, KOMM/STAT/ORGL, non-existent or invalid orgnr (`abc`, 8 or 10 digits, spaces) | **404** | **empty** (no JSON error object) |
| Bank / mortgage company (BANK), non-life insurer (SKADE), life insurer (LIV), pension fund (PENSJ) | **500** | `{"timestamp":…,"status":"500","error":"Internal Server Error","message":"Regnskapet inneholder en oppstillingsplan som ikke er stottet (BANK)","path":…,"trace":uuid}` |
| Same bank with `?år=2025&regnskapstype=SELSKAP` | 404 | empty |
| `regnskapstype=selskap` (lowercase) | 400 | empty |
| `Accept: text/html` | 406 | Spring "Whitelabel Error Page" |
| `/csv` with `Accept: application/json` | 400 | empty |
| `/kopi/{orgnr}/{aar}` with `Accept: application/json` | 406 | JSON error |

Headers on the JSON endpoint: `content-type: application/json`, `transfer-encoding: chunked`, F5 BIG-IP cookies, HSTS. **No** `Cache-Control`, `ETag`, `Last-Modified` or rate-limit headers. `If-Modified-Since`/`If-None-Match` are ignored (always 200). HEAD works (200).
`kopi` and `mellombalanse` responses carry `x-rate-limit-remaining`, which started at 29/30 and fell by 1 per call. It reset to 29 within ~60 s (fixed window, **~30 req/min**, shared between kopi and mellombalanse). I did not drive it to 429. Assume 429 above 30/min.
No 429 on the JSON endpoint in a 40-request run at 2 req/s with keep-alive (37×200, 2×500, 1×404). Latency: cold connection (TLS) 1.2–1.9 s, keep-alive 0.30–0.36 s.
One `ConnectionResetError` was seen on `/robots.txt` only. Implement retries with backoff for network errors, but **not** for the 500 "ikke stottet" message.

## 5. Units and plausibility (verified)

| Company / record | API value | Published | Verdict |
|---|---|---|---|
| Equinor KONSERN 2023, valuta USD | sumDriftsinntekter 107,174,000,000; aarsresultat 11,904,000,000 | Total revenues & other income USD 107,174 m; net income USD 11.9 bn | whole USD ✔ |
| Equinor KONSERN 2024 | 103,774,000,000; 8,829,000,000 | USD 103,774 m; USD 8.83 bn | ✔ |
| Telenor KONSERN 2023, NOK | 80,537,000,000 | ~NOK 80.5 bn | whole NOK ✔ |
| Atea KONSERN 2024 | 34,583,000,000 | ~NOK 34.6 bn | ✔ |
| Small AS 998246202 | 6,504,031 | – | whole NOK (no ×1000) |

`sumDriftsinntekter` = total operating revenue **including other operating income**. Hydro's API figure is ~NOK 4–5 bn above its reported "revenue" line because it includes other income. `salgsinntekter` is the sales line.
Large filers report in millions, so their values end in `000000.00`. The API has already rescaled them.
**Currency is per record and can change between years**: NUF 984746458 is NOK for 2023–2024 and **DKK** for 2024-09..2025-08, with revenue jumping 7× from 224 m NOK to 1,632 m DKK. This is very likely the foreign head office's accounts filed under the NUF number. Equinor's SELSKAP is also in USD.

## 6. Data-quality issues observed (must be handled)

Identity checks over 249 records (tolerance max(2, 0.05%), plus 1 m for values over 1 bn):

| Check | ok | fail |
|---|---|---|
| sumEiendeler == sumEgenkapitalGjeld | 245 | 4 (Telenor SELSKAP 2024: 211,512 m vs 138,134 m; Telenor KONSERN 2024 off by 832 m; SA 913376064 2023 sumEiendeler **0** vs 44,697; AF Gruppen 2 m rounding) |
| sumEgenkapitalGjeld == EK + Gjeld | 249 | 0 |
| sumEiendeler == AM + OM | 249 | 0 |
| sumGjeld == KG + LG | 240 | 9 (provisions are not exposed, so this is expected) |
| EK == innskutt + opptjent | 236 | 13 (KONSERN minority interests; IFRS groups mis-split: Equinor KONSERN 2024 innskutt 42,342 m / opptjent 38 m) |
| driftsresultat == DI − DK | 247 | 0 |
| nettoFinans == FI − FK | 232 | 10 (979911254: nettoFinans sign flipped) |
| EBT == EBIT + nettoFinans | 244 | 4 |
| aarsresultat == EBT − tax | 244 | 5 (Telenor KONSERN 2025: 8,365 m vs 12,292 m, which includes discontinued operations) |

Other anomalies: Telenor KONSERN 2024 `regnskapsregler = regnskapslovenAlminneligRegler` (wrong, Telenor uses IFRS). ENK 934551435 has a negative `sumDriftsinntekter` (−341,829). `oppstillingsplan` is constant. CSV columns use other names and enum spellings: `sumInnskuttEgenkapital` (no typo), `sumLangsiktiggjeld`, `regnskapDokumnettype`, `STORE`, `FORENKLET_ANVENDELSE_IFRS`, `tidsperiode.*`, `resultatregnskapsResultat.*`. Use JSON, not CSV.

## 7. Cross-check with Enhetsregisteret

* `sisteInnsendteAarsregnskap` (string year) == max year(`tilDato`) in the accounts API: **62/62** in a random sweep and **30/30** for companies whose value changed in `GET /enhetsregisteret/api/oppdateringer/enheter?dato=…&includeChanges=true` on 2026-10-12T18:00Z (ops `add`/`replace` on `/sisteInnsendteAarsregnskap`). All were present in the accounts API on 2026-10-13 ~11:40Z, so the lag is under 18 h. Brreg docs say updates run 5 days/week with ~2 days from approval to API.
* `sisteInnsendteAarsregnskap` is the **end-date year**, so a fiscal year 2025-04-01..2026-03-31 shows as "2026".
* All 404s in the random sweep had `sisteInnsendteAarsregnskap` absent, so skipping these companies is safe **for active entities**. Exceptions: (a) deleted entities (`respons_klasse: "SlettetEnhet"`, only navn/orgform/slettedato/historiskeNavn). Their accounts are still served (995682974, 912559203, 998935652, 964426570, all deleted 2026-09-01; DNB ASA deleted 2021 still gives the BANK 500). (b) Very old values (≤2016) give 404 anyway.
* The update feed with `includeChanges=true` also exposes e-mail and mobile numbers of entities, which can be personal data for ENKs. Not relevant here, but do not store them.

## 8. Recommended English mapping

Record-level (attach to **every** financial fact):

| JSON | English key |
|---|---|
| id | `source_record_id` |
| journalnr | `filing_journal_no` (`filing_registered_year = journalnr[:4]`, label as inferred) |
| regnskapstype | `scope`: SELSKAP→`company` (legal entity, unconsolidated), KONSERN→`group` (consolidated, parent = this orgnr) |
| regnskapsperiode.fraDato/tilDato | `period_start`, `period_end`; `fiscal_year = int(period_end[:4])`; `period_days` |
| valuta | `currency` |
| regnkapsprinsipper.regnskapsregler | `accounting_framework`: IFRS / `simplified_IFRS` / `NGAAP` |
| regnkapsprinsipper.smaaForetak | `small_company_rules` |
| revisjon.fravalgRevisjon | `audit_opted_out` |
| revisjon.ikkeRevidertAarsregnskap | `accounts_not_audited` |
| avviklingsregnskap | `liquidation_accounts` |
| virksomhet.morselskap | `is_parent_company` |
| oppstillingsplan | drop (constant "store") |

Line items:

| JSON path (under record) | English |
|---|---|
| resultatregnskapResultat.driftsresultat.driftsinntekter.sumDriftsinntekter | `operating_revenue_total` (headline "revenue") |
| …driftsinntekter.salgsinntekter | `sales_revenue` |
| …driftskostnad.sumDriftskostnad | `operating_expenses_total` |
| …driftskostnad.loennskostnad | `payroll_expenses` |
| …driftsresultat.driftsresultat | `operating_profit` (EBIT) |
| …finansresultat.finansinntekt.sumFinansinntekter | `financial_income` |
| …finansresultat.finanskostnad.sumFinanskostnad | `financial_expenses` |
| …finanskostnad.rentekostnadSammeKonsern / annenRentekostnad | `interest_expense_group` / `interest_expense_other` |
| …finansresultat.nettoFinans | `net_financial_items` |
| resultatregnskapResultat.ordinaertResultatFoerSkattekostnad | `profit_before_tax` |
| resultatregnskapResultat.ordinaertResultatSkattekostnad | `income_tax_expense` |
| resultatregnskapResultat.ekstraordinaerePoster / skattekostnadEkstraordinaertResultat | `extraordinary_items` / `tax_on_extraordinary_items` |
| resultatregnskapResultat.aarsresultat | `net_profit` (annual result) |
| resultatregnskapResultat.totalresultat | `total_comprehensive_income` |
| eiendeler.sumEiendeler | `total_assets` |
| eiendeler.anleggsmidler.sumAnleggsmidler / omloepsmidler.sumOmloepsmidler | `non_current_assets` / `current_assets` |
| eiendeler.goodwill / sumVarer / sumFordringer / sumInvesteringer / sumBankinnskuddOgKontanter | `goodwill` / `inventories` / `receivables` / `current_investments` / `cash_and_bank` |
| egenkapitalGjeld.egenkapital.sumEgenkapital | `total_equity` |
| …innskuttEgenkapital.sumInnskuttEgenkaptial / opptjentEgenkapital.sumOpptjentEgenkapital | `paid_in_equity` / `retained_earnings` (**SELSKAP + NGAAP only**) |
| egenkapitalGjeld.gjeldOversikt.sumGjeld / kortsiktigGjeld.sumKortsiktigGjeld / langsiktigGjeld.sumLangsiktigGjeld | `total_liabilities` / `current_liabilities` / `non_current_liabilities` |
| egenkapitalGjeld.sumEgenkapitalGjeld | `total_equity_and_liabilities` |

## 9. Rules so we never publish a fabricated or mis-scaled financial value

1. **Copy, never compute.** Emit only numbers that exist at a concrete JSON path. Never derive margins, EBITDA, growth or "revenue in NOK". If a derived metric is ever added, label it `derived` with its inputs. Missing key or `{}` means "not reported", **not 0**. A literal `0.00` is reported as 0.
2. **Parse exactly.** Use `json.loads(..., parse_float=Decimal)`. Output the raw value plus `unit: "currency_units"` (no ×1000, no rounding). Optionally add a human-readable string such as "107.2 bn USD", generated from the raw value.
3. **Every fact carries** `currency`, `period_start`, `period_end`, `fiscal_year`, `scope` (company|group), `accounting_framework`, `source_record_id`, `source_url = https://data.brreg.no/regnskapsregisteret/regnskap/{orgnr}/{id}` (record-level, resolves to JSON), `query_url`, `retrieved_at` (UTC), `as_of = period_end`, and `evidence = JSONPath` such as `$[?(@.id==7192427)].resultatregnskapResultat.aarsresultat` plus the raw literal.
4. **Identity guard.** Discard any record whose `virksomhet.organisasjonsnummer != requested orgnr` (never observed, but cheap). Mark group figures as "consolidated group headed by {orgnr}", not as the entity's own.
5. **Never mix scopes, years or currencies.** Headline = latest SELSKAP record. Group = latest KONSERN record, published separately and only if its period_end equals the latest SELSKAP period_end; otherwise label it "latest available group accounts (FY X)". Time series only within the same scope and currency. If the currency changes, break the series and add a flag.
6. **Plausibility flags (lower confidence, explain; do not alter values):** `sumEiendeler != sumEgenkapitalGjeld` beyond 0.1%. `sumEiendeler == 0` while sumEgenkapitalGjeld ≠ 0. Negative operating revenue. `period_days` < 300 or > 400 (first, short or liquidation period). `liquidation_accounts`. Currency ≠ NOK. NUF with currency change or a >5× scale jump ("may be the foreign head office's accounts"). Framework inconsistent with prior years. Consider withholding the balance-sheet block when the balance check fails by >10%, and keep the income statement.
7. **Do not publish the equity split** (`paid_in_equity` / `retained_earnings`) for KONSERN or IFRS records. It is mis-mapped there (Equinor).
8. **Staleness.** Flag `stale` when `today - period_end` > 18 months (e.g. ESEK 2018-only). If Enhetsregisteret `sisteInnsendteAarsregnskap` > the API's latest year, report "newer accounts filed (FY X); figures not yet available" instead of presenting old figures as current.
9. **Status semantics.** 404 means "no structured accounts in register" (an explanation, not an error). 500 "ikke stottet (BANK|SKADE|LIV|PENSJ)" means a "financial institution layout, no open key figures". Optionally emit the filed-years list and the PDF link from `kopi`. Do not retry these.

## 10. Request budget and refresh strategy

* 1 Enhetsregisteret call (needed anyway) + 1 accounts call per company. Skip the accounts call only when the entity is active and lacks `sisteInnsendteAarsregnskap`, or when that year is ≤ current year − 9. This saves roughly 20–30% of calls on random samples (ENK/FLI-heavy).
* The optional `kopi/{orgnr}/aar` call (filed-years list 2011+, PDF evidence link) costs 1 more call and is limited to 30/min. Run it at ≤0.45 req/s, or only for banks/insurers and companies with no JSON accounts.
* Use httpx.AsyncClient with keep-alive (`http2=False` works), concurrency 2, global ≤2 req/s. 100 companies take ~50–60 s.
* Refresh: cache by orgnr and re-fetch when the Enhetsregisteret update feed (`/oppdateringer/enheter?dato=<last>&includeChanges=true`) shows a `/sisteInnsendteAarsregnskap` change, or once per day. The batch lands ~18:00 UTC and is in the API the next morning. `/regnskap/log`'s newest file name gives the register's last refresh time.

## 11. Test org numbers (verified 2026-10-13)

| orgnr | name / form | behaviour | sample |
|---|---|---|---|
| 923609016 | EQUINOR ASA | KONSERN+SELSKAP 2023–25, **USD**, IFRS | equinor_asa_… |
| 982463718 | TELENOR ASA (not 976820479, which does not exist) | NOK; 2024 balance mismatch | telenor_… |
| 914778271 | **Norsk Hydro ASA** (not Brreg) | KONSERN+SELSKAP | norsk_hydro_… |
| 974760673 | REGISTERENHETEN I BRØNNØYSUND (ORGL) | 404 | – |
| 971524960 | **STORTINGET** (STAT), not Oslo kommune | 404 | – |
| 958935420 | OSLO KOMMUNE (KOMM) | 404 | 404_400_406… |
| 984851006 | DNB BANK ASA | 500 BANK | unsupported_… |
| 995568217 / 958995369 / 979276699 / 985621551 | Gjensidige (SKADE) / Storebrand Liv (LIV) / Aker Pensjonskasse (PENSJ) / DNB Boligkreditt (BANK) | 500 | unsupported_… |
| 934551435 | ENK with accounts | first period 2024-11-27..2025-12-31, negative revenue | enk_… |
| 933365573 | ENK without accounts | 404 | 404_… |
| 950610956 | BRL | 3 yrs | brl_… |
| 927301717 / 820358562 | ESEK | 3 yrs / only 2018 (stale) | esek_… |
| 913376064 | SA | 2021+2023 gap, sumEiendeler 0 | sa_… |
| 971349875 | STI | 3 yrs | stiftelse_… |
| 991272658 | ANS | 2023 only | – |
| 984746458 | NUF Bolia | NOK→**DKK** switch | nuf_… |
| 948138646 | ConocoPhillips Norge NUF | NOK | nuf_conoco… |
| 935567645 | konkurs AS | 2025 short period | konkurs_… |
| 812467182 | AS under avvikling | normal 3 yrs | – |
| 912559203 / 995682974 | deleted AS 2026-09-01 | liquidation period 2026 / normal | deleted_… |
| 998246202 / 886582412 | negative equity (small AS / ASA group) | | small_as_…, asa_886… |
| 930408476 | fiscal year ending 03-31 | | as_930408476… |
| 979497539 | KONSERN only for 2023 | | as_979497539… |
| 933187764 | Cavendish Hydrogen ASA | **EUR** | asa_933187764… |
| 997765494 / 917082308 | IKS / SF with KONSERN | | iks_…, sf_… |
| 938386080 | AS registered 2026-09 | 404, kopi `[]` | – |

## Sources

* OpenAPI: https://data.brreg.no/regnskapsregisteret/regnskap/v3/api-docs (live)
* data.norge.no service description (NLOD 2.0, 5 days/week, banks/insurers excluded): https://data.norge.no/en/data-services/4dabc5ea-b57b-3b68-9e48-2ff993be3f6e/nokkeltall-fra-arsregnskapet
* Brreg forum thread (2024–2025 staff statements on 3-year history and copy API): https://forum.brreg.no/t/api-regnskapsregisteret/118
* Brreg open data / NLOD: https://www.brreg.no/en/use-of-data-from-the-bronnoysund-register-centre/open-data/
* Archived repo: https://github.com/brreg/regnskapsregister-api
