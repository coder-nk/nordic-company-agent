"""Offline tests for sources/registry.py and geo.py (respx-mocked data.brreg.no)."""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import httpx
import respx

from nordic_company_agent import geo
from nordic_company_agent.context import RunConfig, RunContext
from nordic_company_agent.envelope import OFFICIAL_REGISTRY_BULK, CompanyResult
from nordic_company_agent.sources import registry
from nordic_company_agent.sources.registry import (RecordSource, array_elements, enhet_claims, locate, members_span,
                                                    redact_birth_dates, span_at)

FIX = Path(__file__).parent / "fixtures" / "registry"
CSV_SNAPSHOT = Path(__file__).resolve().parents[1] / "research" / "samples" / "brreg_feeds_bulk__enheter_totalbestand_csv_head40.csv"
BASE = "https://data.brreg.no/enhetsregisteret/api"
JSON_HDR = {"content-type": "application/json"}


def fx(name: str) -> bytes:
    return (FIX / name).read_bytes()


def make_ctx(tmp_path: Path, **cfg) -> RunContext:
    return RunContext(RunConfig(run_id="test", out_dir=tmp_path, **cfg))


def snapshot_text(ctx: RunContext, sha: str) -> str:
    return gzip.decompress((ctx.snapshots.root / f"{sha}.gz").read_bytes()).decode("utf-8")


def by_field(c: CompanyResult, field: str, qualifier: str | None = None):
    return [cl for cl in c.claims if cl.field == field and (qualifier is None or cl.qualifier == qualifier)]


def one(c: CompanyResult, field: str, qualifier: str | None = None):
    xs = by_field(c, field, qualifier)
    assert len(xs) == 1, (field, xs)
    return xs[0]


def assert_spans_verbatim(ctx: RunContext, companies) -> int:
    n = 0
    for c in companies:
        for cl in c.claims:
            for ev in cl.evidence:
                if ev.claim_span is None:
                    continue
                assert len(ev.claim_span) <= 300
                assert ev.claim_span in snapshot_text(ctx, ev.content_sha256), (cl.field, ev.claim_span)
                n += 1
    return n


# --------------------------------------------------------------------------- geo
def test_county_from_municipality_number():
    assert geo.county_for_municipality("1103") == {"number": "11", "name": "Rogaland"}
    assert geo.county_for_municipality("0301") == {"number": "03", "name": "Oslo"}
    assert geo.county_for_municipality("4626")["name"] == "Vestland"
    assert geo.county_for_municipality("5501")["name"] == "Troms"
    assert geo.county_for_municipality("3403")["name"] == "Innlandet"
    assert geo.county_for_municipality("2100") is None      # Svalbard: outside the county structure
    assert geo.county_for_municipality("3001") is None      # pre-2024 Viken number: never guessed
    assert geo.county_for_municipality("11") is None
    assert geo.county_for_municipality(None) is None


# --------------------------------------------------------------------------- raw JSON helpers
def test_span_helpers_are_verbatim():
    text = '{"a":{"x":1,"y":[ "p" , {"q":"æ/ø"} ]},"b":5976872600.00,"c":"z"}'
    assert span_at(text, "/b") == '"b":5976872600.00'
    assert span_at(text, "/a/y/1") == '{"q":"æ/ø"}'
    assert span_at(text, "/a/y/1/q") == '"q":"æ/ø"'
    assert span_at(text, "/missing") is None
    assert members_span(text, "", ["b", "c"]) == '"b":5976872600.00,"c":"z"'
    assert members_span(text, "/a", ["x", "nope"]) == '"x":1'
    s, v, e = locate(text, "/a/y")
    assert text[v:e] == '[ "p" , {"q":"æ/ø"} ]'
    assert [text[a:b] for a, b in array_elements(text, "/a/y")] == ['"p"', '{"q":"æ/ø"}']


def test_redaction_removes_birth_dates_and_keeps_other_bytes():
    raw = ('{"rollegrupper":[{"roller":[{"person":{"erDoed":false,"fodselsdato":"1970-01-01","navn":{"fornavn":"A"}}},'
           '{"person":{"navn":"B","fodselsdato":"1970-01-01"}},{"x":{"fodselsdato":"1970-01-01"}},'
           '{"rows":[{"fødselsdato":"01.01.1970","navn":"C"}]}]}]}').encode()
    red = redact_birth_dates(raw).decode()
    assert "fodselsdato" not in red and "fødselsdato" not in red and "1970" not in red
    doc = json.loads(red)
    assert doc["rollegrupper"][0]["roller"][0]["person"] == {"erDoed": False, "navn": {"fornavn": "A"}}
    assert '"navn":{"fornavn":"A"}' in red and '{"x":{}}' in red


