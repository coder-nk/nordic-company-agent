"""Offline tests for envelope assembly, refresh diffs (idempotency, carry-forward) and validation."""

import json
from pathlib import Path

from nordic_company_agent.assemble import assemble_envelope
from nordic_company_agent.context import RunConfig, RunContext
from nordic_company_agent.envelope import Claim, CompanyResult, Evidence, ModuleOutcome
from nordic_company_agent.report import validate_envelopes
from nordic_company_agent.viewer import build_site


def _ev(span: str, sha: str = "a" * 64) -> Evidence:
    return Evidence(source_url="https://data.brreg.no/enhetsregisteret/api/enheter/923609016",
                    source_class="official_registry", source_name="Enhetsregisteret",
                    retrieved_at="2026-10-13T10:00:00Z", content_sha256=sha, claim_span=span,
                    locator="/navn", extraction_method="registry@1",
                    page_url="https://virksomhet.brreg.no/nb/oppslag/enheter/923609016")


def _company(ceo: str = "Anders Opedal", revenue: int = 100, website_state: str = "available",
             with_site: bool = True) -> CompanyResult:
    c = CompanyResult("923609016")
    c.data["identity"] = {"match_status": "exact", "confidence": 1.0, "method": "registry_live", "checks": []}
    c.data["registry"] = {"organisasjonsform": {"kode": "ASA"}}
    claims = [
        Claim("legal_name", "EQUINOR ASA", [_ev('"navn":"EQUINOR ASA"')]),
        Claim("legal_form", {"code": "ASA", "description": "Allmennaksjeselskap"}, [_ev('"kode":"ASA"')]),
        Claim("registry_status", "active", [_ev('"konkurs":false')]),
        Claim("ceo", {"name": ceo, "role_code": "DAGL", "role": "Daglig leder"}, [_ev(f'"{ceo}"', "b" * 64)],
              qualifier=None),
        Claim("revenue", revenue, [_ev(f'"sumDriftsinntekter":{revenue}', "c" * 64)], qualifier="2025|company",
              period={"start": "2025-01-01", "end": "2025-12-31", "year": 2025, "scope": "company"},
              display=f"NOK {revenue}"),
    ]
    mods = {"registry": claims[:3], "roles": [claims[3]], "accounts": [claims[4]]}
    for m, cs in mods.items():
        for cl in cs:
            cl.module = m
        c.outcome(ModuleOutcome(m, "available"))
        c.add(cs)
    if with_site and website_state == "available":
        w = Claim("official_website", "https://www.equinor.com/", [_ev("Equinor ASA org.nr 923 609 016", "d" * 64)],
                  confidence=0.97)
        w.module = "website"
        c.add([w])
    c.outcome(ModuleOutcome("website", website_state, note=None if website_state == "available" else "timeout"))
    return c


def _ctx(tmp_path: Path) -> RunContext:
    return RunContext(RunConfig(run_id="t", out_dir=tmp_path, save_snapshots=False))


def _env(tmp_path, c, prev=None, run_id="r2", ts="2026-10-14T10:00:00Z"):
    return assemble_envelope(_ctx(tmp_path), c, run_id=run_id, started_at=ts, completed_at=ts, previous=prev)


def test_envelope_shape_and_validation(tmp_path):
    env = _env(tmp_path, _company(), run_id="r1", ts="2026-10-13T10:00:00Z")
    assert env["organisation_number"] == "923609016"
    assert env["run"]["terminal_status"] == "completed"
    assert env["legal_name"] == "EQUINOR ASA"
    ids = {e["id"] for e in env["evidence"]}
    for c in env["claims"]:
        if c["availability"] == "available":
            assert c["evidence_ids"] and set(c["evidence_ids"]) <= ids
    # explicit missing-data labels exist for modules that never ran / found nothing
    assert any(c["field"] == "chair" and c["availability"] == "not_available" for c in env["claims"])
    v = validate_envelopes([env], ["923609016"])
    assert v["passed"], v
    assert env["summary"]["sentences"] and all(s["text"] for s in env["summary"]["sentences"])


def test_refresh_idempotent(tmp_path):
    e1 = _env(tmp_path, _company(), run_id="r1", ts="2026-10-13T10:00:00Z")
    e2 = _env(tmp_path, _company(), prev=e1)
    assert e2["changes"] == []
    assert e2["refresh"]["content_hash"] == e1["refresh"]["content_hash"]
    ceo1 = next(c for c in e1["claims"] if c["field"] == "ceo")
    ceo2 = next(c for c in e2["claims"] if c["field"] == "ceo")
    assert ceo1["id"] == ceo2["id"] and ceo2["first_seen"] == ceo1["first_seen"]


def test_refresh_detects_material_changes(tmp_path):
    e1 = _env(tmp_path, _company(), run_id="r1", ts="2026-10-13T10:00:00Z")
    e2 = _env(tmp_path, _company(ceo="Kari Nordmann", revenue=120), prev=e1)
    kinds = {(c["field"], c["kind"]) for c in e2["changes"]}
    assert ("ceo", "modified") in kinds
    assert ("revenue", "modified") in kinds
    ch = next(c for c in e2["changes"] if c["field"] == "ceo")
    assert ch["old_value"]["name"] == "Anders Opedal" and ch["new_value"]["name"] == "Kari Nordmann"
    ev_ids = {e["id"] for e in e2["evidence"]}
    assert set(ch["old_evidence_ids"]) <= ev_ids, "previous evidence must be preserved"


def test_failed_source_carries_forward_not_removed(tmp_path):
    e1 = _env(tmp_path, _company(), run_id="r1", ts="2026-10-13T10:00:00Z")
    e2 = _env(tmp_path, _company(website_state="failed", with_site=False), prev=e1)
    assert not any(c["field"] == "official_website" and c["kind"] == "removed" for c in e2["changes"])
    stale = [c for c in e2["claims"] if c.get("stale")]
    assert stale and stale[0]["field"] == "official_website" and stale[0]["value"] == "https://www.equinor.com/"


def test_viewer_builds(tmp_path):
    env = _env(tmp_path, _company(), run_id="r1", ts="2026-10-13T10:00:00Z")
    build_site([env], tmp_path / "site")
    page = (tmp_path / "site" / "company" / "923609016.html").read_text()
    assert "EQUINOR ASA" in page and "View source" in page
    assert (tmp_path / "site" / "index.html").exists()
    json.dumps(env)  # serialisable
