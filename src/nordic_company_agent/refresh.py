"""Refresh as a diff: compare this run's claims with the previous envelope by stable claim key.

Rules
- Only compare claims whose source module was *checked* in both runs (outcome available /
  not_available / not_applicable). A source that failed or was blocked this run never produces
  "removed" changes; its last supported claims are carried forward and marked stale instead.
- Same key + same value ⇒ no change (first_seen preserved). This makes re-runs on the same
  source snapshot idempotent: no duplicate records and no false changes.
- Every change carries old/new values plus evidence ids for both sides (the previous evidence
  objects are copied into the new envelope so both sides stay verifiable).
"""

from __future__ import annotations

from typing import Any

from .envelope import canonical

CHECKED = {"available", "not_available", "not_applicable"}

# Claims produced inside another module's call but owned by a separately-enabled module.
FIELD_MODULE = {"accounts_filing_years": "account_copies", "annual_report_copy": "account_copies"}


def claim_module(c: dict[str, Any]) -> str | None:
    return FIELD_MODULE.get(c.get("field", ""), c.get("module"))

MATERIAL_FIELDS = {
    "legal_name", "legal_form", "registry_status", "bankruptcy", "liquidation", "forced_dissolution", "deletion",
    "business_address", "municipality", "ceo", "chair", "official_website", "accounts_period", "revenue",
    "net_profit", "equity", "parent_company", "ultimate_parent", "share_capital", "auditor", "industry",
    "employees", "group_membership", "lei_status",
}

CHANGE_TYPES = {
    ("ceo", "added"): "new_ceo", ("ceo", "modified"): "new_ceo", ("ceo", "removed"): "ceo_left",
    ("chair", "added"): "new_chair", ("chair", "modified"): "new_chair", ("chair", "removed"): "chair_left",
    ("board_member", "added"): "new_role", ("board_member", "removed"): "removed_role",
    ("deputy_board_member", "added"): "new_role", ("deputy_board_member", "removed"): "removed_role",
    ("auditor", "added"): "new_auditor", ("auditor", "modified"): "new_auditor",
    ("accounts_period", "added"): "new_filing", ("registered_workplace", "added"): "new_location",
    ("registered_workplace", "removed"): "closed_location", ("job_posting", "added"): "new_job",
    ("job_posting", "removed"): "closed_job", ("website_description", "modified"): "changed_description",
    ("business_address", "modified"): "changed_address", ("legal_name", "modified"): "changed_name",
    ("registry_status", "modified"): "changed_status", ("official_website", "added"): "new_website",
    ("official_website", "modified"): "changed_website", ("official_website", "removed"): "website_gone",
    ("employees", "modified"): "changed_employees", ("social_profile", "added"): "new_profile",
    ("social_profile", "removed"): "removed_profile", ("news_item", "added"): "new_activity",
    ("registry_event", "added"): "new_registry_event", ("share_capital", "modified"): "changed_capital",
}

# Fields whose items are time-series/activity streams: new items are 'added' but old items aging
# out of a source window are not 'removed' business events.
WINDOWED_FIELDS = {"registry_event", "role_change_event", "news_item", "state_aid_grant", "tax_incentive_project",
                   "research_grant", "food_inspection", "job_posting_history"}


def _ckey(c: dict[str, Any]) -> tuple[str, str]:
    return (c["key"], c.get("relation", "self"))


