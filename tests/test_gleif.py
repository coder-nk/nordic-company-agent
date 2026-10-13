"""Offline tests for sources/gleif.py (respx-mocked HTTP, fixtures from live GLEIF responses)."""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import respx

from nordic_company_agent.context import RunConfig, RunContext
from nordic_company_agent.envelope import CC0_GLEIF, OFFICIAL_LEI, Claim, CompanyResult, Evidence
from nordic_company_agent.sources import gleif

FIX = Path(__file__).parent / "fixtures" / "external"
BATCH = json.loads((FIX / "gleif_batch.json").read_text())
JSONAPI = {"content-type": "application/vnd.api+json"}


def make_ctx(tmp_path: Path, max_requests: int = 50, reserve: int | None = None) -> RunContext:
    ctx = RunContext(RunConfig(run_id="t", out_dir=tmp_path / "out", max_requests=max_requests,
                               cache_dir=tmp_path / "cache"))
    ctx.planner.set_reservations({"batch_external": max_requests if reserve is None else reserve})
    return ctx


def company(orgnr: str, name: str) -> CompanyResult:
    c = CompanyResult(orgnr)
    c.data["identity"] = {"match_status": "exact"}
    c.data["registry"] = {"organisasjonsnummer": orgnr, "navn": name}
    return c


def run(ctx: RunContext, companies: list[CompanyResult]) -> None:
    async def go():
        try:
            await gleif.run_batch(ctx, companies)
        finally:
            await ctx.client.aclose()
    asyncio.run(go())


def by_key(c: CompanyResult) -> dict[str, Claim]:
    return {cl.key: cl for cl in c.claims}


def mock_parents(router: respx.MockRouter) -> None:
    fx = {"6367001EBAEQ4T2WM033/direct-parent": "gleif_parent_kernel.json",
          "6367001EBAEQ4T2WM033/ultimate-parent": "gleif_parent_awilhelmsen.json",
          }  # 529900LMX1QKF7XGQA66 (SWEROCK) is LAPSED: its stale parents must not even be fetched
    for path, name in fx.items():
        router.get(f"{gleif.BASE}/lei-records/{path}").mock(
            return_value=httpx.Response(200, content=(FIX / name).read_bytes(), headers=JSONAPI))


def test_batch_url_sends_both_formats():
    url = gleif.batch_url(["923609016", "810034882"])
    q = parse_qs(urlsplit(url).query)
    assert q["filter[entity.registeredAs]"] == ["923609016,923 609 016,810034882,810 034 882"]
    assert q["page[size]"] == ["200"]


def test_batch_claims_parents_and_not_available(tmp_path):
    cos = [company("923609016", "EQUINOR ASA"), company("979543883", "DIPS AS"),
           company("983065309", "SWEROCK AS"), company("810034882", "SANDNES ELEKTRISKE AS")]
    ctx = make_ctx(tmp_path)
    with respx.mock(assert_all_called=True) as router:
        batch = router.get(f"{gleif.BASE}/lei-records").mock(
            return_value=httpx.Response(200, json=BATCH, headers=JSONAPI))
        mock_parents(router)
        run(ctx, cos)
        assert batch.call_count == 1
    assert ctx.client.used == 3  # 1 batch + direct + ultimate parent (DIPS only; SWEROCK is LAPSED)
    eq, dips, swe, sandnes = cos

    k = by_key(eq)
    assert k["lei"].value == "OW6OFBNCKXC4US5C7523"
    ev = k["lei"].evidence[0]
    assert ev.source_url == "https://api.gleif.org/api/v1/lei-records/OW6OFBNCKXC4US5C7523"
    assert ev.retrieved_via and ev.retrieved_via.startswith(f"{gleif.BASE}/lei-records?")
    assert ev.page_url == "https://search.gleif.org/#/record/OW6OFBNCKXC4US5C7523"
    assert ev.licence == CC0_GLEIF and ev.source_class == OFFICIAL_LEI and ev.content_sha256
    assert '"registeredAs":"923 609 016"' in ev.claim_span
    st = k["lei_status"].value
    assert st == {"entity_status": "ACTIVE", "registration_status": "ISSUED", "next_renewal": "2027-11-09",
                  "managing_lou": "549300O897ZC5H7CY412", "last_update": "2026-09-16"}
    assert k["former_name|lei:statoilhydro asa"].value == {"name": "STATOILHYDRO ASA", "from": None, "to": None}
    assert k["former_name|lei:statoilhydro asa"].method == "gleif_otherNames_PREVIOUS_LEGAL_NAME"
    assert eq.modules["gleif"].availability == "available"
    assert not any(c.field == "lei_parent" for c in eq.claims)

    kd = by_key(dips)
    assert kd["lei_parent|direct"].value == {"lei": "549300TIS4X45924UI24", "name": "KERNEL AS", "country": "NO",
                                             "registered_as": "818 297 432"}
    assert kd["lei_parent|direct"].relation == "parent"
    assert kd["lei_parent|ultimate"].relation == "ultimate_parent"
    assert kd["lei_parent|ultimate"].value["name"] == "AWILHELMSEN HOLDING AS"
    assert kd["lei_parent|direct"].evidence[0].source_url.endswith("/6367001EBAEQ4T2WM033/direct-parent")
    assert "818297432" in kd["lei_parent|direct"].note

    ks = by_key(swe)
    assert ks["lei_status"].value["registration_status"] == "LAPSED"
    assert "does not mean the company is inactive" in ks["lei_status"].note
    assert "lei_parent|direct" not in ks and "LAPSED" in swe.data["gleif"]["parent_note"]

    assert sandnes.claims == []
    assert sandnes.modules["gleif"].availability == "not_available"
    assert "810 034 882" in sandnes.modules["gleif"].note


