# Envelope schema (`nca.signalpost.envelope/1.0`)

`envelopes.jsonl` holds one JSON object per input organisation number, in input order. Every input yields
exactly one terminal envelope, including invalid numbers, unknown, deleted and removed entities, and
companies whose sources all failed.

```text
schema_version           "nca.signalpost.envelope/1.0"
organisation_number      "916591438"
legal_name               convenience copy of the legal_name claim (null if none)
run                      {run_id, started_at, completed_at, terminal_status: completed|failed,
                          agent: {name, version, llm: null, paid_apis: []}}
identity                 {match_status: exact|subunit|deleted|not_found|invalid|error, confidence, method,
                          organisation_number_formatted, checks: [{name, passed, detail}], ...}
summary                  {text, sentences: [{topic, text, claim_ids, evidence_ids}], unknowns: [...], method}
sections                 the brief's required sections → {availability, claim_count, claim_ids}:
                          legal_identity_and_brand, annual_accounts, leadership_and_workplaces,
                          website_and_profiles, hiring_and_public_activity, claim_level_evidence,
                          refresh_and_changes
modules                  per source module: {availability, note, source_url, retrieved_at, http_status, requests}
claims[]                 atomic facts (below)
evidence[]               evidence records referenced by claims (below)
source_snapshots[]       {sha256, url, retrieved_at, bytes, content_type, path, redacted?} for every
                          content_sha256 cited; files under <out>/snapshots/<sha256>.gz
changes[]                typed changes since the previous run (REFRESH.md)
refresh                  {checked_at, previous_run_id, previous_checked_at, registry_last_changed_at,
                          change_count, material_change_count, stale_claim_count, next_refresh_due,
                          policy, content_hash}
errors[]                 {module, state, message, url}
operations               {requests, requests_by_module, runtime_ms, third_party_cost_usd: 0.0}
licences                 {licence_id: full attribution text} for every licence cited by evidence
                          (NLOD-2.0, CC0-1.0-GLEIF, CC0-1.0-Wikidata, NAV-API-terms, company-site-quote)
```

## Claim

```text
id            "cl-<12 hex>": deterministic hash of key, value, availability, period, relation
key           stable claim key "field|qualifier" (e.g. "revenue|2025|company", "board_member|kari-nordmann",
              "registered_workplace|973152351", "job_posting|nav:<uuid>", "social_profile|facebook|<url>")
field         controlled vocabulary (envelope.FAMILIES), e.g. legal_name, revenue, ceo, official_website
family        legal_identity | corporate_structure | annual_accounts | leadership | workplaces | website |
              social_profiles | hiring | public_activity
value         typed value (null unless availability == "available"; absence is never 0)
display       human-readable rendering
availability  available | not_available | blocked | not_applicable | ambiguous | failed
confidence    0..1
evidence_ids  ["ev-…"]: at least one for every available claim
effective_at  date the source says the value is valid for (role sistEndret, accounts period end, …)
period        accounts only: {start, end, year, scope: company|group}
relation      self (default) | parent | ultimate_parent | subunit | related. Claims about another entity
              are labelled, never presented as the company's own.
match_basis   why the fact belongs to this org number: organisation_number_exact, org_number_on_website,
              registry_linked_website, derived, wikidata_P2333_item_and_handle_name, ...
method        extraction/verification method id
module        source module that produced it
first_seen / last_seen   first and latest run in which this exact key+value was observed
stale         true when carried forward from the previous run because its source failed now
note          caveats (quality flags, reasons for absence)
```

## Evidence

```text
id               "ev-<12 hex>": hash of source_url, content_sha256, locator, claim_span, method
source_url       exact resource fetched (for multi-id batch calls, the canonical single-entity URL that
                 returns the same record; the real request is in retrieved_via)
page_url         human-readable page to verify (virksomhet.brreg.no, company page, NAV ad, GLEIF record)
source_class     official_registry | official_registry_bulk | official_registry_update_feed | official_roles |
                 official_subunits | official_group_structure | official_annual_accounts |
                 official_annual_account_copies | official_signature_prokura | official_state_aid_register |
                 official_public_jobs_feed | official_lei_registry | open_knowledge_base | company_owned |
                 company_linked_profile
source_name, retrieved_at (ISO UTC), effective_at, http_status, final_url, licence (id into envelope.licences)
content_sha256   SHA-256 of the raw response bytes (snapshot stored under the same hash)
claim_span       verbatim fragment of the source containing the value
locator          JSON pointer (/forretningsadresse/adresse/0), page location, or feed item
extraction_method  <module>@<version>
retrieved_via    batch/bulk URL when the record came from a multi-id or bulk request
```

## Change

```text
key, field, kind (added|modified|removed), change_type (new_ceo, new_filing, changed_address, new_job,
closed_job, new_location, closed_location, changed_description, ...), materiality (material|minor),
old_value/old_display/old_claim_id/old_evidence_ids, new_value/new_display/new_claim_id/new_evidence_ids,
previous_run_id, previous_checked_at, current_run_id, detected_at, effective_at
```
Evidence for both sides of every change is included in the envelope's `evidence[]`.
