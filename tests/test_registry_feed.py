"""Offline tests for sources/registry_feed.py (respx-mocked update feeds)."""

from __future__ import annotations

import asyncio
import gzip
import json
from datetime import datetime, timezone
from pathlib import Path

import respx

from nordic_company_agent.context import RunConfig, RunContext
from nordic_company_agent.envelope import CompanyResult
from nordic_company_agent.sources import registry_feed
from nordic_company_agent.sources.registry_feed import changed_fields, compute_since, is_timeline_event

FIX = Path(__file__).parent / "fixtures" / "registry"
JSON_HDR = {"content-type": "application/json"}
ENH = r"https://data\.brreg\.no/enhetsregisteret/api/oppdateringer/enheter\?.*"
ROL = r"https://data\.brreg\.no/enhetsregisteret/api/oppdateringer/roller\?.*"
ORGS = ["923609016", "984851006", "982463718", "810034882", "920649270", "934296877", "988987719"]


def fx(name: str) -> bytes:
    return (FIX / name).read_bytes()


def companies(orgs=ORGS, status="exact"):
    out = []
    for o in orgs:
        c = CompanyResult(o)
        c.data["identity"] = {"organisation_number": o, "match_status": status}
        out.append(c)
    return out


def run(tmp_path, comps, enh=(200, None), rol=(200, None), shared=None):
    with respx.mock(assert_all_called=True) as router:
        router.get(url__regex=ENH).respond(enh[0], content=enh[1] if enh[1] is not None else fx("oppdateringer_enheter_live.json"),
                                           headers=JSON_HDR)
        router.get(url__regex=ROL).respond(rol[0], content=rol[1] if rol[1] is not None else fx("oppdateringer_roller_live.json"),
                                           headers=JSON_HDR)
        ctx = RunContext(RunConfig(run_id="test", out_dir=tmp_path))
        ctx.shared.update(shared or {})
        asyncio.run(registry_feed.run_batch(ctx, comps))
        calls = [str(call.request.url) for call in router.calls]
        asyncio.run(ctx.client.aclose())
    return ctx, calls


def test_helpers():
    assert changed_fields([{"op": "replace", "path": "/forretningsadresse/adresse/0"},
                           {"op": "add", "path": "/historiskeNavn/0", "value": {}},
                           {"op": "replace", "path": "/navn"}, {"op": "remove", "path": "/forretningsadresse/adresse/1"}]) \
        == ["/forretningsadresse/adresse", "/historiskeNavn", "/navn"]
    assert not is_timeline_event({"endringstype": "Ukjent"})
    assert not is_timeline_event({"endringstype": "Endring"})              # republish without patch
    assert is_timeline_event({"endringstype": "Endring", "endringer": [{"path": "/navn"}]})
    assert is_timeline_event({"endringstype": "Sletting"}) and is_timeline_event({"endringstype": "Ny"})
    now = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
    assert compute_since(now) == "2025-10-13T00:00:00.000Z"
    assert compute_since(now, "2025-01-02T03:04:05Z") == "2025-01-02T03:04:05.000Z"
    assert compute_since(now, "2026-10-01T00:00:00Z") == "2025-10-13T00:00:00.000Z"
    assert compute_since(now, "2001-01-01T00:00:00Z").startswith("2023-10-")   # bounded look-back


def test_feed_timeline_and_role_events(tmp_path):
    comps = companies()
    ctx, calls = run(tmp_path, comps, shared={"previous_checked_at": "2026-08-15T00:00:00Z"})
    assert len(calls) == 2 and ctx.client.used == 2
    assert "includeChanges=true" in calls[0] or "includeChanges=true" in calls[1]
    eq = {c.organisation_number: c for c in comps}["923609016"]
    ev = [cl for cl in eq.claims if cl.field == "registry_event"]
    assert len(ev) == 14                                    # 14 patched Endring; the Ukjent republish is dropped
    assert all(cl.value["change_type"] != "Ukjent" for cl in ev)
    ids = [int(cl.qualifier) for cl in ev]
    assert ids == sorted(ids, reverse=True)
    top = ev[0]
    assert top.value["fields"] and all(f.startswith("/") for f in top.value["fields"])
    assert top.evidence[0].source_url.endswith(f"oppdateringsid={top.qualifier}&includeChanges=true&size=1")
    assert top.evidence[0].retrieved_via.startswith("https://data.brreg.no/enhetsregisteret/api/oppdateringer/enheter?")
    feed = eq.data["registry_feed"]
    assert feed["last_change_at"] == top.value["date"] == max(e["date"] for e in feed["events"])
    assert feed["timeline_count"] == 14 and feed["event_count"] == 15
    assert feed["since_previous"]["previous_checked_at"] == "2026-08-15T00:00:00Z"
    assert feed["since_previous"]["events"] == sum(1 for e in feed["events"] if e["date"] > "2026-08-15")
    roles = [cl for cl in eq.claims if cl.field == "role_change_event"]
    assert len(roles) == 6 and len(feed["role_events"]) == 6
    assert roles[0].value["type"] == "no.brreg.enhetsregisteret.rolle.oppdatert"
    assert eq.modules["registry_feed"].availability == "available"
    # evidence spans are verbatim fragments of the stored feed bodies
    for c in comps:
        for cl in c.claims:
            for e in cl.evidence:
                body = gzip.decompress((ctx.snapshots.root / f"{e.content_sha256}.gz").read_bytes()).decode()
                assert e.claim_span and e.claim_span in body
    # the Ukjent republish is dropped; the company's one patched change remains
    small = {c.organisation_number: c for c in comps}["934296877"]
    assert [cl.field for cl in small.claims] == ["registry_event"]
    assert ctx.shared["registry_feed"]["max_update_id"] >= int(top.qualifier)


def test_feed_caps_events_per_company(tmp_path, monkeypatch):
    monkeypatch.setattr(registry_feed, "MAX_EVENTS", 5)
    comps = companies(["984851006"])
    run(tmp_path, comps)
    ev = [cl for cl in comps[0].claims if cl.field == "registry_event"]
    assert len(ev) == 5 and comps[0].data["registry_feed"]["timeline_count"] == 15


def test_feed_no_events_and_failures(tmp_path):
    empty_enh = b'{"_links":{},"page":{"number":0,"size":10000,"totalElements":0,"totalPages":0}}'
    comps = companies(["930459615"])
    run(tmp_path, comps, enh=(200, empty_enh), rol=(200, b"[]"))
    c = comps[0]
    assert c.claims == [] and c.modules["registry_feed"].availability == "not_available"
    assert c.data["registry_feed"]["last_change_at"] is None
    assert c.modules["registry_feed"].source_url.startswith("https://data.brreg.no/enhetsregisteret/api/oppdateringer/")

    comps = companies(["930459615"])
    run(tmp_path / "f", comps, enh=(500, b"x"), rol=(200, b"[]"))
    assert comps[0].modules["registry_feed"].availability == "failed"

    skipped = companies(["930459615"], status="not_found")
    ctx = RunContext(RunConfig(run_id="t", out_dir=tmp_path / "s"))
    asyncio.run(registry_feed.run_batch(ctx, skipped))
    assert skipped[0].modules["registry_feed"].availability == "not_applicable" and ctx.client.used == 0
    json.dumps(skipped[0].modules["registry_feed"].to_dict())
