"""Dated change timeline from the Enhetsregisteret update feeds (batch module, tier "batch_external").

- `GET /oppdateringer/enheter?organisasjonsnummer=<<=500>&dato=<since>&includeChanges=true&size=10000`
  (paged by `oppdateringsid=<last+1>`, bounded) -> `registry_event` [oppdateringsid] claims
  {"date", "change_type", "fields": [changed JSON paths]}.
- `GET /oppdateringer/roller?organisasjonsnummer=<list>&afterTime=<since>&size=10000` (CloudEvents, paged
  by `afterId`) -> `role_change_event` [event id] claims {"date", "type"}.

Mass republishes are not changes: `Ukjent` events and `Endring` events without a patch are dropped
from the timeline (they still count for `last_event_at`). Only the latest MAX_EVENTS per company are
published. Evidence: `source_url` is the canonical single-event URL (`oppdateringsid=<id>&size=1`
returns exactly that event), `retrieved_via` the multi-company request actually made, and
`content_sha256` the SHA-256 of that request's body (stored as a snapshot; `locator` points into it).
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from ..context import RunContext
from ..envelope import NLOD, OFFICIAL_REGISTRY_FEED, Claim, CompanyResult, Evidence, ModuleOutcome, utc_iso
from .registry import (ACCEPT_JSON, BASE, BUDGET_NOTE, array_elements, decode_text, enhet_page, members_span,
                       outcome_state_for_status, pointer, span_at)

MODULE = "registry_feed"
VERSION = "1"
METHOD = f"{MODULE}@{VERSION}"
TIER = "batch_external"
SOURCE_NAME = "Brønnøysundregistrene, Enhetsregisteret update feed (oppdateringer)"
CHUNK = 500
FEED_SIZE = 10000
MAX_PAGES = 3
MAX_EVENTS = 20
WINDOW_DAYS = 365
MAX_LOOKBACK_DAYS = 3 * 365
MAX_BYTES = 48_000_000
MEANINGFUL_WITHOUT_PATCH = {"Ny", "Sletting", "Fjernet"}
ROLE_TYPES = {"no.brreg.enhetsregisteret.rolle.oppdatert": "roles updated",
              "no.brreg.enhetsregisteret.rolle.enhet.oppdatert": "a role-holding entity was updated"}


def _fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _parse_iso(s: Any) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def compute_since(now: datetime, previous_checked_at: Any = None) -> str:
    """Start of the day WINDOW_DAYS ago (UTC); widened to the previous check when that is older."""
    start = (now - timedelta(days=WINDOW_DAYS)).replace(hour=0, minute=0, second=0, microsecond=0)
    prev = _parse_iso(previous_checked_at)
    if prev is not None and prev < start:
        start = max(prev, now - timedelta(days=MAX_LOOKBACK_DAYS))
    return _fmt(start)


def changed_fields(endringer: Any) -> list[str]:
    """JSON-Patch ops -> sorted unique paths without array indices ('/forretningsadresse/adresse/0' ->
    '/forretningsadresse/adresse')."""
    out: set[str] = set()
    for op in endringer or []:
        if isinstance(op, dict) and isinstance(op.get("path"), str):
            parts = [p for p in op["path"].split("/")[1:] if not re.fullmatch(r"\d+|-", p)]
            if parts:
                out.add("/" + "/".join(parts))
    return sorted(out)


def is_timeline_event(ev: dict[str, Any]) -> bool:
    et = ev.get("endringstype")
    if et == "Ukjent":
        return False
    if et in MEANINGFUL_WITHOUT_PATCH:
        return True
    return bool(ev.get("endringer"))


def _enheter_url(orgnrs: list[str], since: str, start_id: int | None) -> str:
    url = (f"{BASE}/oppdateringer/enheter?organisasjonsnummer={','.join(orgnrs)}&dato={quote(since, safe=':')}"
           f"&includeChanges=true&size={FEED_SIZE}")
    return url + (f"&oppdateringsid={start_id}" if start_id is not None else "")


def _roller_url(orgnrs: list[str], since: str, after_id: str | None) -> str:
    url = f"{BASE}/oppdateringer/roller?organisasjonsnummer={','.join(orgnrs)}&afterTime={quote(since, safe=':')}&size={FEED_SIZE}"
    return url + (f"&afterId={after_id}" if after_id is not None else "")


async def _fetch(ctx: RunContext, url: str) -> tuple[Any, str | None, Any]:
    """(result, error_state, error_note) for one feed request."""
    if not ctx.allow(TIER, 1):
        return None, "failed", BUDGET_NOTE
    try:
        res = await ctx.get(url, tier=TIER, source=MODULE, headers=ACCEPT_JSON, max_bytes=MAX_BYTES)
    except Exception as exc:
        return None, "failed", f"feed request failed: {type(exc).__name__}: {exc}"[:300]
    if res.status != 200 or res.truncated:
        state = outcome_state_for_status(res.status) if res.status != 200 else "failed"
        return None, state, f"feed request returned HTTP {res.status}" + (" (truncated)" if res.truncated else "")
    return res, None, None


@dataclass
class _Fetched:
    items: list[dict[str, Any]] = field(default_factory=list)
    err: str | None = None
    note: str | None = None
    first_url: str | None = None
    first_retrieved_at: str | None = None


async def _feed_chunk(ctx: RunContext, orgnrs: list[str], since: str, *, roles: bool) -> _Fetched:
    out = _Fetched()
    cursor: Any = None
    for _page in range(MAX_PAGES):
        url = _roller_url(orgnrs, since, cursor) if roles else _enheter_url(orgnrs, since, cursor)
        if out.first_url is None:
            out.first_url = url
        res, err, note = await _fetch(ctx, url)
        if res is None:
            if not out.items:
                out.err, out.note = err, note
            else:
                out.note = f"later page not fetched: {note}"
            return out
        retrieved_at = utc_iso(res.retrieved_at)
        if out.first_retrieved_at is None:
            out.first_retrieved_at = retrieved_at
        sha = ctx.snapshots.save_result(res, retrieved_at)
        text = decode_text(res.content)
        base = () if roles else ("_embedded", "oppdaterteEnheter")
        elems = array_elements(text, pointer(*base))
        for i, (s, e) in enumerate(elems):
            raw = text[s:e]
            ev = json.loads(raw)
            if isinstance(ev, dict):
                out.items.append({"ev": ev, "raw": raw, "url": url, "sha": sha, "retrieved_at": retrieved_at,
                                  "locator": pointer(*base, i)})
        if len(elems) < FEED_SIZE or not out.items:
            break
        last = out.items[-1]["ev"].get("id" if roles else "oppdateringsid")
        if roles:
            cursor = str(last)
        elif isinstance(last, int):
            cursor = last + 1
        else:
            break
    return out


def _event_evidence(item: dict[str, Any], canonical_url: str, orgnr: str, date: str | None,
                    keys: list[str], date_key: str) -> list[Evidence]:
    raw = item["raw"]
    full = raw if len(raw) <= 300 else None
    span = full or members_span(raw, "", keys)
    common = dict(source_url=canonical_url, source_class=OFFICIAL_REGISTRY_FEED, source_name=SOURCE_NAME,
                  retrieved_at=item["retrieved_at"], content_sha256=item["sha"], extraction_method=METHOD,
                  page_url=enhet_page(orgnr), effective_at=date, http_status=200, licence=NLOD,
                  retrieved_via=item["url"])
    evs = [Evidence(claim_span=span, locator=item["locator"], **common)]
    if full is None and span and f'"{date_key}"' not in span:
        evs.append(Evidence(claim_span=span_at(raw, pointer(date_key)), locator=item["locator"] + pointer(date_key),
                            **common))
    return evs


async def run_batch(ctx: RunContext, companies: list[CompanyResult]) -> None:
    """Never raises; one terminal registry_feed outcome per company."""
    try:
        await _run(ctx, companies)
    except Exception as exc:
        for c in companies:
            if MODULE not in c.modules:
                c.error(MODULE, "failed", f"{type(exc).__name__}: {exc}")
                c.outcome(ModuleOutcome(MODULE, "failed", note=f"module error: {type(exc).__name__}"))


async def _run(ctx: RunContext, companies: list[CompanyResult]) -> None:
    targets: dict[str, list[CompanyResult]] = {}
    for c in companies:
        status = (c.data.get("identity") or {}).get("match_status")
        if status in ("exact", "deleted"):
            targets.setdefault(c.organisation_number, []).append(c)
        else:
            c.outcome(ModuleOutcome(MODULE, "not_applicable", note=f"no registered main unit to follow ({status})"))
    if not targets:
        return
    now = datetime.now(timezone.utc)
    prev = ctx.shared.get("previous_checked_at")
    since = compute_since(now, prev)
    prev_dt = _parse_iso(prev)
    orgnrs = sorted(targets)
    chunks = [orgnrs[i:i + CHUNK] for i in range(0, len(orgnrs), CHUNK)]

    async def do_chunk(chunk: list[str]):
        ev, ro = await asyncio.gather(_feed_chunk(ctx, chunk, since, roles=False),
                                      _feed_chunk(ctx, chunk, since, roles=True))
        return chunk, ev, ro

    results = await asyncio.gather(*(do_chunk(ch) for ch in chunks))
    max_upd, max_role = None, None
    for chunk, ev, ro in results:
        wanted = set(chunk)
        by_org: dict[str, list[dict[str, Any]]] = {o: [] for o in chunk}
        for it in ev.items:
            o = str(it["ev"].get("organisasjonsnummer") or "")
            if o in wanted:
                by_org[o].append(it)
            uid = it["ev"].get("oppdateringsid")
            if isinstance(uid, int):
                max_upd = uid if max_upd is None else max(max_upd, uid)
        roles_by_org: dict[str, list[dict[str, Any]]] = {o: [] for o in chunk}
        for it in ro.items:
            data = it["ev"].get("data") if isinstance(it["ev"].get("data"), dict) else {}
            o = str(data.get("organisasjonsnummer") or "")
            if o in wanted:
                roles_by_org[o].append(it)
            rid = str(it["ev"].get("id") or "")
            if rid.isdigit():
                max_role = int(rid) if max_role is None else max(max_role, int(rid))
        for o in chunk:
            for c in targets[o]:
                _publish(c, o, by_org[o], roles_by_org[o], since, prev, prev_dt, ev, ro)
    cursor = ctx.shared.setdefault("registry_feed", {})
    cursor.update({"since": since, "checked_at": utc_iso(now), "max_update_id": max_upd, "max_role_event_id": max_role})


def _role_id(it: dict[str, Any]) -> int:
    v = str(it["ev"].get("id") or "")
    return int(v) if v.isdigit() else -1


def _publish(c: CompanyResult, orgnr: str, ev_items: list[dict[str, Any]], ro_items: list[dict[str, Any]],
             since: str, prev: Any, prev_dt: datetime | None, ev: _Fetched, ro: _Fetched) -> None:
    def key_id(it: dict[str, Any]) -> int:
        v = it["ev"].get("oppdateringsid")
        return v if isinstance(v, int) else -1

    timeline = sorted((it for it in ev_items if is_timeline_event(it["ev"])), key=key_id, reverse=True)
    roles_sorted = sorted(ro_items, key=_role_id, reverse=True)
    events_out, role_out = [], []
    claims: list[Claim] = []
    for it in timeline[:MAX_EVENTS]:
        e = it["ev"]
        uid = e.get("oppdateringsid")
        date = e.get("dato")
        fields = changed_fields(e.get("endringer"))
        value = {"date": date, "change_type": e.get("endringstype"), "fields": fields}
        events_out.append({"id": uid, **value})
        canon = f"{BASE}/oppdateringer/enheter?oppdateringsid={uid}&includeChanges=true&size=1"
        evs = _event_evidence(it, canon, orgnr, date, ["dato", "endringer", "endringstype", "oppdateringsid",
                                                         "organisasjonsnummer"], "dato")
        shown = ", ".join(f.lstrip("/") for f in fields[:6]) + (" …" if len(fields) > 6 else "")
        claims.append(Claim(field="registry_event", value=value, evidence=evs, qualifier=str(uid), effective_at=date,
                            display=f"{str(date)[:10]} {e.get('endringstype')}" + (f": {shown}" if shown else ""),
                            method=METHOD))
    for it in roles_sorted[:MAX_EVENTS]:
        e = it["ev"]
        eid = str(e.get("id") or "")
        date = e.get("time")
        value = {"date": date, "type": e.get("type")}
        role_out.append({"id": eid, **value})
        canon = (f"{BASE}/oppdateringer/roller?afterId={int(eid) - 1}&size=1" if eid.isdigit()
                 else _roller_url([orgnr], since, None))
        evs = _event_evidence(it, canon, orgnr, date, ["data", "id", "source", "specversion", "time", "type"], "time")
        claims.append(Claim(field="role_change_event", value=value, evidence=evs, qualifier=eid, effective_at=date,
                            display=f"{str(date)[:10]} {ROLE_TYPES.get(str(e.get('type')), e.get('type'))}",
                            method=METHOD))
    feed: dict[str, Any] = {
        "since": since,
        "checked": {"enheter": ev.err is None, "roller": ro.err is None},
        "last_change_at": str(timeline[0]["ev"].get("dato")) if timeline and timeline[0]["ev"].get("dato") else None,
        "last_event_at": max((str(it["ev"]["dato"]) for it in ev_items if it["ev"].get("dato")), default=None),
        "last_role_change_at": max((str(it["ev"]["time"]) for it in ro_items if it["ev"].get("time")), default=None),
        "event_count": len(ev_items),
        "timeline_count": len(timeline),
        "role_event_count": len(ro_items),
        "events": events_out,
        "role_events": role_out,
    }
    if prev_dt is not None:
        def after_prev(s: Any) -> bool:
            d = _parse_iso(s)
            return d is not None and d > prev_dt
        feed["since_previous"] = {
            "previous_checked_at": prev,
            "events": sum(1 for it in timeline if after_prev(it["ev"].get("dato"))),
            "role_events": sum(1 for it in ro_items if after_prev(it["ev"].get("time"))),
        }
    c.data["registry_feed"] = feed
    c.add(claims)
    notes = []
    if ev.err or ev.note:
        notes.append(f"entity feed: {ev.note}")
    if ro.err or ro.note:
        notes.append(f"roles feed: {ro.note}")
    if claims:
        state = "available"
        note = f"{len(events_out)} registry event(s), {len(role_out)} role event(s) since {since[:10]}"
    elif ev.err:
        state, note = ev.err, None
    else:
        state, note = "not_available", f"no register updates since {since[:10]}"
    if notes:
        note = "; ".join(([note] if note else []) + notes)
    src = ev if ev.first_retrieved_at else ro
    c.outcome(ModuleOutcome(MODULE, state, note=note, source_url=src.first_url, retrieved_at=src.first_retrieved_at,
                            http_status=200 if src.first_retrieved_at else None))
