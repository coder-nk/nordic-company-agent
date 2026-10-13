"""Offline tests for sources/public_records.py (tiny declared cache in a temp cache_dir) and the parsing
helpers of tools/build_public_records_cache.py."""

from __future__ import annotations

import asyncio
import gzip
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from nordic_company_agent.context import RunConfig, RunContext
from nordic_company_agent.envelope import OFFICIAL_GRANTS, CompanyResult, field_family, utc_iso
from nordic_company_agent.sources import public_records as pr

ROOT = Path(__file__).resolve().parents[1]
SF_URL = "https://www.forskningsradet.no/siteassets/skattefunn/tall/260902-webliste-skattefunn.xlsx"
NFR_URL = "https://raw.githubusercontent.com/Forskningsradet/open-data/main/datasets/soknader2/dataset.csv"
MT_URL = "https://smilefjes.mattilsynet.no/api/tilsyn.csv"
SHA = {SF_URL: "a" * 64, NFR_URL: "b" * 64, MT_URL: "c" * 64}
EQUINOR, DNB, OTHER = "923609016", "984851006", "810034882"
SUB_FORUS, SUB_MOVED, SUB_FOREIGN = "973861883", "912015351", "999039529"


def sf_row(org, cell_org, sn, title, status="approved", date="2025-09-01"):
    return {"organisation_number": org, "kind": "skattefunn", "record_id": f"skattefunn:soknad:{sn}",
            "application_number": sn, "project_number": "90001532" if status == "approved" else None,
            "date": date, "decision_date": "2025-11-18", "title": title, "status": status,
            "period": {"from": 2025, "to": 2027}, "summary": "Prosjektet skal utvikle ...",
            "source_url": SF_URL, "source_sha256": SHA[SF_URL], "row_key": f"sheet:Endelig!row:{sn}",
            "company_kind_total": 2,
            "raw_cells": {"Organisasjonsnr": cell_org, "Søknadsnummer": sn, "Prosjekttittel": title,
                          "Godkjent": "JA" if status == "approved" else "NEI", "Søknadsdato": f"{date}T00:00:00"}}


def nfr_row(org, pn, status, amount=None, date="2025-03-10"):
    cells = {"organisasjonsnummer": org, "prosjektnummer": pn, "prosjekttittel": f"Prosjekt {pn}",
             "prosjektfase": {"granted": "Bevilgning", "rejected": "Avslag"}[status],
             "soknadsdato": f"{date} 20:41:45", "tildelt_belop": amount or "0.0"}
    return {"organisation_number": org, "kind": "research_grant", "record_id": f"forskningsradet:{pn}",
            "project_number": pn, "date": date, "title": f"Prosjekt {pn}", "status": status, "phase": cells["prosjektfase"],
            "amount": amount, "currency": "NOK" if amount else None, "period": {"from": 2025, "to": 2028},
            "programme": "REKRUTTERING", "source_url": NFR_URL, "source_sha256": SHA[NFR_URL],
            "row_key": f"csv:line:{pn}", "company_kind_total": 21, "raw_cells": cells}


def food_row(org, loc, tid, date="2024-03-05", grade="1", name="Kantine Forus"):
    return {"organisation_number": org, "kind": "food_inspection", "record_id": f"mattilsynet:{tid}", "date": date,
            "title": name, "status": grade, "grade": grade, "smiley": "smile" if grade in ("0", "1") else "sad_mouth",
            "location_organisation_number": loc, "location_name": name, "location_registry_name": name.upper(),
            "location_kind": "underenhet" if loc != org else "enhet", "address": "Forusbeen 50, 4035 STAVANGER",
            "parent_basis": "underenhet_overordnetEnhet" if loc != org else "enhet",
            "source_url": MT_URL, "source_sha256": SHA[MT_URL], "row_key": f"csv:line:{len(tid)}",
            "company_kind_total": 1,
            "raw_cells": {"orgnummer": loc, "navn": name, "dato": date[8:10] + date[5:7] + date[:4],
                          "total_karakter": grade, "tilsynid": tid}}


