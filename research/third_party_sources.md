# Third-party (non-brreg) free/keyless sources keyed by Norwegian org number

Research date: 2026-10-13. Every test was live, using UA `nordic-company-agent/0.1 (research prototype)` at no more than about 2 req/s per host.
Hit rates are measured against the **eligible universe** (`data/signalpost-company-universe-2025.jsonl.gz`, 411,160 org numbers; 92% AS, 2.4% BRL, 2.1% ESEK, 1.3% STI, 0.6% NUF ...). That file is the population Builderr samples daily companies from.
Helper scripts: `research/scripts/third_party_sources__*.py`. Samples: `research/samples/third_party_sources__*`.

## 0. Executive summary / ranking

| # | Source | Access | Org-number exact? | Batch | Universe hit rate | Requests per 100 cos | Include? |
|---|--------|--------|-------------------|-------|-------------------|----------------------|----------|
| 1 | **GLEIF API** | keyless, CC0 | exact, but **format-sensitive** (`"923 609 016"` and `"923609016"` both occur) | 100 orgnr (200 filter values) per GET | **9.6%** (48/500 random), 25% of those LAPSED | 1 + ~0.6 (parents) | **YES** |
| 2 | **SkatteFUNN lists** (Forskningsrådet xlsx) | keyless file download | exact orgnr column | whole file (2 files) | **3.1%** applied / **2.5%** approved | 2 (per run, cacheable) | **YES** (R&D facts + project summaries) |
| 3 | **Finanstilsynet registry API** | keyless, 1000 req/60 s | exact (`query=<orgnr>`) | `ids=` up to 10,000 per GET; index via `/filter` (171 pages) | **1.8%** (mostly accounting firms, loan brokers, insurance agents) | 0-1 with cached index | **YES** |
| 4 | **Mattilsynet smilefjes CSV** | keyless, daily file | orgnr = **underenhet** (needs parent mapping) | whole file (17 MB) | **~1.7%** (via subunits) | 1 | **YES** (cheap) |
| 5 | **Wikidata** (P2333) | keyless, CC0 | exact, all values plain 9 digits | 100 orgnr per SPARQL/search | **1.1%** (31% for 250+ employees) | 1 (SPARQL) or 2-4 (Action API) | **YES** (website, socials, Wikipedia, ticker, LEI) |
| 6 | **NAV stillingsfeed** (jobs) | public bearer token (no signup) | employer.orgnr = **underenhet** | feed pages of 1000 items; entries 1 per ad | est. 2-4% have active ads | 3-10 pages + 10-30 entries | **YES, if a cache is allowed** (jobs field family) |
| 7 | Arbeidstilsynet **renholdsregisteret** XML | keyless, daily file | exact (hovedenhet + underenheter) | whole file (22 MB) | 0.58% | 1 | yes (cheap) |
| 8 | Forskningsrådet applications CSV (GitHub) | keyless | exact orgnr column | whole file (54 MB) | 0.85% applied, 0.58% granted | 1 | optional |
| 9 | Geonorge address API | keyless, CC BY 4.0 | address -> coords (not orgnr) | 1 per company (bulk CSV 152 MB alternative) | 93% exact first try | 100 | optional / low priority |
| 10 | Arbeidstilsynet BHT XML | keyless | exact | whole file 3.4 MB | 0.02% | 1 | skip/optional |
| - | Arbeidstilsynet JSON APIs (bemanning, bilpleie, asbest) | documented as open | exact (`/virksomheter/{orgnr}`) | full list endpoint | n/a | - | **HTTP 403 from our IP**; retest from an EU IP |
| - | Doffin | **free self-service subscription key** | - | - | low (mostly public buyers) | - | flag; skip unless key |
| - | Patentstyret | **free self-service subscription key** (`/register/v1/IprCasesByCompany`) | orgnr | - | unknown (trademark owners) | - | flag; good candidate if key OK |
| - | Aksjonærregisteret (Skatteetaten) | CSV via **emailed link** (needs an email) / Maskinporten API | orgnr | bulk | ~all AS | - | not automatable without a human; personal data |
| - | Skattelister | press only / ID-porten | - | - | - | - | not usable |

Per 100 companies, the recommended set (1-8) costs roughly **10-25 outbound requests in total**. With cached daily files it is under 15. That leaves almost the whole 2,000-request budget for brreg, regnskap and websites.
NOTE: the evaluation contract says the official batch is currently **1,000 companies** (it may grow to 1,100) under the same 2,000-request cap. At that size, per-company calls (Geonorge, per-orgnr Finanstilsynet search, NAV entries) are **not affordable**. Only batched or bulk sources scale: GLEIF (10 requests per 1,000), Wikidata (10), and the bulk files (1 each).

