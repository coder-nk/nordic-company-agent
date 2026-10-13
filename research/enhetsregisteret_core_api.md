# Enhetsregisteret core API (data.brreg.no): live probe findings

Probed live on 2026-10-13 from this machine. All samples are in `research/samples/enhetsregisteret__*`.
Each JSON sample is wrapped as `{"_request": {url, status, retrieved_at, headers}, "body": <raw response>}`.
Samples are capped at about 200 KB.

The OpenAPI spec is embedded in the Redoc page. It was extracted to `samples/enhetsregisteret__openapi_spec_extracted.json`
(OpenAPI 3, "Enhetsregisterets dokumentasjon for åpne data 2.0.0"). Use it to generate models.

---

## 0. TL;DR for the engineers building the agent

| Need | Endpoint | Batch? | Notes |
|---|---|---|---|
| Core facts for N companies | `GET /enhetsregisteret/api/enheter?organisasjonsnummer=a,b,c,...&size=N` | **Yes, up to 2000 orgnrs per request** (20 KB URL works). 2001 gives HTTP 400. | Body is byte-identical to the single GET per entity. **Deleted (SlettetEnhet) and 410-removed entities are silently omitted**, as are sub-unit numbers. Response order is not request order. |
| One company | `GET /enhetsregisteret/api/enheter/{orgnr}` | no | 200 `respons_klasse:"Enhet"` or `"SlettetEnhet"`. 410 for removed entities. 404 for unknown numbers. 400 if the input is not exactly 9 digits. |
| Sub-unit numbers given as input | `GET /enhetsregisteret/api/underenheter?organisasjonsnummer=a,b,...` | Yes (comma list) | A sub-unit number on `/enheter/{n}` gives **404**. |
| Sub-units of a company | `GET /enhetsregisteret/api/underenheter?overordnetEnhet={orgnr}&size=...` | **No**: only one parent per call (a comma list gives 400) | Paging cap: (page+1)*size ≤ 10 000. |
| Roles (board, CEO, auditor, ...) | `GET /enhetsregisteret/api/enheter/{orgnr}/roller` | **No** batch endpoint | 404 for deleted, unknown and UTLA entities. Bulk alternative: `/api/roller/totalbestand` (130 MB gz). |
| Group / parent company | `GET /enhetsregisteret/api/konsernstruktur/{orgnr}` | no | **New 2026-06-24.** Returns the full tree from the ultimate parent, even when queried with a subsidiary. 404 if not in a group. |
| Has this changed since my last fetch? | `GET /enhetsregisteret/api/oppdateringer/enheter?organisasjonsnummer=a,b,...&dato=<ISO>` (and `/oppdateringer/underenheter`, `/oppdateringer/roller?organisasjonsnummer=...&afterTime=...`) | **Yes** (comma list; 500 tested) | Gives per-org publication timestamps plus a JSON-Patch (`includeChanges=true`). This is the "as-of / last changed" date the entity record itself lacks. |
| Signatur / prokura | **Not in the open API** | n/a | Only in the server-rendered HTML of `virksomhet.brreg.no` (RSC payload key `"signProk"`) and in legacy `w2.brreg.no/enhet/sok/detalj.jsp`. See section 7. |
| Evidence URL for humans | `https://virksomhet.brreg.no/nb/oppslag/enheter/{orgnr}` (sub-units: `/nb/oppslag/underenheter/{orgnr}`) | n/a | Works. `https://data.brreg.no/enhetsregisteret/oppslag/enheter/{orgnr}` 308-redirects there. |

**Request budget for 100 companies:**
* 1 batch call for entities
* plus 1 batch call on `/underenheter?organisasjonsnummer=` for numbers that were not found
* plus individual GETs only for still-missing numbers (to tell deleted, 410 and unknown apart)
* plus 100 role calls
* plus about 100 konsernstruktur calls (only where `erIKonsern=true`)
* plus about 30–100 sub-unit list calls

That comes to roughly **250–350 requests**. With HTTP keep-alive each call takes about 0.3 s from here; without keep-alive it is about 1.2 s.

For **1,000–1,100 companies** (the builderr v2 batch size), use bulk files for roles and sub-units. Three bulk downloads cost 3 requests,
about 430 MB gz in total, and took roughly 0.3–0.5 MB/s from this host. Or plan around the per-company role calls (about 1,100).

---

## 1. Base URLs, versions, headers, limits

* Base: `https://data.brreg.no/enhetsregisteret/api`. The root `GET /enhetsregisteret/api` lists HAL links.
* Docs (Redoc): `https://data.brreg.no/enhetsregisteret/api/dokumentasjon/no/index.html` (English: `/dokumentasjon/en/index.html`).
  Swagger UI: `.../dokumentasjon/no/swagger-ui.html`.
* Media types:
  * Default `application/json` (HAL, latest version).
  * Explicit v2: `Accept: application/vnd.brreg.enhetsregisteret.enhet.v2+json` gives the identical body.
  * `Accept: ...enhet.v1+json`, `text/html` or `application/xml` give **406** `application/problem+json`. `*/*` works.
  * **Recommendation:** send `Accept: application/json`.
* Response headers on lookup and search endpoints:
  * `cache-control: no-cache, no-store, max-age=0, must-revalidate`, `pragma: no-cache`, `expires: 0`.
  * **No ETag, no Last-Modified, no rate-limit headers, no gzip.** The server ignores `Accept-Encoding: gzip`: `?size=100` was 158,706 bytes with or without it.
  * Responses are `transfer-encoding: chunked` with BIG-IP load-balancer cookies, which can be ignored.
  * **Conditional GET is not supported.** `If-None-Match` and `If-Modified-Since` return 200 with the full body.
