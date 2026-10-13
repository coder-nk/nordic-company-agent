# Brreg change feeds, bulk data, announcements and side registers

Researched 2026-10-13. All requests sent with UA `nordic-company-agent/0.1 (research prototype)` at 2 req/s or slower.
Samples are in `research/samples/brreg_feeds_bulk__*`.
The authoritative machine-readable spec is the OpenAPI document embedded in the Redoc page
`https://data.brreg.no/enhetsregisteret/api/dokumentasjon/no/index.html`. It is extracted to
`samples/brreg_feeds_bulk__enhetsregisteret_openapi.json` (OpenAPI 3, v2.0.0). It covers Enhetsregisteret,
Frivillighetsregisteret and Partiregisteret, and includes a changelog.
(`/enhetsregisteret/api/docs/index.html` redirects to it. There is no `/v3/api-docs`. `https://data.brreg.no/robots.txt`
resets the connection, so the host publishes no robots policy. The data is NLOD-licensed open data and needs no registration or key.)

Terms used below:
- **enhet** = main entity
- **underenhet** = sub-unit / establishment (the "bedrift" level)
- **feed** = `/oppdateringer/*`

---

## 0. TL;DR for the engineers

| Need | Endpoint | Batch? | Cost |
|---|---|---|---|
| Fetch up to ~1,000–2,000 *registered* enheter in one call | `GET /enhetsregisteret/api/enheter?organisasjonsnummer=a,b,c,...&size=N` | yes, comma list, `maxItems 2000` (URL ≤ ~20 KB works; 30 KB → HTTP 400) | 100 orgs = 1 req, ~2.6 s, ~190 KB |
| Same for underenheter | `GET /enhetsregisteret/api/underenheter?organisasjonsnummer=...` | yes, same limit | |
| Which of my orgs changed since cursor X (+ what changed) | `GET /enhetsregisteret/api/oppdateringer/enheter?organisasjonsnummer=<list>&oppdateringsid=<X+1>&includeChanges=true&size=10000` | yes, comma list (2,000 OK) | 1 req per 2,000 stored orgs |
| Which of my orgs had role changes | `GET /enhetsregisteret/api/oppdateringer/roller?organisasjonsnummer=<list>&afterId=<Y>&size=10000` | yes | 1 req per 2,000 orgs |
| Full change history for a company (dates) | same feeds with `organisasjonsnummer=` and no cursor | yes | 100 random orgs → 1,346 enhet events (312 KB) + 302 role events, 1 req each |
| Deleted/removed company status | `GET /enhetsregisteret/api/enheter/{orgnr}` | no | 200 + `respons_klasse:"SlettetEnhet"` or **410** (Fjernet) |
| Group tree | `GET /enhetsregisteret/api/konsernstruktur/{orgnr}` | no | 404 if not in a group; call only when `erIKonsern==true` |
| Roles the company holds in *other* entities | `GET /enhetsregisteret/api/roller/enheter/{orgnr}/juridiskeroller` | no | |
| Signature / prokura rule text | `GET https://data.brreg.no/fullmakt/enheter/{orgnr}/signatur` and `/prokura` | no | open, no key |
| Voluntary-org data (bank account, ICNPO, grasrot) | `GET https://data.brreg.no/frivillighetsregisteret/api/frivillige-organisasjoner/{orgnr}?spraak=NOB` or daily 25 MB CSV | no orgnr batch; CSV = 1 req | |
| State aid grants (amounts) | `POST https://stottetiltak-registerinfo-api.app.brreg.no/api/v1/soek/stoettetildeling` | no (1 orgnr per call; repeated fields are ANDed) | open, no key |
| Uniform random sample of registered enheter | MOD11 rejection sampling through the batch search (Section 2.4) | 2,000 candidates/req → ~130 hits | 1,000 profiles ≈ 8 requests |
| Kunngjøringer (announcements) | `w2.brreg.no/kunngjoring/...` is **Disallowed in robots.txt**. No open API exists (the XML subscription is paid). | – | **do not use** |
| Beneficial owners (RRH) | `https://rrh.brreg.no/api/oppslag/...` → 401, Maskinporten-only | – | **not available** |

---

## 1. Update feeds (oppdateringer)

### 1.1 `/enhetsregisteret/api/oppdateringer/enheter` and `/oppdateringer/underenheter`

Parameters (all optional, from the spec and verified):

| param | meaning |
|---|---|
| `dato` | ISO-8601 `yyyy-MM-dd'T'HH:mm:ss.SSS'Z'`, inclusive ("fra og med"). This is the **time the update was published in the API**, not the legal registration date. |
| `updatedBefore` | upper bound, inclusive (added 2025-11-18) |
| `oppdateringsid` | **inclusive** lower bound on the sequential id (verified: `oppdateringsid=25316061` returns 25316061 first). Brreg recommends `last+1`. |
| `organisasjonsnummer` | **comma-separated list** of orgnrs (verified with 1,000 and 2,000 numbers: a 2,000-number URL of ~20 KB returns 200; 3,000 numbers / 30 KB returns `400 Bad request` from the front proxy; 5,000 numbers resets the connection) |
| `includeChanges` | `true` adds `endringer`, an **RFC 6902 JSON Patch** array against the enhet JSON (added 2025-09-23; populated from about **2025-09-13**; older events have no patch) |
| `page`, `size` | size 1..10000, default 20. Hard cap: `size*(page+1) <= 10000`, otherwise 400 `"size * (page+1) kan ikke overstige 10_000"`. Beyond 10k results you **must** advance with `oppdateringsid`. |
| `sort` | `id,ASC` (default) or `id,DESC`. Only `id` is supported. |