---

## 1. GLEIF API (api.gleif.org) — RECOMMENDED

* Base: `https://api.gleif.org/api/v1`. No key. Data licence is CC0 (LEI Data Terms of Use). The documented rate limit is **60 requests/min per user**; no rate-limit headers were seen. 429 is the expected error.
* Media type `application/vnd.api+json` (JSON:API). `page[size]` max **200** (500 gives 400). Page-number pagination stops at 10,000 results; use `page[cursor]=*` beyond that.
* **Lookup by org number:** `GET /lei-records?filter[entity.registeredAs]=<v1>,<v2>,...&page[size]=200`
  * **Exact, string-equality match. Format-sensitive!** In 200 Norwegian records, `registeredAs` was spaced `"923 609 016"` in ~85% and plain `"923609016"` in ~15%. One record had a **zero-width space** (`935120​470`).
  * Always send **both formats** for each orgnr in one comma list. 100 orgnr = 200 values gives a 2.7 kB URL and a 1.2-1.9 s response. Tested OK.
  * `filter[fulltext]` and fuzzycompletions do NOT match registeredAs.
  * Optionally add `filter[entity.jurisdiction]=NO`. Note: combining it with the plain format returned 0 for Equinor, because Equinor is stored spaced.
  * After the response, normalise `registeredAs` with `re.sub(r'\D','',s)` to map records back to orgnr.
  * Registration authorities: `RA000472` = Enhetsregisteret (~93%), `RA000473` = Foretaksregisteret, `RA000971` (a few). Accept any of these when the digits match.
* **Hit rate:** 48/500 random universe companies (**9.6%**), 47 AS + 1 FLI. Status `ISSUED` 36, `LAPSED` 12. All had `entity.status=ACTIVE`. There were no duplicate LEIs per orgnr in the sample.
* **Fields** (`data[].attributes`):
  * `lei`
  * `entity.legalName.name`
  * `entity.otherNames[]` (type `PREVIOUS_LEGAL_NAME` etc.; useful for rename history)
  * `entity.legalAddress{addressLines,city,postalCode,region(NO-11),country}` and `entity.headquartersAddress`
  * `entity.registeredAs`, `entity.registeredAt.id`, `entity.jurisdiction`
  * `entity.legalForm.id` (ELF code, e.g. `YI42` = AS, `IQGE` = ASA)
  * `entity.status`
  * `entity.creationDate` (incorporation date)
  * `entity.successorEntity` / `expiration` (for merged or dissolved entities)
  * `registration{initialRegistrationDate,lastUpdateDate,status(ISSUED|LAPSED|RETIRED|...),nextRenewalDate,managingLou,corroborationLevel,validatedAs}`
  * `bic[]`, `ocid`, `spglobal[]`, `conformityFlag`
  * top-level `meta.goldenCopy.publishDate`: use this as the source as-of date. The data refreshes several times daily; we saw publishDate 2026-10-13T00:00Z.
* **Relationships:** the `relationships` object tells you, **without an extra call**, whether a parent exists.
  * `relationships.direct-parent.links` has `lei-record` + `relationship-record` when a parent is reported, or only `reporting-exception` when there is none.
  * Of the 48 random hits, **3 had a direct parent** and 45 a reporting exception. 1 had `direct-children`.
  * Parent record: `GET /lei-records/{lei}/direct-parent` and `/ultimate-parent`. These return the full parent lei-record, including the parent's `registeredAs`. Cross-check the parent orgnr with brreg; the parent may be foreign.
  * Relationship metadata: `GET /lei-records/{lei}/direct-parent-relationship` gives `relationship.type=IS_DIRECTLY_CONSOLIDATED_BY`, `status`, `periods[]` (ACCOUNTING_PERIOD, RELATIONSHIP_PERIOD start), `registration.corroborationLevel`, `validFrom`.
  * Reporting exception: `GET /lei-records/{lei}/direct-parent-reporting-exception` gives `attributes.reason` (`NATURAL_PERSONS`, `NON_CONSOLIDATING`, `NO_KNOWN_PERSON`, ...). "Owned by natural persons" is a useful, explainable fact.
  * Children: `GET /lei-records?filter[ownedBy]=<LEI>` (Equinor ASA: 14 direct children) and `/lei-records/{lei}/direct-children`.
  * `filter[owns]=<childLEI>` returns the parent. A comma list is accepted, but you lose the child-to-parent mapping, so prefer per-LEI calls; they are rare.
  * There is no `/relationship-records` collection endpoint (404).
