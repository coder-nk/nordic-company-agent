"""Offline tests for sources/jobs.py (declared NAV cache in a temp dir + respx-mocked live feed)."""

from __future__ import annotations

import asyncio
import gzip
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import respx

from nordic_company_agent.context import RunConfig, RunContext
from nordic_company_agent.envelope import NAV_TERMS, OFFICIAL_JOBS_FEED, CompanyResult, utc_iso
from nordic_company_agent.sources import jobs

FIX = Path(__file__).parent / "fixtures" / "external"
U1, U2, U3, U4 = ("11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222",
                  "33333333-3333-4333-8333-333333333333", "44444444-4444-4444-8444-444444444444")
U5, U6 = "55555555-5555-4555-8555-555555555555", "66666666-6666-4666-8666-666666666666"
TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.c2lnbmF0dXJl"
CURSOR_URL = f"{jobs.NAV}/api/v1/feed/cursor-page"


def row(uuid, org, emp, *, kind="underenhet", expires="2099-01-01T00:00:00+01:00", title="Ingeniør",
        modified="2026-10-12T08:00:00.000000+02:00"):
    return {"organisation_number": org, "employer_organisation_number": emp, "employer_kind": kind,
            "employer_registry_name": "EQUINOR ASA AVD FORUS" if kind == "underenhet" else None,
            "employer_name": "Equinor ASA", "uuid": uuid, "title": title, "url": jobs.public_url(uuid),
            "published": "2026-10-01T10:00:00+02:00", "expires": expires, "updated": "2026-10-02T10:00:00+02:00",
            "location": "STAVANGER", "positions": 1, "status": "ACTIVE", "feed_modified": modified,
            "checked_at": "2026-10-12T12:00:00Z", "feed_entry_url": jobs.entry_url(uuid)}


