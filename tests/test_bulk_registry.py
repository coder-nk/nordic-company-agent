"""Bulk-file path for roles and sub-units (used for large batches under tight budgets)."""

from __future__ import annotations

import asyncio
import gzip
import json
from datetime import datetime, timezone
from pathlib import Path

from nordic_company_agent.context import RunConfig, RunContext
from nordic_company_agent.envelope import CompanyResult
from nordic_company_agent.sources import roles, subunits
from nordic_company_agent.sources.bulk_registry import _parse_roles, _parse_subunits

FIX = Path(__file__).parent / "fixtures" / "registry"


def _company(orgnr: str) -> CompanyResult:
    c = CompanyResult(orgnr)
    c.data["identity"] = {"organisation_number": orgnr, "match_status": "exact"}
    c.data["registry"] = {"organisasjonsnummer": orgnr, "organisasjonsform": {"kode": "ASA"}}
    return c


def test_parse_and_use_roles_bulk(tmp_path):
    doc = json.loads((FIX / "roller_ASA_923609016.json").read_text())
    doc["organisasjonsnummer"] = "923609016"
    other = dict(doc, organisasjonsnummer="984851006")
    path = tmp_path / "roles.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump([other, doc], fh)
    items = _parse_roles(path, {"923609016"})
    assert set(items) == {"923609016"}
    ctx = RunContext(RunConfig(run_id="t", out_dir=tmp_path))
    ctx.shared["bulk_roles"] = {"url": "https://data.brreg.no/enhetsregisteret/api/roller/totalbestand",
                                "final_url": "https://data.brreg.no/x.json.gz", "retrieved_at": "2026-10-13T10:00:00Z",
                                "retrieved_dt": datetime(2026, 10, 13, 10, tzinfo=timezone.utc), "items": items}
    c = _company("923609016")
    asyncio.run(roles.run(ctx, c))
    assert ctx.client.used == 0
    assert c.modules["roles"].availability == "available"
    ceo = [cl for cl in c.claims if cl.field == "ceo"][0]
    assert ceo.value["name"] == "Anders Opedal"
    assert ceo.evidence[0].retrieved_via.endswith("/roller/totalbestand")
    snap = gzip.decompress((ctx.snapshots.root / f"{ceo.evidence[0].content_sha256}.gz").read_bytes()).decode()
    assert "fodselsdato" not in snap and ceo.evidence[0].claim_span in snap
    missing = _company("982463718")
    asyncio.run(roles.run(ctx, missing))
    assert missing.modules["roles"].availability == "not_available"
    asyncio.run(ctx.client.aclose())


def test_parse_and_use_subunits_bulk(tmp_path):
    subs = [{"organisasjonsnummer": "973152351", "navn": "EQUINOR ASA AVD X", "overordnetEnhet": "923609016",
             "beliggenhetsadresse": {"adresse": ["Forusbeen 50"], "postnummer": "4035", "poststed": "STAVANGER",
                                     "kommune": "STAVANGER", "kommunenummer": "1103"}},
            {"organisasjonsnummer": "912345678", "navn": "ANNEN AS AVD", "overordnetEnhet": "984851006"},
            {"organisasjonsnummer": "973152300", "navn": "EQUINOR ASA NEDLAGT", "overordnetEnhet": "923609016",
             "nedleggelsesdato": "2020-01-01"}]
    path = tmp_path / "sub.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(subs, fh)
    items = _parse_subunits(path, {"923609016"})
    assert len(items["923609016"]) == 2
    ctx = RunContext(RunConfig(run_id="t", out_dir=tmp_path))
    ctx.shared["bulk_subunits"] = {"url": "https://data.brreg.no/enhetsregisteret/api/underenheter/lastned",
                                   "retrieved_at": "2026-10-13T10:00:00Z", "file_sha256": "f" * 64,
                                   "last_modified": "Tue, 13 Oct 2026 02:03:00 GMT", "items": items}
    c = _company("923609016")
    asyncio.run(subunits.run(ctx, c))
    assert ctx.client.used == 0
    wp = [cl for cl in c.claims if cl.field == "registered_workplace"]
    assert [cl.qualifier for cl in wp] == ["973152351"]
    cnt = [cl for cl in c.claims if cl.field == "registered_workplace_count"][0]
    assert cnt.value == 1 and cnt.match_basis == "derived"
    asyncio.run(ctx.client.aclose())