* ISINs: `GET /lei-records/{lei}/isins` (Equinor: 24 ISINs, mostly bonds). Low value.
* **Matching:** orgnr digits equality is authoritative, because GLEIF validates against brreg (`validatedAt RA000472`). Still store a name-similarity check against the brreg name. A mismatch is usually a rename lag; `otherNames` helps. **LAPSED** means the LEI was not renewed, NOT that the company is inactive. Say so in the explanation.
* Samples:
  * `third_party_sources__gleif_lei_record_equinor.json`
  * `..._gleif_lei_record_with_parent.json` (Equinor Energy AS -> Equinor ASA)
  * `..._gleif_direct_parent_relationship.json`
  * `..._gleif_reporting_exception.json`
  * `..._gleif_batch_random_universe_hits.json`
* Script: `research/scripts/third_party_sources__gleif_batch.py`.

## 2. Wikidata — RECOMMENDED (low hit rate but 1 request per 100 companies)

* P2333 (Norwegian organisation number) currently has **10,401 statements, all plain 9-digit strings**. There are no spaces; the format constraint holds.
* **Hit rate** vs universe: **4,590 / 411,160 = 1.12%**.
  * By size: 0 employees 0.61%; 1-9 1.67%; 10-49 3.79%; 50-249 12.8%; 250+ **31%**.
  * By form: AS 2,304, **STI 1,498 (28% of all foundations; a bulk import)**, FLI 401, ASA 95.
* Property coverage among the 4,590 universe hits:

  | Property | Hits | Share |
  |----------|------|-------|
  | P159 HQ | 4,576 | 99% |
  | P571 inception | 4,165 | |
  | P452 industry | 4,038 | |
  | **P856 website** | 3,600 | **78%** |
  | P1128 employees | 2,477 | |
  | **P2013 Facebook** | 1,249 | |
  | **P2003 Instagram** | 795 | |
  | P1278 LEI | 762 | |
  | P154 logo | 543 | |
  | P749 parent | 477 | |
  | P2002 X/Twitter | 444 | |
  | P127 owned by | 443 | |
  | **P4264 LinkedIn company** | 344 | |
  | P6782 ROR | 324 | |
  | P2397 YouTube | 322 | |
  | P625 coords | 322 | |
  | P355 subsidiaries | 230 | |
  | P169 CEO | 193 | |
  | P3417 Quora | 115 | |
  | P2139 revenue | 111 | |
  | P414 exchange | 102 | |
  | P946 ISIN | 82 | |
  | P576 dissolved | 42 | |

  Sitelinks: nowiki 2,381, enwiki 925, nnwiki 631.
* **Endpoints**
  * **WDQS SPARQL:** `POST https://query.wikidata.org/sparql`, `Accept: application/sparql-results+json`, form field `query`. The query uses `VALUES ?orgnr { "923609016" ... }` with up to 100 values, `OPTIONAL` blocks, `GROUP_CONCAT` and `SERVICE wikibase:label`.
    * The tested 100-orgnr query (3.5 k chars) took **22 s** and returned 10 rows (`samples/third_party_sources__wikidata_sparql_batch_query.rq.txt`, result `..._sparql_batch100_result.json`).
    * **GOTCHA (live today):** WDQS answered **HTTP 429 `retry-after: 1000`** with the message *"Aggressively rate-limiting to 1 req / min - this rule was created during active wdqs outage"*. `query-main.wikidata.org` shares the limit.
    * Plan for at most **1 query/min**, honour `retry-after`, and fall back to the Action API.
    * The Wikimedia UA policy wants contact info in the UA, e.g. `nordic-company-agent/0.1 (https://github.com/<org>/<repo>; research prototype) httpx/0.27`. Do not put a personal email in it.
  * **Action API fallback (worked reliably):**
    1. `GET https://www.wikidata.org/w/api.php?action=query&list=search&srsearch=haswbstatement:P2333=923609016|P2333=984851006|...&srlimit=max&srprop=&srnamespace=0&format=json`. 100 values in one `|`-joined search gave a 2.1 kB URL and a ~2 s response. `srlimit` max is 500 for anonymous users. The search returns QIDs only.
    2. `GET ...?action=wbgetentities&ids=Q1|Q2...(<=50)&props=labels|claims|sitelinks|descriptions&languages=nb|nn|en&format=json` maps P2333 back to orgnr. The Equinor entity alone is 91 kB.
    3. Entity-valued claims (HQ, CEO, industry, parent) need one more `wbgetentities&props=labels` call to resolve labels.

    Total: 3-4 requests per 100 companies.
  * Use GET for the Action API. A body-less POST returned **411 Content Length Required** from Varnish.
  * QLever (`https://qlever.dev/api/wikidata`, formerly qlever.cs.uni-freiburg.de, now 308) is fast and great for offline analysis. It is a research service, its data is dump-based (possibly days old), and it has no published ToS for production. **Use it only for development stats.**
