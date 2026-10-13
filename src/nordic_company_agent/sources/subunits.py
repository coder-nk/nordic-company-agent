"""Registered sub-units (underenheter / workplaces) of a main unit.

`GET /enhetsregisteret/api/underenheter?overordnetEnhet={orgnr}&size=1000` (one parent per call; paged,
at most 3 pages). Each workplace claim cites the canonical single-record URL `/underenheter/{n}` as
`source_url`, with the list request in `retrieved_via`; `content_sha256` is the SHA-256 of that
record's raw bytes sliced verbatim from the list body (stored as its own snapshot). The count claim
cites the list body itself (`/page/totalElements`).
"""

from __future__ import annotations

import json
from typing import Any

from ..context import RunContext
from ..envelope import NLOD, OFFICIAL_SUBUNITS, Claim, CompanyResult, Evidence, ModuleOutcome, utc_iso
from ..orgnr import formatted
from .registry import (ACCEPT_JSON, BASE, BUDGET_NOTE, address_display, array_elements, decode_text, enhet_page,
                       members_span, outcome_state_for_status, span_at, subunit_value, underenhet_page, underenhet_url)

MODULE = "subunits"
VERSION = "1"
METHOD = f"{MODULE}@{VERSION}"
TIER = "subunits"
SOURCE_NAME = "Brønnøysundregistrene, Enhetsregisteret sub-units API (underenheter)"
PAGE_SIZE = 1000
MAX_PAGES = 3
MAX_CLAIMS = 200          # workplace claims per company (the count claim always reports the full total)
MAX_BYTES = 24_000_000


def list_url(orgnr: str, page: int = 0) -> str:
    url = f"{BASE}/underenheter?overordnetEnhet={orgnr}&size={PAGE_SIZE}"
    return url + (f"&page={page}" if page else "")


async def run(ctx: RunContext, company: CompanyResult) -> None:
    try:
        await _run(ctx, company)
    except Exception as exc:
        company.error(MODULE, "failed", f"{type(exc).__name__}: {exc}")
        company.outcome(ModuleOutcome(MODULE, "failed", note=f"module error: {type(exc).__name__}"))


