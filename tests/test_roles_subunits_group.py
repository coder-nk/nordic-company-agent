"""Offline tests for sources/roles.py, subunits.py, group.py and authorisations.py."""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
from pathlib import Path

import respx

from nordic_company_agent.context import RunConfig, RunContext
from nordic_company_agent.envelope import CompanyResult
from nordic_company_agent.sources import authorisations, group, roles, subunits

FIX = Path(__file__).parent / "fixtures" / "registry"
BASE = "https://data.brreg.no/enhetsregisteret/api"
JSON_HDR = {"content-type": "application/json"}


def fx(name: str) -> bytes:
    return (FIX / name).read_bytes()


def company(orgnr: str, status: str = "exact", **registry) -> CompanyResult:
    c = CompanyResult(orgnr)
    c.data["identity"] = {"organisation_number": orgnr, "match_status": status}
    rec = {"organisasjonsnummer": orgnr, "organisasjonsform": {"kode": registry.pop("form", "AS")}}
    rec.update(registry)
    c.data["registry"] = rec
    return c


def run(module, c: CompanyResult, tmp_path: Path, routes: dict[str, tuple[int, bytes]]) -> RunContext:
    with respx.mock(assert_all_called=True) as router:
        for url, (status, body) in routes.items():
            router.get(url).respond(status, content=body, headers=JSON_HDR)
        ctx = RunContext(RunConfig(run_id="test", out_dir=tmp_path))
        asyncio.run(module.run(ctx, c))
        ctx.used = ctx.client.used  # type: ignore[attr-defined]
        asyncio.run(ctx.client.aclose())
    return ctx


def snap(ctx: RunContext, sha: str) -> str:
    return gzip.decompress((ctx.snapshots.root / f"{sha}.gz").read_bytes()).decode("utf-8")


def fields(c: CompanyResult, field: str):
    return [cl for cl in c.claims if cl.field == field]


def assert_verbatim(ctx: RunContext, c: CompanyResult) -> None:
    for cl in c.claims:
        for ev in cl.evidence:
            if ev.claim_span:
                assert ev.claim_span in snap(ctx, ev.content_sha256), (cl.field, ev.claim_span)
                assert "1970" not in ev.claim_span  # synthetic birth dates never reach a span


# --------------------------------------------------------------------------- roles
def test_roles_asa_current_roles_and_redaction(tmp_path):
    c = company("923609016", form="ASA")
    url = f"{BASE}/enheter/923609016/roller"
    raw = fx("roller_ASA_923609016.json")
    ctx = run(roles, c, tmp_path, {url: (200, raw)})
    assert ctx.used == 1 and c.modules["roles"].availability == "available"
    ceo = fields(c, "ceo")[0]
    assert ceo.value == {"name": "Anders Opedal", "role_code": "DAGL", "role": "Daglig leder", "group_code": "DAGL",
                         "group": "Daglig leder", "last_changed": "2020-11-02"}
    assert ceo.qualifier is None and ceo.relation == "self" and ceo.effective_at == "2020-11-02"
    assert fields(c, "chair")[0].value["name"] == "Jarle Kjell Roth"
    board = {cl.qualifier: cl for cl in fields(c, "board_member")}
    assert "finn-bjørn-ruyter" not in board                      # avregistrert: not a current role
    assert "frank-indreland-gundersen" in board                   # fornavn + mellomnavn + etternavn
    assert board["hilde-møllerstad"].value["elected_by"] == "Representant for de ansatte"
    assert c.data["roles_deregistered_skipped"] == 1
    auditor = fields(c, "auditor")[0]
    assert auditor.relation == "related" and auditor.value["organisation_number"]
    # personal data: no birth dates or erDoed anywhere in output or stored snapshot
    dumped = json.dumps([cl.value for cl in c.claims] + c.data["roles"], ensure_ascii=False)
    assert "1970" not in dumped and "fodselsdato" not in dumped and "erDoed" not in dumped
    original_sha = hashlib.sha256(raw).hexdigest()
    entry = ctx.snapshots.index[original_sha]
    assert entry["redacted"] and "fodselsdato" not in snap(ctx, original_sha)
    assert all(ev.content_sha256 == original_sha for cl in c.claims for ev in cl.evidence)
    assert_verbatim(ctx, c)