* **GOTCHAS for correctness ("keep profiles current", "wrong company"):**
  1. `wdt:` (truthy) **includes former CEOs** when ranks are not maintained. Telenor returned both Sigve Brekke (end 2024-11-30) and Benedicte Fasmer; Hydro returned Aasheim (ended 2023) and Kallevik. Use `p:P169/ps:P169` with `FILTER NOT EXISTS { ?st pq:P582 ?end }` and report `pq:P580` as the as-of date (`samples/..._wikidata_sparql_qualifiers_ceo_end_dates.json`).
  2. **Wrong-entity social handles:** Telenor ASA's P2003 Instagram is `telenorsverige` (Telenor Sweden). Treat Wikidata socials as *candidates*. Publish them only when corroborated: the handle is linked from the verified company website, or there is a single handle without a country qualifier. Label any group or subsidiary relationship explicitly.
  3. Equinor has ~30 P856 websites, with `P407` language and `P1001` jurisdiction qualifiers. Prefer the one whose domain matches the brreg `hjemmeside`, or the one with jurisdiction Q20 (Norway).
  4. **90 org numbers map to more than 1 item (187 items)**: brand/channel vs company (TVNorge vs Warner Bros. Discovery Norway), predecessor/successor (Sparebanken Vest / Sparebanken Norge), schools sharing a kommune subunit. Pick the item whose label matches the brreg name. Otherwise output `ambiguous`, or label the others as "brand/related item".
  5. **61 items carry more than 1 P2333.** Some P2333 values are **underenhet** numbers (schools, kindergartens). That is harmless for universe hovedenheter, but always check the brreg record type.
  6. **Do NOT publish Wikidata financials** (P2139 revenue, P2295 net profit) as company facts. They are stale (Telenor revenue 2019, preferred rank) and risk the "fabricated or incorrect financial value" disqualification. Use Regnskapsregisteret instead. Employees (P1128) are also stale; brreg `antallAnsatte` wins.
* Useful output per hit:
  * website(s)
  * Facebook / Instagram / LinkedIn / X / YouTube handles, converted to URLs: `https://www.facebook.com/{P2013}`, `https://www.instagram.com/{P2003}`, `https://www.linkedin.com/company/{P4264}`, `https://x.com/{P2002}`, `https://www.youtube.com/channel/{P2397}`
  * no/en Wikipedia URL (good for the "explanations" score)
  * description (`schema:description@nb`)
  * logo (Commons FilePath)
  * ticker (P414 + pq:P249), ISIN, LEI (cross-check with GLEIF)
  * parent org with its own P2333 (cross-check with brreg/GLEIF)
  * inception, dissolved (P576)

  Evidence = entity URL `https://www.wikidata.org/wiki/Qxxx` + property id + statement GUID.
* Scripts: `third_party_sources__wd_sparql.py`, `third_party_sources__wd_action.py`.

## 3. Kartverket / Geonorge address API — OPTIONAL