def write_cache(cache: Path, built_at: datetime, with_cursor: bool = True) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    rows = [row(U1, "923609016", "973861883"), row(U2, "923609016", "923609016", kind="enhet"),
            row(U3, "923609016", "973861883", expires="2020-01-01T00:00:00+01:00"),
            row(U4, "984851006", "984851006", kind="enhet")]
    with gzip.open(cache / "nav_jobs.jsonl.gz", "wt", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    meta = {"built_at": utc_iso(built_at), "active_ads": len(rows), "companies": 2,
            "feed_cursor": ({"page_id": "cursor-page", "page_url": CURSOR_URL,
                             "last_item_modified": "2026-10-12T08:00:00+02:00"} if with_cursor else {})}
    (cache / "nav_jobs.meta.json").write_text(json.dumps(meta))


def make_ctx(tmp_path: Path, max_requests: int = 30) -> RunContext:
    ctx = RunContext(RunConfig(run_id="t", out_dir=tmp_path / "out", max_requests=max_requests,
                               cache_dir=tmp_path / "cache"))
    ctx.planner.set_reservations({"batch_external": max_requests})
    return ctx


def company(orgnr: str, name: str, subunits=None) -> CompanyResult:
    c = CompanyResult(orgnr)
    c.data["identity"] = {"match_status": "exact"}
    c.data["registry"] = {"organisasjonsnummer": orgnr, "navn": name}
    if subunits is not None:
        c.data["subunits"] = subunits
    return c


def run(ctx, cos):
    async def go():
        try:
            await jobs.run_batch(ctx, cos)
        finally:
            await ctx.client.aclose()
    asyncio.run(go())


def mock_live(router, token_status=200):
    tok = router.get(jobs.TOKEN_URL).mock(return_value=httpx.Response(
        token_status, text=f"Current public token for Nav Job Vacancy Feed:\n{TOKEN}\n"))
    feed = router.get(CURSOR_URL).mock(return_value=httpx.Response(
        200, content=(FIX / "nav_feed_page.json").read_bytes(), headers={"content-type": "application/json"}))
    entry = router.get(jobs.entry_url(U5)).mock(return_value=httpx.Response(
        200, content=(FIX / "nav_feedentry_new.json").read_bytes(), headers={"content-type": "application/json"}))
    other = router.get(jobs.entry_url(U6)).mock(return_value=httpx.Response(500))
    return tok, feed, entry, other


def postings(c):
    return {cl.qualifier: cl for cl in c.claims if cl.field == "job_posting"}


def test_cache_plus_live_feed(tmp_path):
    write_cache(tmp_path / "cache", datetime.now(timezone.utc) - timedelta(days=1))
    eq = company("923609016", "EQUINOR ASA", [{"organisasjonsnummer": "973861883", "navn": "EQUINOR ASA AVD FORUS"}])
    dnb = company("984851006", "DNB BANK ASA")
    sandnes = company("810034882", "SANDNES ELEKTRISKE AS", [])
    ctx = make_ctx(tmp_path)
    with respx.mock(assert_all_called=False) as router:
        tok, feed, entry, other = mock_live(router)
        run(ctx, [eq, dnb, sandnes])
        assert (tok.call_count, feed.call_count, entry.call_count, other.call_count) == (1, 1, 1, 0)
        assert feed.calls[0].request.headers["authorization"] == f"Bearer {TOKEN}"
    assert ctx.client.used == 3

    p = postings(eq)
    assert set(p) == {f"nav:{U1}", f"nav:{U5}"}          # U2 became INACTIVE, U3 expired
    sub = p[f"nav:{U1}"]
    assert sub.relation == "self" and sub.match_basis == "employer_subunit_of_organisation_number"
    assert "underenhet) 973861883" in sub.note and "declared NAV cache" in sub.note
    ev = sub.evidence[0]
    assert ev.source_url == jobs.entry_url(U1) and ev.page_url == jobs.public_url(U1)
    assert ev.retrieved_via.startswith("declared cache cache/nav_jobs.jsonl.gz")
    assert ev.source_class == OFFICIAL_JOBS_FEED and ev.licence == NAV_TERMS and ev.content_sha256
    assert any(e.source_url == CURSOR_URL for e in sub.evidence)       # still ACTIVE in today's feed
    new = p[f"nav:{U5}"]
    assert new.value == {"title": "Senior ingeniør, havvind", "url": jobs.public_url(U5),
                         "published": "2026-10-13T09:00:00+02:00", "expires": "2099-11-12T00:00:00+01:00",
                         "employer_name": "Equinor ASA", "employer_organisation_number": "923609016",
                         "location": "STAVANGER", "positions": 2}
    assert new.match_basis == "employer_organisation_number_exact" and new.note is None
    assert new.evidence[0].source_url == jobs.entry_url(U5) and new.evidence[0].locator == "/ad_content/employer/orgnr"
    assert eq.first_value("active_job_count") == 2 and eq.first_value("hiring_status") == "hiring"
    assert eq.get("active_job_count")[0].confidence == 0.9
    assert eq.data["jobs"]["removed_inactive"] == [U2] and eq.data["jobs"]["expired"] == [U3]
    assert eq.modules["jobs"].availability == "available"

    assert set(postings(dnb)) == {f"nav:{U4}"} and dnb.first_value("active_job_count") == 1

    assert not postings(sandnes)
    assert sandnes.first_value("active_job_count") == 0
    assert sandnes.first_value("hiring_status") == "no_active_ads_on_nav"
    assert sandnes.modules["jobs"].availability == "not_available"

    # Contact persons never reach the snapshot store.
    for f in (tmp_path / "out" / "snapshots").glob("*.gz"):
        assert b"kari.nordmann" not in gzip.decompress(f.read_bytes())
    assert any(v.get("redacted") for v in ctx.snapshots.index.values())


def test_stale_cache_without_live_feed(tmp_path):
    write_cache(tmp_path / "cache", datetime.now(timezone.utc) - timedelta(days=10))
    eq = company("923609016", "EQUINOR ASA", [{"organisasjonsnummer": "973861883", "navn": "x"}])
    ctx = make_ctx(tmp_path)
    with respx.mock(assert_all_called=False) as router:
        mock_live(router, token_status=503)
        run(ctx, [eq])
    assert set(postings(eq)) == {f"nav:{U1}", f"nav:{U2}"}   # U3 expired; no live INACTIVE info
    cnt = eq.get("active_job_count")[0]
    assert cnt.value == 2 and cnt.confidence == 0.6
    assert "could not be paged to now" in cnt.note


def test_very_old_cache_without_live_feed_publishes_nothing(tmp_path):
    write_cache(tmp_path / "cache", datetime.now(timezone.utc) - timedelta(days=30))
    eq = company("923609016", "EQUINOR ASA", [{"organisasjonsnummer": "973861883", "navn": "x"}])
    ctx = make_ctx(tmp_path)
    with respx.mock(assert_all_called=False) as router:
        mock_live(router, token_status=503)
        run(ctx, [eq])
    assert not eq.claims
    assert eq.modules["jobs"].availability == "failed" and "too old" in eq.modules["jobs"].note


def test_cache_mapping_used_when_subunits_unknown(tmp_path):
    write_cache(tmp_path / "cache", datetime.now(timezone.utc) - timedelta(days=1))
    eq = company("923609016", "EQUINOR ASA")   # no company.data["subunits"]
    ctx = make_ctx(tmp_path)
    with respx.mock(assert_all_called=False) as router:
        mock_live(router)
        run(ctx, [eq])
    p = postings(eq)
    assert f"nav:{U1}" in p and "overordnetEnhet" in p[f"nav:{U1}"].note
    assert p[f"nav:{U1}"].confidence <= 0.8


def test_no_cache_and_no_feed_is_failed(tmp_path):
    eq = company("923609016", "EQUINOR ASA")
    ctx = make_ctx(tmp_path)
    with respx.mock(assert_all_called=False) as router:
        router.get(jobs.TOKEN_URL).mock(return_value=httpx.Response(503))
        run(ctx, [eq])
    assert eq.modules["jobs"].availability == "failed"
    assert not eq.get("active_job_count") and not eq.get("hiring_status")


def test_budget_denied_uses_cache_only(tmp_path):
    write_cache(tmp_path / "cache", datetime.now(timezone.utc) - timedelta(hours=6))
    eq = company("923609016", "EQUINOR ASA", [{"organisasjonsnummer": "973861883", "navn": "x"}])
    ctx = make_ctx(tmp_path, max_requests=0)
    run(ctx, [eq])
    assert ctx.client.used == 0
    cnt = eq.get("active_job_count")[0]
    assert cnt.value == 2 and cnt.confidence == 0.8 and "budget" in cnt.note


def test_name_normalisation():
    assert jobs.norm_name("Equinor Asa") == jobs.norm_name("EQUINOR ASA") == "equinor"
    assert jobs.norm_name("Tårnstua As") == "tårnstua"
