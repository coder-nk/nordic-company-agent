"""Offline tests for sources/website.py: respx-mocked HTTP through the real BudgetedClient/RunContext.

The SSRF guard resolves hosts before every request; tests pre-seed its host cache so no DNS is used.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
import respx

from nordic_company_agent import http as H
from nordic_company_agent.context import RunConfig, RunContext
from nordic_company_agent.envelope import COMPANY_LINKED_PROFILE, COMPANY_OWNED, COMPANY_SITE, CompanyResult, Evidence
from nordic_company_agent.sources import website

FX = Path(__file__).parent / "fixtures" / "website"
RECORDS = json.loads((FX / "registry_records.json").read_text())
HTML = {"content-type": "text/html; charset=utf-8"}


def fx(prefix: str) -> str:
    return next(FX.glob(f"{prefix}*.html")).read_text()


def make_ctx(tmp_path: Path, cache_rows: list[dict] | None = None, *, live: bool = False,
             max_requests: int = 50) -> RunContext:
    cache = tmp_path / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    if cache_rows is not None:
        (cache / "site_discovery.jsonl").write_text("".join(json.dumps(r) + "\n" for r in cache_rows))
    return RunContext(RunConfig(run_id="t", out_dir=tmp_path / "out", cache_dir=cache, live_discovery=live,
                                max_requests=max_requests))


def run_site(ctx: RunContext, orgnr: str, routes: dict[str, httpx.Response], *, rec: dict | None = None,
             hosts: tuple[str, ...] = ()) -> CompanyResult:
    for h in hosts:
        H._SAFE_HOST_CACHE[h] = True
    c = CompanyResult(orgnr)
    c.data["registry"] = rec if rec is not None else RECORDS.get(orgnr, {})
    c.data["identity"] = {"match_status": "exact"}

    async def go():
        with respx.mock(assert_all_called=False) as router:
            for u, resp in routes.items():
                router.get(u).mock(return_value=resp)
            router.route().mock(return_value=httpx.Response(404, text="not found"))
            await website.run(ctx, c)
        await ctx.client.aclose()

    asyncio.run(go())
    return c


def claims(c: CompanyResult, field: str, availability: str = "available"):
    return [x for x in c.claims if x.field == field and x.availability == availability]


def values(c: CompanyResult, field: str):
    return [x.value for x in claims(c, field)]


def page(title: str, body: str, lang: str = "no", head: str = "") -> str:
    return f"<html lang='{lang}'><head><title>{title}</title>{head}</head><body>{body}</body></html>"


# --------------------------------------------------------------------------- L1 / L2
def test_l1_registry_website_with_redirect(tmp_path):
    ctx = make_ctx(tmp_path)
    c = run_site(ctx, "912342344", {
        "https://www.ekomas.no/": httpx.Response(301, headers={"location": "https://ekomas.no/"}),
        "https://ekomas.no/": httpx.Response(200, text=fx("company_websites__L1_footer"), headers=HTML),
    }, hosts=("www.ekomas.no", "ekomas.no"))
    out = c.modules["website"]
    assert out.availability == "available"
    assert values(c, "official_website") == ["https://ekomas.no/"]
    ver = values(c, "website_verification")[0]
    assert ver["level"] == "L1" and "912 342 344" in ver["proof_span"]
    assert values(c, "contact_email") == ["kontakt@ekomas.no"]
    assert values(c, "website_address") == ["Industriveien 9B 2021 Skedsmokorsket"]
    site = claims(c, "official_website")[0]
    assert site.confidence == 0.97
    ev: Evidence = site.evidence[0]
    assert ev.source_url == "https://ekomas.no/" and ev.source_class == COMPANY_OWNED and ev.licence == COMPANY_SITE
    assert ev.extraction_method == "website@1" and len(ev.content_sha256) == 64 and ev.retrieved_at.endswith("Z")
    assert "912 342 344" in ev.claim_span and len(ev.claim_span) <= 300
    assert ctx.snapshots.entry(ev.content_sha256) is not None
    # budget: robots + 301 + robots + home + contact page <= 5, all counted by the client
    assert ctx.client.used <= 5 and out.requests == ctx.client.used
    d = c.data["website"]
    assert d["level"] == "L1" and d["url"] == "https://ekomas.no/" and d["domain"] == "ekomas.no"
    assert d["emails"] == ["kontakt@ekomas.no"]
    assert d["pages"][0]["url"] == "https://www.ekomas.no/" and d["pages"][0]["final_url"] == "https://ekomas.no/"
    assert d["pages"][0]["redirects"] == ["https://www.ekomas.no/"]
    assert all({"url", "final_url", "status", "sha256", "retrieved_at"} <= set(p) for p in d["pages"] if "error" not in p)


def test_l2_wix_site_without_platform_socials(tmp_path):
    ctx = make_ctx(tmp_path)
    c = run_site(ctx, "879887682", {"https://www.nmg.no/": httpx.Response(200, text=fx("company_websites__L2_wix"),
                                                                          headers=HTML)}, hosts=("www.nmg.no",))
    assert c.modules["website"].availability == "available"
    site = claims(c, "official_website")[0]
    assert site.value == "https://www.nmg.no/" and site.confidence == 0.85
    assert site.match_basis == "registry_website_corroborated"
    assert values(c, "contact_email") == ["post@nmg.no"]
    assert values(c, "social_profile") == []
    assert values(c, "brand_name") == ["New Media Generation"]


def test_cache_lead_fetches_home_and_proof_page_and_feed(tmp_path):
    rows = [{"organisation_number": "917387079", "checked": ["registry_website"], "leads": [{
        "kind": "registry_website", "start": "https://www.mf.no/", "final_url": "https://mf.no/", "final_domain": "mf.no",
        "proof_url": "https://mf.no/kontakt", "proof_page": "contact", "proof_ctx": "Org.nr.: NO [[917 387 079]] MVA",
        "proof_src": "text", "proof_kw": True, "credit_ctx": False, "group_ctx": False, "other_orgnrs": [],
        "title": "Kontakt | MF", "site_name": "", "checked_at": "2026-10-13T14:00:00Z"}]}]
    home = page("MF vitenskapelig høyskole",
                "<h1>MF vitenskapelig høyskole</h1><a href='/kontakt'>Kontakt</a><p>MF vitenskapelig høyskole for "
                "teologi, religion og samfunn tilbyr studier på bachelor-, master- og ph.d.-nivå i Oslo.</p>",
                head="<meta name='description' content='MF vitenskapelig høyskole for teologi, religion og samfunn.'>"
                     "<link rel='alternate' type='application/rss+xml' title='MF » Kommentarer' href='/comments/feed/'>"
                     "<link rel='alternate' type='application/rss+xml' title='MF » Feed' href='https://mf.no/feed/'>")
    feed = """<?xml version="1.0"?><rss version="2.0"><channel><title>MF</title>