* `GET https://ws.geonorge.no/adresser/v1/sok` (also `/punktsok` for reverse lookup). No auth ("Det er ikke nødvendig med innlogging"). The API returns at most the first 10,000 results; for bulk use, download the files. Kartverket address data is open (CC BY 4.0). No rate-limit headers were seen.
* OpenAPI: `https://ws.geonorge.no/adresser/v1/openapi.json`.
* Parameters: `sok`, `fuzzy`, `sokemodus`, `adressenavn`, `adressetekst`, `nummer`, `bokstav` (**send `bokstav=` empty to require no letter**), `postnummer`, `kommunenummer`, `objtype`, `utkoordsys` (4258 = ETRS89 lat/lon), `treffPerSide`, `side`, `filtrer` (field projection), `asciiKompatibel`.
* Response fields: `adresser[].{adressetekst, adressenavn, nummer, bokstav, postnummer, poststed, kommunenummer, kommunenavn, gardsnummer, bruksnummer, festenummer, objtype (Vegadresse|Matrikkeladresse), representasjonspunkt{epsg,lat,lon}, oppdateringsdato, stedfestingverifisert}`; `metadata.totaltAntallTreff`.
* **Exact-match strategy** (tested on 100 random universe companies, 102 requests):
  1. Take brreg `forretningsadresse` (fallback `postadresse`). Pick the last `adresse[]` line that matches `^<street> <nr><letter?>`. Skip `c/o`, `Postboks`, `Pb`, and empty lines.
  2. `sok?adressetekst=<street nr+letter>&postnummer=<pnr>&treffPerSide=3&utkoordsys=4258`. Accept only if exactly 1 hit. This matched **93/100** with one request.
  3. Fallback: `adressenavn=<street>&nummer=<nr>&bokstav=<letter or empty>&kommunenummer=<kn>`. Post-filter `adressenavn` by case-insensitive equality, because `adressenavn` is a word/prefix match: "Kildevegen" also hits "Østre Kildeveg".
  4. Fallback: `sok=... &fuzzy=true` gives low confidence. In the test, 2 of 3 fuzzy hits had the **wrong letter** ("Heimdalsveien 14" -> "Heimdalsvegen 14A"; "Uelands gate 57 - 4" -> "57A"). Publish fuzzy only with `confidence=low`, or not at all.
  5. 4/100 had no geocodable street line ("Ørnes", "kjevikdalen", empty) and were reported as `not_available`.
* Bulk alternative (zero requests at run time): `https://nedlasting.geonorge.no/geonorge/Basisdata/MatrikkelenAdresse/CSV/Basisdata_0000_Norge_4258_MatrikkelenAdresse_CSV.zip` (151.7 MB, Last-Modified 2026-10-10). Can be cached and declared.
* Value for scoring: coordinates are probably not a scored field family. **Low priority**; the cost is 1 request per company. Samples: `..._geonorge_adresser_sok_structured_equinor.json`, `..._geonorge_geocode_results_sample.json`. Script: `third_party_sources__geocode.py`.

## 4. Finanstilsynet registry (Virksomhetsregisteret) — RECOMMENDED via a cached index

* Swagger: `https://api.finanstilsynet.no/registry/swagger/v2/swagger.json`. Base: `https://api.finanstilsynet.no/registry/v2`. No key.
* Documented rate limit: **1000 req / 60 s per IP**; `/report/json` 5 req / 60 s. GET responses are cached for up to 3600 s (`/legal-entities?ids` is not cached). Licence: data.norge.no says "not provided"; it is a public authority register. Contact: virksomhetsregisteret@finanstilsynet.no.
* `api.finanstilsynet.no/` root returns 403 and `register.finanstilsynet.no` does not exist. The correct path prefix is `/registry/v2`.
* Endpoints:
  * `GET /legal-entities/search?query=<orgnr>` gives an exact hit: `{legalEntityId, finanstilsynetId, organisationNumber, leiCode, name}`. 1 request per orgnr, so too costly for 1,000 companies.
  * `GET /legal-entities?ids=<id>&ids=<id>...` (explode style, **maxItems 10000**) returns full records: `licences[] {licenceType{code,name.norwegian/english}, registeredDate, supervisoryAuthority, serviceProviderType (Licensed|Agent|...), licensedEntity, services[], borderCrossingActivity[]}`, `addresses[]`, `relationsToOther[]`, `participants`, `participatesIn`, `leiCode`, `auditorNumber`, `remarks`.
    * DNB is 445 kB because of cross-border services.
    * Filter `licences` to those where `licensedEntity.legalEntityId == legalEntityId` and `serviceProviderType == "Licensed"` to get the entity's *own* licences. The rest are agent relations for other companies.
  * `GET /legal-entities/filter?query=&entityType=Company&countryCodes=NOR&pageSize=50&page=N`: total **8,510** NOR companies, 50 per page, max 300 pages. Licences are inline. A full index takes **171 requests** (~5 min, slow responses). The endpoint lists entities **currently** holding at least one licence; lost licences disappear.
  * `/licence-types`: 54 codes (sample saved).
* **Hit rate:** 7,404 universe orgnrs (**1.80%**).
  * REGS external accounting firm: 2,191
  * LFORMIDL loan intermediary: 2,066
  * AKSFORAGV ancillary insurance agent (car dealers etc.): 1,929
  * ALTINVAIF AIF: 515
  * EIEMGLFTK estate agency: 454
  * REVS audit firm: 401
  * VERPAPFOND UCITS: 297
  * FORAGNTFTK insurance agency: 282
  * FOAVALIN AIFM: 236
  * FFOR investment firm: 90
  * BANK: 88