# --------------------------------------------------------------------------- batch module (live path)
def _mock_live(router):
    router.get(url__regex=r"https://data\.brreg\.no/enhetsregisteret/api/enheter\?organisasjonsnummer=.*").respond(
        200, content=fx("enheter_batch_live.json"), headers=JSON_HDR)
    router.get(f"{BASE}/enheter/922052778").respond(200, content=fx("enhet_deleted_922052778.json"), headers=JSON_HDR)
    router.get(f"{BASE}/enheter/934516451").respond(410, content=fx("enhet_410_934516451.json"), headers=JSON_HDR)
    router.get(f"{BASE}/enheter/973861883").respond(404, content=b"")
    router.get(f"{BASE}/underenheter/973861883").respond(200, content=fx("underenhet_973861883.json"), headers=JSON_HDR)
    router.get(f"{BASE}/enheter/976820479").respond(404, content=b"")
    router.get(f"{BASE}/underenheter/976820479").respond(404, content=b"")


def test_run_batch_resolves_all_identity_states(tmp_path):
    orgs = ["923609016", "810034882", "922052778", "934516451", "973861883", "976820479", "123456789"]
    with respx.mock(assert_all_called=True) as router:
        _mock_live(router)
        ctx = make_ctx(tmp_path)
        comps = [CompanyResult(o) for o in orgs]
        asyncio.run(registry.run_batch(ctx, comps))
        used = ctx.client.used
        asyncio.run(ctx.client.aclose())
    c = {x.organisation_number: x for x in comps}
    assert used == 7  # 1 batch + deleted + 410 + (404 + sub-unit) + (404 + 404); invalid costs nothing
    status = {o: x.data["identity"]["match_status"] for o, x in c.items()}
    assert status == {"923609016": "exact", "810034882": "exact", "922052778": "deleted", "934516451": "not_found",
                      "973861883": "subunit", "976820479": "not_found", "123456789": "invalid"}
    for x in comps:
        assert "registry" in x.modules

    eq = c["923609016"]
    ident = eq.data["identity"]
    assert ident["confidence"] == 1.0 and ident["method"] == "brreg_enheter_batch"
    assert {k["name"]: k["passed"] for k in ident["checks"]}["organisation_number_echo"] is True
    assert one(eq, "legal_name").value == "EQUINOR ASA"
    assert one(eq, "organisation_number").display == "923 609 016"
    cap = one(eq, "share_capital")
    assert cap.value["amount"] == Decimal("5976872600.00") and isinstance(cap.value["amount"], Decimal)
    assert cap.value["shares"] == 2390749040 and cap.effective_at == "2026-07-02"
    assert one(eq, "employees").value == {"count": 21272, "as_of": "2026-09-10"}
    county = one(eq, "county")
    assert county.value == {"number": "11", "name": "Rogaland"}
    assert county.match_basis == "derived" and county.method == "kommunenummer_prefix_county_2024"
    assert one(eq, "business_address").display == "Forusbeen 50, 4035 STAVANGER"
    assert one(eq, "registry_status").value == "active"
    assert [cl.value["code"] for cl in sorted(by_field(eq, "industry"), key=lambda x: x.qualifier)] == \
        ["06.100", "06.200", "19.200"]
    assert len(by_field(eq, "former_name")) == 4 and one(eq, "former_name", "2018-05-16").value["name"] == "STATOIL ASA"
    assert one(eq, "group_membership").value is True
    assert one(eq, "registry_website").value == "www.equinor.com"
    assert one(eq, "latest_submitted_accounts_year").value == 2025
    # evidence: canonical record URL, batch URL in retrieved_via, per-record sha of the verbatim slice
    ev = one(eq, "legal_name").evidence[0]
    assert ev.source_url == f"{BASE}/enheter/923609016"
    assert ev.retrieved_via.startswith(f"{BASE}/enheter?organisasjonsnummer=")
    assert ev.page_url == "https://virksomhet.brreg.no/nb/oppslag/enheter/923609016"
    body = snapshot_text(ctx, ev.content_sha256)
    assert hashlib.sha256(body.encode()).hexdigest() == ev.content_sha256
    assert body in fx("enheter_batch_live.json").decode()  # byte-identical slice of the batch body
    assert json.loads(body)["organisasjonsnummer"] == "923609016"
    assert eq.data["registry"]["navn"] == "EQUINOR ASA"
    assert eq.data["registry_evidence"].locator == "/organisasjonsnummer"
    assert registry.registry_evidence(eq, "/hjemmeside").claim_span == '"hjemmeside":"www.equinor.com"'

    sa = c["810034882"]
    assert one(sa, "business_address").display == "Strandgata 21, 4307 SANDNES"
    assert one(sa, "registry_email").value == "post@sandneselektriske.no"

    de = c["922052778"]
    assert one(de, "registry_status").value == "deleted"
    assert one(de, "deletion").value == {"date": "2026-10-12"}
    assert one(de, "legal_form").value["code"] == "ENK"
    assert not by_field(de, "business_address") and de.modules["registry"].availability == "available"

    rm = c["934516451"]
    assert rm.claims == [] and rm.data["identity"]["removed"] is True
    assert rm.modules["registry"].availability == "not_available"
    assert "legal reasons" in rm.modules["registry"].note and "registry" not in rm.data
    assert not any(e.get("url") == f"{BASE}/enheter/934516451" for e in ctx.snapshots.index.values())

    su = c["973861883"]
    assert su.data["identity"]["parent_organisation_number"] == "923609016"
    assert su.data["parent_organisation_number"] == "923609016"
    assert su.claims and all(cl.relation == "subunit" for cl in su.claims)
    assert one(su, "parent_company").value == {"organisation_number": "923609016", "relationship": "main_unit"}
    assert one(su, "employees").value["count"] == 39

    assert c["976820479"].modules["registry"].availability == "not_available" and c["976820479"].claims == []
    assert c["123456789"].modules["registry"].availability == "not_applicable"
    assert assert_spans_verbatim(ctx, comps) > 60