Response (HAL):
```json
{"_embedded":{"oppdaterteEnheter":[      // "oppdaterteUnderenheter" for underenheter
   {"oppdateringsid":25316062,"dato":"2026-10-12T04:05:11.715Z","organisasjonsnummer":"935252369",
    "endringstype":"Endring",
    "endringer":[{"op":"add","path":"/historiskeNavn/0","value":{"fraDato":"2025-03-23 14:05:02","navn":"MARIUS AGA ANKERVOLD","tilDato":"2026-10-12 06:00:55"}},
                 {"op":"replace","path":"/navn","value":"ANKERVOLD & RAUNHOLM MEKANISKE"}],
    "_links":{"enhet":{"href":"https://data.brreg.no/enhetsregisteret/api/enheter/935252369"}}}]},
 "_links":{"first":..,"self":..,"next":..,"last":..},
 "page":{"number":0,"size":5,"totalElements":9470,"totalPages":1894}}
```
For underenheter the link key is `_links.underenhet`.

`endringstype` values (spec table):
- `Ukjent`: unknown. Used for events from before the type existed and for mass republishes.
- `Ny`: new entity.
- `Endring`: changed.
- `Sletting`: deleted in the register.
- `Fjernet`: removed from Åpne Data. **Brreg requires copies to delete it too.** `GET /enheter/{orgnr}` then returns **410** with body `{"organisasjonsnummer":..,"slettedato":..}` only.

Observed facts:
- **Retention: complete history since the start.** `oppdateringsid=1` is dated `2018-04-23T06:03:29.443Z`. Equinor has 179 events from 2018-05-15 to today. The latest ids on 2026-10-13 were enheter `25328886` and underenheter `21461110`. The two feeds have separate id sequences.
- **Latency:** events appear throughout the day (04:00–19:00 UTC in small batches) plus a big nightly batch around 22:00–23:00 UTC. Bulk files are rebuilt around 04:00–04:30 CEST.
- **Daily volume (enheter):** 800–10k events on normal days. 2026-10-12 had 8,739 events (Endring 8,200, Ny 391, Sletting 142, Fjernet 6) covering 8,669 distinct orgs, which is 3.8 MB with `includeChanges` and took 13 s for `size=10000`. Underenheter peak around the 12th–15th of each month, when NAV's monthly employee update lands: there were 64k sub-unit events from 2026-10-12 onwards.
- **Mass republishes (not real changes):**
  - 2025-11-13: 1,146,083 enhet events (practically every entity, `Ukjent`, no patch).
  - 2025-04-08: 693,569 events (`Endring` without patch).
  - Treat any `Ukjent` event, or an `Endring` without `endringer`, on such days as "no semantic change". Always diff the fresh record against the stored one before claiming that something changed.
- **What the feed tells you:** without `includeChanges` you get only orgnr + timestamp + type. With it you get the exact changed JSON paths and values. Most frequent paths on 2026-10-12:
  - `registreringsdatoAntallAnsatte*` and `/antallAnsatte` (monthly NAV noise)
  - `/harRegistrertAntallAnsatte`
  - MVA flags/dates
  - addresses, `/aktivitet/N`, `/vedtektsfestetFormaal/N`, `/vedtektsdato`
  - `/underAvvikling` + `/underAvviklingDato`
  - `/erIKonsern`, `/navn` + `/historiskeNavn/N`, `/sisteInnsendteAarsregnskap`, `/naeringskode1`
  - `/kapital/*`, `/epostadresse`, `/mobil`, `/hjemmeside`
  - `/konkurs` + `/konkursdato`, `/paategninger`, `/fravalgRevisjon*`, `/tvangsopplostPga*Dato`
- **Deletions come in two steps:**
  1. A `Sletting` event, which has no patch.
  2. An `Endring` that removes every field and adds `/slettedato`. Example: 922052778 had `Endring` id 25316650 followed by `Sletting` id 25316652 in the same second.
  - Afterwards `GET /enheter/{orgnr}` returns 200 `{"respons_klasse":"SlettetEnhet","navn":..,"organisasjonsform":..,"slettedato":"2026-10-12","historiskeNavn":[]}`.
- **Bankruptcy example:** `[{"op":"replace","path":"/konkurs","value":true},{"op":"add","path":"/konkursdato","value":"2026-10-12"}]`.
- **Timestamps:** `historiskeNavn.fraDato/tilDato` use the format `yyyy-MM-dd HH:mm:ss` in **Oslo local time without a zone** (tilDato `06:00:55` ↔ feed `04:05Z`). Feed `dato` is UTC.

### 1.2 `/enhetsregisteret/api/oppdateringer/roller` (CloudEvents 1.0)