* Bulk files (`/enheter/lastned`, `/underenheter/lastned`, `/enheter/lastned/csv`, `/roller/totalbestand`) do send `etag` and `last-modified`.
  * The last-modified value is in a **non-RFC format**: `Tue Oct 13 04:26:35 CEST 2026`.
  * They send `content-length` and `accept-ranges: bytes`, but **Range is ignored** (you get 200 with the full body) and **If-None-Match still returns 200**.
  * Compare the etag yourself with a HEAD request; HEAD works.
  * The files are produced nightly at about 04:00–04:30 CEST.
* HEAD works on `/enheter/{orgnr}` and `/underenheter/{orgnr}` (200/404). It is not useful for telling deleted entities apart (they also give 200).
* **Rate limits:** none are documented and no 429 was seen in about 250 requests at ≤1.7 req/s. Brreg's guidance is "be considerate; use bulk + oppdateringer for full copies".
  * Recommendation: ≤2–4 concurrent requests, exponential backoff on 429/5xx, and httpx keep-alive.
  * Latency from this host: TCP+TLS handshake about 0.9 s, server time under 50 ms, round trip about 0.3 s. **Reuse connections.**
* `robots.txt`:
  * data.brreg.no reset the connection when asked for robots.txt.
  * virksomhet.brreg.no: `Allow: /`.
  * www.brreg.no: `Crawl-delay: 10`, disallows pdf files and a few paths.
* Search and paging:
  * `size` defaults to 20. **`size*(page+1) ≤ 10 000`**, otherwise 400 `"size * (page+1) kan ikke overstige 10_000"`.
  * `page` is 0-based. The `page` object has `{number,size,totalElements,totalPages}`.
* Totals today: 1,177,983 active enheter and 868,065 active underenheter.

### Errors (verified)
| Input | Status | Body |
|---|---|---|
| `12345678`, `9236090160`, `9ECD01011`, `923%20609%20016` | 400 | `{"tidsstempel","status":400,"feilmelding":"Feilaktig forespørsel","valideringsfeil":[{"feilaktigVerdi","feilmelding":"Organisasjonsnummer må være et nummer med nøyaktig 9 siffer","parametere"}],"sti","antallFeil"}` |
| `123456789` (bad MOD11) | **404 with an empty body**. The API does **not** validate the checksum. | |
| `976820479` (valid MOD11, never existed) | 404 with an empty body (also on `/roller`, `/underenheter`, `/konsernstruktur`) | |
| Sub-unit number on `/enheter/{n}`, or main-unit number on `/underenheter/{n}` | 404 | |
| Deleted entity | **200**, `respons_klasse:"SlettetEnhet"` | `{organisasjonsnummer, navn, organisasjonsform, historiskeNavn, slettedato, _links}` |
| Removed entity (legal reasons; update type `Fjernet`) | **410** | `{"organisasjonsnummer","slettedato","_links"}`. You **must also purge** any cached copy. |
| `/enheter/{deleted}/roller` | 404 | |
| `/enheter/{UTLA}/roller` | 404 (no roles) | |
| Missing roles on `/konsernstruktur/{n}` for an entity not in a group | 404 | |

Samples: `error_bad_checksum`, `error_letters`, `error_valid_nonexist`, `enhet_SLETTET_971422556`, `enhet_GONE410_*`, `underenhet_GONE410_*`, `underenhet_SlettetUnderEnhet_*`.

### Org-number MOD11 rule (validate client-side; the API won't)
```
weights = [3,2,7,6,5,4,3,2]            # for digits 1..8
s = sum(int(d)*w for d,w in zip(orgnr[:8], weights))
r = 11 - (s % 11)
check = 0 if r == 11 else r            # r == 10 means INVALID number
valid = len(orgnr)==9 and orgnr.isdigit() and r != 10 and check == int(orgnr[8])
```
* Normalise input first: strip spaces, dots, `NO` prefix and `MVA` suffix.
* Main units and sub-units share the same number space and the same rule.
* Verified: 923609016, 958935420 and 974760673 are valid; 123456789 is invalid. `000000000` passes MOD11 but does not exist.

---

## 2. `GET /enheter/{orgnr}`: every field seen (35 diverse entities probed)

Types probed (samples are `enhet_<TYPE>_<orgnr>.json` and `roller_<TYPE>_<orgnr>.json`):

| Type | Org number | Entity |
|---|---|---|
| AS | 930459615 | 1850 NUMMEROPPLYSNINGEN AS |
| AS | 929431650 | |
| AS (new) | 938661723 | |
| ASA | 923609016 | Equinor |
| ASA | 984851006 | DNB Bank |
| ENK | 969444062 | |
| NUF | 920058817 | Nordea Bank Abp NUF |
| FLI | 943838240 | 4H Norge |
| BRL | 992614293 | |
| ESEK | 931071475 | |
| SA | 947942638 | TINE |
| STI | 984549520 | |
| ANS | 990153884 | |
| DA | 993231223 | |
| KOMM | 958935420 | Oslo kommune |
| FYLK | 921707134 | |
| STAT | 971524960 | Stortinget |
| ORGL | 974760673 | Registerenheten i Brønnøysund |
| SF | 917082308 | Bane NOR |
| IKS | 830301402 | |
| KF | 974795035 | |
| SPA | 937901003 | SpareBank 1 SMN |
| KS | 922240906 | |
| UTLA | 995783568 | |
| UTLA | 976779649 | |
| GFS | 939717609 | |
| KBO (konkursbo) | 937662068 | |
| PRE | 993055956 | |
| SAM | 924964448 | |
| BBL | 940007801 | |
| Konkurs | 935567645 | |
| Konkurs (new) | 937614446 | |
| Under avvikling | 812467182 | |
| Under tvangsavvikling | 931744682 | |
| Tvangsavviklet | 816373972 | |
| Party (partiregisteret) | 971526939 | Arbeiderpartiet |
| With FADR påtegning | 988601764 | |
| With hjelpeenhetskode | 932436620 | |
| Deleted | 971422556 | |
| 410 | 934464516 etc. | |