* **Recommendation:** build the orgnr -> {legalEntityId, licences} index from `/filter`. Build it offline or at most daily, as a declared cache. At run time, do one `/legal-entities?ids=...` call for the batch's hits to get fresh details and evidence.
  * Evidence URL for humans: `https://www.finanstilsynet.no/virksomhetsregisteret/detalj/?id=<legalEntityId>`.
  * Non-hits become `not_applicable/not_available` with the reason "not in Finanstilsynet register of current licences as of <index date>".
* Samples: `..._finanstilsynet_registry_swagger_v2.json`, `..._finanstilsynet_filter_page1.json` (trimmed), `..._finanstilsynet_legal_entity_dnb_trimmed.json`, `..._finanstilsynet_licence_types.json`. Script: `third_party_sources__ft_index.py`.

## 5. Arbeidstilsynet open registers

* **Renholdsregisteret** (cleaning companies, NACE 81.2):
  * `GET https://registerdata.arbeidstilsynet.no/renhold_register.xml`: 22.3 MB, regenerated daily (`<DatoTidGenerert>2026-10-13T03:04+02:00`), XSD `RegisterXML6.xsd`. Keyless, open licence.
  * Structure: `Register/Virksomhet/{Hovedenhet{Organisasjonsnummer, Organisasjonsform, Navn, Næringskoder/Kode@verdi, Godkjenningsstatus, Kontaktinformasjon{Telefon,Mobil,Webadresse}, Stedsinformasjon{Postadresse,Forretningsadresse}}, Underenheter/Avdeling{...}}`.
  * 7,937 hovedenheter. Status: Ikke godkjent 2,965; Godkjent uten ansatte 2,870; Godkjent med ansatte 1,874; HMS-kortbestilling under behandling 168; Søknad under behandling 60.
  * **Universe hit rate 0.58%** (2,376). 1 request per run.
  * Also gives phone numbers (2,454) and websites (648). "Ikke godkjent" (not approved) is a decision-useful compliance fact for cleaning companies.
* **Bedriftshelsetjeneste (BHT)**: `https://registerdata.arbeidstilsynet.no/bht_register.xml` (3.4 MB, same schema). 128 approved, 86 in universe (0.02%). Optional.
* **JSON APIs** (documented in the embedded OpenAPI at `https://openapi.arbeidstilsynet.no/`; throttling 5 req/s; header `Content-Version: 1.2`):
  * `https://data.arbeidstilsynet.no/bemanningsforetaksregisteret2/api/virksomheter[/{orgnummer}]`
  * `https://data.arbeidstilsynet.no/bilpleieregisteret/api/virksomheter[/{orgnummer}]`
  * `https://data.arbeidstilsynet.no/asbestregisteret/api/virksomheter/registerstatus` and `/virksomheter/{orgnummer}/registerstatus`
  * **All returned `403 {"message":"Forbidden"}` (AWS API Gateway ForbiddenException) from our IP (14.139.180.52, India),** with or without UA. Likely a geo/WAF restriction; the docs say they are "åpent for hele verden".
  * Other XML file names (`bemanning_register.xml`, `bilpleie_register.xml`, `asbest_register.xml`) gave 403 as well.
  * **Action:** retest from an EU/NO host. If they work, the full-list endpoints give the whole register in one request each. Bemanning (staffing agencies) is likely about 0.5-1% of AS.

## 6. Mattilsynet smilefjes (food-safety inspections) — RECOMMENDED (cheap)

* hotell.difi.no is dead (no DNS/connection).
* Current source: `GET https://data.mattilsynet.no/smilefjes-tilsyn.csv`: 17.2 MB, `;`-separated, UTF-8, Last-Modified daily (2026-10-13 02:54 GMT), ETag supported, so use `If-None-Match` for refresh.
* Columns: `tilsynsobjektid;orgnummer;navn;adrlinje1;adrlinje2;postnr;poststed;tilsynid;sakref;status;dato(DDMMYYYY);total_karakter;tilsynsbesoektype;tema1_no;...;karakter1..4`.
* 48,619 inspections, 2015-03-30 .. 2026-10-12; 4,608 in the last 365 days.
* **`orgnummer` is the UNDERENHET (location) number:** 8,670 distinct, 392/400 sampled are active underenheter. Map it to the parent via brreg `underenheter?overordnetEnhet=<orgnr>` (already needed for locations) or via the bulk underenheter file.
  * Only 82 universe orgnrs match directly. Through the parent mapping, about **1.7%** of universe companies have inspections.
