"""Offline tests for sources/wikidata.py (respx-mocked WDQS; fixture = live SPARQL response)."""

from __future__ import annotations

import asyncio
import gzip
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import respx

from nordic_company_agent.context import RunConfig, RunContext
from nordic_company_agent.envelope import CC0_WIKIDATA, OPEN_KNOWLEDGE_BASE, CompanyResult
from nordic_company_agent.sources import wikidata
from nordic_company_agent.sources.wikidata import handle_matches_company

FIX = Path(__file__).parent / "fixtures" / "external"
SPARQL = json.loads((FIX / "wikidata_sparql.json").read_text())
NAMES = {"923609016": ("EQUINOR ASA", "www.equinor.com"), "984851006": ("DNB BANK ASA", "www.dnb.no"),
         "982463718": ("TELENOR ASA", "www.telenor.no/"), "914778271": ("Norsk Hydro ASA", "www.hydro.com"),
         "979543883": ("DIPS AS", "www.dips.com"), "983065309": ("SWEROCK AS", ""),
         "810034882": ("SANDNES ELEKTRISKE AS", "")}
SPARQL_HEADERS = {"content-type": "application/sparql-results+json;charset=utf-8"}


def make_ctx(tmp_path: Path, index: dict | None = None, max_requests: int = 20) -> RunContext:
    cache = tmp_path / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    if index is not None:
        with gzip.open(cache / "wikidata_p2333.json.gz", "wt") as fh:
            json.dump(index, fh)
        (cache / "wikidata_p2333.meta.json").write_text(json.dumps({"built_at": "2026-10-13T14:48:53Z"}))
    ctx = RunContext(RunConfig(run_id="t", out_dir=tmp_path / "out", max_requests=max_requests, cache_dir=cache))
    ctx.planner.set_reservations({"batch_external": max_requests})
    return ctx


def company(orgnr: str) -> CompanyResult:
    name, site = NAMES[orgnr]
    c = CompanyResult(orgnr)
    c.data["identity"] = {"match_status": "exact"}
    c.data["registry"] = {"organisasjonsnummer": orgnr, "navn": name, "hjemmeside": site or None}
    return c


def run(ctx: RunContext, companies: list[CompanyResult]) -> None:
    async def go():
        try:
            await wikidata.run_batch(ctx, companies)
        finally:
            await ctx.client.aclose()
    asyncio.run(go())


def keys(c: CompanyResult) -> dict:
    return {cl.key: cl for cl in c.claims}


INDEX = {"923609016": ["Q1776022"], "984851006": ["Q80788"], "982463718": ["Q845632"], "914778271": ["Q511031"],
         "979543883": ["Q11964312"], "983065309": ["Q19379205"]}


def test_no_indexed_company_costs_zero_requests(tmp_path):
    ctx = make_ctx(tmp_path, index=INDEX)
    c = company("810034882")
    with respx.mock(assert_all_called=False) as router:
        route = router.get(wikidata.ENDPOINT)
        run(ctx, [c])
        assert route.call_count == 0
    assert ctx.client.used == 0
    assert c.modules["wikidata"].availability == "not_available"
    assert "declared index" in c.modules["wikidata"].note


def test_one_sparql_get_and_claims(tmp_path):
    ctx = make_ctx(tmp_path, index=INDEX)
    cos = [company(o) for o in ("923609016", "984851006", "982463718", "914778271", "983065309", "810034882")]
    with respx.mock(assert_all_called=False) as router:
        route = router.get(wikidata.ENDPOINT).mock(return_value=httpx.Response(200, json=SPARQL, headers=SPARQL_HEADERS))
        run(ctx, cos)
        assert route.call_count == 1
        req = route.calls[0].request
        q = parse_qs(urlsplit(str(req.url)).query)
        assert q["format"] == ["json"]
        assert '"923609016"' in q["query"][0] and '"810034882"' not in q["query"][0]  # cache pre-selection
        assert "pq:P582" in q["query"][0] and "DeprecatedRank" in q["query"][0]
        assert req.headers["user-agent"].startswith("nordic-company-agent/")
    assert ctx.client.used == 1
    eq, dnb, tel, hydro, swe, sandnes = cos

    k = keys(eq)
    item = k["wikidata_item|Q1776022"]
    assert item.value == {"id": "Q1776022", "url": "https://www.wikidata.org/wiki/Q1776022", "label": "Equinor"}
    ev = item.evidence[0]
    assert ev.source_url == "https://www.wikidata.org/wiki/Q1776022"
    assert ev.retrieved_via.startswith(wikidata.ENDPOINT + "?format=json&query=")
    assert ev.licence == CC0_WIKIDATA and ev.source_class == OPEN_KNOWLEDGE_BASE
    assert ev.locator.startswith("/results/bindings/") and len(ev.claim_span) <= 300
    assert k["encyclopedia_entry|nb"].value == {"language": "nb", "url": "https://no.wikipedia.org/wiki/Equinor",
                                                "title": "Equinor"}
    assert {"encyclopedia_entry|en", "encyclopedia_entry|nn"} <= set(k)
    assert k["stock_listing|EQNR:Q909158"].value == {"ticker": "EQNR", "exchange": "Oslo Børs", "isin": "NO0010096985"}
    socials = {cl.value["url"] for cl in eq.claims if cl.field == "social_profile"}
    assert socials == {"https://www.facebook.com/Equinor", "https://www.instagram.com/equinor",
                       "https://x.com/equinor", "https://x.com/EquinorASA"}
    for cl in eq.claims:
        if cl.field == "social_profile":
            assert cl.confidence == 0.8 and cl.method == "wikidata_P2333"
            assert cl.note == "linked from the Wikidata item that carries this organisation number"
    dropped = {d["handle"] for d in eq.data["wikidata"]["socials_dropped"]}
    assert {"Equinor_UK", "Equinor_DE", "Equinor_NA"} <= dropped
    # Website is only a lead (no official_website claim), preferring the registry website domain.
    assert eq.data["wikidata"]["website"] == "https://equinor.com"
    assert not any(cl.field in ("official_website", "employees", "revenue") for c in cos for cl in c.claims)

    tel_socials = {cl.value["handle"] for cl in tel.claims if cl.field == "social_profile"}
    assert "telenorsverige" not in tel_socials and "telenor-group" in tel_socials
    assert any(d["handle"] == "telenorsverige" for d in tel.data["wikidata"]["socials_dropped"])

    assert {cl.value["handle"] for cl in dnb.claims if cl.field == "social_profile"} == {"dnb"}
    assert keys(hydro)["stock_listing|NHY"].value["exchange"] == "Oslo Børs"

    # SWEROCK's item is labelled with a former name: published, but with a note and lower confidence.
    sk = keys(swe)
    assert "differs from the registry name" in sk["wikidata_item|Q19379205"].note
    assert sk["encyclopedia_entry|nb"].confidence < 0.9
    assert all(c.modules["wikidata"].availability == "available" for c in (eq, dnb, tel, hydro, swe))
    assert sandnes.modules["wikidata"].availability == "not_available"