Parameters:
- `afterTime`: ISO, exclusive.
- `afterId`: exclusive (verified: `afterId=4709795` returns 4709796 first).
- `organisasjonsnummer`: comma list, works with 1,000 numbers.
- `size`: 1..10000, default 100.

There is **no page metadata**. The response is a bare JSON array. Keep paging with `afterId=<last id>` until you get fewer than `size` events.

```json
[{"specversion":"1.0","id":"4709795","source":"https://data.brreg.no/enhetsregisteret/api/enheter/934152891/roller",
  "type":"no.brreg.enhetsregisteret.rolle.oppdatert","time":"2026-10-12T04:00:40.039Z",
  "data":{"organisasjonsnummer":"934152891"}}]
```
- Types seen:
  - `no.brreg.enhetsregisteret.rolle.oppdatert`: the company's own roles changed (7,137 events on 2026-10-12).
  - `no.brreg.enhetsregisteret.rolle.enhet.oppdatert`: an *entity that holds a role* in this company changed (87 events that day). Verified: 823560192 got this event when its regnskapsfører 989550659 was deleted at 08:05.
- The event carries **only the orgnr, with no diff**. Re-fetch `/enheter/{orgnr}/roller` and diff locally.
- Retention: history back to **2022-03-14** (initial load). Every company in a 100-random sample had at least one event.
- Volume: about 7k events/day, 1.76 MB for `size=10000`.
- Useful companion: in `/enheter/{orgnr}/roller` each `rollegrupper[].sistEndret` (date) **equals the announcement date** of the last change for that role group. For Equinor:
  - DAGL `sistEndret` 2020-11-02 = kunngjøring "Daglig leder" 02.11.2020
  - STYR 2026-09-04 = "Styre" 04.09.2026
  - REVI 2019-07-12 = "Revisor" 12.07.2019

  This is the legal way to date role changes without scraping kunngjøringer. Also keep `roller[].avregistrert` (true = deregistered person, which should be hidden or flagged).

### 1.3 Design: how the agent should use the feeds

**(a) Detect which stored profiles changed with minimal requests.**

Persist three cursors: `enh_cursor` (max `oppdateringsid` seen), `und_cursor`, and `rol_cursor` (max CloudEvent `id`). Per run:

1. For each chunk of ≤ 1,000 stored enhet orgnrs (≤ 2,000 works, but 1,000 leaves URL headroom):
   `GET /oppdateringer/enheter?organisasjonsnummer=<chunk>&oppdateringsid=<enh_cursor+1>&includeChanges=true&size=10000&sort=id,ASC`
   - 1,000 profiles → 1 request (a test with 1,000 numbers and `dato`=12 days back returned 42 events in 1.6 s).
   - If `totalElements > 10000`, repeat with `oppdateringsid=<last id+1>`.
2. `GET /oppdateringer/roller?organisasjonsnummer=<chunk>&afterId=<rol_cursor>&size=10000`. 1,000 orgs over 6 weeks gave 110 events in 8 s.
3. Sub-units: keep a list of underenhet orgnrs per parent and run the same query on `/oppdateringer/underenheter`.
4. Changed set = union of orgnrs. Then:
   - Re-fetch them in **one** batch call: `GET /enheter?organisasjonsnummer=<changed>&size=<n>`.
   - Fetch `/enheter/{o}/roller` only for the role-changed orgs.
   - **The batch search omits deleted and removed entities.** For any orgnr missing from the batch result, call `GET /enheter/{orgnr}`: 200 `SlettetEnhet` = deleted (keep the tombstone with `slettedato`), 410 = Fjernet (**purge the profile**), 404 = not an enhet (try `/underenheter/{orgnr}`).
   - Alternatively, apply the JSON Patch from `endringer` directly to the stored JSON. This is exact for post-2025-09 events, but you should still periodically re-fetch to self-heal.
5. Advance the cursors only after the profiles are persisted.

Alternative for very large stores: tail the global feed instead (`oppdateringsid=<cursor+1>&size=10000`, about 1–2 requests/day for enheter, but 3.8 MB) and intersect locally. With ≤ 10k profiles the filtered call is cheaper.

Steady-state cost for 1,000 profiles: **about 3 requests + 1 batch re-fetch + k role fetches**.

**(b) Date each change.**

Use dates in this priority:
1. **A legal or registered date inside the record**, if the changed path has one:
   - `konkursdato`, `underAvviklingDato`, `tvangsopplostPga*Dato`, `tvangsavvikletPgaManglendeSlettingDato`
   - `kapital.innfortDato`, `vedtektsdato`, `slettedato`, `registreringsdato*`
   - `historiskeNavn[].tilDato` for name changes
   - `stiftelsesdato`
   - `roller.rollegrupper[].sistEndret` for roles
2. Otherwise **`dato` of the feed event**: the time Brreg published it. Observed to be the same day as the registration, e.g. Equinor's capital change was published 2026-07-02T22:00Z and the kunngjøring is dated 02.07.2026.

Store the evidence:
- `source_url` = the feed query URL (or `_links.enhet.href`)
- `oppdateringsid`, `dato`, `endringstype`
- the JSON Patch op (`path` + `value`) as `evidence`

Per-field "last changed" values are reliable only for events after about 2025-09-13. Earlier events tell you *that* something changed and when, but not what.

