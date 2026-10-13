# Refresh

`nca run` is also the refresh command. If `<out>/envelopes.jsonl` exists (or `--previous` is given), the new run
is diffed against it, and the previous files are first copied to `<out>/history/<previous_run_id>/`.

* **Stable claim keys** (`field|qualifier`) and deterministic ids: an identical source snapshot yields
  identical claims and **zero changes**. Tested in `tests/test_assemble_refresh.py`, and on a live 50-company
  rerun: 0 changes.
* **Typed changes** with materiality: `new_ceo`, `new_chair`, `new_role`, `removed_role`, `new_auditor`,
  `new_filing`, `changed_address`, `changed_name`, `changed_status`, `new_location`, `closed_location`, `new_job`,
  `closed_job`, `new_website`, `changed_description`, `changed_employees`, `new_registry_event`, ...
  Single-valued fields (CEO, chair, auditor, website, parent) report `modified` with old and new values.
* **Evidence for both sides:** old evidence records are copied into the new envelope.
* **Failed refresh never erases:** if a source fails, is blocked or is disabled this run, its last supported
  claims are carried forward with `stale: true` and a note. They are not reported as removed.
* Only sources checked in both runs can produce `added`/`removed`. A source not checked last time yields
  first observations, not changes.
* **Dating changes:** the Enhetsregisteret update feed (`/oppdateringer/enheter`, `/oppdateringer/roller`)
  gives `registry_last_changed_at` and dated `registry_event` claims. Role groups carry `sistEndret`.
  Accounts carry period end dates.
* **Cadence** (`refresh.next_refresh_due`): daily for companies with active job ads, otherwise weekly. Registry
  and roles are cheap to re-check daily via the update feeds; accounts change mainly February–July.