def test_without_index_queries_all(tmp_path):
    ctx = make_ctx(tmp_path, index=None)
    cos = [company("923609016"), company("810034882")]
    with respx.mock(assert_all_called=False) as router:
        route = router.get(wikidata.ENDPOINT).mock(return_value=httpx.Response(200, json=SPARQL, headers=SPARQL_HEADERS))
        run(ctx, cos)
        assert '"810034882"' in parse_qs(urlsplit(str(route.calls[0].request.url)).query)["query"][0]
    assert cos[0].modules["wikidata"].availability == "available"
    assert cos[1].modules["wikidata"].availability == "not_available"


def test_multiple_items_is_ambiguous(tmp_path):
    rows = [b for b in SPARQL["results"]["bindings"] if b["orgnr"]["value"] == "923609016"]
    other = json.loads(json.dumps(rows[0]))
    other["item"]["value"] = "http://www.wikidata.org/entity/Q999999"
    other["itemLabel"]["value"] = "Equinor brand"
    doc = {"head": SPARQL["head"], "results": {"bindings": rows + [other]}}
    ctx = make_ctx(tmp_path, index={"923609016": ["Q1776022", "Q999999"]})
    c = company("923609016")
    with respx.mock(assert_all_called=False) as router:
        router.get(wikidata.ENDPOINT).mock(return_value=httpx.Response(200, json=doc, headers=SPARQL_HEADERS))
        run(ctx, [c])
    assert c.modules["wikidata"].availability == "ambiguous"
    assert {cl.field for cl in c.claims} == {"wikidata_item"}
    assert sorted(cl.qualifier for cl in c.claims) == ["Q1776022", "Q999999"]
    assert all("no other Wikidata facts are published" in cl.note for cl in c.claims)


def test_429_retry_then_blocked(tmp_path):
    ctx = make_ctx(tmp_path, index=INDEX)
    c = company("923609016")
    with respx.mock(assert_all_called=False) as router:
        route = router.get(wikidata.ENDPOINT).mock(return_value=httpx.Response(429, headers={"retry-after": "0"}))
        run(ctx, [c])
        assert route.call_count == 2
    assert c.modules["wikidata"].availability == "blocked" and not c.claims


def test_429_then_success(tmp_path):
    ctx = make_ctx(tmp_path, index=INDEX)
    c = company("923609016")
    with respx.mock(assert_all_called=False) as router:
        router.get(wikidata.ENDPOINT).mock(side_effect=[
            httpx.Response(429, headers={"retry-after": "0"}),
            httpx.Response(200, json=SPARQL, headers=SPARQL_HEADERS)])
        run(ctx, [c])
    assert c.modules["wikidata"].availability == "available"


def test_budget_denied(tmp_path):
    ctx = make_ctx(tmp_path, index=INDEX, max_requests=0)
    c = company("923609016")
    run(ctx, [c])
    assert c.modules["wikidata"].availability == "failed"
    assert c.modules["wikidata"].note == wikidata.BUDGET_NOTE


def test_handle_matching():
    assert handle_matches_company("https://www.instagram.com/equinor", "EQUINOR ASA")
    assert handle_matches_company("https://x.com/EquinorASA", "EQUINOR ASA")
    assert not handle_matches_company("https://x.com/Equinor_UK", "EQUINOR ASA")
    assert not handle_matches_company("https://www.instagram.com/telenorsverige", "TELENOR ASA")
    assert handle_matches_company("https://www.linkedin.com/company/telenor-group", "TELENOR ASA")
    assert handle_matches_company("https://www.facebook.com/norskhydroasa", "Norsk Hydro ASA")
    assert handle_matches_company("https://www.facebook.com/dnb", "DNB BANK ASA")
    assert not handle_matches_company("https://www.youtube.com/channel/UCKNvAlRIR_ixP4EWDrhxJUA", "DNB BANK ASA")
    assert not handle_matches_company("https://www.facebook.com/equi", "EQUINOR ASA")