def write_cache(cache: Path, built_at: datetime | None = None, rows=None) -> str:
    cache.mkdir(parents=True, exist_ok=True)
    built = utc_iso(built_at or datetime.now(timezone.utc))
    rows = rows if rows is not None else [
        sf_row(EQUINOR, EQUINOR, "50002133", "Prediktivt lagerstyringssystem"),
        sf_row(EQUINOR, DNB, "50009999", "Row whose org-number cell is another company"),   # must be dropped
        nfr_row(EQUINOR, "359129", "granted", amount="2198000.0"),
        nfr_row(EQUINOR, "354138", "rejected"),
        food_row(EQUINOR, SUB_FORUS, "Z1_TilsynAvtale"),
        food_row(EQUINOR, SUB_FOREIGN, "Z2_TilsynAvtale", name="Not a current sub-unit"),
        food_row(DNB, SUB_MOVED, "Z3_TilsynAvtale", date="2025-01-02", name="Moved location"),
        food_row(DNB, DNB, "Z4_TilsynAvtale", date="2023-06-01", grade="3", name="DNB kantine"),
    ]
    with gzip.open(cache / "public_records.jsonl.gz", "wt", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    meta = {"built_at": built, "files": [
        {"url": SF_URL, "kind": "skattefunn", "sha256": SHA[SF_URL], "bytes": 10, "fetched_at": built},
        {"url": NFR_URL, "kind": "research_grant", "sha256": SHA[NFR_URL], "bytes": 10, "fetched_at": built},
        {"url": MT_URL, "kind": "food_inspection", "sha256": SHA[MT_URL], "bytes": 10, "fetched_at": built}]}
    (cache / "public_records.meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return built


def make_ctx(tmp_path: Path) -> RunContext:
    return RunContext(RunConfig(run_id="t", out_dir=tmp_path / "out", cache_dir=tmp_path / "cache", max_requests=5))


def company(orgnr: str, subunits=None, complete=True, status="exact") -> CompanyResult:
    c = CompanyResult(orgnr)
    c.data["identity"] = {"match_status": status}
    if subunits is not None:
        c.data["subunits"] = [{"organisation_number": s, "name": f"SUB {s}", "closed": None} for s in subunits]
        c.data["subunits_meta"] = {"complete": complete}
    return c


def run(ctx, cos):
    async def go():
        try:
            await pr.run_batch(ctx, cos)
        finally:
            await ctx.client.aclose()
    asyncio.run(go())


def avail(c: CompanyResult, field: str):
    return [cl for cl in c.claims if cl.field == field and cl.availability == "available"]


# ----------------------------------------------------------------------------- module
def test_exact_orgnr_required_and_claim_values(tmp_path):
    write_cache(tmp_path / "cache")
    ctx = make_ctx(tmp_path)
    eq = company(EQUINOR, subunits=[SUB_FORUS])
    run(ctx, [eq])
    assert ctx.client.used == 0                                   # cache only, no HTTP
    sf = avail(eq, "tax_incentive_project")
    assert [c.qualifier for c in sf] == ["skattefunn:soknad:50002133"]   # row with another org-number cell dropped
    assert eq.data["public_records"]["dropped"] == {"location_not_a_registered_subunit": 1,
                                                    "organisation_number_mismatch": 1}
    v = sf[0].value
    assert v["status"] == "approved" and v["title"] == "Prediktivt lagerstyringssystem"
    assert v["period"] == {"from": 2025, "to": 2027} and v["date"] == "2025-09-01"
    assert sf[0].relation == "self" and sf[0].match_basis == "organisation_number_exact"

    rg = {c.qualifier: c for c in avail(eq, "research_grant")}
    assert set(rg) == {"forskningsradet:359129", "forskningsradet:354138"}
    g = rg["forskningsradet:359129"].value
    assert g["amount"] == Decimal("2198000.0") and g["currency"] == "NOK" and g["status"] == "granted"
    assert g["programme"] == "REKRUTTERING"
    assert rg["forskningsradet:354138"].value["amount"] is None          # rejected: no amount, never 0
    assert "NOK 2 198 000" in rg["forskningsradet:359129"].display

    out = eq.modules[pr.MODULE]
    assert out.availability == "available"
    assert "the cache keeps the 2 most recent of 21" in out.note
    assert eq.data["public_records"]["published"] == {"tax_incentive_project": 1, "research_grant": 2,
                                                      "food_inspection": 1}


def test_food_inspection_subunit_relation(tmp_path):
    write_cache(tmp_path / "cache")
    ctx = make_ctx(tmp_path)
    eq = company(EQUINOR, subunits=[SUB_FORUS, SUB_MOVED])   # SUB_MOVED is cached under DNB but now Equinor's
    dnb = company(DNB, subunits=[])                          # complete list without SUB_MOVED
    run(ctx, [eq, dnb])
    fi = {c.qualifier: c for c in avail(eq, "food_inspection")}
    assert set(fi) == {"mattilsynet:Z1_TilsynAvtale", "mattilsynet:Z3_TilsynAvtale"}   # Z2 not a registered sub-unit
    c = fi["mattilsynet:Z1_TilsynAvtale"]
    assert c.relation == "subunit" and c.match_basis == "location_subunit_of_organisation_number"
    assert c.value["location_organisation_number"] == SUB_FORUS and c.value["grade"] == "1"
    assert c.value["address"] == "Forusbeen 50, 4035 STAVANGER" and c.confidence == pr.CONFIDENCE
    assert SUB_FORUS in c.note
    # DNB: its own premises (relation self); the moved location is not its sub-unit any more.
    d = avail(dnb, "food_inspection")
    assert [x.qualifier for x in d] == ["mattilsynet:Z4_TilsynAvtale"]
    assert d[0].relation == "self" and d[0].value["grade"] == "3"
    assert dnb.data["public_records"]["dropped"] == {"location_not_a_registered_subunit": 1}


def test_food_inspection_without_subunit_list_trusts_cached_mapping(tmp_path):
    write_cache(tmp_path / "cache")
    ctx = make_ctx(tmp_path)
    eq = company(EQUINOR)                                    # no sub-unit list this run
    run(ctx, [eq])
    fi = {c.qualifier: c for c in avail(eq, "food_inspection")}
    assert set(fi) == {"mattilsynet:Z1_TilsynAvtale", "mattilsynet:Z2_TilsynAvtale"}
    for c in fi.values():
        assert c.relation == "subunit" and c.match_basis == "location_subunit_of_organisation_number_cached"
        assert c.confidence == pr.CONFIDENCE_CACHED_MAPPING
        assert "overordnetEnhet per Enhetsregisteret when the cache was built" in c.note
    # An incomplete sub-unit list also falls back to the cached mapping for locations it does not show.
    ctx2 = make_ctx(tmp_path)
    eq2 = company(EQUINOR, subunits=[SUB_FORUS], complete=False)
    run(ctx2, [eq2])
    rel = {c.qualifier: c.match_basis for c in avail(eq2, "food_inspection")}
    assert rel == {"mattilsynet:Z1_TilsynAvtale": "location_subunit_of_organisation_number",
                   "mattilsynet:Z2_TilsynAvtale": "location_subunit_of_organisation_number_cached"}


def test_evidence_fields(tmp_path):
    built = write_cache(tmp_path / "cache")
    ctx = make_ctx(tmp_path)
    eq = company(EQUINOR, subunits=[SUB_FORUS])
    run(ctx, [eq])
    ev = avail(eq, "tax_incentive_project")[0].evidence[0]
    d = ev.to_dict()
    assert d["source_url"] == SF_URL and d["source_class"] == OFFICIAL_GRANTS
    assert d["content_sha256"] == SHA[SF_URL] and d["retrieved_at"] == built
    assert d["locator"] == "sheet:Endelig!row:50002133"
    assert '"Organisasjonsnr":"923609016"' in d["claim_span"]
    assert '"Prosjekttittel":"Prediktivt lagerstyringssystem"' in d["claim_span"]
    assert d["retrieved_via"].startswith(f"declared cache cache/public_records.jsonl.gz (built {built}")
    assert d["licence"] == "NLOD-2.0-SkatteFUNN" and d["extraction_method"] == "public_records@1:cache"
    rg = next(c for c in avail(eq, "research_grant") if c.qualifier == "forskningsradet:359129").evidence[0].to_dict()
    assert rg["page_url"] == "https://prosjektbanken.forskningsradet.no/project/FORISS/359129"
    assert '"tildelt_belop":"2198000.0"' in rg["claim_span"] and rg["licence"] == "NLOD-2.0-Forskningsradet"
    fe = avail(eq, "food_inspection")[0].evidence[0].to_dict()
    assert fe["source_class"] == pr.OFFICIAL_FOOD_INSPECTIONS == "official_food_inspections"
    assert fe["source_url"] == MT_URL and fe["licence"] == "CC-BY-4.0-Mattilsynet"
    assert '"orgnummer":"973861883"' in fe["claim_span"] and '"total_karakter":"1"' in fe["claim_span"]
    assert all(len(e.claim_span or "") <= 300 for c in eq.claims for e in c.evidence)
    # Source files are listed in the run's snapshot index (bytes not stored) so envelopes cite them.
    assert ctx.snapshots.entry(SHA[SF_URL])["url"] == SF_URL
    assert field_family("food_inspection") == "public_activity"
    assert field_family("tax_incentive_project") == "public_activity"


def test_not_available_and_placeholders(tmp_path):
    built = write_cache(tmp_path / "cache")
    ctx = make_ctx(tmp_path)
    other = company(OTHER)
    run(ctx, [other])
    out = other.modules[pr.MODULE]
    assert out.availability == "not_available"
    assert out.note == (f"checked the declared public-records cache built {built[:10]}: no SkatteFUNN, "
                        "Research Council or food-inspection records")
    fields = sorted(c.field for c in other.claims)
    assert fields == ["food_inspection", "research_grant", "tax_incentive_project"]
    assert all(c.availability == "not_available" and c.value is None for c in other.claims)
    sf = next(c for c in other.claims if c.field == "tax_incentive_project")
    assert [e.source_url for e in sf.evidence] == [SF_URL] and sf.evidence[0].content_sha256 == SHA[SF_URL]
    assert other.data["public_records"]["published"] == {"tax_incentive_project": 0, "research_grant": 0,
                                                         "food_inspection": 0}
    # A company with some records gets explicit not_available labels only for the missing kinds.
    ctx2 = make_ctx(tmp_path)
    dnb = company(DNB, subunits=[])
    run(ctx2, [dnb])
    na = sorted(c.field for c in dnb.claims if c.availability == "not_available")
    assert na == ["research_grant", "tax_incentive_project"]


def test_missing_cache_fails_and_non_exact_is_not_applicable(tmp_path):
    ctx = make_ctx(tmp_path)                      # empty cache dir
    eq, sub = company(EQUINOR), company(SUB_FORUS, status="subunit")
    run(ctx, [eq, sub])
    assert eq.modules[pr.MODULE].availability == "failed"
    assert "cache/public_records.jsonl.gz is missing" in eq.modules[pr.MODULE].note
    assert {c.availability for c in eq.claims} == {"failed"} and len(eq.claims) == 3
    assert sub.modules[pr.MODULE].availability == "not_applicable" and not sub.claims


def test_stale_food_cache_lowers_confidence(tmp_path):
    write_cache(tmp_path / "cache", built_at=datetime.now(timezone.utc) - timedelta(days=10))
    ctx = make_ctx(tmp_path)
    eq = company(EQUINOR, subunits=[SUB_FORUS])
    run(ctx, [eq])
    fi = avail(eq, "food_inspection")[0]
    assert fi.confidence == round(pr.CONFIDENCE - 0.1, 3)
    assert "refresh daily" in fi.note
    assert avail(eq, "tax_incentive_project")[0].confidence == pr.CONFIDENCE


def test_deterministic(tmp_path):
    write_cache(tmp_path / "cache")
    ids = []
    for _ in range(2):
        ctx = make_ctx(tmp_path)
        eq = company(EQUINOR, subunits=[SUB_FORUS, SUB_MOVED])
        run(ctx, [eq])
        ids.append([(c.id, [e.id for e in c.evidence]) for c in eq.claims])
    assert ids[0] == ids[1]


# ----------------------------------------------------------------------------- builder parsing
def _builder():
    spec = importlib.util.spec_from_file_location("build_public_records_cache",
                                                  ROOT / "tools" / "build_public_records_cache.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_builder_parses_sources(tmp_path):
    from collections import Counter

    import openpyxl

    b = _builder()
    universe = {EQUINOR, DNB}
    # SkatteFUNN (new system layout)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Endelig"
    ws.append(["Søknadsdato", "Søknadsnummer", "Prosjektnummer", "Prosjektansvarlig", "Prosjekttittel",
               "Organisasjonsnr", "Godkjent", "Fra-år", "Til-år", "Vedtaksdato", "Populærvitenskapelig sammendrag"])
    ws.append([datetime(2025, 9, 1), 50002133, 90001532, "EQUINOR ASA", "Tittel A", int(EQUINOR), "JA",
               datetime(2025, 3, 1), datetime(2027, 11, 30), datetime(2025, 11, 18), "ord " * 300])
    ws.append([datetime(2025, 9, 2), 50002134, None, "EQUINOR ASA", "Tittel B", int(EQUINOR), None,
               datetime(2025, 1, 1), datetime(2025, 12, 31), None, None])
    ws.append([datetime(2025, 9, 3), 50002135, None, "NOT IN UNIVERSE AS", "Tittel C", 810034882, "NEI",
               datetime(2025, 1, 1), datetime(2025, 12, 31), datetime(2025, 10, 1), None])
    ws.append([datetime(2025, 9, 3), 50002136, None, "BAD AS", "Tittel D", 923609017, "NEI",
               datetime(2025, 1, 1), datetime(2025, 12, 31), datetime(2025, 10, 1), None])
    xp = tmp_path / "260902-webliste-skattefunn.xlsx"
    wb.save(xp)
    st: Counter = Counter()
    rows = list(b.iter_skattefunn(xp, SF_URL, "x", universe, st))
    assert [r["record_id"] for r in rows] == ["skattefunn:soknad:50002133", "skattefunn:soknad:50002134"]
    a, p = rows
    assert a["status"] == "approved" and a["date"] == "2025-09-01" and a["decision_date"] == "2025-11-18"
    assert a["period"] == {"from": 2025, "to": 2027} and a["project_number"] == "90001532"
    assert len(a["summary"]) <= 600 and a["summary_truncated"] and "ord " * 10 in a["summary"]
    assert a["raw_cells"]["Organisasjonsnr"] == EQUINOR and a["raw_cells"]["Prosjekttittel"] == "Tittel A"
    assert a["row_key"] == "sheet:Endelig!row:2"
    assert p["status"] == "no_decision_recorded" and p["decision_date"] is None
    assert st["skattefunn_invalid_orgnr"] == 1

    # Forskningsrådet CSV (a multi-line title shifts the physical line numbers)
    header = ("prosjektnummer,prosjekttittel,soknadsfrist,soknadsdato,prosjektfase,prosjektstart,prosjektslutt,"
              "prosjektansvarlig_type,prosjektansvarlig_navn,organisasjonsnummer,aktivitet,utlysningtittel,"
              "prosjekttype,tildelt_belop,prosjektleder\n")
    body = (f'1,"Linje en\nlinje to",,2011-08-30 00:00:00,Avsluttet,2012,2016,Organisasjon,EQUINOR ASA,{EQUINOR},'
            'PETROMAKS,Utlysning,Prosjektstøtte,4000000.0,Ola Nordmann\n'
            f'2,Avslått,,2012-01-01 00:00:00,Avslag,2012,2013,Organisasjon,EQUINOR ASA,{EQUINOR},X,Y,Z,0.0,Kari\n'
            f'3,Stipend,,2012-01-01 00:00:00,Bevilgning,2012,2013,Person,Kari Nordmann,{EQUINOR},X,Y,Z,10.0,Kari\n'
            '4,Ingen org,,2012-01-01 00:00:00,Bevilgning,2012,2013,Organisasjon,NOS-N,0,X,Y,Z,10.0,Per\n')
    cp = tmp_path / "dataset.csv"
    cp.write_text(header + body, encoding="utf-8")
    st = Counter()
    rows = list(b.iter_nfr(cp, NFR_URL, "y", universe, st))
    assert [r["record_id"] for r in rows] == ["forskningsradet:1", "forskningsradet:2"]
    g, rj = rows
    assert g["amount"] == "4000000.0" and g["currency"] == "NOK" and g["status"] == "completed"
    assert g["row_key"] == "csv:line:2" and rj["row_key"] == "csv:line:4"
    assert g["period"] == {"from": 2012, "to": 2016} and g["programme"] == "PETROMAKS"
    assert rj["amount"] is None and rj["status"] == "rejected"
    assert "Ola Nordmann" not in json.dumps(rows, ensure_ascii=False)          # person fields never stored
    assert st["research_grant_personal_stipend_skipped"] == 1 and st["research_grant_invalid_orgnr"] == 1

    # Mattilsynet CSV: location orgnr mapped to the parent via the brreg map
    mp = tmp_path / "tilsyn.csv"
    mp.write_text("tilsynsobjektid;orgnummer;navn;adrlinje1;adrlinje2;postnr;poststed;tilsynid;sakref;status;dato;"
                  "total_karakter;tilsynsbesoektype\n"
                  f"T1;{SUB_FORUS};Kantine;Forusbeen 50;;4035;STAVANGER;Z1;2024/1;0;05032024;1;0\n"
                  f"T2;{DNB};DNB kantine;Dronning Eufemias gate 30;;0191;OSLO;Z2;2024/2;0;01062023;3;0\n"
                  f"T3;{SUB_MOVED};Slettet;Gate 1;;0001;OSLO;Z3;2024/3;0;01012020;0;0\n", encoding="utf-8")
    orgmap = {SUB_FORUS: {"kind": "underenhet", "parent": EQUINOR, "name": "EQUINOR ASA AVD FORUS"},
              DNB: {"kind": "enhet", "parent": None, "name": "DNB BANK ASA"},
              SUB_MOVED: {"kind": "unknown", "parent": None, "name": None}}
    st = Counter()
    rows = list(b.iter_smilefjes(mp, MT_URL, "z", universe, orgmap, st))
    assert [(r["organisation_number"], r["location_organisation_number"]) for r in rows] == [(EQUINOR, SUB_FORUS),
                                                                                           (DNB, DNB)]
    f = rows[0]
    assert f["date"] == "2024-03-05" and f["grade"] == "1" and f["smiley"] == "smile"
    assert f["address"] == "Forusbeen 50, 4035 STAVANGER" and f["row_key"] == "csv:line:2"
    assert f["raw_cells"]["dato"] == "05032024" and f["record_id"] == "mattilsynet:Z1"
    assert st["food_inspection_unmapped"] == 1