Verified identities of the "known" test numbers:

| Org number | Identity |
|---|---|
| 923609016 | EQUINOR ASA |
| 984851006 | DNB BANK ASA |
| **976820479** | **does not exist (404)**. Telenor ASA is **982463718**. |
| **914778271** | **Norsk Hydro ASA** (not Brønnøysundregistrene). Brreg's own number is **974760673**, REGISTERENHETEN I BRØNNØYSUND (ORGL under NFD 912660680). |
| **971524960** | **STORTINGET** (STAT), not Oslo kommune. Oslo kommune is **958935420**. |

### 2.1 Field list (Enhet, `respons_klasse:"Enhet"`)
All fields are **omitted when empty**: there are no nulls, except `paategninger: []` and `historiskeNavn: []`, which can be empty arrays.

| Field | Type | Meaning / notes | Example |
|---|---|---|---|
| `respons_klasse` | str | `"Enhet"` or `"SlettetEnhet"` (discriminator) | `"Enhet"` |
| `organisasjonsnummer` | str(9) | | `"923609016"` |
| `navn` | str | Current name (registered spelling; mixed case possible) | `"EQUINOR ASA"` |
| `organisasjonsform` | {kode, beskrivelse, utgaatt?, _links} | Legal form. 42 codes for enheter: ADOS ANNA ANS AS ASA BA BBL BO BRL DA ENK EOFG ESEK FKF FLI FYLK GFS IKJP IKS KBO KF KIRK KOMM KS KTRF NUF OPMV ORGL PERS PK PRE SA SAM SE SF SPA STAT STI SÆR TVAM UTLA VPFO. Sub-units: BEDR, AAFY. (`codelist_organisasjonsformer_*`) | `{"kode":"ASA","beskrivelse":"Allmennaksjeselskap"}` |
| `historiskeNavn[]` | [{navn, fraDato, tilDato}] | **Added 2026-06-16.** Previous names with **timestamps `"YYYY-MM-DD HH:MM:SS"`** (not ISO 'T'). Ordered oldest first. The current name is not included. Gaps can occur. | Equinor: Den norske stats oljeselskap a.s → STATOIL ASA → STATOILHYDRO ASA → STATOIL ASA |
| `forretningsadresse` | {adresse[], postnummer, poststed, kommunenummer, kommune, landkode, land} | Business address. `adresse` is an **array of lines**, possibly `[]`. Foreign: no kommunenummer. | `Forusbeen 50, 4035 STAVANGER, 1103` |
| `postadresse` | same shape | Postal address (absent for about 40% of entities) | `Postboks 8500` |
| `naeringskode1/2/3` | {kode, beskrivelse} | **SN2025 codes (NACE Rev. 2.1-based) are already live.** Old SN2007 codes return 0 hits (`62.010`, `63.110`, `68.209`, `41.109`, `47.111`); new ones exist (`62.100` Dataprogrammeringstjenester, `63.100`, `41.000`, `68.320`). The docs' examples (41.109) are outdated. **There are no separate SN2007/SN2025 fields** and no "standard" indicator. `00.000 "Uoppgitt"` is a placeholder (treat as unknown). | `{"kode":"06.100","beskrivelse":"Utvinning av råolje"}` |
| `hjelpeenhetskode` | {kode, beskrivelse} | Auxiliary-unit code (e.g. 70.100 Hovedkontortjenester). Present on enheter and underenheter. Display as "Yter tjenester til eget konsern". | `932436620` |
| `antallAnsatte` | int | Employees (from NAV Aa-registeret). **Omitted when 0–4.** Use with `harRegistrertAntallAnsatte`. | 21272 |
| `harRegistrertAntallAnsatte` | bool | true and `antallAnsatte` missing means **1–4 employees**. false means none registered. | true |
| `registreringsdatoAntallAnsatteNAVAaregisteret` | date | as-of date of the employee count in NAV | `"2026-09-10"` |
| `registreringsdatoAntallAnsatteEnhetsregisteret` | date | when ER stored that count (updated monthly, around the 10th–15th) | `"2026-09-14"` |
| `hjemmeside` | str | Website **without scheme**, sometimes with a trailing `/` | `"www.equinor.com"`, `"www.oslo.kommune.no/"` |
| `epostadresse` | str | **Contact e-mail is present** (since 2024-10). Often absent for AS/ASA, common for FLI/KOMM/BRL. | `"4hnorge@4h.no"` |
| `telefon` | str | Free-format: `"51 99 00 00"`, `"02 180"`, `"03080"`, `"447768396480"` | |
| `mobil` | str | | `"926 33 798"` |
| `stiftelsesdato` | date | Founding date. **Absent for ENK, KOMM, FYLK, STAT, ORGL, NUF.** `1900-01-01` appears as a placeholder (Arbeiderpartiet), so treat it as unknown. | `"1972-09-18"` |
| `registreringsdatoEnhetsregisteret` | date | ER registration date (ER started in 1995, so older entities show 1995) | `"1995-03-12"` |
| `registrertIForetaksregisteret` + `registreringsdatoForetaksregisteret` | bool + date | Register of Business Enterprises | `true`, `"1988-04-28"` |
| `registrertIMvaregisteret` + `registreringsdatoMerverdiavgiftsregisteret` + `registreringsdatoMerverdiavgiftsregisteretEnhetsregisteret` | bool + dates | VAT-registered (the VAT number is `NO{orgnr}MVA`) | `"1989-07-01"` |
| `frivilligMvaRegistrertBeskrivelser[]` + `registreringsdatoFrivilligMerverdiavgiftsregisteret` | [str] + date | voluntary VAT registration | `["Utleier av bygg eller anlegg"]` |
| `registrertIFrivillighetsregisteret` + `registreringsdatoFrivillighetsregisteret` | bool + date | volunteer register (FLI/STI). Details: see section 8. | |
| `registrertIStiftelsesregisteret` | bool | | |
| `registrertIPartiregisteret` + `registreringsdatoPartiregisteret` | bool + date | political parties (24 entities) | |
| `institusjonellSektorkode` | {kode, beskrivelse} | SSB institutional sector | `2100` Private aksjeselskaper mv., `1120` Statlig eide AS, `3200` Banker, `6100` Statsforvaltningen, `6500` Kommuneforvaltningen, `7000` Ideelle org., `8200` Personlig næringsdrivende, `8300` Borettslag, `2300` Personlige foretak, `1510`, `4500`, `5700` |
| `sisteInnsendteAarsregnskap` | str(YYYY) | Last annual accounts filed. A **useful pointer** to Regnskapsregisteret. | `"2025"` |
| `kapital` | {belop, valuta, type, innfortDato, antallAksjer?, innbetalt?, fulltInnbetalt?, bundet?} | **Added 2025-11.** Registered capital. Types seen: `Aksjekapital` (AS/ASA), `Innskuddskapital` (SF), `Eierandelskapital` (SPA), `Grunnkapital` (STI), `Selskapskapital` (KS, with `bundet`). **No kapital on SA/ANS/DA/ENK.** `belop` is a JSON number with decimals (`5976872600.00`, `117131.84`). **Parse with Decimal** (`json.loads(..., parse_float=Decimal)`). `innfortDato` is the date the capital was registered. | Equinor `{"belop":5976872600.00,"antallAksjer":2390749040,"type":"Aksjekapital","valuta":"NOK","innfortDato":"2026-07-02"}` |
| `vedtektsdato` | date | date of current articles | `"2026-05-12"` |
| `vedtektsfestetFormaal[]` | [str] | Statutory purpose, **split into lines of about 70 characters**. Join with a space. | |
| `aktivitet[]` | [str] | Business activity text (same line-splitting). Mainly ENK/AS. | |
| `maalform` | str | `Bokmål` / `Nynorsk` | |
| `erIKonsern` | bool | Part of a group. Then call `/konsernstruktur`. | true |
| `konkurs` + `konkursdato` | bool + date | Bankrupt. `konkursdato` = court ruling date. A separate KBO entity "<NAME> KONKURSBO" is created. | `true`, `"2026-07-08"` |
| `underAvvikling` + `underAvviklingDato` | bool + date | voluntary liquidation | `"2026-09-02"` |
| `underTvangsavviklingEllerTvangsopplosning` | bool | forced liquidation | |
| `tvangsopplostPgaManglendeDagligLederDato` / `...ManglendeRevisorDato` / `...ManglendeRegnskapDato` / `...MangelfulltStyreDato` / `tvangsavvikletPgaManglendeSlettingDato` | date | reason and date of forced dissolution | `tvangsopplostPgaManglendeRegnskapDato:"2026-06-04"` |
| `underRekonstruksjonsforhandlingDato`, `underUtenlandskInsolvensbehandlingDato` | date | in the schema; not observed | |
| `fravalgRevisjonDato`, `fravalgRevisjonBeslutningsDato` | date | opted out of audit (small AS) | `"2022-12-13"` |
| `paategninger[]` | [{infotype, tekst, innfoertDato}] | **Register warnings.** Seen: `FADR` ("grunn til å anta at forretningsadressen er feil"), `SIGN` (NUF: signature changed in home country). The doc lists `NAVN`, `FADR`, and others. **Surface these as risk flags.** | |
| `overordnetEnhet` + `_links.overordnetEnhet` | str | **Public-sector parent only** (ORGL, KF, ...). Not the corporate parent. | ORGL 974760673 → 912660680 (NFD) |
| `registreringsnummerIHjemlandet`, `foretaksformIHjemlandet{kode,beskrivelse,beskrivelseBokmaal}`, `utenlandskRegisterNavn`, `utenlandskRegisterAdresse{adresse[],land,poststed}`, `underlagtLovgivningLand`, `underlagtLovgivningLandKode` | | **Only on UTLA** (foreign entity), not on NUF. A NUF links to its UTLA main company via the role `HFOR`. | UTLA 976779649 `HE492533`; 995783568 `Companies House`, `LTD`, `GB` |
| `_links.self.href` | url | canonical API URL | |