**Build a timeline once per company:** `GET /oppdateringer/enheter?organisasjonsnummer=<100 orgs>&size=10000&includeChanges=true` (100 random orgs → 1,346 events, median 6 per org, max 148, 312 KB, 6.7 s), plus `GET /oppdateringer/roller?organisasjonsnummer=<100>&size=10000` (302 events, 74 KB). That is two requests for 100 companies. The earliest `Ny` event gives the "first seen in open data" date (or 2018-05 for older entities, which appear as `Ukjent`).

---

## 2. Bulk downloads

HEAD results (2026-10-13). Every file has `etag` and `last-modified`:

| URL | file | size | format |
|---|---|---|---|
| `/enhetsregisteret/api/enheter/lastned` | `enheter_alle.json.gz` | **210.7 MB** | gzip JSON array of enhet objects (same schema as the API, no HAL) |
| `/enhetsregisteret/api/enheter/lastned/csv` | `enheter_<ts>.csv.gz` | **155.0 MB** | gzip CSV, 89 columns, every cell double-quoted (`""` escapes), embedded newlines in cells |
| `/enhetsregisteret/api/enheter/lastned/regneark` | `enheter_alle.xlsx` | 423.3 MB | xlsx |
| `/enhetsregisteret/api/underenheter/lastned` | `underenheter_alle.json.gz` | 89.2 MB | gzip JSON |
| `/enhetsregisteret/api/underenheter/lastned/csv` | `underenheter_<ts>.csv.gz` | 61.6 MB | gzip CSV |
| `/enhetsregisteret/api/underenheter/lastned/regneark` | xlsx | 164.0 MB | |
| `/enhetsregisteret/api/roller/totalbestand` | `roller_<ts>.json.gz` | 129.9 MB | gzip JSON (roles incl. birth date) |
| `/frivillighetsregisteret/api/frivillige-organisasjoner/totalbestand/csv` | `frivillige-organisasjoner_<ts>.csv` | 25.3 MB | plain CSV, 40 columns, 72,882 rows |
| `/partiregisteret/api/lastned/csv` | `2013partiregisteret_<date>.csv` | 3 KB | `;`-separated, **no header**, 13 columns |

Gotchas:
- **Range requests are ignored.** The server advertises `accept-ranges: bytes`, but `-r 0-1048575` streams the whole file. To peek, stream and abort.
- **Conditional GET is ignored.** `If-None-Match` / `If-Modified-Since` still return 200 with the full body. Use HEAD and compare `etag` / `last-modified` yourself.
- **Throughput is about 0.3–0.6 MB/s.** Frivillighet CSV: 25 MB in 85 s. Enheter CSV: 60 MB in about 2 min. The full enheter CSV therefore takes about 4–8 min. API JSON responses are **not gzip-compressed**, even with `Accept-Encoding: gzip`.
- `/enheter/lastned` *with* filters returns at most 10,000 entities as gzip.
- Bulk files contain registered entities only, so deletions must come from the feed.

CSV header (enheter):
`organisasjonsnummer, navn, organisasjonsform.kode, organisasjonsform.beskrivelse, naeringskode1..3.kode/beskrivelse, hjelpeenhetskode.*, harRegistrertAntallAnsatte, antallAnsatte, registreringsdatoAntallAnsatteEnhetsregisteret, registreringsdatoantallansatteNAVAaregisteret, hjemmeside, epostadresse, telefon, mobil, postadresse.*, forretningsadresse.*, institusjonellSektorkode.*, sisteInnsendteAarsregnskap, registreringsdatoenhetsregisteret, stiftelsesdato, registrertIMvaRegisteret, registreringsdatoMerverdiavgiftsregisteret(+Enhetsregisteret), frivilligMvaRegistrertBeskrivelser, registreringsdatoFrivilligMerverdiavgiftsregisteret, registrertIFrivillighetsregisteret, registreringsdatoFrivillighetsregisteret, registrertIForetaksregisteret, registreringsdatoForetaksregisteret, registrertIStiftelsesregisteret, registrertIPartiregisteret, registreringsdatoPartiregisteret, konkurs, konkursdato, underAvvikling, underAvviklingDato, underTvangsavviklingEllerTvangsopplosning, tvangsopplostPga{ManglendeDagligLeder,ManglendeRevisor,ManglendeRegnskap,MangelfulltStyre}Dato, tvangsavvikletPgaManglendeSlettingDato, overordnetEnhet, maalform, vedtektsdato, vedtektsfestetFormaal, aktivitet, paategninger, underUtenlandskInsolvensbehandlingDato, underRekonstruksjonsforhandlingDato, fravalgRevisjonDato, fravalgRevisjonBeslutningsDato, erIKonsern, kapital.{belop,antallAksjer,type,bundet,valuta,innbetalt,fulltInnbetalt,innfortDato}, registreringsnummerIHjemlandet, utenlandskRegisterNavn, utenlandskRegisterAdresse.*, underlagtLovgivningLand(+Kode), foretaksformIHjemlandet.*`
(first 40 rows: `samples/brreg_feeds_bulk__enheter_totalbestand_csv_head40.csv`).

### 2.1 Population counts (search API `page.totalElements`, 2026-10-13)

