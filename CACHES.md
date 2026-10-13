# Declared caches

The evaluation contract allows cached public-universe material if it is declared. This agent ships three
caches under `cache/`. None of them is used as **evidence**. Each is a discovery index that decides
*where to look*; every published claim is re-fetched live during the run and carries its own retrieval
time and content hash.

| cache | built by | content | how the agent uses it | freshness |
|---|---|---|---|---|
| `site_discovery.jsonl.gz` (+ `.meta.json`) | `tools/build_site_discovery.py` | Per universe org number: candidate domains (registry website, own-domain e-mail, legal-name slugs) on which that exact checksum-valid org number was printed when crawled, with the proof page URL and context. Also which candidate kinds were checked without success. | The website module fetches the proof page and home page **live** and re-runs the identity gate. Nothing from the cache is published. A stale lead that no longer proves identity is dropped. | Built 2026-10-13/14 from the public 411,160-company universe. Rebuild any time. |
| `nav_jobs.jsonl.gz` (+ `.meta.json`) | `tools/build_nav_jobs_cache.py` | Active NAV Arbeidsplassen job ads with employer org number (sub-unit mapped to parent via Enhetsregisteret). | At run time the jobs module pages the NAV feed **forward from the cache cursor**, so new, changed and inactive ads are applied (inactive ads are removed, as NAV's terms require). New ads are confirmed by fetching the feed entry. | Cursor and build time in the meta file. |
| `wikidata_p2333.json.gz` (+ `.meta.json`) | `tools/build_wikidata_index.py` | Map org number → Wikidata QIDs for all items with P2333 (Norwegian organisation number). | Pre-selects which batch companies need a Wikidata query, so most batches need no Wikidata request. Claims come from a live SPARQL query. | Rebuild weekly. |

All caches derive from the public universe (`signalpost-company-universe-2025.jsonl.gz`), the open
Enhetsregisteret bulk file (NLOD 2.0), NAV's public feed and Wikidata (CC0). None contains personal data
beyond what the sources publish about businesses.