async def _run(ctx: RunContext, company: CompanyResult) -> None:
    orgnr = company.organisation_number
    status = (company.data.get("identity") or {}).get("match_status")
    if status != "exact":
        company.outcome(ModuleOutcome(MODULE, "not_applicable",
                                      note=f"sub-units are listed only for active main units ({status})"))
        return
    records: list[tuple[str, dict[str, Any], str, str]] = []   # (raw text, rec, list url, retrieved_at)
    total: int | None = None
    first: dict[str, Any] | None = None
    n_req = 0
    partial_note = None
    bulk = ctx.shared.get("bulk_subunits")
    if bulk is not None:
        await _from_bulk(ctx, company, bulk)
        return
    for page in range(MAX_PAGES):
        url = list_url(orgnr, page)
        if not ctx.allow(TIER, 1):
            if page == 0:
                company.outcome(ModuleOutcome(MODULE, "failed", note=BUDGET_NOTE, source_url=url))
                return
            partial_note = f"only {page} page(s) fetched: {BUDGET_NOTE}"
            break
        try:
            res = await ctx.get(url, tier=TIER, source=MODULE, headers=ACCEPT_JSON, max_bytes=MAX_BYTES)
            n_req += 1
        except Exception as exc:
            msg = f"sub-unit list failed: {type(exc).__name__}: {exc}"[:300]
            if page == 0:
                company.error(MODULE, "failed", msg, url)
                company.outcome(ModuleOutcome(MODULE, "failed", note=msg, source_url=url, requests=n_req))
                return
            partial_note = msg
            break
        retrieved_at = utc_iso(res.retrieved_at)
        if res.status != 200 or res.truncated:
            msg = f"sub-unit list returned HTTP {res.status}" + (" (truncated)" if res.truncated else "")
            if page == 0:
                state = outcome_state_for_status(res.status) if res.status != 200 else "failed"
                company.outcome(ModuleOutcome(MODULE, state, note=msg, source_url=url, retrieved_at=retrieved_at,
                                              http_status=res.status, requests=n_req))
                return
            partial_note = msg
            break
        sha = ctx.snapshots.save_result(res, retrieved_at)
        text = decode_text(res.content)
        try:
            doc = json.loads(text)
        except ValueError as exc:
            if page == 0:
                company.outcome(ModuleOutcome(MODULE, "failed", note=f"unparseable sub-unit list: {exc}"[:300],
                                              source_url=url, retrieved_at=retrieved_at, http_status=200, requests=n_req))
                return
            partial_note = "unparseable later page"
            break
        if page == 0:
            pg = doc.get("page") or {}
            total = pg.get("totalElements") if isinstance(pg.get("totalElements"), int) else None
            first = {"url": url, "sha": sha, "text": text, "retrieved_at": retrieved_at}
        for s, e in array_elements(text, "/_embedded/underenheter"):
            rtext = text[s:e]
            records.append((rtext, json.loads(rtext), url, retrieved_at))
        if total is None or (page + 1) * PAGE_SIZE >= total:
            break
    assert first is not None

    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    mismatched = 0
    for rtext, rec, url, retrieved_at in records:
        sub = str(rec.get("organisasjonsnummer") or "")
        if not sub or sub in seen:
            continue
        if str(rec.get("overordnetEnhet") or "") != orgnr:
            mismatched += 1          # echo check: never attach another unit's workplace
            continue
        seen.add(sub)
        v = subunit_value(rec)
        v["closed"] = rec.get("nedleggelsesdato")
        items.append({"value": v, "text": rtext, "via": url, "retrieved_at": retrieved_at, "rec": rec})
    items.sort(key=lambda it: it["value"]["organisation_number"])
    active = [it for it in items if not it["value"]["closed"]]
    closed = [it for it in items if it["value"]["closed"]]
    company.data["subunits"] = [dict(it["value"]) for it in items]
    complete = total is None or len(records) >= total
    count = len(active) if complete else max(0, (total or 0) - len(closed))
    company.data["subunits_meta"] = {"total_reported": total, "fetched": len(items), "active": len(active),
                                     "closed": len(closed), "complete": complete, "echo_mismatches": mismatched,
                                     "count": count}

    # Choose which workplaces become claims: all when <= MAX_CLAIMS, else the largest by employees.
    chosen = active
    if len(active) > MAX_CLAIMS:
        chosen = sorted(active, key=lambda it: (-(it["value"]["employees"] or 0), it["value"]["organisation_number"]))
        chosen = sorted(chosen[:MAX_CLAIMS], key=lambda it: it["value"]["organisation_number"])
    claims: list[Claim] = []
    for it in chosen:
        v = {k: it["value"].get(k) for k in ("organisation_number", "name", "address", "industry", "employees", "started")}
        if it["value"].get("employees_range"):
            v["employees_range"] = it["value"]["employees_range"]
        sub = v["organisation_number"]
        rbytes = it["text"].encode("utf-8")
        rsha = ctx.snapshots.save(rbytes, url=underenhet_url(sub), retrieved_at=it["retrieved_at"],
                                  content_type="application/json")
        common = dict(source_url=underenhet_url(sub), source_class=OFFICIAL_SUBUNITS, source_name=SOURCE_NAME,
                      retrieved_at=it["retrieved_at"], content_sha256=rsha, extraction_method=METHOD,
                      page_url=underenhet_page(sub), http_status=200, licence=NLOD, retrieved_via=it["via"])
        span = members_span(it["text"], "", ["organisasjonsnummer", "overordnetEnhet"])
        evs = [Evidence(claim_span=span, locator="/overordnetEnhet", **common)]
        name_span = span_at(it["text"], "/navn")
        if name_span and (not span or name_span not in span):
            evs.append(Evidence(claim_span=name_span, locator="/navn", **common))
        addr = (it["rec"].get("beliggenhetsadresse") or {}) if isinstance(it["rec"].get("beliggenhetsadresse"), dict) else {}
        disp = f"{v.get('name')} ({formatted(sub)})" + (f", {address_display(addr)}" if address_display(addr) else "")
        claims.append(Claim(field="registered_workplace", value=v, evidence=evs, qualifier=sub, display=disp,
                            relation="subunit", method=METHOD, effective_at=v.get("started")))
    count_span = span_at(first["text"], "/page/totalElements")
    count_ev = Evidence(source_url=first["url"], source_class=OFFICIAL_SUBUNITS, source_name=SOURCE_NAME,
                        retrieved_at=first["retrieved_at"], content_sha256=first["sha"],
                        claim_span=count_span, locator="/page/totalElements" if count_span else "/",
                        extraction_method=METHOD, page_url=enhet_page(orgnr), http_status=200, licence=NLOD)
    count_note = None
    if not complete:
        count_note = f"count from page.totalElements ({total}); {len(items)} sub-units fetched"
    if len(active) > len(chosen):
        count_note = (count_note + "; " if count_note else "") + \
            f"workplace claims limited to the {len(chosen)} largest by employees"
    claims.append(Claim(field="registered_workplace_count", value=count, evidence=[count_ev], display=str(count),
                        method=METHOD, note=count_note))
    company.add(claims)
    note = partial_note
    if count:
        company.outcome(ModuleOutcome(MODULE, "available", note=note or f"{count} registered sub-unit(s)",
                                      source_url=first["url"], retrieved_at=first["retrieved_at"], http_status=200,
                                      requests=n_req))
    else:
        company.outcome(ModuleOutcome(MODULE, "not_available", note=note or "no registered sub-units (underenheter)",
                                      source_url=first["url"], retrieved_at=first["retrieved_at"], http_status=200,
                                      requests=n_req))