def test_rejects_foreign_record_and_accepts_zero_width_space(tmp_path):
    rec = copy.deepcopy(BATCH["data"][0])
    foreign = copy.deepcopy(rec)
    foreign["id"] = foreign["attributes"]["lei"] = "XXXXFOREIGN000000000"
    foreign["attributes"]["entity"]["legalAddress"]["country"] = "SE"
    foreign["attributes"]["entity"]["registeredAt"]["id"] = "RA000544"
    foreign["attributes"]["entity"]["registeredAs"] = "810034882"
    zw = copy.deepcopy(rec)
    zw["id"] = zw["attributes"]["lei"] = "ZWSP0000000000000000"
    zw["attributes"]["entity"]["registeredAs"] = "935120​470"
    zw["attributes"]["entity"]["otherNames"] = []
    doc = {"meta": {**BATCH["meta"], "pagination": {**BATCH["meta"]["pagination"], "total": 2}},
           "data": [foreign, zw]}
    cos = [company("810034882", "SANDNES ELEKTRISKE AS"), company("935120470", "SOME AS")]
    ctx = make_ctx(tmp_path)
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{gleif.BASE}/lei-records").mock(return_value=httpx.Response(200, json=doc, headers=JSONAPI))
        run(ctx, cos)
    assert cos[0].modules["gleif"].availability == "not_available" and not cos[0].claims
    assert cos[1].first_value("lei") == "ZWSP0000000000000000"
    assert "differs from the registry name" in by_key(cos[1])["lei"].note


def test_former_name_not_duplicated(tmp_path):
    c = company("923609016", "EQUINOR ASA")
    ev = Evidence(source_url="x", source_class="official_registry", source_name="brreg", retrieved_at="2026-01-01T00:00:00Z",
                  content_sha256=None)
    c.add([Claim(field="former_name", value={"name": "StatoilHydro ASA", "from": "2007-10-01", "to": "2009-11-02"},
                 evidence=[ev], qualifier="2009-11-02")])
    ctx = make_ctx(tmp_path)
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{gleif.BASE}/lei-records").mock(return_value=httpx.Response(200, json=BATCH, headers=JSONAPI))
        run(ctx, [c])
    assert [cl.qualifier for cl in c.claims if cl.field == "former_name"] == ["2009-11-02"]


def test_truncated_page_is_failed_not_absent(tmp_path):
    doc = {"meta": {**BATCH["meta"], "pagination": {**BATCH["meta"]["pagination"], "total": 250}}, "data": []}
    c = company("810034882", "SANDNES ELEKTRISKE AS")
    ctx = make_ctx(tmp_path)
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{gleif.BASE}/lei-records").mock(return_value=httpx.Response(200, json=doc, headers=JSONAPI))
        run(ctx, [c])
    assert c.modules["gleif"].availability == "failed"


def test_budget_denied_and_blocked(tmp_path):
    c = company("923609016", "EQUINOR ASA")
    ctx = make_ctx(tmp_path, max_requests=0)
    run(ctx, [c])
    assert c.modules["gleif"].availability == "failed"
    assert c.modules["gleif"].note == gleif.BUDGET_NOTE and ctx.client.used == 0

    c2 = company("923609016", "EQUINOR ASA")
    ctx2 = make_ctx(tmp_path / "b")
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{gleif.BASE}/lei-records").mock(return_value=httpx.Response(429))
        run(ctx2, [c2])
    assert c2.modules["gleif"].availability == "blocked" and not c2.claims


def test_not_applicable_without_exact_identity(tmp_path):
    c = company("923609016", "EQUINOR ASA")
    c.data["identity"] = {"match_status": "not_found"}
    ctx = make_ctx(tmp_path)
    run(ctx, [c])
    assert c.modules["gleif"].availability == "not_applicable" and ctx.client.used == 0


def test_deterministic(tmp_path):
    def once(p):
        c = company("979543883", "DIPS AS")
        ctx = make_ctx(p)
        with respx.mock(assert_all_called=False) as router:
            router.get(f"{gleif.BASE}/lei-records").mock(return_value=httpx.Response(200, json=BATCH, headers=JSONAPI))
            mock_parents(router)
            run(ctx, [c])
        return [(cl.id, [e.id for e in cl.evidence]) for cl in c.claims]
    assert once(tmp_path / "1") == once(tmp_path / "2")