def test_roles_entity_holders_and_other_codes(tmp_path):
    c = company("922240906", form="KS")
    ctx = run(roles, c, tmp_path, {f"{BASE}/enheter/922240906/roller": (200, fx("roller_KS_922240906.json"))})
    bm = fields(c, "business_manager")[0]
    assert bm.relation == "related" and bm.value["organisation_number"] == "993147044"
    assert bm.value["name"] == "ABLER NORDIC AS" and bm.value["role_code"] == "FFØR"
    assert fields(c, "general_partner")[0].value["organisation_number"] == "922100209"
    assert fields(c, "contact_person")[0].relation == "self"
    assert fields(c, "auditor")[0].value["name"] == "DELOITTE AS"
    assert_verbatim(ctx, c)

    k = company("937662068", form="KBO")
    ctx = run(roles, k, tmp_path / "k", {f"{BASE}/enheter/937662068/roller": (200, fx("roller_KBO_937662068.json"))})
    other = {cl.qualifier: cl for cl in fields(k, "role_other")}
    assert set(other) == {"BOBE|adv-eivind-nordby-johnsen", "KDEB|933724751"}
    assert other["KDEB|933724751"].relation == "related"
    assert "postadresse" not in json.dumps(other["BOBE|adv-eivind-nordby-johnsen"].value)
    assert_verbatim(ctx, k)

    i = company("830301402", form="IKS")
    ctx = run(roles, i, tmp_path / "i", {f"{BASE}/enheter/830301402/roller": (200, fx("roller_IKS_830301402.json"))})
    partners = fields(i, "partner")
    assert partners and all(p.value.get("liability_share") for p in partners)
    assert len({p.key for p in i.claims}) == len(i.claims)       # stable keys are unique
    assert_verbatim(ctx, i)


def test_roles_states_without_data(tmp_path):
    c = company("971422556", status="deleted", form="ENK")
    asyncio.run(roles.run(RunContext(RunConfig(run_id="t", out_dir=tmp_path)), c))
    assert c.modules["roles"].availability == "not_applicable" and c.claims == []
    u = company("995783568", form="UTLA")
    asyncio.run(roles.run(RunContext(RunConfig(run_id="t", out_dir=tmp_path)), u))
    assert u.modules["roles"].availability == "not_applicable"
    n = company("930459615")
    run(roles, n, tmp_path / "n", {f"{BASE}/enheter/930459615/roller": (404, b"")})
    assert n.modules["roles"].availability == "not_available" and n.claims == []


# --------------------------------------------------------------------------- subunits
def test_subunits_list_and_count(tmp_path):
    c = company("982463718", form="ASA")
    url = f"{BASE}/underenheter?overordnetEnhet=982463718&size=1000"
    ctx = run(subunits, c, tmp_path, {url: (200, fx("underenheter_982463718.json"))})
    wp = fields(c, "registered_workplace")
    assert len(wp) == 3 and all(cl.relation == "subunit" for cl in wp)
    assert sorted(cl.qualifier for cl in wp) == [cl.qualifier for cl in wp]
    first = wp[0]
    assert first.evidence[0].source_url == f"{BASE}/underenheter/{first.qualifier}"
    assert first.evidence[0].retrieved_via == url
    assert set(first.value) >= {"organisation_number", "name", "address", "industry", "employees", "started"}
    count = fields(c, "registered_workplace_count")[0]
    assert count.value == 3 and count.evidence[0].claim_span == '"totalElements":3'
    assert len(c.data["subunits"]) == 3 and c.data["subunits_meta"]["complete"] is True
    assert c.modules["subunits"].availability == "available"
    assert_verbatim(ctx, c)


def test_subunits_zero_is_a_checked_count(tmp_path):
    c = company("930459615")
    body = b'{"_links":{"self":{"href":"x"}},"page":{"number":0,"size":1000,"totalElements":0,"totalPages":0}}'
    run(subunits, c, tmp_path, {f"{BASE}/underenheter?overordnetEnhet=930459615&size=1000": (200, body)})
    assert fields(c, "registered_workplace_count")[0].value == 0
    assert fields(c, "registered_workplace") == [] and c.modules["subunits"].availability == "not_available"