<item><title>Ny rektor ved MF</title><link>https://mf.no/nyheter/ny-rektor</link><pubDate>Tue, 06 Oct 2026 10:00:00 +0200</pubDate></item>
<item><title>Ekstern sak</title><link>https://example.org/x</link><pubDate>Tue, 06 Oct 2026 10:00:00 +0200</pubDate></item>
<item><title>Udatert</title><link>https://mf.no/nyheter/udatert</link></item>
<item><title>Studiestart</title><link>https://mf.no/nyheter/studiestart</link><pubDate>Mon, 17 Aug 2026 08:00:00 GMT</pubDate></item>
</channel></rss>"""
    ctx = make_ctx(tmp_path, rows)
    c = run_site(ctx, "917387079", {
        "https://mf.no/": httpx.Response(200, text=home, headers=HTML),
        "https://mf.no/kontakt": httpx.Response(200, text=fx("company_websites__L1_contact_page"), headers=HTML),
        "https://mf.no/feed/": httpx.Response(200, text=feed, headers={"content-type": "application/rss+xml"}),
    }, hosts=("mf.no", "www.mf.no"))
    assert values(c, "official_website") == ["https://mf.no/"]
    assert values(c, "website_verification")[0]["proof_url"] == "https://mf.no/kontakt"
    news = values(c, "news_item")
    assert [n["url"] for n in news] == ["https://mf.no/nyheter/ny-rektor", "https://mf.no/nyheter/studiestart"]
    assert news[0]["published"] == "2026-10-06T08:00:00Z"
    ni = claims(c, "news_item")[0]
    assert ni.effective_at == "2026-10-06" and ni.evidence[0].source_url == "https://mf.no/feed/"
    socials = claims(c, "social_profile")
    assert {s.value["platform"] for s in socials} == {"facebook", "instagram", "linkedin", "tiktok"}
    assert all(s.evidence[0].source_class == COMPANY_LINKED_PROFILE for s in socials)
    assert all(s.evidence[0].source_url == "https://mf.no/kontakt" for s in socials)
    assert values(c, "careers_page") == ["https://mf.no/ledige-stillinger"]
    assert ctx.client.used <= 5
    assert c.data["website"]["news"][0]["title"] == "Ny rektor ved MF"


def test_cache_lead_without_live_orgnr_falls_back_to_ambiguous(tmp_path):
    rows = [{"organisation_number": "912342344", "checked": ["name_joined_no"], "leads": [{
        "kind": "name_joined_no", "start": "ekomx.no", "final_url": "https://ekomx.no/", "final_domain": "ekomx.no",
        "proof_url": "https://ekomx.no/", "proof_page": "home", "proof_ctx": "", "proof_src": "text", "proof_kw": True,
        "credit_ctx": False, "group_ctx": False, "other_orgnrs": [], "title": "", "site_name": "",
        "checked_at": "2026-10-13T14:00:00Z"}]}]
    ctx = make_ctx(tmp_path, rows)
    rec = dict(RECORDS["912342344"])
    rec.pop("hjemmeside")
    body = "<p>" + "Vi selger kaffe og te fra hele verden. " * 12 + "</p>"
    c = run_site(ctx, "912342344", {"https://ekomx.no/": httpx.Response(200, text=page("Ekomx kaffe", body), headers=HTML)},
                 rec=rec, hosts=("ekomx.no",))
    assert c.modules["website"].availability == "ambiguous"
    oc = claims(c, "official_website", "ambiguous")[0]
    assert oc.value is None and "does not print" in oc.note
    assert [x for x in c.claims if x.availability == "available"] == []


# --------------------------------------------------------------------------- never publish
def test_webhuset_parking_redirect_is_not_available(tmp_path):
    ctx = make_ctx(tmp_path)
    c = run_site(ctx, "982182107", {
        "https://www.nyenor.no/": httpx.Response(302, headers={"location": "https://www.webhuset.no/parkering"}),
        "https://www.webhuset.no/parkering": httpx.Response(200, text=fx("company_websites__L0_parked_host"), headers=HTML),
    }, hosts=("www.nyenor.no", "www.webhuset.no"))
    assert c.modules["website"].availability == "not_available"
    assert "parked" in c.modules["website"].note
    assert [x for x in c.claims if x.availability == "available"] == []
    blob = json.dumps([x.value for x in c.claims] + [x.note for x in c.claims], default=str)
    assert "981532848" not in blob and "webhuset-as" not in blob and "90203000" not in blob


def test_hugedomains_for_sale(tmp_path):
    ctx = make_ctx(tmp_path)
    c = run_site(ctx, "984421664", {
        "https://www.hopetravel.com/": httpx.Response(301, headers={"location": "https://www.hugedomains.com/domain_profile.cfm?d=hopetravel.com"}),
        "https://www.hugedomains.com/domain_profile.cfm?d=hopetravel.com": httpx.Response(
            200, text=fx("company_websites__L0_parked_for_sale"), headers=HTML),
    }, hosts=("www.hopetravel.com", "www.hugedomains.com"))
    assert c.modules["website"].availability == "not_available"
    assert [x for x in c.claims if x.availability == "available"] == []


def test_cloudflare_403_is_blocked(tmp_path):
    ctx = make_ctx(tmp_path)
    c = run_site(ctx, "882912272", {"https://www.tent.no/": httpx.Response(403, text=fx("company_websites__L0_cloudflare"),
                                                                            headers=HTML)}, hosts=("www.tent.no",))
    assert c.modules["website"].availability == "blocked"
    assert claims(c, "official_website", "blocked")
    assert ctx.client.used == 2          # robots.txt + home; a bot wall is not retried


def test_robots_disallow_is_blocked(tmp_path):
    ctx = make_ctx(tmp_path)
    c = run_site(ctx, "882912272", {"https://www.tent.no/robots.txt": httpx.Response(
        200, text="User-agent: *\nDisallow: /\n", headers={"content-type": "text/plain"})}, hosts=("www.tent.no",))
    assert c.modules["website"].availability == "blocked"
    assert ctx.client.used == 1


def test_hijacked_cjk_domain(tmp_path):
    ctx = make_ctx(tmp_path)
    c = run_site(ctx, "976939395", {"https://sakarina.com/": httpx.Response(
        200, text=fx("company_websites__L0_hijacked"), headers=HTML)}, hosts=("sakarina.com",))
    assert c.modules["website"].availability == "not_available"
    assert "hijacked" in c.modules["website"].note


def test_sister_company_site_is_ambiguous(tmp_path):
    ctx = make_ctx(tmp_path)
    c = run_site(ctx, "982322553", {"https://www.atticum.no/": httpx.Response(
        200, text=fx("company_websites__L0_sister"), headers=HTML)}, hosts=("www.atticum.no",))
    assert c.modules["website"].availability == "ambiguous"
    oc = claims(c, "official_website", "ambiguous")[0]
    assert oc.value is None and "982574544" in oc.note
    assert [x for x in c.claims if x.availability == "available"] == []


def test_js_redirect_to_other_company_site(tmp_path):
    lesja = page("Lesja Bulldozerlag AS", "<h1>Lesja Bulldozerlag</h1><p>" + "Graving og transport i Lesja. " * 10 +
                 "</p><footer>Lesja Bulldozerlag AS · Org.nr 913 860 349 · Tlf 61 24 30 00</footer>")
    ctx = make_ctx(tmp_path)
    c = run_site(ctx, "929237617", {
        "https://www.lbl.as/": httpx.Response(200, text=(FX / "redirect_js_window_location_reconstructed__929237617.html").read_text(),
                                              headers=HTML),
        "http://www.lesja-bulldozerlag.no/": httpx.Response(200, text=lesja, headers=HTML),
    }, hosts=("www.lbl.as", "www.lesja-bulldozerlag.no"))
    assert c.modules["website"].availability == "ambiguous"
    assert "913860349" in c.modules["website"].note
    assert [x for x in c.claims if x.availability == "available"] == []


def test_multi_company_footer_only_block_facts(tmp_path):
    ctx = make_ctx(tmp_path)
    c = run_site(ctx, "920209319", {"https://www.advokateneiba47.no/": httpx.Response(
        200, text=fx("company_websites__L1_multi"), headers=HTML)}, hosts=("www.advokateneiba47.no",))
    assert c.modules["website"].availability == "available"
    assert values(c, "contact_email") == []          # thomas@ is personal, even though it is the registry e-mail
    assert values(c, "contact_phone") == ["+4745020113"]
    blob = json.dumps([x.value for x in c.claims], default=str)
    for bad in ("tore@okseter.no", "nina@advokat-nba.no", "91841861", "93420277", "post@advokateneiba47.no"):
        assert bad not in blob


def test_sports_club_sponsor_social(tmp_path):
    home = page("Finnsnes IL Fotball", "<h1>Finnsnes IL Fotball</h1><a href='/om-oss'>Om oss</a><p>" +
                "Velkommen til Finnsnes IL Fotball, klubben for alle. " * 6 + "</p>")
    ctx = make_ctx(tmp_path)
    c = run_site(ctx, "979448708", {
        "https://www.fil.no/": httpx.Response(200, text=home, headers=HTML),
        "https://www.fil.no/om-oss": httpx.Response(200, text=fx("company_websites__L1_sports_club"), headers=HTML),
    }, hosts=("www.fil.no",))
    urls = [v["url"] for v in values(c, "social_profile")]
    assert "https://www.facebook.com/FinnsnesIlFotball" in urls
    assert not any("exploenergi" in u for u in urls)
    assert values(c, "contact_phone") == ["+4777842333"]


def test_shared_domain_from_cache_blocks_l2(tmp_path):
    rows = [{"organisation_number": "912342344", "checked": ["registry_website"], "leads": [{
        "kind": "registry_website", "start": "https://www.nmg.no/", "final_url": "https://www.nmg.no/",
        "final_domain": "nmg.no", "proof_url": "https://www.nmg.no/", "proof_page": "home", "proof_ctx": "",
        "proof_src": "text", "proof_kw": True, "credit_ctx": False, "group_ctx": False, "other_orgnrs": [],
        "title": "", "site_name": "", "checked_at": "2026-10-13T14:00:00Z"}]}]
    ctx = make_ctx(tmp_path, rows)
    c = run_site(ctx, "879887682", {"https://www.nmg.no/": httpx.Response(200, text=fx("company_websites__L2_wix"),
                                                                          headers=HTML)}, hosts=("www.nmg.no",))
    assert c.modules["website"].availability == "ambiguous"
    assert "912342344" in c.modules["website"].note


# --------------------------------------------------------------------------- terminal states / budget
def test_no_lead_is_not_available(tmp_path):
    ctx = make_ctx(tmp_path)
    rec = {"navn": "INGEN NETTSIDE AS", "organisasjonsform": {"kode": "AS"}}
    c = run_site(ctx, "912342344", {}, rec=rec)
    assert c.modules["website"].availability == "not_available"
    assert claims(c, "official_website", "not_available")
    assert ctx.client.used == 0


def test_dead_registry_domain(tmp_path, monkeypatch):
    async def no_dns(url: str) -> None:
        raise httpx.ConnectError("DNS resolution failed for www.dead-domain-xyz.no: [Errno -2] Name or service not known")
    monkeypatch.setattr(H, "assert_public_url", no_dns)
    ctx = make_ctx(tmp_path)
    rec = dict(RECORDS["912342344"], hjemmeside="www.dead-domain-xyz.no")
    c = run_site(ctx, "912342344", {}, rec=rec)
    out = c.modules["website"]
    assert out.availability == "not_available" and "resolve" in out.note
    assert ctx.client.used == 0


def test_budget_refusal_is_failed(tmp_path):
    ctx = make_ctx(tmp_path, max_requests=1)
    c = run_site(ctx, "912342344", {"https://www.ekomas.no/": httpx.Response(200, text="x", headers=HTML)},
                 hosts=("www.ekomas.no",))
    out = c.modules["website"]
    assert out.availability == "failed" and "budget" in out.note
    assert ctx.client.used == 0


def test_registry_facebook_hjemmeside_becomes_social_profile(tmp_path):
    ctx = make_ctx(tmp_path)
    rec = {"navn": "BAKERIET PÅ HJØRNET AS", "hjemmeside": "www.facebook.com/bakerietpahjornet",
           "organisasjonsform": {"kode": "AS"}}
    c = CompanyResult("912342344")
    c.data["registry"] = rec
    c.data["registry_evidence"] = Evidence(
        source_url="https://data.brreg.no/enhetsregisteret/api/enheter/912342344", source_class="official_registry",
        source_name="Enhetsregisteret", retrieved_at="2026-10-13T00:00:00Z", content_sha256="0" * 64,
        locator="/organisasjonsnummer", extraction_method="brreg@1")
    asyncio.run(website.run(ctx, c))
    sp = claims(c, "social_profile")
    assert sp and sp[0].value["url"] == "https://www.facebook.com/bakerietpahjornet"
    assert sp[0].evidence[0].locator == "/hjemmeside"
    assert c.modules["website"].availability == "not_available"


def test_live_candidate_dns_then_l1(tmp_path, monkeypatch):
    async def fake_resolves(host: str) -> bool:
        return host == "kaffebrenneriet-test.no"
    monkeypatch.setattr(website, "_resolves", fake_resolves)
    ctx = make_ctx(tmp_path, live=True)
    rec = {"navn": "KAFFEBRENNERIET TEST AS", "organisasjonsform": {"kode": "AS"},
           "epostadresse": "post@kaffebrenneriet-test.no"}
    home = page("Kaffebrenneriet Test", "<p>" + "Vi brenner kaffe i Bergen. " * 10 +
                "</p><footer>Kaffebrenneriet Test AS · Org.nr 912 342 344 · post@kaffebrenneriet-test.no</footer>")
    c = run_site(ctx, "912342344", {"https://kaffebrenneriet-test.no/": httpx.Response(200, text=home, headers=HTML)},
                 rec=rec, hosts=("kaffebrenneriet-test.no",))
    assert c.modules["website"].availability == "available"
    assert values(c, "official_website") == ["https://kaffebrenneriet-test.no/"]
    assert values(c, "contact_email") == ["post@kaffebrenneriet-test.no"]
    assert ctx.planner.spent["discovery"] >= 2


def test_live_discovery_disabled(tmp_path, monkeypatch):
    called = []

    async def fake_resolves(host: str) -> bool:
        called.append(host)
        return True
    monkeypatch.setattr(website, "_resolves", fake_resolves)
    ctx = make_ctx(tmp_path, live=False)
    rec = {"navn": "KAFFEBRENNERIET TEST AS", "epostadresse": "post@kaffebrenneriet-test.no"}
    c = run_site(ctx, "912342344", {}, rec=rec)
    assert called == [] and c.modules["website"].availability == "not_available"


def test_deterministic_claims(tmp_path):
    def once(sub: str):
        ctx = make_ctx(tmp_path / sub)
        c = run_site(ctx, "920209319", {"https://www.advokateneiba47.no/": httpx.Response(
            200, text=fx("company_websites__L1_multi"), headers=HTML)}, hosts=("www.advokateneiba47.no",))
        return [(x.key, json.dumps(x.value, sort_keys=True, default=str), [e.claim_span for e in x.evidence])
                for x in c.claims]
    assert once("a") == once("b")


def test_never_raises(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(website, "_leads", boom)
    ctx = make_ctx(tmp_path)
    c = CompanyResult("912342344")
    asyncio.run(website.run(ctx, c))
    assert c.modules["website"].availability == "failed" and c.errors


@pytest.mark.parametrize("data,expected", [
    (b"<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'><entry><title>A</title>"
     b"<link rel='alternate' href='https://firma.no/a'/><published>2026-09-01T10:00:00Z</published></entry></feed>",
     ["https://firma.no/a"]),
    (b"<?xml version='1.0'?><!DOCTYPE x [<!ENTITY a 'b'>]><rss><channel><item><title>&a;</title>"
     b"<link>https://firma.no/b</link><pubDate>Tue, 06 Oct 2026 10:00:00 GMT</pubDate></item></channel></rss>", []),
    (b"not xml", []),
])
def test_parse_feed(data, expected):
    items = website.parse_feed(data, "https://firma.no/feed/", lambda u: "firma.no" in u)
    assert [i["url"] for i in items] == expected


def test_offsite_subpage_redirect_ignored(tmp_path):
    home = page("Kaffebar Test", "<h1>Kaffebar Test</h1><a href='/kontakt'>Kontakt</a><p>" + "Kaffe og kaker. " * 20 +
                "</p><footer>Kaffebar Test AS · Org.nr 912 342 344</footer>")
    other = page("Booking", "<p>Booking platform</p><a href='tel:22334455'>22 33 44 55</a><footer>Org.nr 920 209 319</footer>")
    ctx = make_ctx(tmp_path)
    rec = {"navn": "KAFFEBAR TEST AS", "hjemmeside": "www.kaffebartest.no", "organisasjonsform": {"kode": "AS"}}
    c = run_site(ctx, "912342344", {
        "https://www.kaffebartest.no/": httpx.Response(200, text=home, headers=HTML),
        "https://www.kaffebartest.no/kontakt": httpx.Response(302, headers={"location": "https://booking.example.com/x"}),
        "https://booking.example.com/x": httpx.Response(200, text=other, headers=HTML),
    }, rec=rec, hosts=("www.kaffebartest.no", "booking.example.com"))
    v = c.data["website"]
    assert v["level"] == "L1" and v["other_orgnrs"] == []
    assert values(c, "contact_phone") == []