* Grade semantics: `total_karakter` 0-1 = smiling face, 2 = straight mouth, 3 = sad face (also 4/5 = not assessed). Publish the latest inspection date + grade per location, with the parent relationship labelled ("location X of company Y").
* Sample: `..._mattilsynet_smilefjes_tilsyn_head40.csv`.

## 7. Other sources

### NAV stillingsfeed (job ads), arbeidsplassen.nav.no
* Token: `GET https://pam-stilling-feed.nav.no/api/publicToken` returns a public JWT (rotates irregularly). Send it as `Authorization: Bearer`. The terms are at `https://arbeidsplassen.nav.no/vilkar-api`: free, and you must remove inactive ads.
* Feed: `GET /api/v1/feed`. Each page holds **1000 items**. `If-Modified-Since: <RFC1123>` jumps to the page around that date; follow `next_url`.
  * Items: `{id, url:/api/v1/feedentry/<uuid>, title, date_modified, _feed_entry{uuid,status ACTIVE|INACTIVE,title,businessName,municipal,sistEndret}}`. **No orgnr in the feed items.**
* Entry: `GET /api/v1/feedentry/<uuid>` returns `ad_content{title, published, expires, updated, link (public arbeidsplassen URL), employer{name, orgnr, description, homepage}, workLocations[], jobtitle, positioncount, engagementtype, extent, sector, occupationCategories[]}`.
  * **`employer.orgnr` is the UNDERENHET** in 25/25 sampled; map to the parent through brreg.
* Volume: 3 days produced 7 pages, 5,410 items, 3,693 ACTIVE events, 1,579 distinct active ads, 1,236 distinct business names (~500 new or updated active ads per day).
* Expected coverage: a few percent of universe companies have an active ad at any time (estimate, not measured).
* Cost model:
  * Keep a cache of feed items (cheap, ~3 pages/day).
  * Fetch entries only for candidate ads whose `businessName` matches the batch company's name or one of its subunit names. A name match is a candidate only; **confirm by orgnr from the entry**.
  * Replaying 60 days without a cache is ~150 pages, too many for one daily run.
* Samples: `..._nav_stillingsfeed_page_trimmed.json`, `..._nav_stillingsfeed_feedentry_active.json`.

### SkatteFUNN (Forskningsrådet), high value
* Files:
  * `https://www.forskningsradet.no/siteassets/skattefunn/tall/260902-webliste-skattefunn.xlsx` (1.7 MB; new system May 2024+). Columns: `Søknadsdato, Søknadsnummer, Prosjektnummer, Prosjektansvarlig, Prosjekttittel, Organisasjonsnr, Fylke, Kommune, Poststed, Godkjent (JA/NEI/None), Fra-år, Til-år, Vedtaksdato, Populærvitenskapelig sammendrag`.
  * `.../skattefunn-innsendte-soknader-2002-2024.xlsx` (12.2 MB). Columns: `Innsendt dato, Prosjektnummer, Bedriftsnavn, Prosjekttittel, Organisasjonsnummer, Fylke, Kommunenavn, Søknad godkjent (JA/NEI), Søknad avslått, Prosjektets fra-år, Prosjektets til-år, Vedtaksdato, Populærvitenskapelig sammendrag`.
* The file name is date-stamped (260902 = 2026-09-02), so **scrape the link from `https://www.forskningsradet.no/skattefunn/suksesshistorier/`**.
* 24,797 distinct orgnrs. **Universe: 3.13% applied, 2.50% approved (JA).**
* The project title and popular-science summary are company-specific descriptive text, good for "useful explanations".
* Sample: `..._skattefunn_webliste_first15.json`.

### Forskningsrådet applications/grants
* `https://raw.githubusercontent.com/Forskningsradet/open-data/main/datasets/soknader2/dataset.csv`: 54 MB, 88,268 rows, `organisasjonsnummer` column, status `prosjektfase` (Avslag/Avsluttet/Bevilgning/Vurderes), `tildelt_belop`, programme. NLOD.
* Universe: 0.85% applied, 0.58% with a grant.
* Also `datasets/bevilgningereu`.
* Prosjektbanken has no documented API.
* Sample: `..._forskningsradet_soknader2_head30.csv`.