# --------------------------------------------------------------------------- group
def test_group_subsidiary_finds_parent_and_ultimate_parent(tmp_path):
    c = company("980321932", erIKonsern=True)
    ctx = run(group, c, tmp_path, {f"{BASE}/konsernstruktur/980321932": (200, fx("konsernstruktur_980321932.json"))})
    parent = fields(c, "parent_company")[0]
    assert parent.relation == "parent"
    assert parent.value == {"organisation_number": "990888213", "name": "EQUINOR ENERGY AS", "ownership": "100%",
                            "since": "2025-11-08", "link_type": "Konsern datter"}
    ult = fields(c, "ultimate_parent")[0]
    assert ult.relation == "ultimate_parent" and ult.value == {"organisation_number": "923609016", "name": "EQUINOR ASA"}
    assert fields(c, "group_subsidiary") == []
    assert c.data["group"]["level"] == 2 and not c.data["group"]["is_root"]
    assert_verbatim(ctx, c)


def test_group_root_lists_direct_subsidiaries(tmp_path):
    c = company("982463718", erIKonsern=True, form="ASA")
    ctx = run(group, c, tmp_path, {f"{BASE}/konsernstruktur/982463718": (200, fx("konsernstruktur_982463718.json"))})
    subs = fields(c, "group_subsidiary")
    assert len(subs) == 10 and all(s.relation == "related" for s in subs)
    assert all(s.value["ownership"] for s in subs)
    pc = fields(c, "parent_company")[0]
    assert pc.availability == "not_applicable" and pc.value is None
    assert not fields(c, "ultimate_parent")
    assert_verbatim(ctx, c)


def test_group_gating_and_404(tmp_path):
    c = company("930459615", erIKonsern=False)
    ctx = RunContext(RunConfig(run_id="t", out_dir=tmp_path))
    asyncio.run(group.run(ctx, c))
    assert c.modules["group"].availability == "not_available" and ctx.client.used == 0
    d = company("930459615", erIKonsern=True)
    run(group, d, tmp_path / "d", {f"{BASE}/konsernstruktur/930459615": (404, b"")})
    assert d.modules["group"].availability == "not_available" and d.claims == []


# --------------------------------------------------------------------------- authorisations
FM = "https://data.brreg.no/fullmakt/enheter"


def test_authorisations_signature_and_prokura(tmp_path):
    c = company("934296877", registrertIForetaksregisteret=True)
    raw_sig = fx("fullmakt_signatur_934296877.json")
    ctx = run(authorisations, c, tmp_path, {f"{FM}/934296877/signatur": (200, raw_sig),
                                            f"{FM}/934296877/prokura": (200, fx("fullmakt_prokura_934296877_none.json"))})
    sig = fields(c, "signature_rule")[0]
    assert sig.value == {"text": "Styrets leder alene.", "holders": ["Even Andor Skavhaug"]}
    pro = fields(c, "prokura_rule")[0]
    assert pro.availability == "not_available" and "Signeringsgrunnlag finnes ikke" in pro.note
    assert c.modules["authorisations"].availability == "available"
    sha = hashlib.sha256(raw_sig).hexdigest()
    assert ctx.snapshots.index[sha]["redacted"] and "fodselsdato" not in snap(ctx, sha)
    assert_verbatim(ctx, c)

    s = company("810034882", registrertIForetaksregisteret=True)
    run(authorisations, s, tmp_path / "s", {
        f"{FM}/810034882/signatur": (404, b""),
        f"{FM}/810034882/prokura": (200, fx("fullmakt_prokura_810034882.json"))})
    p = fields(s, "prokura_rule")[0]
    assert p.value == {"text": "Prokura hver for seg", "holders": ["Jan Marton Aarrestad", "Morten Aarrestad"]}
    assert fields(s, "signature_rule")[0].availability == "not_available"


def test_authorisations_not_machine_readable_and_gating(tmp_path):
    c = company("923609016", registrertIForetaksregisteret=True, form="ASA")
    na = fx("fullmakt_signatur_equinor_na.json")
    run(authorisations, c, tmp_path, {f"{FM}/923609016/signatur": (200, na), f"{FM}/923609016/prokura": (200, na)})
    assert {cl.availability for cl in c.claims} == {"not_available"}
    assert "Generelle unntak" in fields(c, "signature_rule")[0].note
    assert c.modules["authorisations"].availability == "not_available"
    f = company("943838240", registrertIForetaksregisteret=False, form="FLI")
    ctx = RunContext(RunConfig(run_id="t", out_dir=tmp_path / "f"))
    asyncio.run(authorisations.run(ctx, f))
    assert f.modules["authorisations"].availability == "not_applicable" and ctx.client.used == 0
