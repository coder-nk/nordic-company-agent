# nordic-company-agent

A deterministic company-research agent for the Builderr **Signalpost** challenge. Give it Norwegian
organisation numbers. It returns **one terminal envelope per company**: legal identity, filed accounts,
leadership, workplaces, the verified official website and company-linked profiles, hiring and dated
public activity. Every fact carries its source URL, retrieval time, reporting date, content hash and a
verbatim evidence span, plus an explicit availability state for anything it could not find.

* **No LLM, no paid API, no API keys:** declared third-party cost **$0.00**.
* **Identity first:** every published fact is tied to the exact organisation number. Websites are
  published only when the site prints that number, or when the registry-filed site is independently
  corroborated. Otherwise the website claim is `ambiguous` or `not_available`.
* **Budgeted:** every outbound HTTP request (redirect hops, retries and robots.txt included) counts against
  a hard cap. The defaults are 2,000 requests and 40 minutes per batch.
* **Refresh as a diff:** re-running against the previous envelopes emits typed, evidence-backed
  changes. An identical source snapshot produces zero changes. A failed source keeps its last value,
  marked stale; it is never silently removed.

## One command

```bash
./run.sh batches/smoke-100.jsonl out/smoke
```

`run.sh` installs the pinned environment (`uv sync --frozen`, or a plain `venv` + `requirements.txt`
if `uv` is missing) and runs:

```bash
uv run nca run --input <organisations.jsonl|.json|.txt> --out <dir> \
    [--bulk brreg-enheter.csv]        # optional frozen registry snapshot (identity anchor, 0 requests)
    [--previous old/envelopes.jsonl]  # default: <dir>/envelopes.jsonl from the last run
    [--max-requests 2000] [--time-budget 40]
```

Input formats: JSONL (`{"organisation_number": "..."}` per line), JSON (a list or
`{"organisation_numbers": [...]}`), or plain text with one number per line. Invalid numbers (MOD11) still
get an envelope (`identity.match_status = "invalid"`, zero requests). Duplicates are dropped and reported.

Outputs in `<dir>`:

| file | content |
|---|---|
| `envelopes.jsonl` | exactly one envelope per input, in input order (schema in `DATA_SCHEMA.md`) |
| `run-report.json` | requests (total, by source, by host), p50/p95 runtime, cost, availability per module and field, contract validation |
| `changes.jsonl` | every material and minor change since the previous run |
| `snapshots/` | gzip raw response bodies, keyed by the SHA-256 cited in evidence (role payloads stored without birth dates) |
| `site/index.html` | static viewer: search, coverage, per-fact "View source", compare. Works offline and on mobile |
| `history/<run_id>/` | the previous run's envelopes, preserved before they are overwritten |

Other commands: `nca company 923609016 --summary`, `nca serve` (HTTP: `GET /companies/:orgnr`,
`POST /research`, `POST /refresh`), `nca validate out/smoke/envelopes.jsonl`, `nca viewer`, `nca sample`.

## Models, APIs and licences

| source | used for | access | licence |
|---|---|---|---|
| Brønnøysundregistrene Enhetsregisteret API (+ update feeds, group structure, signature/prokura) | identity, addresses, industry, employees, capital, roles, workplaces, group, dated registry events | keyless open API | NLOD 2.0 |
| Regnskapsregisteret open API | latest 3 filed annual accounts (company and group) | keyless open API | NLOD 2.0 |
| Company-owned websites | verified site, description, brand, contacts, social profiles, careers page, RSS news | public pages, robots.txt honoured, SSRF-safe | quoted as evidence only |
| NAV Arbeidsplassen job feed | active job ads by employer org number | public rotating token (no account) | NAV API terms |
| GLEIF API | LEI, LEI status, LEI parents | keyless | CC0 |
| Wikidata | Wikidata item, Wikipedia article, listing, linked profiles (P2333 = org number) | keyless SPARQL | CC0 |
| Støtteregisteret (Brreg) | dated state-aid grants | keyless | NLOD 2.0 |

**No language model is used.** The summaries are deterministic templates over published claims, and
every sentence cites claim and evidence ids. No proff.no, purehelp, 1881, gulesider, LinkedIn, Meta,
Google or search-engine scraping. See `LIMITATIONS.md` and `CRAWLERS.md`.

## Expected run cost

* Third-party cost: **$0.00** per run (no paid API, no LLM).
* Requests and runtime: see `smoke/run-report.json` (measured on the 100-company smoke batch).
  Batched endpoints keep registry identity, change feeds, GLEIF and Wikidata at about one request per
  100–1,000 companies. The per-company costs are accounts (1), roles (1), workplaces (1) and the
  website (about 3–5 when a lead exists).

## Declared caches (`cache/`, see `CACHES.md`)

* `site_discovery.jsonl.gz`: for universe companies, domains on which the exact org number was printed
  when crawled (leads only; the agent re-fetches and re-verifies live before publishing).
* `nav_jobs.jsonl.gz`: active NAV job ads keyed by employer org number; refreshed forward from its
  cursor at run time; inactive ads removed.
* `wikidata_p2333.json.gz`: org number → Wikidata QID map, so batches with no Wikidata entities need no
  Wikidata request.

Rebuild with `tools/build_site_discovery.py`, `tools/build_nav_jobs_cache.py` and `tools/build_wikidata_index.py`.

## Development

```bash
uv sync
uv run pytest -q
```

Design and module contract: `DESIGN.md`. Research notes behind every source decision: `research/`.
