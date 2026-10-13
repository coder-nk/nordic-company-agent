"""Tests for sources/accounts.py (Regnskapsregisteret annual accounts).

Offline tests replay the raw research samples (tests/fixtures/accounts/) through the real
RunContext/BudgetedClient with respx. The central invariant: every published figure equals the
value at its JSON pointer in the raw body, and its claim span is a verbatim slice of that body.

The live smoke test runs only with NCA_LIVE=1 (hard cap of 60 requests).
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import os
import random
import re
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import respx

from nordic_company_agent.context import RunConfig, RunContext
from nordic_company_agent.envelope import NLOD, OFFICIAL_ACCOUNTS, CompanyResult, dedupe_claims
from nordic_company_agent.sources import accounts

FIX = Path(__file__).parent / "fixtures" / "accounts"
BASE = "https://data.brreg.no/regnskapsregisteret/regnskap"
LIST_FIXTURES = sorted(FIX.glob("list_*.json"))
MONEY_FIELDS = {f for f, _, _ in accounts.FIELDS}


def arun(coro):
    return asyncio.run(coro)


def fixture(orgnr: str) -> bytes:
    (path,) = FIX.glob(f"list_{orgnr}__*.json")
    return path.read_bytes()


def make_company(orgnr: str, form: str = "AS", siste: str | None = "2025", exact: bool = True) -> CompanyResult:
    c = CompanyResult(orgnr)
    c.data["identity"] = {"match_status": "exact" if exact else "candidate"}
    reg = {"organisasjonsnummer": orgnr, "organisasjonsform": {"kode": form}}
    if siste is not None:
        reg["sisteInnsendteAarsregnskap"] = siste
    c.data["registry"] = reg
    return c


def make_ctx(tmp_path: Path, modules=("accounts",), max_requests: int = 50) -> RunContext:
    return RunContext(RunConfig(run_id="test", out_dir=tmp_path, modules=set(modules), max_requests=max_requests))


async def _run(tmp_path, company, routes, modules=("accounts",), ctx=None, runner=None):
    ctx = ctx or make_ctx(tmp_path, modules)
    with respx.mock(assert_all_called=False) as router:
        mocked = {url: router.get(url).mock(return_value=resp) for url, resp in routes.items()}
        await (runner or accounts.run)(ctx, company)
    await ctx.client.aclose()
    return ctx, mocked


def run_list(tmp_path, orgnr, form="AS", siste="2025", body=None, status=200, **kw):
    company = make_company(orgnr, form, siste)
    body = fixture(orgnr) if body is None else body
    resp = httpx.Response(status, content=body, headers={"content-type": "application/json"})
    ctx, mocked = arun(_run(tmp_path, company, {f"{BASE}/{orgnr}": resp}, **kw))
    return company, ctx, mocked[f"{BASE}/{orgnr}"]


def resolve(doc, pointer: str):
    cur = doc
    for part in pointer.split("/")[1:]:
        part = part.replace("~1", "/").replace("~0", "~")
        cur = cur[int(part)] if isinstance(cur, list) else cur[part]
    return cur


def by_key(company):
    return {c.key: c for c in company.claims}


def orgnr_of(path: Path) -> str:
    return path.name.split("_")[1]


def form_of(raw: bytes) -> str:
    return json.loads(raw)[0]["virksomhet"]["organisasjonsform"]


def latest_year_of(raw: bytes) -> str:
    return max(r["regnskapsperiode"]["tilDato"][:4] for r in json.loads(raw))


# --------------------------------------------------------------------------- core invariants
@pytest.mark.parametrize("path", LIST_FIXTURES, ids=lambda p: p.name[5:40])
def test_every_figure_equals_raw_value_at_pointer_and_span_is_verbatim(tmp_path, path):
    orgnr, raw = orgnr_of(path), path.read_bytes()
    text = raw.decode("utf-8")
    company, ctx, route = run_list(tmp_path, orgnr, form_of(raw), siste=latest_year_of(raw))
    assert route.call_count == 1
    assert company.modules["accounts"].availability == "available"
    records = {int(r["id"]): r for r in accounts.parse_json_exact(text)}
    sha = hashlib.sha256(raw).hexdigest()
    numeric = 0
    for c in company.claims:
        assert c.evidence, c.key
        for ev in c.evidence:
            assert ev.content_sha256 == sha
            assert ev.claim_span and ev.claim_span in text, (c.key, ev.claim_span)
            assert len(ev.claim_span) <= 300
            assert ev.licence == NLOD and ev.source_class == OFFICIAL_ACCOUNTS
            assert ev.page_url == f"https://virksomhet.brreg.no/nb/oppslag/enheter/{orgnr}"
            assert ev.retrieved_via == f"{BASE}/{orgnr}"
            m = re.fullmatch(rf"{BASE}/{orgnr}/(\d+)", ev.source_url)
            assert m, ev.source_url
            rec = records[int(m.group(1))]
            assert ev.effective_at == rec["regnskapsperiode"]["tilDato"]
            # the span is exactly the "key":value text of the pointed member
            target = resolve(rec, ev.locator)
            key = ev.locator.rsplit("/", 1)[1]
            assert ev.claim_span.startswith(f'"{key}":')
            assert accounts.parse_json_exact(ev.claim_span.split(":", 1)[1]) == target
        if c.field in MONEY_FIELDS:
            ev = c.evidence[0]
            rec = records[int(ev.source_url.rsplit("/", 1)[1])]
            raw_value = resolve(rec, ev.locator)
            assert isinstance(raw_value, Decimal)
            # the JSON path is exactly the documented one for this field
            assert ev.locator == accounts.json_pointer(*accounts.FIELD_PATHS[c.field])
            literal = ev.claim_span.split(":", 1)[1]
            assert Decimal(literal) == raw_value
            if c.availability == "available":
                numeric += 1
                assert isinstance(c.value, Decimal)
                assert c.value == raw_value and str(c.value) == literal   # no rounding, no scaling
                cur = rec["valuta"]
                assert c.display.startswith(cur + " ")
                assert Decimal(c.display[len(cur) + 1:].replace(" ", "")) == raw_value
                assert c.period == {"start": rec["regnskapsperiode"]["fraDato"],
                                    "end": rec["regnskapsperiode"]["tilDato"],
                                    "year": int(rec["regnskapsperiode"]["tilDato"][:4]),
                                    "scope": {"SELSKAP": "company", "KONSERN": "group"}[rec["regnskapstype"]]}
                assert c.qualifier.startswith(f"{c.period['year']}|{c.period['scope']}")
            else:
                assert c.value is None and c.display is None and c.note
    assert numeric > 0


@pytest.mark.parametrize("path", LIST_FIXTURES, ids=lambda p: p.name[5:40])
def test_no_figure_without_a_numeric_key_and_none_missed(tmp_path, path):
    """A claim exists for (record, field) iff the key exists with a numeric value (split rules aside)."""
    orgnr, raw = orgnr_of(path), path.read_bytes()
    company, _, _ = run_list(tmp_path, orgnr, form_of(raw), siste=latest_year_of(raw))
    emitted = {(int(c.evidence[0].source_url.rsplit("/", 1)[1]), c.field)
               for c in company.claims if c.field in MONEY_FIELDS}
    expected = set()
    for r in accounts.parse_json_exact(raw.decode()):
        for fld, p, _ in accounts.FIELDS:
            v = accounts._resolve(r, p)
            if isinstance(v, Decimal):
                if fld in accounts.SPLIT_FIELDS and (r["regnskapstype"] == "KONSERN" or
                                                     r["regnkapsprinsipper"].get("regnskapsregler") != accounts.NGAAP_CODE):
                    continue
                expected.add((int(r["id"]), fld))
    split = {(i, f) for i, f in expected if f in accounts.SPLIT_FIELDS}
    assert emitted - expected == set()
    assert expected - emitted <= split          # only a non-reconciling split may be dropped


def test_span_index_matches_parser_on_all_fixtures():
    for path in LIST_FIXTURES:
        text = path.read_text(encoding="utf-8")
        doc = accounts.parse_json_exact(text)
        spans = accounts.index_spans(text)
        assert "" in spans and spans[""][1:] == (0, len(text))
        for ptr, (s0, v0, v1) in spans.items():
            assert accounts.parse_json_exact(text[v0:v1]) == resolve(doc, ptr)
            if ptr and not re.search(r"/\d+$", ptr):
                assert text[s0:v0].startswith('"') and text[s0:v0].rstrip().endswith(":")


def test_span_index_handles_whitespace_and_escapes():
    text = '[ {"a" : 1.50 , "b\\"/x" : {"c":[1, -2e3]} , "d": "x\\"y"} ]'
    spans = accounts.index_spans(text)
    s0, v0, v1 = spans["/0/a"]
    assert text[s0:v1] == '"a" : 1.50' and text[v0:v1] == "1.50"
    assert text[slice(*spans['/0/b"~1x/c/1'][1:])] == "-2e3"
    assert text[slice(*spans["/0/d"][1:])] == '"x\\"y"'
    with pytest.raises(ValueError):
        accounts.index_spans('{"a":1')


def test_format_amount():
    f = accounts.format_amount
    assert f(Decimal("790400865.00"), "NOK") == "NOK 790 400 865"
    assert f(Decimal("-341829.00"), "NOK") == "NOK -341 829"
    assert f(Decimal("143580000000.00"), "USD") == "USD 143 580 000 000"
    assert f(Decimal("0.00"), "NOK") == "NOK 0"
    assert f(Decimal("1234.50"), "EUR") == "EUR 1 234.50"
    assert f(Decimal("999"), "DKK") == "DKK 999"
    assert accounts.format_ratio(Decimal("0.3788")) == "37.88 %"


# --------------------------------------------------------------------------- Equinor (USD, group)
def test_equinor_usd_group_and_company(tmp_path):
    company, _, _ = run_list(tmp_path, "923609016", "ASA")
    k = by_key(company)
    assert k["net_profit|2023|group"].value == Decimal("11904000000.00")
    assert k["revenue|2023|group"].value == Decimal("107174000000.00")
    assert k["revenue|2023|group"].display == "USD 107 174 000 000"
    assert k["revenue|2024|group"].value == Decimal("103774000000.00")
    assert k["accounts_currency|2025|group"].value == "USD"
    assert k["accounts_currency|2025|company"].value == "USD"
    p = k["accounts_period|2025|company"]
    assert p.value["framework"] == "simplified_IFRS" and p.value["record_id"] == 7192427
    assert p.value["audited"] is True and p.value["audit_opt_out"] is False
    assert p.value["liquidation_accounts"] is False and p.value["small_company"] is False
    assert k["accounts_period|2023|group"].value["framework"] == "IFRS"
    assert k["net_profit|2025|company"].evidence[0].source_url == f"{BASE}/923609016/7192427"
    assert k["net_profit|2025|company"].evidence[0].locator == "/resultatregnskapResultat/aarsresultat"
    assert "not NOK" in k["revenue|2025|group"].note
    # equity split never published for group / IFRS records
    assert not [c for c in company.claims if c.field in accounts.SPLIT_FIELDS]
    # six records x (period + currency) + figures; 2 derived for latest company year only
    assert sum(1 for c in company.claims if c.field == "accounts_period") == 6
    er, om = k["equity_ratio|2025|company"], k["operating_margin|2025|company"]
    assert er.match_basis == "derived" and er.method == "equity/total_assets"
    assert er.value == (Decimal("39182000000.00") / Decimal("103432000000.00")).quantize(Decimal("0.0001"))
    assert er.display == "37.88 %"
    assert {e.locator for e in er.evidence} == {"/egenkapitalGjeld/egenkapital/sumEgenkapital",
                                                "/eiendeler/sumEiendeler"}
    assert om.method == "operating_profit/revenue"
    assert not [c for c in company.claims if c.field in ("equity_ratio", "operating_margin")
                and c.qualifier != "2025|company"]
    data = company.data["accounts"]
    assert data["status"] == "available" and len(data["records"]) == 6
    assert data["latest"]["scope"] == "company" and data["latest"]["year"] == 2025
    assert data["latest"]["values"]["net_profit"] == Decimal("5731000000.00")
    assert data["latest_group"]["values"]["revenue"] == Decimal("106462000000.00")
    assert data["derived"]["equity_ratio"] == er.value
    assert data["pending_year"] is None


def test_deterministic_ids_and_order(tmp_path):
    a, _, _ = run_list(tmp_path / "a", "923609016", "ASA")
    b, _, _ = run_list(tmp_path / "b", "923609016", "ASA")
    assert [c.id for c in a.claims] == [c.id for c in b.claims]
    assert [c.key for c in a.claims] == [c.key for c in b.claims]
    assert len({c.key for c in a.claims}) == len(a.claims)        # unique stable keys
    assert len(dedupe_claims(list(a.claims))) == len(a.claims)


def test_same_year_records_get_distinct_keys(tmp_path):
    """Two company records ending in the same calendar year (e.g. short first period + full year)."""
    raw = fixture("930408476").decode()
    spans = accounts.index_spans(raw)
    s0, _, s1 = spans["/2"]
    extra = raw[s0:s1].replace('"id":7239096', '"id":7239001').replace(
        '"fraDato":"2025-04-01","tilDato":"2026-03-31"', '"fraDato":"2026-01-01","tilDato":"2026-01-31"')
    assert extra != raw[s0:s1]
    body = (raw[:-1] + "," + extra + "]").encode()
    company, _, _ = run_list(tmp_path, "930408476", "AS", siste="2026", body=body)
    keys = [c.key for c in company.claims]
    assert len(keys) == len(set(keys))
    assert "revenue|2026|company" in keys and "revenue|2026|company|2026-01-31" in keys
    assert by_key(company)["revenue|2026|company"].evidence[0].source_url.endswith("/7239096")


def test_newer_accounts_pending_note(tmp_path):
    company, _, _ = run_list(tmp_path, "923609016", "ASA", siste="2026")
    k = by_key(company)
    assert "newer accounts (FY2026)" in k["net_profit|2025|company"].note
    assert "newer accounts" not in (k["net_profit|2024|company"].note or "")
    assert company.data["accounts"]["pending_year"] == 2026
    assert k["net_profit|2025|company"].value == Decimal("5731000000.00")   # value untouched


# --------------------------------------------------------------------------- quality rules
def test_telenor_balance_mismatch(tmp_path):
    company, _, _ = run_list(tmp_path, "982463718", "ASA")
    k = by_key(company)
    # SELSKAP 2024: assets 211 512 m vs equity+liabilities 138 134 m -> balance sheet withheld
    for f in ("total_assets", "equity", "total_liabilities"):
        c = k[f"{f}|2024|company"]
        assert c.availability == "ambiguous" and c.value is None and "balance sheet withheld" in c.note
    assert k["revenue|2024|company"].availability == "available"
    assert k["net_profit|2024|company"].value == Decimal("20525000000.00")
    # KONSERN 2024: 0.36 % mismatch -> kept, noted, lower confidence
    c = k["total_assets|2024|group"]
    assert c.availability == "available" and c.value == Decimal("228038000000.00")
    assert "balance check failed" in c.note and c.confidence <= 0.7
    assert k["revenue|2024|group"].confidence == 1.0
    assert "framework" in k["accounts_period|2024|group"].note


def test_nuf_currency_switch_withholds_foreign_currency_year(tmp_path):
    company, _, _ = run_list(tmp_path, "984746458", "NUF")
    k = by_key(company)
    dkk = [c for c in company.claims if c.qualifier == "2025|company" and c.field in MONEY_FIELDS]
    assert dkk and all(c.availability == "ambiguous" and c.value is None for c in dkk)
    assert "head office" in dkk[0].note and "currency changed" in dkk[0].note
    assert k["accounts_currency|2025|company"].value == "DKK"
    assert "head office" in k["accounts_period|2025|company"].note
    assert k["revenue|2024|company"].value == Decimal("223586875.00")
    assert k["revenue|2024|company"].availability == "available"
    assert not [c for c in company.claims if c.field in ("equity_ratio", "operating_margin")]
    data = company.data["accounts"]
    assert data["latest"]["year"] == 2024 and data["latest"]["currency"] == "NOK"


def test_nuf_in_nok_is_published(tmp_path):
    company, _, _ = run_list(tmp_path, "948138646", "NUF")
    assert all(c.availability == "available" for c in company.claims)


def test_zero_total_assets_and_year_gap(tmp_path):
    company, _, _ = run_list(tmp_path, "913376064", "SA", siste="2023")
    k = by_key(company)
    assert k["total_assets|2023|company"].availability == "ambiguous"
    assert "total assets NOK 0" in k["total_assets|2023|company"].note
    assert k["revenue|2023|company"].value == Decimal("37500.00")
    assert k["total_assets|2021|company"].value == Decimal("0.00")
    assert "total assets 0" in k["total_assets|2021|company"].note
    assert "equity_ratio|2023|company" not in k
    assert {c.period["year"] for c in company.claims if c.period} == {2021, 2023}


def test_negative_revenue_and_long_first_period(tmp_path):
    company, _, _ = run_list(tmp_path, "934551435", "ENK")
    k = by_key(company)
    rev = k["revenue|2025|company"]
    assert rev.value == Decimal("-341829.00") and rev.display == "NOK -341 829"
    assert "negative operating revenue" in rev.note and rev.confidence <= 0.6
    assert k["accounts_period|2025|company"].value["days"] == 400
    assert "operating_margin|2025|company" not in k


def test_liquidation_short_period(tmp_path):
    company, _, _ = run_list(tmp_path, "912559203", "AS", siste=None)
    k = by_key(company)
    p = k["accounts_period|2026|company"]
    assert p.value["liquidation_accounts"] is True and p.value["days"] == 189
    assert "liquidation accounts" in k["net_profit|2026|company"].note
    assert "189-day" in k["net_profit|2026|company"].note
    assert k["net_profit|2026|company"].value == Decimal("3661402.00")
    assert "revenue|2026|company" not in k           # driftsinntekter is {} -> not reported, not 0
    assert p.value["audited"] is False and p.value["audit_opt_out"] is True


def test_empty_objects_are_not_zero(tmp_path):
    company, _, _ = run_list(tmp_path, "935450918", "AS", siste="2025")
    k = by_key(company)
    for f in ("fixed_assets", "long_term_liabilities", "financial_income"):
        assert f"{f}|2025|company" not in k
    assert k["equity|2025|company"].value == Decimal("-376035.00")
    assert k["paid_in_equity|2025|company"].value == Decimal("23500.00")
    assert k["retained_earnings|2025|company"].value == Decimal("-399535.00")
    assert "296-day" in k["revenue|2025|company"].note


def test_net_financial_sign_flip_withheld(tmp_path):
    company, _, _ = run_list(tmp_path, "979911254", "SA", siste="2023")
    k = by_key(company)
    c = k["net_financial_items|2021|company"]
    assert c.availability == "ambiguous" and "opposite sign" in c.note
    assert k["financial_income|2021|company"].value == Decimal("110.00")


def test_group_lag_note(tmp_path):
    company, _, _ = run_list(tmp_path, "979497539", "AS")
    k = by_key(company)
    assert "latest available group accounts (FY2023)" in k["revenue|2023|group"].note
    assert company.data["accounts"]["latest_group"]["year"] == 2023


def test_stale_single_old_filing(tmp_path):
    company, _, _ = run_list(tmp_path, "820358562", "ESEK", siste="2018")
    k = by_key(company)
    assert "stale" in k["revenue|2018|company"].note
    assert company.data["accounts"]["stale"] is True


def test_negative_equity_group_split_suppressed(tmp_path):
    company, _, _ = run_list(tmp_path, "886582412", "ASA")
    k = by_key(company)
    assert "paid_in_equity|2025|group" not in k and "retained_earnings|2023|group" not in k
    assert "paid_in_equity|2025|company" not in k                       # 2025 company record is IFRS
    assert "paid_in_equity|2024|company" in k                           # NGAAP company record
    withheld = company.data["accounts"]["records"]
    assert any("equity split" in v for r in withheld for v in r["withheld"].values())


# --------------------------------------------------------------------------- HTTP outcomes
@pytest.mark.parametrize("path", sorted(FIX.glob("http500_*.json")), ids=lambda p: p.stem)
def test_financial_institution_500_not_retried(tmp_path, path):
    orgnr, code = path.stem.split("_")[1:3]
    company, _, route = run_list(tmp_path, orgnr, "ASA", body=path.read_bytes(), status=500)
    out = company.modules["accounts"]
    assert route.call_count == 1                                       # never retried
    assert out.availability == "not_available" and out.http_status == 500
    assert "financial-institution layout not served by the open API" in out.note and code in out.note
    assert company.claims == [] and company.errors == []
    assert company.data["accounts"]["unsupported_layout"] == code


def test_other_500_is_failed(tmp_path):
    company, _, route = run_list(tmp_path, "923609016", "ASA", body=b'{"message":"boom"}', status=500)
    assert company.modules["accounts"].availability == "failed" and company.errors
    assert route.call_count == 1


def test_404_not_available(tmp_path):
    company, _, route = run_list(tmp_path, "938386080", "AS", siste=None, body=b"", status=404)
    out = company.modules["accounts"]
    assert out.availability == "not_available" and route.call_count == 1
    assert out.note.startswith("no accounts in Regnskapsregisteret's open API")
    assert company.claims == [] and company.data["accounts"]["status"] == "not_available"


def test_404_with_old_registry_year(tmp_path):
    company, _, _ = run_list(tmp_path, "975820645", "FLI", siste="2011", body=b"", status=404)
    assert "2011" in company.modules["accounts"].note


def test_429_blocked(tmp_path):
    company, _, _ = run_list(tmp_path, "923609016", "ASA", body=b"", status=429)
    assert company.modules["accounts"].availability == "blocked"


def test_garbage_body_failed_never_raises(tmp_path):
    company, _, _ = run_list(tmp_path, "923609016", "ASA", body=b"[{not json")
    assert company.modules["accounts"].availability == "failed" and company.claims == []


def test_foreign_orgnr_records_discarded(tmp_path):
    body = fixture("923609016").replace(b'"organisasjonsnummer":"923609016"', b'"organisasjonsnummer":"914778271"')
    company, _, _ = run_list(tmp_path, "923609016", "ASA", body=body)
    assert company.modules["accounts"].availability == "ambiguous" and company.claims == []


@pytest.mark.parametrize("form", ["ENK", "FLI", "ESEK", "KOMM", "STAT", "ORGL"])
def test_non_filing_form_without_accounts_skipped(tmp_path, form):
    company = make_company("974760673", form, siste=None)
    ctx, mocked = arun(_run(tmp_path, company, {f"{BASE}/974760673": httpx.Response(404)}))
    assert company.modules["accounts"].availability == "not_applicable"
    assert ctx.client.used == 0 and mocked[f"{BASE}/974760673"].call_count == 0


def test_enk_with_filed_accounts_is_fetched(tmp_path):
    company, _, route = run_list(tmp_path, "934551435", "ENK", siste="2025")
    assert route.call_count == 1 and company.modules["accounts"].availability == "available"


def test_identity_not_exact_skipped(tmp_path):
    company = make_company("923609016", "ASA", exact=False)
    ctx, mocked = arun(_run(tmp_path, company, {f"{BASE}/923609016": httpx.Response(200, content=b"[]")}))
    assert company.modules["accounts"].availability == "not_applicable" and ctx.client.used == 0


def test_budget_refused(tmp_path):
    company = make_company("923609016", "ASA")
    ctx = make_ctx(tmp_path, max_requests=0)
    _, mocked = arun(_run(tmp_path, company, {f"{BASE}/923609016": httpx.Response(200, content=b"[]")}, ctx=ctx))
    out = company.modules["accounts"]
    assert out.availability == "failed" and out.note == accounts.NOT_CHECKED_BUDGET
    assert mocked[f"{BASE}/923609016"].call_count == 0


def test_snapshot_saved(tmp_path):
    company, ctx, _ = run_list(tmp_path, "923609016", "ASA")
    sha = hashlib.sha256(fixture("923609016")).hexdigest()
    entry = ctx.snapshots.entry(sha)
    assert entry and entry["url"] == f"{BASE}/923609016"
    assert gzip.decompress((tmp_path / entry["path"]).read_bytes()) == fixture("923609016")


# --------------------------------------------------------------------------- copy API (extras)
def _kopi(orgnr: str, remaining: str = "29", status: int = 200) -> httpx.Response:
    body = (FIX / f"kopi_{orgnr}_aar.json").read_bytes() if status == 200 else b""
    return httpx.Response(status, content=body,
                          headers={"content-type": "application/json", "x-rate-limit-remaining": remaining})


def test_copy_years(tmp_path):
    company = make_company("923609016", "ASA")
    routes = {f"{BASE}/923609016": httpx.Response(200, content=fixture("923609016")),
              f"{BASE}/aarsregnskap/kopi/923609016/aar": _kopi("923609016")}
    arun(_run(tmp_path, company, routes, modules=("accounts", "account_copies")))
    k = by_key(company)
    years = k["accounts_filing_years"]
    assert years.value == list(range(2011, 2026)) and years.evidence[0].claim_span.startswith('["2011"')
    assert company.modules["account_copies"].availability == "available"
    copies = [c for c in company.claims if c.field == "annual_report_copy"]
    assert [c.qualifier for c in copies] == ["2021", "2022", "2023", "2024", "2025"]
    assert k["annual_report_copy|2025"].value == f"{BASE}/aarsregnskap/kopi/923609016/2025"
    assert k["annual_report_copy|2025"].evidence[0].claim_span == '"2025"'


def test_copy_rate_limit_stops_below_floor(tmp_path):
    async def go():
        ctx = make_ctx(tmp_path, ("accounts", "account_copies"))
        a, b = make_company("923609016", "ASA"), make_company("984851006", "ASA")
        with respx.mock(assert_all_called=False) as router:
            router.get(f"{BASE}/aarsregnskap/kopi/923609016/aar").mock(return_value=_kopi("923609016", "3"))
            second = router.get(f"{BASE}/aarsregnskap/kopi/984851006/aar").mock(return_value=_kopi("984851006"))
            await accounts.run_copies(ctx, a)
            await accounts.run_copies(ctx, b)
        await ctx.client.aclose()
        return a, b, second

    a, b, second = arun(go())
    assert a.modules["account_copies"].availability == "available"
    assert b.modules["account_copies"].availability == "failed" and "rate limit" in b.modules["account_copies"].note
    assert second.call_count == 0


def test_copy_skipped_when_extras_not_allowed(tmp_path):
    company = make_company("923609016", "ASA")
    ctx = make_ctx(tmp_path, ("accounts", "account_copies"))
    ctx.planner.set_reservations({"registry": 10_000})       # nothing left for the extras tier
    arun(_run(tmp_path, company, {}, ctx=ctx, runner=accounts.run_copies))
    assert "account_copies" not in company.modules and company.claims == []


def test_copy_empty_list(tmp_path):
    company = make_company("938386080", "AS", siste=None)
    arun(_run(tmp_path, company, {f"{BASE}/aarsregnskap/kopi/938386080/aar": _kopi("938386080")},
              modules=("account_copies",), runner=accounts.run_copies))
    assert company.modules["account_copies"].availability == "not_available"


# --------------------------------------------------------------------------- live smoke test
LIVE_FIXED = ["923609016", "984851006", "982463718", "979543883", "810034882"]


@pytest.mark.skipif(not os.environ.get("NCA_LIVE"), reason="live smoke test: set NCA_LIVE=1")
def test_live_smoke(tmp_path):
    rng = random.Random(int(os.environ.get("NCA_LIVE_SEED", "20261013")))
    universe = Path(__file__).resolve().parents[1] / "data" / "signalpost-company-universe-2025.jsonl.gz"
    with gzip.open(universe, "rt", encoding="utf-8") as fh:
        pool = [json.loads(line)["organisation_number"] for line in fh if line.strip()]
    orgnrs = LIVE_FIXED + rng.sample(pool, 5)
    out_dir = Path(os.environ.get("NCA_LIVE_OUT", "/tmp/nca-dev-acc"))

    async def go():
        ctx = RunContext(RunConfig(run_id="dev", out_dir=out_dir, max_requests=60))
        results = []
        for o in orgnrs:
            c = CompanyResult(o)
            res = await ctx.get(f"https://data.brreg.no/enhetsregisteret/api/enheter/{o}", tier="registry",
                                source="registry", headers={"Accept": "application/json"})
            c.data["registry"] = json.loads(res.content) if res.status in (200, 410) and res.content else {}
            c.data["identity"] = {"match_status": "exact"}
            await accounts.run(ctx, c)
            results.append(c)
        await ctx.client.aclose()
        return ctx, results

    ctx, results = arun(go())
    assert ctx.client.used <= 60
    report = []
    for c in results:
        out = c.modules["accounts"]
        cp = c.modules.get("account_copies")
        report.append(f"{c.organisation_number} {c.data['registry'].get('organisasjonsform', {}).get('kode')} "
                      f"accounts={out.availability} copies={cp.availability if cp else '-'} "
                      f"claims={len(c.claims)} note={out.note}")
        assert out.availability in ("available", "not_available", "not_applicable"), (c.organisation_number, out)
        for cl in c.claims:
            if cl.field not in MONEY_FIELDS:
                continue
            ev = cl.evidence[0]
            entry = ctx.snapshots.entry(ev.content_sha256)
            raw = gzip.decompress((out_dir / entry["path"]).read_bytes()).decode("utf-8")
            assert ev.claim_span in raw
            rec = next(r for r in accounts.parse_json_exact(raw) if str(r["id"]) == ev.source_url.rsplit("/", 1)[1])
            if cl.availability == "available":
                assert cl.value == resolve(rec, ev.locator)
    print("\n" + "\n".join(report))
    by = {c.organisation_number: c for c in results}
    eq = by_key(by["923609016"])
    assert by["923609016"].modules["accounts"].availability == "available"
    assert any(k.startswith("revenue|") and k.endswith("|group") for k in eq)
    assert all(eq[k].value == "USD" for k in eq if k.startswith("accounts_currency|"))
    dnb = by["984851006"].modules["accounts"]
    assert dnb.availability == "not_available" and "financial-institution layout" in dnb.note
    assert by["982463718"].modules["accounts"].availability == "available"