### Other sources checked
* **Doffin**: `api.doffin.no/public/v2/search` returns `401 missing subscription key`. Keys are a free self-service Azure APIM subscription; flag it. Mostly public buyers; winners appear only in award notices. Low hit rate for random AS.
* **Patentstyret**: `developer.patentstyret.no` (Azure APIM; **free self-service key**, NLOD, updated every 15 min). `/register/v1/IprCasesByCompany` looks up by org number, then `/register/Trademark/v1/{applicationId}`. `api.patentstyret.no` returned 502 without a key. The legacy `ws.patentstyret.no` (keyless) is keyed by application id only. A good candidate *if* the user accepts a free key; hit rate not measured.
* **Aksjonærregisteret** (Skatteetaten): bulk CSV for all AS (31.12 holdings), but only via an **emailed one-week download link**, and the APIs need Maskinporten plus a Norwegian org. It contains personal data (names, birth year). Corporate shareholders (orgnr) would give ownership edges for nearly every AS. Not automatable here; needs a human with an email (do not use a personal email in code).
* **Skattelister**: press-only bulk (NOK 1,500/list), otherwise ID-porten login. Not usable.
* **data.norge.no catalogue**: `POST https://search.api.fellesdatakatalog.digdir.no/search/datasets` with `{"query":"organisasjonsnummer","pagination":{"size":100,"page":0}}`. Works keyless; 135 hits. Relevant orgnr-keyed datasets besides the above:
  * Statens vegvesen *Transportørregister* (haulage/passenger licences; candidate for transport AS; not tested)
  * Luftfartstilsynet *Registrerte luftfartøy med eiere*
  * Nkom *ekomnett-autorisasjoner* and *radio vendor registrations*
  * Landbruksdirektoratet *produksjonstilskudd / leveranser* (farms, mostly ENK, outside the universe)
  * Digdir *Enhetene i offentlig forvaltning* (public sector, outside the universe)
  * Brreg *Nordiske virksomhetsdata*
  * Arbeidstilsynet BHT/asbest/bemanning

  The dataset detail API host `dataset.api.fellesdatakatalog.digdir.no` did not resolve, and `datasets.fellesdatakatalog.digdir.no/datasets/<id>` returned 404 for turtle. Use the data.norge.no web pages to find distribution URLs.
* **Euronext/Oslo Børs**: no keyless official API; restrictive terms. Ticker/ISIN come from Wikidata (P414/P249, P946) and GLEIF `/isins`. The universe has only 189 ASA.
* **Not found / not useful**: Lånekassen, Statsforvalteren, Datatilsynet, SSB (no open firm-level orgnr data), Norsk Tipping grasrotandel (no documented open API; terms unclear; not used). These were not tested in depth.
* **Avoid (ToS forbid scraping):** proff.no, purehelp.no, 1881.no, gulesider.no.

## 8. Recommended integration (per daily batch)

1. **Bulk/daily files** (download once per run, or use a declared cache, with ETag/If-Modified-Since): renhold XML, smilefjes CSV, SkatteFUNN xlsx (x2), optionally the Forskningsrådet CSV and BHT XML. That is 4-6 requests regardless of batch size. Build orgnr indexes in memory. Subunit-keyed sources (smilefjes, NAV) are joined through the batch's brreg underenheter list.
2. **GLEIF**: 1 request per 100 orgnr (both formats) + 1 request per hit that has `direct-parent.links.lei-record` (~6% of hits).
3. **Wikidata**: 1 SPARQL per 100 orgnr (honour the 1 req/min rule and retry-after), with the Action API fallback (search + wbgetentities). Publish websites and socials as *candidates corroborated by the website*. Filter ended statements. Never publish Wikidata financials.
4. **Finanstilsynet**: cached `/filter` index (refresh weekly or daily, 171 requests, outside the run) + 1 `/legal-entities?ids=` call per batch.
5. **NAV jobs**: only if a cache job is acceptable. Otherwise skip, or limit to a candidate-entry budget.
6. **Geonorge**: only if the budget allows (1 per company) or via the cached bulk CSV.
7. Every fact records: source URL (API URL + human URL), `retrieved_at`, source as-of (GLEIF `goldenCopy.publishDate` / `registration.lastUpdateDate`; Wikidata statement `pq:P580/P585` + entity `modified`; file `Last-Modified` / `DatoTidGenerert`; NAV `sistEndret`), evidence (JSON path / XML XPath / CSV row key), and match reason. Match reasons:
   * "registeredAs digits == orgnr; validatedAt RA000472"
   * "P2333 == orgnr; label≈brreg name"
   * "underenhet <x> has overordnetEnhet == orgnr (brreg)"