- Total registered enheter: **1,177,979**.
- "Active" (`konkurs=false&underAvvikling=false&underTvangsavviklingEllerTvangsopplosning=false`): **1,165,231**.
- Underenheter: 868,058.

The search index excludes deleted entities. Flags:

| flag | count |
|---|---|
| konkurs=true | 3,259 |
| underAvvikling | 8,246 |
| underTvangs… | 1,435 |
| underKonkursbehandling | 4,694 |
| Foretaksregisteret | 578,244 |
| MVA | 411,227 |
| Frivillighetsreg. | 72,883 |
| Stiftelsesreg. | 5,967 |
| Partireg. | 24 |
| ≥1 employee | 175,871 |
| ≥5 employees | 71,802 |

| form | total | active | share of total |
|---|---|---|---|
| ENK | 464,744 | 464,578 | 39.5 % |
| AS | 433,710 | 423,269 | 36.8 % |
| FLI | 129,024 | 129,010 | 11.0 % |
| ESEK | 33,399 | 33,399 | 2.8 % |
| UTLA | 28,326 | 28,326 | 2.4 % |
| NUF | 25,839 | 25,831 | 2.2 % |
| DA | 10,282 | 9,129 | 0.9 % |
| BRL | 10,242 | 10,207 | 0.9 % |
| KBO (konkursbo) | 7,745 | 7,745 | 0.7 % |
| SAM | 7,294 | 7,294 | 0.6 % |
| ANS | 7,066 | 6,256 | 0.6 % |
| SA | 6,852 | 6,759 | 0.6 % |
| STI | 5,968 | 5,961 | 0.5 % |
| ANNA 1,687 · ORGL 1,599 · KIRK 1,419 · BA 708 · VPFO 382 · KOMM 357 · IKS 220 · ASA 213 · KF 146 · KS 114 · OPMV 105 · PRE 93 · PK 72 · SPA 67 · SÆR 62 · BO 58 · BBL 44 · GFS 29 · ADOS 25 · STAT 20 · FYLK 14 · KTRF 12 · PERS 11 · TVAM 11 · FKF 8 · SF 8 · SE 4 · EOFG 0 · IKJP 0 | | | |

(Full JSON: `samples/brreg_feeds_bulk__orgform_distribution_2026-10-13.json`. The code list is in `samples/brreg_feeds_bulk__organisasjonsformer_enheter.json`.)

Implication: a uniform "random company" is about 40 % ENK, 37 % AS and 11 % FLI. Coverage work should prioritise those three forms, plus ESEK/UTLA/NUF, which are about 7 % together.

### 2.2 Search API paging caps

- `/enheter`: `size*(page+1) <= 10000`, otherwise 400. `size=10000` works but takes 52 s and returns about 19 MB. `size=1000` takes 25 s and 1.9 MB.
- `/enheter?size=1&page=9999` is the deepest reachable item, so you cannot sample uniformly by random page across 1.18M rows without partitioning.
- Orgnr range of registered enheter: `810034882` … `999670504`.

### 2.3 Batch fetch by orgnr list

- `/enheter?organisasjonsnummer=<≤2000 csv>&size=<n>`: 100 orgs = 2.6 s, 190 KB. Works together with other filters (AND), e.g. `&organisasjonsform=AS,ASA&konkurs=false`.
- **It only returns registered (non-deleted) entities.** It returned 1 of 4 when 3 deleted/removed orgnrs were included.
- `/underenheter?overordnetEnhet=` does **not** accept a list (400). Either fetch one parent per request, or index the 62 MB underenheter CSV.

### 2.4 Uniform random sample of org numbers (recommended)

The orgnr is 8 digits plus a MOD11 check digit (weights 3,2,7,6,5,4,3,2; `r = 11 - sum%11`; r=11→0; r=10 → invalid).

Procedure:
1. Draw a random 8-digit prefix uniformly in `[81003488, 99967051]`.
2. Compute the check digit and discard invalid ones.
3. Batch 2,000 candidates into one `/enheter?organisasjonsnummer=...&size=2000`.

Measured with 3 requests (seed 42): **133, 125, 125 hits per 2,000** candidates, a 6.4 % hit rate.

Each registered enhet has exactly one candidate number, so accepted hits are **an exactly uniform sample of registered enheter**. They are also distinct, unless you merge batches with replacement.

The 383 hits came out as ENK 148, AS 141, FLI 46, UTLA 13, NUF 9, DA 8, ESEK 7, … This matches the population (`samples/brreg_feeds_bulk__random_sample_rejection_383_hits.json`).

- To restrict to "active", add `&konkurs=false&underAvvikling=false&underTvangsavviklingEllerTvangsopplosning=false` (optionally also exclude `KBO`).
- To stratify, add `&organisasjonsform=...`.
- **1,000 profiles ≈ 8 requests; 10,000 ≈ 77 requests.**
- The fallback is to download `enheter/lastned/csv` once (155 MB, 1 request, about 5 min) and sample rows. The result is exact, but the download is heavier.
- Deleted entities can't be sampled this way. The test's "100 random companies" may come from the same register; nobody knows whether deleted ones are included, so the agent must handle 200-SlettetEnhet, 410, 404 and underenhet numbers anyway.