def diff(previous: dict[str, Any] | None, current_claims: list[dict[str, Any]],
         current_modules: dict[str, dict[str, Any]], *, run_id: str, detected_at: str,
         registry_changed_at: str | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (changes, carried_forward_claims)."""
    if not previous:
        return [], []
    prev_modules = previous.get("modules", {})
    prev_run = previous.get("run", {})
    prev_claims = [c for c in previous.get("claims", []) if c.get("availability") == "available"]
    cur_avail = [c for c in current_claims if c.get("availability") == "available"]

    def checked(mods: dict[str, Any], module: str | None) -> bool:
        if not module:
            return True
        st = (mods.get(module) or {}).get("availability")
        return st in CHECKED

    old = {}
    for c in prev_claims:
        old.setdefault(_ckey(c), c)
    new = {}
    for c in cur_avail:
        new.setdefault(_ckey(c), c)

    # Single-valued fields: a different qualifier means the same slot changed value
    # (e.g. ceo|ola-nordmann -> ceo|kari-nordmann is one 'modified' change, not add+remove).
    single_old = {c["field"]: c for c in prev_claims if "|" not in c["key"] and c.get("relation", "self") == "self"}

    changes: list[dict[str, Any]] = []
    carried: list[dict[str, Any]] = []
    for k, nc in new.items():
        module = claim_module(nc)
        oc = old.get(k)
        if oc is None:
            if not checked(prev_modules, module):
                continue  # source not checked last time: first observation, not a change
            changes.append(_change("added", nc["field"], None, nc, previous, run_id, detected_at, registry_changed_at))
        elif canonical(oc.get("value")) != canonical(nc.get("value")):
            changes.append(_change("modified", nc["field"], oc, nc, previous, run_id, detected_at, registry_changed_at))
    for k, oc in old.items():
        if k in new:
            continue
        module = claim_module(oc)
        if not checked(current_modules, module):
            stale = dict(oc)
            stale["stale"] = True
            stale["note"] = (f"carried forward from run {prev_run.get('run_id')} ({prev_run.get('completed_at')}): "
                             f"source '{module}' could not be re-checked in this run")
            carried.append(stale)
            continue
        if not checked(prev_modules, module) or oc["field"] in WINDOWED_FIELDS:
            continue
        changes.append(_change("removed", oc["field"], oc, None, previous, run_id, detected_at, registry_changed_at))
    # Collapse single-valued add+remove pairs into 'modified'.
    by_field: dict[str, list[dict[str, Any]]] = {}
    for ch in changes:
        by_field.setdefault(ch["field"], []).append(ch)
    merged: list[dict[str, Any]] = []
    for field, chs in by_field.items():
        adds = [c for c in chs if c["kind"] == "added"]
        rems = [c for c in chs if c["kind"] == "removed"]
        if field in {"ceo", "chair", "deputy_chair", "auditor", "accountant", "official_website", "general_partner",
                     "business_manager", "parent_company", "ultimate_parent"} and len(adds) == 1 and len(rems) == 1:
            a, r = adds[0], rems[0]
            m = dict(a)
            m["kind"] = "modified"
            m["change_type"] = CHANGE_TYPES.get((field, "modified"), f"{field}_modified")
            m["old_value"], m["old_display"] = r["old_value"], r.get("old_display")
            m["old_claim_id"], m["old_evidence_ids"] = r.get("old_claim_id"), r.get("old_evidence_ids", [])
            merged.append(m)
        else:
            merged.extend(chs)
    merged.sort(key=lambda c: (0 if c["materiality"] == "material" else 1, c["field"], c["key"]))
    _ = single_old
    return merged, carried


def _change(kind: str, field: str, oc: dict | None, nc: dict | None, previous: dict, run_id: str,
            detected_at: str, registry_changed_at: str | None) -> dict[str, Any]:
    ref = nc or oc or {}
    ch: dict[str, Any] = {
        "key": ref.get("key"),
        "field": field,
        "kind": kind,
        "change_type": CHANGE_TYPES.get((field, kind), f"{field}_{kind}"),
        "materiality": "material" if field in MATERIAL_FIELDS else "minor",
        "previous_run_id": previous.get("run", {}).get("run_id"),
        "previous_checked_at": previous.get("run", {}).get("completed_at"),
        "current_run_id": run_id,
        "detected_at": detected_at,
    }
    if oc is not None:
        ch.update(old_value=oc.get("value"), old_display=oc.get("display"), old_claim_id=oc.get("id"),
                  old_evidence_ids=oc.get("evidence_ids", []))
    if nc is not None:
        ch.update(new_value=nc.get("value"), new_display=nc.get("display"), new_claim_id=nc.get("id"),
                  new_evidence_ids=nc.get("evidence_ids", []))
        eff = nc.get("effective_at")
        if eff:
            ch["effective_at"] = eff
    if "effective_at" not in ch and registry_changed_at and ref.get("module", "").startswith("registry"):
        ch["effective_at"] = registry_changed_at
    return ch


def carry_first_seen(previous: dict[str, Any] | None, claims: list[dict[str, Any]], now: str) -> None:
    """Set first_seen/last_seen on available claims, preserving first_seen across unchanged runs."""
    prev = {}
    if previous:
        for c in previous.get("claims", []):
            if c.get("availability") == "available":
                prev[(c["key"], c.get("relation", "self"), canonical(c.get("value")))] = c
    for c in claims:
        if c.get("availability") != "available" or c.get("stale"):
            continue
        p = prev.get((c["key"], c.get("relation", "self"), canonical(c.get("value"))))
        c["first_seen"] = (p or {}).get("first_seen") or now
        c["last_seen"] = now