def test_echo_mismatch_publishes_nothing(tmp_path):
    other = json.loads(fx("enhet_deleted_922052778.json"))
    other["organisasjonsnummer"] = "923609016"
    with respx.mock() as router:
        router.get(url__regex=r".*/enheter\?organisasjonsnummer=.*").respond(
            200, content=b'{"_links":{},"page":{"size":1,"totalElements":0,"totalPages":0,"number":0}}', headers=JSON_HDR)
        router.get(f"{BASE}/enheter/922052778").respond(200, json=other)
        ctx = make_ctx(tmp_path)
        c = CompanyResult("922052778")
        asyncio.run(registry.run_batch(ctx, [c]))
        asyncio.run(ctx.client.aclose())
    assert c.data["identity"]["match_status"] == "error"
    assert c.claims == [] and c.modules["registry"].availability == "ambiguous"


def test_batch_failure_without_snapshot_is_failed(tmp_path):
    with respx.mock() as router:
        router.get(url__regex=r".*/enheter\?organisasjonsnummer=.*").respond(500, content=b"oops")
        ctx = make_ctx(tmp_path)
        c = CompanyResult("923609016")
        asyncio.run(registry.run_batch(ctx, [c]))
        asyncio.run(ctx.client.aclose())
    assert c.data["identity"]["match_status"] == "error"
    assert c.modules["registry"].availability == "failed" and c.claims == []


# --------------------------------------------------------------------------- snapshot fallback
def test_snapshot_used_when_live_register_not_checked(tmp_path):
    ctx = make_ctx(tmp_path, max_requests=0, registry_snapshot=CSV_SNAPSHOT)
    comps = [CompanyResult("810034882"), CompanyResult("923609016")]
    asyncio.run(registry.run_batch(ctx, comps))
    asyncio.run(ctx.client.aclose())
    sa, eq = comps
    assert ctx.client.used == 0
    assert sa.data["identity"]["match_status"] == "exact" and sa.data["identity"]["source"] == "snapshot"
    assert sa.data["identity"]["confidence"] == 0.95
    ev = one(sa, "legal_name").evidence[0]
    assert ev.source_class == OFFICIAL_REGISTRY_BULK and ev.source_url == str(CSV_SNAPSHOT)
    assert ev.content_sha256 == hashlib.sha256(CSV_SNAPSHOT.read_bytes()).hexdigest()
    assert ev.claim_span == "SANDNES ELEKTRISKE AS" and ev.locator.endswith("column=navn")
    assert ctx.shared["registry_snapshot_sha256"] == ev.content_sha256
    assert one(sa, "share_capital").value["amount"] == Decimal("2375000.00")
    assert one(sa, "business_address").display == "Strandgata 21, 4307 SANDNES"
    assert one(sa, "county").value["name"] == "Rogaland"
    # not in the snapshot and live not checked: no identity, no claims
    assert eq.data["identity"]["match_status"] == "error" and eq.claims == []
    assert eq.modules["registry"].availability == "failed"