```python
W=[3,2,7,6,5,4,3,2]
def orgnr_from_prefix(p8:str):
    r = 11 - sum(int(d)*w for d,w in zip(p8,W)) % 11
    return None if r == 10 else p8 + str(0 if r == 11 else r)
def is_valid_orgnr(s):  # use this to validate user input too
    return len(s)==9 and s.isdigit() and orgnr_from_prefix(s[:8]) == s
```

---

## 3. Kunngjøringer (official announcements)

- **Legacy HTML:** `https://w2.brreg.no/kunngjoring/hent_nr.jsp?orgnr=<orgnr>` (list) and `hent_en.jsp?kid=<14-digit id>&sokeverdi=<orgnr>&spraak=nb` (detail).
  - Charset is ISO-8859-1/windows-1252.
  - The list is a `<table>` with rows of (date `dd.mm.yyyy`, link text = kunngjøringstype). The header shows "Antall treff N".
  - Equinor had 195 announcements back to 2000. Types seen: Styre, Vedtektsdato, Godkjente årsregnskap, Godkjent årsregnskap med bærekraftsrapportering, Kapital, Erverv av egne aksjer-fullmakt, Utdeling av utbytte - fullmakt, Nedsettelse av kapital, Daglig leder, Forretningsadresse, Foretaksnavn, Revisor, Fullmakt kapitalforhøyelse, Vedtektsfestet formål, Signatur, Nedsettelse av overkursfond, Fusjonsplan, Fisjonsplan, Fisjonsbeslutning. `kid` looks like `<year><10-digit seq>`.
  - Sample (one research fetch, before reading robots): `samples/brreg_feeds_bulk__kunngjoring_hent_nr_equinor_ROBOTS_DISALLOWED.html`.
- **`https://w2.brreg.no/robots.txt` has `Disallow: /kunngjoring/`** (also `/enhet/`, `/frivillighetsregisteret/`, `/oppslag/` and others). **The agent must not crawl it.**
- `www.brreg.no/registersok/kunngjoringer/` is a WordPress page that only links to w2 (`dagens.jsp`, `nyelr.jsp`). `www.brreg.no/robots.txt` sets `Crawl-delay: 10`.
- `virksomhet.brreg.no` (new lookup UI; robots `Allow: /`, has sitemaps) has the announcements component behind a feature flag (`APP_PUBLIC__FEATURE_KUNNGJORINGER`). It is currently off and shows only a link to w2. Its backends (`kunngjoring-tlg-api.apps.ocp01.prd.base.brreg.no`, `virksomhet-tlg-api.app-prd.brreg.no`) are not publicly reachable.
- **Official machine access:**
  - Paid XML subscription delivered over SFTP, under contract (Foretaks-, Konkurs-, Regnskaps-, Parti- and Stiftelses-kunngjøringer).
  - Free e-mail subscription for up to 5,000 orgnrs (daily/weekly e-mail), which is impractical for an agent.
  - data.norge.no lists "API: Not provided".
- **Replacement strategy:**
  - Use the enhet feed with JSON Patch, plus dated record fields (`konkursdato`, `underAvviklingDato`, `vedtektsdato`, `kapital.innfortDato`, `historiskeNavn.tilDato`, `sisteInnsendteAarsregnskap`, `fravalgRevisjonDato`, …).
  - Use roles `rollegrupper[].sistEndret`, which matches the announcement dates exactly in the Equinor test.
  - Use the roles feed for timestamps.
  - Together these cover the most common announcement types (Styre, Daglig leder, Revisor, Kapital, Vedtekter, Navn, Adresse, Årsregnskap, Konkurs, Avvikling).

---

## 4. Side registers and other data.brreg.no APIs

### 4.1 Frivillighetsregisteret (open, documented in the same OpenAPI)

- `GET /frivillighetsregisteret/api/frivillige-organisasjoner/{orgnr}?spraak=NOB` returns 404 `{"status":404,"message":"Not found"}` if the org is not registered. Only call it when the enhet has `registrertIFrivillighetsregisteret=true` (72,883 orgs).
- Fields:
  - `frivilligOrganisasjonsstatus`, `innfoertDato`, `foersteGangInnfoert`
  - **`kontonummer`** (bank account)
  - `grasrotandel.deltarI` / `utestengelsesperiode`
  - `regnskapsrapportering.{harPaatattSegRapporteringsplikt, avslutningsdatoForRegnskapsperiode, sistInnsendteAarsregnskap}`
  - `vedtekter.{frivilligRegistrerteVedtekter, sistOppdaterteVedtekter}`
  - `icnpoKategorier[].{icnpoNummer, kategori, navn (only with spraak=NOB)}`
  - `paategninger`
- List: `GET /frivillighetsregisteret/api/frivillige-organisasjoner?size=<n>&searchAfter=<last orgnr>` (keyset paging, sorted by orgnr). There is **no orgnr filter**.
- **Daily CSV** (`/totalbestand/csv`, 25 MB, 1 request, built around 04:00): 40 columns including `kontonummer`, ICNPO 1–3, grasrot, regnskapsrapportering, `epostadresse`, `harPaategninger`. Index it locally if many FLI orgs are involved.
- Supporting endpoints: `/frivillighetsregisteret/api/icnpo-kategorier`, `/informasjonstyper`.