There is **no** field for "last updated in register". To get it, use `/oppdateringer/enheter?organisasjonsnummer=` (see section 6).

Fields **not** present in this API:
* LEI
* share capital history
* shareholders (go to Skatteetaten's aksjonærregister; not in this API)
* financials (go to Regnskapsregisteret)
* signatur/prokura
* beneficial owners (RRH, restricted)

### 2.2 Gotchas
* **Oslo kommune (KOMM)** `antallAnsatte` = 557. Most employees sit on ORGL children (Utdanningsetaten 23,846).
  Find the children with `/enheter?overordnetEnhet=958935420` (15 children).
* For Equinor, the `postadresse.adresse` of a sub-unit holds the **old company name** ("Den norske stats oljeselskap as") as an address line. Do not treat it as a name.
* Batch search accepts `000000000` silently.
* Batch search drops deleted entities without telling you. You must follow up on any requested number that is missing.
* `naeringskode` descriptions changed with SN2025. Never hard-code SN2007 tables.
* The `kommune` name is upper-case. `land`/`landkode` are always present on forretningsadresse.

---

## 3. Roles: `GET /enheter/{orgnr}/roller`

Structure (`samples/enhetsregisteret__roller_*`):
```json
{"_links":{"self":{"href":".../enheter/947942638/roller"},"enhet":{"href":".../enheter/947942638"}},
 "rollegrupper":[
   {"type":{"kode":"STYR","beskrivelse":"Styre","_links":{...}},
    "sistEndret":"2026-05-08",                       // date the group was last changed
    "roller":[
      {"type":{"kode":"LEDE","beskrivelse":"Styrets leder","_links":{...}},
       "person":{"navn":{"fornavn":"Rolf Øyvind","mellomnavn"?:..., "etternavn":"Thune"},
                 "fodselsdato":"1973-10-11","erDoed":false},
       "avregistrert":false,
       "rekkefolge":0,
       "valgtAv"?:{"kode":"AREP","beskrivelse":"Representant for de ansatte"},
       "ansvarsandel"?:"1/4" | "46,90%"},
      {"type":{"kode":"REVI",...},
       "enhet":{"organisasjonsnummer":"987009713","navn":["PRICEWATERHOUSECOOPERS AS"],
                "organisasjonsform":{"kode":"AS",...},"erSlettet":false,
                "godkjenningsstatus":"Godkjent revisjonsforetak", "_links":{...}},
       "avregistrert":false,"rekkefolge":0},
      {"type":{"kode":"BOBE",...},
       "bostyrer":{"navn":"Adv. Tore M Famestad",
                   "postadresse":{"adresse":["Håkons gate 12"],"postnummer":"2317","poststed":"HAMAR","landkode":"NO"},
                   "erDoed":false}, ...}
 ]}]}
```
* **The holder is exactly one of `person` / `enhet` / `bostyrer`.**
* `enhet.navn` is an **array of name lines** (split at about 35 characters, e.g. `["KRISTIANSANDSREGIONEN BRANN OG","REDNING IKS"]`). **Join with a space.**
* `person.fodselsdato` is a **full birth date in the open API**. Privacy: output only name plus role; birth year at most. Brreg's own page shows "(f. 1980)".
* `rekkefolge` gives the order within a group. STYR is ordered LEDE, NEST, MEDL, VARA (and OBS).
* `avregistrert: true` means the person/firm **has reported themselves out of the role** (fratrådt), but the company has not yet filed a replacement. **Exclude these from "current" roles, or label them.**
  * Seen on Equinor MEDL Finn Bjørn Ruyter.
  * Seen on REGN of the bankrupt company 935567645.
  * w2.brreg.no shows "Avregistrert" for these.
* `fratraadt` was **removed on 2026-06-16**. Do not expect it.
* `erDoed` exists on person and bostyrer. No true example was found among 8 "DØDSBO" ENKs.
* Fields in the spec but not observed: `stadfestetFremtidsfullmakt`, `begrensetRettsligHandleevne`.
* Role groups (`rollegruppetyper`, 21):

  | Code | Meaning |
  |---|---|
  | DAGL | daglig leder |
  | STYR | styre |
  | REVI | revisor |
  | REGN | regnskapsfører |
  | INNH | innehaver (ENK) |
  | DELT | deltakere, ANS/DA/IKS (DTSO/DTPR) |
  | KOMP | komplementar (KS) |
  | KONT | kontaktperson (NUF/KS/FLI) |
  | FFØR | forretningsfører (BRL/KS) |
  | BEST | bestyrende reder (PRE) |
  | BOBE | bostyrer (konkurs) |
  | KDEB | konkursdebitor, on KBO pointing to the bankrupt company |
  | KENK | |
  | EIKM | eierkommune (KF) |
  | ORGL | parent in public sector |
  | ADOS | |
  | HFOR | foreign main enterprise, on NUF |
  | REPR | |
  | HLSE | |
  | KIRK | |
  | KTRF | |
  | OPMV | |

* Role types (`rolletyper`, 27): ADOS BEST BOBE DAGL DTPR DTSO EIKM FFØR HFOR HLSE INNH KDEB KENK KIRK KOMP KONT KTRF LEDE MEDL NEST OBS OPMV ORGL REGN REPR REVI VARA.
  * There are **no SIGN/PROK/SIFE/SIHV/POFE/POHV** in the open code list.
  * Representatives (`valgtAv`): A-AK, B-AK, C-AK, AREP.
* Observed patterns per form:

  | Form | Role groups |
  |---|---|
  | AS | DAGL, STYR (LEDE/MEDL), REVI or REGN |
  | ENK | INNH (person), REVI, REGN |
  | ANS | DAGL, DELT(DTSO ×6), REVI, REGN |
  | DA | DELT(DTPR with `ansvarsandel:"1/4"`) |
  | IKS | DELT(DTPR held by **enheter**, `ansvarsandel:"46,90%"`), STYR |
  | KF | EIKM (owner municipality) |
  | ORGL | ORGL → parent STAT |
  | KS | FFØR(enhet), KONT(person), KOMP(enhet), REVI |
  | NUF | DAGL, REVI, HFOR → 920156762 NORDEA BANK ABP |
  | KBO | BOBE(bostyrer), KDEB → bankrupt company |
  | Konkurs AS | adds BOBE |
  | PRE | BEST, DELT |
  | BRL | FFØR(BBL), STYR, REVI, REGN |
  | KOMM / FYLK / STAT | DAGL (rådmann/kommunedirektør) and sometimes REVI/REGN only |
  | UTLA | 404 |

* `ansvarsandel` uses fractions or **comma-decimal percentages**.
* There is **no batch roles endpoint**.
  * Bulk: `GET /api/roller/totalbestand` is a 130 MB gzip JSON array, about 22× expansion. Each element is `{_links, organisasjonsnummer, rollegrupper}`, the same shape as above. Built nightly at about 04:00.
  * Sample: `bulk_roller_totalbestand_first3.json`.
* Legal note from the docs: role data is open, but **compiling person-centric overviews may only include roles in næringsvirksomhet** (enhetsregisterloven § 9-1).
  * Example: a person's role in a FLI must not be listed alongside their AS roles.
  * Do not build person→roles cross-indexes.
* Related endpoint: `GET /api/roller/enheter/{orgnr}/juridiskeroller?size=&searchAfter=` lists roles that **an entity** holds in other entities (auditor, deltaker, ...).
  * Cursor paging via `_links.next.href` (`searchAfter=<orgnr>`).
  * Top level: `{organisasjonsnummer, erSlettet, enheter:[{organisasjonsnummer,navn,roller:[{type,avregistrert,rekkefolge}]}]}`.
  * Sample: `juridiskeroller_987009713_PwC.json`.
* Authorised only (Maskinporten): `/autorisert-api/...` endpoints add fødselsnummer. Not needed.

---

## 4. Sub-units (underenheter, BEDR/AAFY)

* `GET /api/underenheter/{orgnr}` gives 200 `respons_klasse:"Underenhet"`.
  * Fields: `organisasjonsnummer, navn, historiskeNavn, organisasjonsform, overordnetEnhet` (the **main unit**, plus `_links.overordnetEnhet`), `beliggenhetsadresse` (location address, same shape as forretningsadresse; `adresse` can be `[]`), `postadresse`, `naeringskode1-3`, `hjelpeenhetskode`, `antallAnsatte`, `harRegistrertAntallAnsatte`, `registreringsdatoAntallAnsatte*`, `oppstartsdato`, `datoEierskifte`, `registreringsdatoEnhetsregisteret`, `registrertIMvaregisteret`, `registreringsdatoMerverdiavgiftsregisteret*`, `frivilligMvaRegistrertBeskrivelser`, `hjemmeside`, `epostadresse`, `telefon`, `mobil`.
  * Sample: `underenhet_973861883.json`.
* Deleted sub-unit: 200 `respons_klasse:"SlettetUnderEnhet"` `{organisasjonsnummer,navn,organisasjonsform,historiskeNavn,slettedato,`**`nedleggelsesdato`**`}`.
  * `nedleggelsesdato` is **not in the schema but present**.
  * **Active sub-units never carry `nedleggelsesdato`**: a search with `fraNedleggelsesdato=1900-01-01` gives 0 hits.
* Removed sub-unit: **410** `{organisasjonsnummer, slettedato, _links}` (seen 69 times in one day's `Fjernet` updates).
* List of sub-units: `GET /api/underenheter?overordnetEnhet={orgnr}&size=100&page=n`.
  * Equinor has 35, DNB 59.
  * **Single parent only** (a comma list gives 400).
  * Only active sub-units are returned.
  * Other filters: `fraOppstartsdato/tilOppstartsdato`, `fraDatoEierskifte`, `fraNedleggelsesdato`, `beliggenhetsadresse.kommunenummer/postnummer/poststed`, `naeringskode`, `organisasjonsnummer` (comma list), sort fields.
* **Public-sector hierarchy is different.** ORGL/KF/... children are *enheter*, not underenheter. Use `GET /api/enheter?overordnetEnhet={orgnr}` (also single value only).
* Bulk: `/api/underenheter/lastned` (89 MB gz).

---

## 5. Group structure: `GET /api/konsernstruktur/{orgnr}` (new 2026-06-24)

* Root: `{organisasjonsnummer, navn, organisasjonsform{kode,beskrivelse}, children[]}`.
* Each child: `{nivaa, knytningsform{kode: KKKK|KMOR|KGRL|KDAT, beskrivelse}, navn, organisasjonsnummer, parentNavn, parentOrganisasjonsnummer, grunnlag ("100%", "95,00%"), dato, organisasjonsform, children[]}`.
* **Querying a subsidiary (980321932) returns the whole tree rooted at the ultimate parent (EQUINOR ASA).** This gives:
  * **ultimate parent** = root
  * **immediate parent** = `parentOrganisasjonsnummer` of the node whose `organisasjonsnummer` equals the query
  * **ownership basis** = `grunnlag`
  * as-of date = `dato`
* Equinor: 34 direct children, 54 nodes, all KDAT. Size about 24 KB.
* 404 when the entity is not in a group. Check `erIKonsern` first to save requests.
* It appears to cover only Norwegian-registered group members.
* CSV variant: `/api/konsernstruktur/{orgnr}/csv`.

---

## 6. Keeping profiles current: update feeds

* `GET /api/oppdateringer/enheter?dato=<yyyy-MM-ddTHH:mm:ss.SSSZ>&oppdateringsid=<id>&organisasjonsnummer=a,b,...&includeChanges=true&size=≤10000&page=&sort=id,ASC|DESC`
  * Response: `_embedded.oppdaterteEnheter[]: {oppdateringsid, dato (publication timestamp, ISO Z), organisasjonsnummer, endringstype, endringer?[JSON-Patch ops], _links.enhet}`.
  * `endringstype`: `Ny | Endring | Sletting | Fjernet | Ukjent`.
  * `Fjernet` means you must delete the entity from your cache. It returns 410.
  * One day had about 10k entity updates (625 Endring / 317 Ny / 58 Sletting in the first 1000).
  * Most frequently patched paths: forretningsadresse, naeringskode1 (SN2025 recoding), kapital, postadresse, vedtektsdato, aktivitet, underAvvikling, navn + historiskeNavn, erIKonsern, epostadresse, mobil.
  * Sample: `oppdateringer_enheter_includeChanges.json`.
  * **Per-org batching works**: `organisasjonsnummer=923609016,984851006,971524960&sort=id,DESC` returns their recent changes in one call (`oppdateringer_enheter_multi_orgnr_includeChanges.json`). Equinor has 179 updates in history.
  * **Use the newest `dato` per orgnr as `source_last_changed`** evidence.
  * Updates are published in hourly-ish batches (timestamps like 04:05Z, 05:05Z, 22:00Z). Lookups and search hit the live DB, so they are fresher than the bulk files.
  * Gotcha: `page.totalElements` with a `dato` filter looks inconsistent (27k "since Oct 1" vs 9.5k "since Oct 12"). Page by `oppdateringsid` (use the last id + 1), not by totals.
* `GET /api/oppdateringer/underenheter`: same shape (`oppdaterteUnderenheter`).
* `GET /api/oppdateringer/roller?afterTime=<ISO>&afterId=<n>&organisasjonsnummer=a,b,...&size=≤10000` (default 100)
  * Returns a **CloudEvents array**: `[{specversion:"1.0", id, source:".../enheter/{orgnr}/roller", type:"no.brreg.enhetsregisteret.rolle.oppdatert", time, data:{organisasjonsnummer}}]`.
  * No diff; you must refetch `/roller`.
  * 500 orgnrs in one call worked.
* **Refresh recipe** for a cached profile set:
  1. One call to `/oppdateringer/enheter?organisasjonsnummer=<≤500 list>&dato=<last_run>`.
  2. One call to `/oppdateringer/roller?organisasjonsnummer=<list>&afterTime=<last_run>`.
  3. Refetch only the changed entities (batch search) and changed roles.
  4. Mark `Sletting` entries as deleted (with `slettedato`) and purge `Fjernet` entries.

---

## 7. Human-readable evidence pages

* **`https://virksomhet.brreg.no/nb/oppslag/enheter/{orgnr}`**: 200, Next.js server-rendered page, about 200–260 KB. Also `/en/...` and `/nn/...`.
  * `/oppslag/enheter/{orgnr}` without a language prefix 307-redirects to `/nb/...`.
  * Sub-units: `/nb/oppslag/underenheter/{orgnr}`.
  * A nonexistent number gives 307 (to search).
  * `robots.txt`: `Allow: /`.
  * **Use this as the `source_url` shown to humans.** Cite the API URL as the machine evidence.
* The RSC flight payload (`self.__next_f.push([1,"..."])`) embeds:
  * the raw API JSON (`"virksomhet":{...}`)
  * the roles (`"mergedRoller":{"rolleRes":...}`)
  * **`"signProk":{"SIGN_HEAD":[...], "POHV":[...], "POFE"?:[...]}`**
* **signProk is the only openly reachable source of signaturrett/prokura.** Examples:
  * 930459615 `SIGN_HEAD:["Daglig leder eller styrets leder hver for seg."]`
  * Equinor `SIGN_HEAD:["Daglig leder alene.","Styrets leder alene.","To styremedlemmer i fellesskap."]`, `POHV:[12 names "(f. 1967)"]`
  * DNB similar
* Extraction recipe (tested):
  1. Regex `self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>`.
  2. JSON-unescape each match and concatenate.
  3. Find `"signProk"` and brace-match the object.
* The frontend config in the same payload reveals internal APIs (`virksomhet-tlg-api.app-prd.brreg.no`, `regnskap-opendata-api.app-prd.brreg.no`). They are **not publicly resolvable**, so don't use them.
* Sample: `virksomhet_page_930459615.html`.
* Legacy page **`https://w2.brreg.no/enhet/sok/detalj.jsp?orgnr={orgnr}`**: 200, **ISO-8859-1/cp1252**, about 40 KB, simple HTML table, flagged "under utfasing" (being phased out).
  * Shows: name, form, addresses, website, registration date, stiftelsesdato, Daglig leder, formål, aktivitet, næringskoder, sektorkode, registrations, "Inngår i konsern", "Sist innsendte årsregnskap", full board (with an "Avregistrert" marker), **Signatur**, **Prokura hver for seg**, and revisor with address.
  * The easiest signatur/prokura parser, but it may disappear.
  * Sample: `w2_detalj_923609016.html`.
* Scraping these pages isn't prohibited by robots.txt. They aren't part of the NLOD API, so mark them as a secondary source, limit to ≤1 request per company, and only fetch when signatur/prokura is wanted.

---

## 8. Frivillighetsregisteret (bonus, same host)

* `GET https://data.brreg.no/frivillighetsregisteret/api/frivillige-organisasjoner/{orgnr}`
  * Returns 200 with `{organisasjonsnummer, frivilligOrganisasjonsstatus, kontonummer (bank account!), innfoertDato, foersteGangInnfoert, grasrotandel{deltarI, utestengelsesperiode}, regnskapsrapportering{...}, vedtekter{...}, icnpoKategorier[{kategori, icnpoNummer, rekkefoelge}], paategninger}`.
  * Returns 404 `{"status":404,"message":"Not found"}` for non-members.
  * The `organisasjonsnummer` query param on the search endpoint is **ignored** (it returned unrelated orgs), so there is no batching.
  * Only call it when `registrertIFrivillighetsregisteret=true`.
  * Sample: `frivillighetsregisteret_943838240.json`.

---

## 9. Bulk downloads (if the batch is large)

| File | Size (gz) | Format |
|---|---|---|
| `/api/enheter/lastned` | 210.7 MB | JSON array of v2 objects, **non-HAL** (`links: []` instead of `_links`). About 9× expansion. |
| `/api/enheter/lastned/csv` | 155 MB | all cells quoted, embedded newlines |
| `/api/underenheter/lastned` | 89 MB | |
| `/api/roller/totalbestand` | 130 MB | |

* Bulk files contain **active entities only**.
* Use a streaming JSON parser (ijson) or `json.JSONDecoder.raw_decode` loops.
* Download speed from this host was about 0.3–0.5 MB/s, which is too slow to fetch everything inside a 45-minute window *and* do other work. If bulk is used, fetch it ahead of time (nightly, around 05:00 CEST) and refresh with section 6.
* Filtered `lastned?organisasjonsnummer=...` is capped at 10,000.
* Samples: `bulk_enheter_lastned_first3.json`, `bulk_roller_totalbestand_first3.json`.

---

## 10. License / terms

* All data is under **NLOD 2.0** (Norsk lisens for offentlige data). It is free, needs no key, and commercial use is allowed.
* **Attribution is required**, e.g. «Inneholder data under Norsk lisens for offentlige data (NLOD) tilgjengeliggjort av Brønnøysundregistrene». It can go on an About/README page.
* Do not misrepresent the data. Personal data subject to confidentiality is not covered.
* Contact: opendata@brreg.no.
* No documented rate limit. Brreg recommends bulk plus update feeds for full copies.
* Paid "full tilgang" (NOK 100k/yr + NOK 1/lookup) exists but is irrelevant here.
* Person/role restriction: enhetsregisterloven § 9-1 (see section 3).

---

## 11. Concrete recommendations for the agent

1. **Input validation:** normalise the input, check MOD11 locally, and return `invalid_orgnr` with an explanation without any request.
2. **Resolution pipeline per batch** (all httpx async, one keep-alive client, ≤4 concurrent, retry/backoff):
   1. `GET /enheter?organisasjonsnummer=<≤2000>&size=<n>` (1 request).
   2. Missing numbers: `GET /underenheter?organisasjonsnummer=<missing>` (1 request). If a number is a sub-unit, profile it and link `overordnetEnhet`.
   3. Still missing: individual `GET /enheter/{n}`. 200 `SlettetEnhet` means a "deleted" profile with `slettedato` + `historiskeNavn` + `navn`. 410 means "removed, publish nothing". 404 means "not found".
3. **Per active entity:**
   * `/roller`: skip for UTLA and deleted entities.
   * `/konsernstruktur`: only if `erIKonsern`.
   * `/underenheter?overordnetEnhet=`: only if you want sub-unit counts or locations. The first page with `size=100` gives `totalElements`.
   * Optional: `virksomhet.brreg.no` page for signatur/prokura.
4. **Evidence per fact:**
   * `source_url` = API URL (`.../enheter/{orgnr}`) plus the human URL (`virksomhet.brreg.no/nb/oppslag/enheter/{orgnr}`).
   * `evidence` = JSON pointer (e.g. `/kapital/belop`, `/rollegrupper/1/roller/0`).
   * `retrieved_at` = now.
   * `source_date` = the field's own date where available (below), else the latest `oppdateringer.dato` for the orgnr. Field-specific dates:
     * `kapital.innfortDato`
     * `registreringsdatoAntallAnsatteNAVAaregisteret`
     * role group `sistEndret`
     * `konkursdato`
     * `vedtektsdato`
     * `paategninger[].innfoertDato`
     * konsern `dato`
     * `sisteInnsendteAarsregnskap` year
   * Identity confidence is 1.0 for everything from these endpoints because they are keyed by orgnr. Record `navn` + `organisasjonsnummer` in every fact.
5. **Never fabricate numbers:**
   * Only emit `kapital.belop` (registered share capital, not revenue).
   * Parse it with Decimal.
   * Label `antallAnsatte` as "from NAV Aa-registeret as of <date>".
   * Emit "1–4" (not a number) when only `harRegistrertAntallAnsatte` is set.
6. **Status flags to emit:**
   * active / deleted (`slettedato`) / removed (410)
   * konkurs (+date)
   * underAvvikling (+date)
   * tvangsavvikling (+reason date)
   * paategninger warnings
   * `avregistrert` role holders
   * `erDoed` persons
7. **Normalisation:**
   * Join `aktivitet`/`vedtektsfestetFormaal`/`enhet.navn` line arrays with spaces.
   * Prefix `hjemmeside` with `https://`.
   * Treat `stiftelsesdato 1900-01-01` and `naeringskode 00.000` as unknown.
   * Parse `historiskeNavn` timestamps (space-separated).
   * Normalise comma-decimal percentages.
8. **Freshness:** use the section 6 refresh recipe, which needs about 2 requests per 500 companies to detect changes. Never rely on HTTP caching (none exists).
9. **Do not use** the authorised `/autorisert-api/*` or the internal `*.app-prd.brreg.no` hosts.