def test_live_record_preferred_and_compared_with_snapshot(tmp_path):
    with respx.mock(assert_all_called=False) as router:
        _mock_live(router)
        ctx = make_ctx(tmp_path, registry_snapshot=CSV_SNAPSHOT)
        c = CompanyResult("810034882")
        asyncio.run(registry.run_batch(ctx, [c]))
        asyncio.run(ctx.client.aclose())
    assert c.data["identity"]["source"] == "live"
    checks = {k["name"]: k for k in c.data["identity"]["checks"]}
    assert checks["snapshot_consistency"]["passed"] is True
    assert one(c, "legal_name").evidence[0].source_url == f"{BASE}/enheter/810034882"


# --------------------------------------------------------------------------- claim builder on varied record types
def _claims_for(rec: dict):
    text = json.dumps(rec, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    src = RecordSource(orgnr=rec["organisasjonsnummer"], kind="live", text=text, source_url="u", source_class="x",
                       source_name="n", retrieved_at="2026-10-13T00:00:00Z", sha="s", page_url="p")
    c = CompanyResult(rec["organisasjonsnummer"])
    c.add(enhet_claims(registry.loads_decimal(text), src))
    for cl in c.claims:
        for ev in cl.evidence:
            assert ev.claim_span is None or ev.claim_span in text, (cl.field, ev.claim_span)
    return c


def test_claims_for_varied_entity_types():
    recs = json.loads(fx("enhet_records_varied.json"))

    k = _claims_for(recs["KONKURS_935567645"])
    assert one(k, "registry_status").value == "bankrupt"
    assert one(k, "bankruptcy").value == {"date": "2026-07-08"}
    emp = one(k, "employees")
    assert emp.availability == "not_available" and emp.value is None
    assert one(k, "audit_exemption").value == {"date": "2025-05-27", "decision_date": "2025-03-07"}

    t = _claims_for(recs["TVANGSAVVIKLET_816373972"])
    assert one(t, "registry_status").value == "forced_dissolution"
    assert one(t, "forced_dissolution").value["date"] == "2026-10-10"
    assert one(t, "liquidation").value == {"date": "2025-07-02"}

    n = _claims_for(recs["NUF_920058817"])
    ann = one(n, "registry_annotation", "SIGN")
    assert ann.value["code"] == "SIGN" and ann.effective_at == "2026-10-05"
    assert not by_field(n, "share_capital")

    u = _claims_for(recs["UTLA_995783568"])
    fr = one(u, "foreign_registration").value
    assert fr["registration_number"] == "05428049" and fr["register_name"] == "Companies House"
    assert fr["governing_law_country_code"] == "GB"
    assert not by_field(u, "municipality") and not by_field(u, "county")
    assert one(u, "country").value == {"code": "GB", "name": "Storbritannia"}
    assert one(u, "business_address").display == "33 Longford Avenue, UB13QN Southall, Storbritannia"

    ks = _claims_for(recs["KS_922240906"])
    cap = one(ks, "share_capital").value
    assert cap["type"] == "Selskapskapital" and cap["bound"] == Decimal("485071360.0") and cap["fully_paid"] is False

    o = _claims_for(recs["ORGL_974760673"])
    psp = one(o, "public_sector_parent")
    assert psp.value == {"organisation_number": "912660680"} and psp.relation == "parent"
    assert one(o, "county").value["name"] == "Nordland"

    f = _claims_for(recs["PAATEGNING_FADR_988601764"])
    assert one(f, "registry_annotation", "FADR").value["text"].startswith("Foretaksregisteret har grunn")
    assert one(f, "business_address").display == "3533 TYRISTRAND"

    b = _claims_for(recs["BRL_992614293"])
    assert one(b, "employees").value["range"] == "1-4" and one(b, "employees").value["count"] is None

    p = _claims_for(recs["PARTI_971526939"])
    assert not by_field(p, "founding_date")  # 1900-01-01 placeholder is never published


def test_never_raises_on_garbage(tmp_path):
    with respx.mock() as router:
        router.get(url__regex=r".*").mock(side_effect=httpx.ConnectError("boom"))
        ctx = make_ctx(tmp_path)
        comps = [CompanyResult("923609016"), CompanyResult("not-a-number")]
        asyncio.run(registry.run_batch(ctx, comps))
        asyncio.run(ctx.client.aclose())
    assert all("registry" in c.modules for c in comps)
    assert comps[0].modules["registry"].availability == "failed"
    assert comps[1].data["identity"]["match_status"] == "invalid"