### 4.2 Partiregisteret

- `GET /partiregisteret/api/lastned/csv`: 3 KB, `;`-separated, no header.
- Columns: orgnr, registered party name, org form, business address, postnr, kommunenr, kommune, land, postal address, postnr, poststed, land.

### 4.3 Fullmakt: signature and prokura (open, no key)

Docs: `https://data.brreg.no/fullmakt/docs/index.html`.

- Open endpoint: `GET https://data.brreg.no/fullmakt/enheter/{orgnr}/signatur` or `/prokura` with `Accept: application/json`. It returns birth date only, never fnr.
- The variant with fnr and the control service live on `data.vcert.brreg.no` and require an enterprise certificate.
- Response fields:
  - `enhet`
  - `oppslagsData.{oppslagsIdent, oppslagsTidspunkt}`
  - `signeringsGrunnlag.{kode, tekstforklaring, muligeSigneringsRoller.personRolleGrunnlag[{navn, fodselsdato, rolle.kode}], signaturProkuraRoller.signaturProkuraFritekst}`. Example fritekst: **"Daglig leder eller styrets leder hver for seg."**
  - `signeringsKombinasjon.kombinasjon[{kode (STYR, R0001…R00xx, SIGN, SIFE, SIHV, PROK, POFE, POHV, DELT, INNH), tekstforklaring, personRolleKombinasjon[]}]`
  - or `status.rutineStatus` (e.g. `NA` = "Generelle unntak…", returned for Equinor ASA)
- **This is a good "useful fact"** (signing rules) with clean evidence, one request per company per type. The person data is public, but publish only the rule text and role codes unless the names are needed.

### 4.4 Konsernstruktur (group tree; added 2026-06-24)

- `GET /enhetsregisteret/api/konsernstruktur/{orgnr}` (also `/csv`).
- Returns the **whole tree from the ultimate parent**: the same 17 KB document for Equinor ASA and for its sub-subsidiary 980321932. Node fields: `{organisasjonsnummer, navn, organisasjonsform, knytningsform{kode:"KDAT",beskrivelse:"Konsern datter"}, grunnlag:"100%", dato, nivaa, parentOrganisasjonsnummer, parentNavn, children[]}`.
- Returns 404 for entities that are not in a group. Gate the call on `erIKonsern`.

### 4.5 Juridiske roller (roles this entity holds elsewhere)

- `GET /enhetsregisteret/api/roller/enheter/{orgnr}/juridiskeroller?size=&searchAfter=`
- Response: `{organisasjonsnummer, erSlettet, enheter[{organisasjonsnummer, navn, roller[{type.kode e.g. DTPR/REGN/REVI, ansvarsandel, avregistrert}]}]}`.

### 4.6 Støtteregisteret (state aid; replaces the discontinued "Registeret for offentlig støtte")

- API base `https://stottetiltak-registerinfo-api.app.brreg.no` (OpenAPI at `/v3/api-docs`, saved).
- The spec declares bearerAuth globally, but **the search works without a token**.
- `POST /api/v1/soek/stoettetildeling?pageSize=1000&page=1&sortBy=MOTTATT_DATO|ENDRET|OPPRETTET|STOETTETILTAKSNUMMER&sortDirection=DESC&language=NOB` with JSON body `[{"field":"STOETTEMOTTAKER_ORGNR","value":"923609016","matchType":"EXACT_MATCH"}]`.
  - Equinor: 80 grants, 98 KB, 2.4 s.
  - `pageSize` max 1000; results capped at 100k.
  - Other fields: `STOETTEGIVER_ORGNR`, `TILDELINGSDATO` (`DATE_GREATER_THAN_OR_EQUAL` / `DATE_LESS_THAN_OR_EQUAL`), `STATSSTOETTEKATEGORI`, `NAERING`/`REGION` (`ONE_OF`), …
  - **Repeating a field ANDs it**, so there is no multi-orgnr batch (both a space-separated `CONTAINS_WORDS` value and a comma `EXACT_MATCH` value returned 0).
- Grant fields: `stoettetiltaksnummer, status, type, mottattDato, endret, region, stoetteinstrument, stoerrelseStoettemottaker, naering, tildeltBeloep{beloep,valuta}` or `beloepsintervall`, `tilknyttetStoetteordning, tildelingsdato, eoesRettsligGrunnlag{statsstoettekategori, primaerrettsligGrunnlag, artikkel}, rolle[{type: rolletype.stoettegiver|rolletype.stoettemottaker, rolleinnehaverVirksomhet{organisasjonsnummer,navn}}]`.
  - Amounts are explicit and attributed to the recipient orgnr, so they are safe financial facts with evidence.
- Full dumps: `https://stotte.brreg.no/nb/oppslag/stoettetildeling/totalbestand/csv` (306 MB) and `/json` (481 MB). These are too big for a daily run. Instead, maintain a local index incrementally with `sortBy=ENDRET&sortDirection=DESC` paging until `endret < cursor`, or call per company.
- Human UI: `https://stotte.brreg.no/nb/oppslag/stoettetildeling/<nr>`. Note that `stotte.brreg.no/robots.txt` is not a real file: it returns the HTML app.