async def _from_bulk(ctx: RunContext, company: CompanyResult, bulk: dict[str, Any]) -> None:
    """Sub-units from the nightly underenheter bulk file (one request for the whole batch)."""
    orgnr = company.organisation_number
    raws = bulk["items"].get(orgnr) or []
    retrieved_at = bulk["retrieved_at"]
    items = []
    for raw in raws:
        text = raw.decode("utf-8")
        rec = json.loads(text)
        if str(rec.get("overordnetEnhet") or "") != orgnr:
            continue
        v = subunit_value(rec)
        v["closed"] = rec.get("nedleggelsesdato")
        items.append({"value": v, "text": text, "rec": rec})
    items.sort(key=lambda it: it["value"]["organisation_number"])
    active = [it for it in items if not it["value"]["closed"]]
    company.data["subunits"] = [dict(it["value"]) for it in items]
    company.data["subunits_meta"] = {"total_reported": len(items), "fetched": len(items), "active": len(active),
                                     "closed": len(items) - len(active), "complete": True, "source": "bulk",
                                     "count": len(active)}
    chosen = active
    if len(active) > MAX_CLAIMS:
        chosen = sorted(active, key=lambda it: (-(it["value"]["employees"] or 0), it["value"]["organisation_number"]))
        chosen = sorted(chosen[:MAX_CLAIMS], key=lambda it: it["value"]["organisation_number"])
    claims: list[Claim] = []
    evs_all = []
    for it in chosen:
        v = {k: it["value"].get(k) for k in ("organisation_number", "name", "address", "industry", "employees", "started")}
        if it["value"].get("employees_range"):
            v["employees_range"] = it["value"]["employees_range"]
        sub = v["organisation_number"]
        rsha = ctx.snapshots.save(it["text"].encode("utf-8"), url=underenhet_url(sub), retrieved_at=retrieved_at,
                                  content_type="application/json")
        span = members_span(it["text"], "", ["organisasjonsnummer", "overordnetEnhet"])
        ev = Evidence(source_url=underenhet_url(sub), source_class=OFFICIAL_SUBUNITS, source_name=SOURCE_NAME,
                      retrieved_at=retrieved_at, content_sha256=rsha, claim_span=span, locator="/overordnetEnhet",
                      extraction_method=METHOD + "+bulk", page_url=underenhet_page(sub), http_status=200,
                      licence=NLOD, retrieved_via=bulk["url"], effective_at=bulk.get("last_modified"))
        evs_all.append(ev)
        addr = (it["rec"].get("beliggenhetsadresse") or {}) if isinstance(it["rec"].get("beliggenhetsadresse"), dict) else {}
        disp = f"{v.get('name')} ({formatted(sub)})" + (f", {address_display(addr)}" if address_display(addr) else "")
        claims.append(Claim(field="registered_workplace", value=v, evidence=[ev], qualifier=sub, display=disp,
                            relation="subunit", method=METHOD, effective_at=v.get("started")))
    count = len(active)
    count_ev = Evidence(source_url=bulk["url"], source_class=OFFICIAL_SUBUNITS, source_name=SOURCE_NAME,
                        retrieved_at=retrieved_at, content_sha256=bulk.get("file_sha256"),
                        claim_span=f'{count} elements with "overordnetEnhet":"{orgnr}" and no nedleggelsesdato',
                        locator="$[?(@.overordnetEnhet=='" + orgnr + "')]", extraction_method=METHOD + "+bulk",
                        page_url=enhet_page(orgnr), http_status=200, licence=NLOD,
                        effective_at=bulk.get("last_modified"))
    claims.append(Claim(field="registered_workplace_count", value=count, evidence=[count_ev], display=str(count),
                        method=METHOD + "+bulk", match_basis="derived",
                        note="counted in the nightly underenheter bulk file"))
    company.add(claims)
    state = "available" if count else "not_available"
    company.outcome(ModuleOutcome(MODULE, state, source_url=bulk["url"], retrieved_at=retrieved_at, http_status=200,
                                  note=(f"{count} registered sub-unit(s) (nightly bulk file)" if count
                                        else "no registered sub-units (nightly bulk file)")))
