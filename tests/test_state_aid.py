"""Offline tests for sources/state_aid.py (respx-mocked Støtteregisteret API, live fixtures)."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path

import httpx
import respx

from nordic_company_agent.context import RunConfig, RunContext
from nordic_company_agent.envelope import NLOD, OFFICIAL_STATE_AID, CompanyResult, canonical
from nordic_company_agent.sources import state_aid

FIX = Path(__file__).parent / "fixtures" / "external"
API = state_aid.API
SEARCH_PATH = "/api/v1/soek/stoettetildeling"
EMPTY = {"searchTime": "2026-10-13T16:45:34+02:00", "pageSize": 100, "page": 1, "stoettetildeling": []}


def make_ctx(tmp_path: Path, max_requests: int = 20) -> RunContext:
    ctx = RunContext(RunConfig(run_id="t", out_dir=tmp_path / "out", max_requests=max_requests,
                               cache_dir=tmp_path / "cache"))
    ctx.planner.set_reservations({"extras": max_requests})
    return ctx


def company(orgnr: str) -> CompanyResult:
    c = CompanyResult(orgnr)
    c.data["identity"] = {"match_status": "exact"}
    return c


def run(ctx, cos):
    async def go():
        try:
            for c in cos:
                await state_aid.run(ctx, c)
        finally:
            await ctx.client.aclose()
    asyncio.run(go())


def mock_all(router, search_body: bytes):
    host = "stottetiltak-registerinfo-api.app.brreg.no"
    search = router.post(host=host, path=SEARCH_PATH).mock(
        return_value=httpx.Response(200, content=search_body, headers={"content-type": "application/json"}))
    for name in state_aid.CODELISTS:
        router.get(f"{API}/kodeliste/{name}").mock(return_value=httpx.Response(
            200, content=(FIX / f"stotte_kodeliste_{name}.json").read_bytes(),
            headers={"content-type": "application/json"}))
    schemes = router.post(host=host, path="/api/v1/soek/stoetteordning").mock(return_value=httpx.Response(
        200, content=(FIX / "stotte_schemes_trimmed.json").read_bytes(), headers={"content-type": "application/json"}))
    return search, schemes


def test_recent_grants_exact_decimals(tmp_path):
    body = (FIX / "stotte_equinor.json").read_bytes()
    eq = company("923609016")
    ctx = make_ctx(tmp_path)
    with respx.mock(assert_all_called=False) as router:
        search, schemes = mock_all(router, body)
        run(ctx, [eq])
        req = search.calls[0].request
        assert json.loads(req.content) == [{"field": "STOETTEMOTTAKER_ORGNR", "value": "923609016",
                                            "matchType": "EXACT_MATCH"}]
        assert req.headers["content-type"] == "application/json"
        assert "pageSize=100" in str(req.url)
        assert schemes.call_count == 1
    assert ctx.client.used == 1 + len(state_aid.CODELISTS) + 1
    grants = [c for c in eq.claims if c.field == "state_aid_grant"]
    assert len(grants) == 10
    dates = [g.value["date"] for g in grants]
    assert dates == sorted(dates, reverse=True) and dates[0] == "2026-09-07"
    top = grants[0]
    assert top.qualifier == "1000307993"
    assert top.value["amount"] == Decimal("14118201.16") and isinstance(top.value["amount"], Decimal)
    assert top.value["currency"] == "NOK" and top.value["grantor"] == "SKATTEETATEN"
    assert top.value["scheme_id"] == "1000029830" and top.value["scheme"] == "Regionalt DA 2026-"
    assert top.value["purpose"] == "Regionalstøtte"
    assert top.value["instrument"] == "Skatte- eller avgiftsfritak"
    assert top.display.startswith("2026-09-07: NOK 14 118 201,16 from SKATTEETATEN")
    ev = top.evidence[0]
    assert ev.source_class == OFFICIAL_STATE_AID and ev.licence == NLOD
    assert ev.page_url == "https://stotte.brreg.no/nb/oppslag/stoettetildeling/1000307993"
    assert ev.retrieved_via.startswith("POST ") and "STOETTEMOTTAKER_ORGNR" in ev.retrieved_via
    raw = (FIX / "stotte_equinor.json").read_text(encoding="utf-8") if (FIX / "stotte_equinor.json").exists() else None
    assert "14118201.16" in top.evidence[1].claim_span and top.evidence[1].locator.endswith("/tildeltBeloep")
    for e in top.evidence[:2]:
        assert e.claim_span and (raw is None or e.claim_span in raw), e.claim_span
    assert eq.modules["state_aid"].availability == "available"
    assert eq.data["state_aid"]["grant_count"] == 80
    # Values serialise without float noise.
    assert '"amount":14118201.16' in canonical(top.value)


def test_interval_grant_has_no_point_amount(tmp_path):
    doc = json.loads((FIX / "stotte_equinor.json").read_text())
    doc["stoettetildeling"] = [g for g in doc["stoettetildeling"] if "beloepsintervall" in g][:1]
    c = company("923609016")
    ctx = make_ctx(tmp_path)
    with respx.mock(assert_all_called=False) as router:
        mock_all(router, json.dumps(doc).encode())
        run(ctx, [c])
    g = c.get("state_aid_grant")[0]
    assert g.value["amount"] is None
    assert g.value["amount_range"]["from"] == Decimal("30000000.0") and g.value["amount_range"]["currency"] == "EUR"
    assert "interval" in g.note


def test_recipient_must_match_exactly(tmp_path):
    doc = json.loads((FIX / "stotte_equinor.json").read_text())
    c = company("990888213")   # the search answered for another recipient: nothing may be published
    ctx = make_ctx(tmp_path)
    with respx.mock(assert_all_called=False) as router:
        mock_all(router, json.dumps(doc).encode())
        run(ctx, [c])
    assert not c.claims and c.modules["state_aid"].availability == "not_available"


def test_no_grants_and_failures(tmp_path):
    c = company("810034882")
    ctx = make_ctx(tmp_path)
    with respx.mock(assert_all_called=False) as router:
        mock_all(router, json.dumps(EMPTY).encode())
        run(ctx, [c])
    assert c.modules["state_aid"].availability == "not_available" and ctx.client.used == 1

    c2 = company("810034882")
    ctx2 = make_ctx(tmp_path / "b")
    with respx.mock(assert_all_called=False) as router:
        router.post(host="stottetiltak-registerinfo-api.app.brreg.no", path=SEARCH_PATH).mock(
            return_value=httpx.Response(403))
        run(ctx2, [c2])
    assert c2.modules["state_aid"].availability == "blocked"

    c3 = company("810034882")
    ctx3 = make_ctx(tmp_path / "c", max_requests=0)
    run(ctx3, [c3])
    assert c3.modules["state_aid"].availability == "failed" and c3.modules["state_aid"].note == state_aid.BUDGET_NOTE


def test_lookups_shared_across_companies(tmp_path):
    body = (FIX / "stotte_equinor.json").read_bytes()
    a, b = company("923609016"), company("923609016")
    ctx = make_ctx(tmp_path)
    with respx.mock(assert_all_called=False) as router:
        search, schemes = mock_all(router, body)
        run(ctx, [a, b])
        assert search.call_count == 2 and schemes.call_count == 1
    assert [c.id for c in a.claims] == [c.id for c in b.claims]