### 4.7 Kompensasjonsordningen (COVID-19 business compensation 2020–2022, historical)

- API `https://data.brreg.no/kompensasjonsordning/api` (the GUI sends `X-Client-ID`). Download: `GET /kompensasjonsordning/api/tilskudd/csv` (11.2 MB, 37,062 grants, `;`-separated, decimal comma, BOM).
- Columns: `tilskuddsnummer, tilskuddsmottakerOrganisasjonsnummer, tilskuddsmottakerNavn, kommune*, fylke*, naeringskode1-3, tilskuddsbelop, kompensasjonFasteUunngaeligeKostnader, kompensasjonTaptVarelager, tildelingsdato, tilskuddsperiode, sammenligningsperiode, sumFasteUunngaaeligeKostanderITilskuddsperioden, omsetningITilskuddsperiode, omsetningISammenligningsperiode, justeringsfaktor, storrelseForetak, uavhengigMedsignererOrganisasjonsnummer/Navn, konsernsoknadSamlet, ansvarligForetak*, antallTilskuddTilForetakIKonsernet, summertTilskuddsbelopKonsern`.
- It contains **reported revenue figures** per period. These are optional, historical facts; label them with the period. Also available: `/tilbakebetalingskrav/csv`, `/tilskudd/json`, and `POST /tilskudd/filter`. The quarantine-expenses scheme is at `/kompensasjonsordning/innreise/innsyn/`.

### 4.8 Stiftelsesregisteret

- Kept by **Lotteri- og stiftelsestilsynet**, not Brreg. `www.stiftelsesregisteret.no` is a Salesforce Experience Cloud (LWR) app: robots `Allow: /`, but there is no documented API and the data loads through Aura/Apex calls, so it is hard to use.
- Stiftelser (5,968 STI) are in Enhetsregisteret with `registrertIStiftelsesregisteret`, `vedtektsfestetFormaal`, roles, and accounts in Regnskapsregisteret. **Use those, and skip stiftelsesregisteret.no.**
- Lottstift open data (`https://lottstift.no/nb/om-oss/apne-data/`) offers XLSX lists per year of momskompensasjon recipients/amounts, bingo, and Norsk Tipping surplus grants. They are keyed by organisation and could be a niche source for FLI orgs. Each file needs a licence check.

### 4.9 Reelle rettighetshavere (beneficial owners)

- `https://rrh.brreg.no/api/oppslag/...` returns **401** "Forespørsel mangler Authorization header" even for `/kodelister`.
- Access is Maskinporten-only, with scopes `brreg:reelle/offentlig | rapporteringspliktig(.begrenset) | media | sivilsamfunnsorganisasjon | hoeyereutdanningsinstitusjon`, granted on application.
- There is no open web search. **Not usable.**

### 4.10 Other services checked

| Service | Status |
|---|---|
| Nordic business lookup (`nsg.data.altinn.no`) | needs a free data.altinn.no API key (self-service). Flag it if used. |
| Oppgaveregisteret (`/oppgaveregisteret/api/skjema*.json`) | form catalogue, not company-specific. Skip. |
| Kommunalt rapporteringsregister (`/kommunalt-rapporteringsregister/api/rapporteringsplikter?organisasjonsnummer=`) | KOMM/FYLK only. |
| Konkursregisteret | no open API. Konkurs info comes from the enhet fields plus the KBO entity and its `BOST` role. |
| Autorisert roller / rolleutskrift | Maskinporten only. |
| Brreg RSS feed | news, operational messages and "Åpne data - Enhetsregisteret" notices only. |

No published rate limits were found and no 429s were seen.

---

## 5. Recommendations

1. **The core per-company fetch for N companies:**
   - one batch `/enheter?organisasjonsnummer=`
   - one feed-history call each for enheter (`includeChanges`) and roller
   - `/enheter/{o}/roller` per company
   - optional calls, each gated by a flag in the enhet: `konsernstruktur` (`erIKonsern`), `fullmakt/signatur` (AS/ASA/…), `frivillighetsregisteret` (`registrertIFrivillighetsregisteret`), `stoettetildeling` per company

   That comes to roughly 3 + 2–5 requests per company, which fits ≤ 20/company.
2. **Keep profiles current with the filtered feeds** (Section 1.3): about 3 requests per 1,000 profiles plus one batch re-fetch. Respect `Fjernet` by purging, keep `Sletting` tombstones, ignore mass-republish noise, and diff before reporting a change.
3. **Date facts from in-record legal dates first, then from feed `dato`.** Keep `oppdateringsid` and the JSON Patch op as evidence.
4. **Do not touch `w2.brreg.no/kunngjoring`** (robots-disallowed) or RRH. Use `rollegrupper[].sistEndret` and the feed to reconstruct the announcement timeline.
5. **Build the ≥1,000-profile corpus by MOD11 rejection sampling** (about 8 requests for 1,000 uniform samples). Stratify if you want guaranteed coverage of rare forms (NUF, BRL, ESEK, SA, STI, ANS/DA, KBO, KOMM, deleted ones).
6. Validate the MOD11 check digit locally before any request, so the agent doesn't spend budget on typos.
