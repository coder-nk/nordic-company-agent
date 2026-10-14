"""L3d: a registry-filed website the gate cannot verify as exclusively this entity's is published as the company's
declared website (URL only, relation labelled) when the live site carries the company's own name."""

from __future__ import annotations

import asyncio

import httpx
import respx

from nordic_company_agent import http as H
from nordic_company_agent.contact_sharing import finalize_declared, label_by_website
from nordic_company_agent.envelope import Claim, CompanyResult, Evidence
from nordic_company_agent.sources import website
from nordic_company_agent.web import verify as V

from test_website_module import FX, HTML, RECORDS, claims, fx, make_ctx, page

REG_EV = Evidence(source_url="https://data.brreg.no/enhetsregisteret/api/enheter/{}", source_class="official_registry",
                  source_name="Brønnøysundregistrene, Enhetsregisteret", retrieved_at="2026-10-14T06:00:00Z",
                  content_sha256="0" * 64, locator="", extraction_method="registry@1")


def run_declared(ctx, orgnr: str, routes: dict[str, httpx.Response], rec: dict, hosts: tuple[str, ...]) -> CompanyResult:
    for h in hosts:
        H._SAFE_HOST_CACHE[h] = True
    c = CompanyResult(orgnr)
    c.data["registry"] = rec
    c.data["identity"] = {"match_status": "exact"}
    c.data["registry_evidence"] = Evidence(**{**REG_EV.__dict__, "source_url": REG_EV.source_url.format(orgnr)})

    async def go():
        with respx.mock(assert_all_called=False) as router:
            for u, resp in routes.items():
                router.get(u).mock(return_value=resp)
            router.route().mock(return_value=httpx.Response(404, text="not found"))
            await website.run(ctx, c)
        await ctx.client.aclose()

    asyncio.run(go())
    return c


def site_claims(c: CompanyResult) -> list[Claim]:
    return [x for x in c.claims if x.availability == "available" and x.field != "official_website"
            and x.field != "website_verification"]


ATTICUM_GROUP = page("Atticum — Rådgivning og digitale løsninger", "<h1>Atticum</h1><p>" +
                     "Rådgivning, nettløsninger og drift for bedrifter. " * 8 +
                     "</p><p>Atticum Nettløsninger AS utvikler nettsider og apper.</p>"
                     "<footer>Atticum AS · Org.nr 982 574 544 · Storgata 1, 0155 Oslo</footer>")


def test_sister_site_filed_by_the_company_is_declared_group_site(tmp_path):
    ctx = make_ctx(tmp_path)
    c = run_declared(ctx, "982322553", {"https://www.atticum.no/": httpx.Response(200, text=ATTICUM_GROUP, headers=HTML)},
                     RECORDS["982322553"], ("www.atticum.no",))
    site = claims(c, "official_website")
    assert len(site) == 1
    s = site[0]
    assert s.value == "https://www.atticum.no/"
    assert s.method.endswith(":L3d") and s.confidence == V.DECLARED_CONFIDENCE
    assert s.relation == "group_member"
    assert "982574544" in s.note and "declared website" in s.note
    assert [e.locator for e in s.evidence][0] == "/hjemmeside"           # the company's own filing is the evidence
    ver = claims(c, "website_verification")[0]
    assert ver.value["level"] == "L3d"
    assert site_claims(c) == []                                          # no facts from an unverified site
    assert c.modules["website"].availability == "available"
    assert c.data["website"]["state"] == "ambiguous"                     # the gate verdict is unchanged


def test_sister_site_that_never_names_the_company_is_not_declared(tmp_path):
    c = run_declared(make_ctx(tmp_path), "982322553", {"https://www.atticum.no/": httpx.Response(
        200, text=fx("company_websites__L0_sister"), headers=HTML)}, RECORDS["982322553"], ("www.atticum.no",))
    assert claims(c, "official_website") == []


def test_group_root_owns_its_group_site(tmp_path):
    ctx = make_ctx(tmp_path)
    c = run_declared(ctx, "982322553", {"https://www.atticum.no/": httpx.Response(200, text=ATTICUM_GROUP, headers=HTML)},
                     RECORDS["982322553"], ("www.atticum.no",))
    c.add([Claim(field="group_subsidiary", value="982574544", evidence=[REG_EV], qualifier="982574544",
                 relation="related")])
    assert finalize_declared(c) == "self"
    s = claims(c, "official_website")[0]
    assert s.relation == "self" and "heads its registered group (1 subsidiary)" in s.note
    c2 = run_declared(make_ctx(tmp_path / "b"), "982322553", {"https://www.atticum.no/": httpx.Response(
        200, text=ATTICUM_GROUP, headers=HTML)}, RECORDS["982322553"], ("www.atticum.no",))
    c2.add([Claim(field="group_subsidiary", value="982574544", evidence=[REG_EV], qualifier="982574544",
                  relation="related"),
            Claim(field="parent_company", value="999999999", evidence=[REG_EV], relation="parent")])
    label_by_website(c2)
    assert claims(c2, "official_website")[0].relation == "group_member"


def test_chain_domain_without_the_company_name_stays_ambiguous(tmp_path):
    rec = {"navn": "KJØKKENDRIFT NO 1 RYGGE AS", "organisasjonsform": {"kode": "AS"}, "hjemmeside": "www.kitchn.no"}
    home = page("Kitch'n – alt til kjøkkenet", "<h1>Kitch'n</h1><p>" + "Kjøkkenutstyr og gaver i over 70 butikker. " * 8 +
                "</p><footer>Kitch'n AS · Org.nr 976 711 556</footer>")
    c = run_declared(make_ctx(tmp_path), "919694580", {"https://www.kitchn.no/": httpx.Response(200, text=home, headers=HTML)},
                     rec, ("www.kitchn.no",))
    assert claims(c, "official_website") == []
    assert claims(c, "official_website", "ambiguous")
    assert "declared" not in c.data["website"]


def test_filed_domain_redirecting_to_another_company_stays_ambiguous(tmp_path):
    lesja = page("Lesja Bulldozerlag AS", "<h1>Lesja Bulldozerlag</h1><p>" + "Graving og transport i Lesja. " * 10 +
                 "</p><footer>Lesja Bulldozerlag AS · Org.nr 913 860 349 · Tlf 61 24 30 00</footer>")
    c = run_declared(make_ctx(tmp_path), "929237617", {
        "https://www.lbl.as/": httpx.Response(200, text=(FX / "redirect_js_window_location_reconstructed__929237617.html")
                                              .read_text(), headers=HTML),
        "http://www.lesja-bulldozerlag.no/": httpx.Response(200, text=lesja, headers=HTML),
    }, RECORDS["929237617"], ("www.lbl.as", "www.lesja-bulldozerlag.no"))
    assert [x for x in c.claims if x.availability == "available"] == []


def test_namesake_printing_its_own_number_is_never_declared(tmp_path):
    rec = {"navn": "TØMRER HANSEN AS", "organisasjonsform": {"kode": "AS"}, "hjemmeside": "www.tomrerhansen.no"}
    home = page("Tømrer Hansen", "<h1>Tømrer Hansen</h1><p>" + "Vi bygger hus og hytter på Østlandet. " * 8 +
                "</p><footer>Tømrer Hansen AS · Org.nr 982 574 544 · Storgata 1, 0155 Oslo</footer>")
    c = run_declared(make_ctx(tmp_path), "912342344", {"https://www.tomrerhansen.no/": httpx.Response(
        200, text=home, headers=HTML)}, rec, ("www.tomrerhansen.no",))
    assert claims(c, "official_website") == []
    assert "declared" not in c.data["website"]


def test_parked_filed_domain_is_not_declared(tmp_path):
    rec = {"navn": "PARKERT FIRMA AS", "organisasjonsform": {"kode": "AS"}, "hjemmeside": "www.parkertfirma.no"}
    home = page("parkertfirma.no is for sale", "<h1>This domain is for sale</h1><p>Buy this domain today.</p>")
    c = run_declared(make_ctx(tmp_path), "912342344", {"https://www.parkertfirma.no/": httpx.Response(
        200, text=home, headers=HTML)}, rec, ("www.parkertfirma.no",))
    assert claims(c, "official_website") == []


def test_declared_alignment():
    prof = V.name_profile("WORK SYSTEM NORWAY AS", "AS")
    assert V.declared_alignment("worksystem.no", prof)
    assert V.declared_alignment("sildajazz.no", V.name_profile("STIFTELSEN SILDAJAZZEN", "STI"))
    assert V.declared_alignment("afgruppen.no", V.name_profile("AF GRUPPEN ASA", "ASA"))
    assert V.declared_alignment("tgs.com", V.name_profile("TGS ASA", "ASA"))
    assert not V.declared_alignment("vestbo.no", V.name_profile("SIVLESGATE 14 AS", "AS"))
    assert not V.declared_alignment("kitchn.no", V.name_profile("KJØKKENDRIFT NO 1 RYGGE AS", "AS"))
    assert not V.declared_alignment("bate.no", V.name_profile("SAMEIET ST OLAV", "SAM"))
    # a place word alone never aligns a domain
    assert not V.declared_alignment("narvik.no", V.name_profile("NARVIK GOLFKLUBB", "FLI"), places={"narvik"})


def test_own_name_filed_domain_forwarding_to_current_site(tmp_path):
    rec = {"navn": "HJELPEMIDDELSPESIALISTEN AS", "organisasjonsform": {"kode": "AS"},
           "hjemmeside": "www.hjelpemiddelspesialisten.no"}
    home = page("HM Spes – hjelpemidler", "<h1>HM Spes</h1><p>" + "Hjelpemidler for hverdagen, levert over hele landet. " * 8
                + "</p><footer>HM Spes er en del av Hjelpemiddelspesialisten AS</footer>")
    c = run_declared(make_ctx(tmp_path), "979299753", {
        "https://www.hjelpemiddelspesialisten.no/": httpx.Response(301, headers={"location": "https://hm-spes.no/"}),
        "https://hm-spes.no/": httpx.Response(200, text=home, headers=HTML),
    }, rec, ("www.hjelpemiddelspesialisten.no", "hm-spes.no"))
    s = claims(c, "official_website")
    assert [x.value for x in s] == ["https://hm-spes.no/"]
    assert "hjelpemiddelspesialisten.no, which carries the company name, forwards to it" in s[0].note
    assert s[0].relation == "self"


def test_declared_site_publishes_only_own_name_socials(tmp_path):
    rec = {"navn": "HAIKJEFTEN AS", "organisasjonsform": {"kode": "AS"}, "hjemmeside": "www.haikjeften.no"}
    home = page("Haikjeften – ferske fiskeretter", "<h1>Haikjeften</h1><p>" + "Fiskerestaurant på brygga. " * 10 +
                "</p><a href='https://www.facebook.com/haikjeften'>Facebook</a>"
                "<a href='https://www.instagram.com/haikjeften/'>Instagram</a>"
                "<a href='https://www.facebook.com/visitbergen'>Visit Bergen</a>"
                "<a href='mailto:post@haikjeften.no'>post@haikjeften.no</a>"
                "<footer>Haikjeften AS · Bryggen 1, 5003 Bergen · Drives av Sjømat Gruppen AS, org.nr 982 574 544</footer>")
    c = run_declared(make_ctx(tmp_path), "922532184", {"https://www.haikjeften.no/": httpx.Response(
        200, text=home, headers=HTML)}, rec, ("www.haikjeften.no",))
    site = claims(c, "official_website")
    assert len(site) == 1 and site[0].method.endswith(":L3d")
    urls = sorted(x.value["url"] for x in claims(c, "social_profile"))
    assert urls == ["https://www.facebook.com/haikjeften", "https://www.instagram.com/haikjeften"]
    assert all(x.relation == site[0].relation and x.evidence[0].claim_span for x in claims(c, "social_profile"))
    assert claims(c, "contact_email") == []                    # nothing else from an unverified site


def test_group_site_social_needs_every_name_word():
    prof = V.name_profile("PEAB BYGG AS", "AS")
    assert not all(w in "peab" for w in ["peab", "bygg"])
    assert V.social_handle_ok("afgruppen", V.name_profile("AF GRUPPEN ASA", "ASA"), None, "ASA")
    assert V.social_handle_ok("af_gruppen", V.name_profile("AF GRUPPEN ASA", "ASA"), None, "ASA")
    assert not V.social_handle_ok("gullsmedm", V.name_profile("GULLSMED MARTINSEN AS", "AS"), None, "AS")
    assert prof.core == ["peab", "bygg"]


def test_holding_entity_on_its_brand_site_is_group_member(tmp_path):
    rec = {"navn": "HOBBY BOAT EIENDOM AS", "organisasjonsform": {"kode": "AS"}, "hjemmeside": "hobbyboat.no"}
    home = page("Hobby Boat – båter for alle", "<h1>Hobby Boat</h1><p>" + "Vi bygger båter i Norge siden 1975. " * 8 +
                "</p>")
    c = run_declared(make_ctx(tmp_path), "899220412", {"https://hobbyboat.no/": httpx.Response(200, text=home, headers=HTML)},
                     rec, ("hobbyboat.no",))
    s = claims(c, "official_website")
    assert len(s) == 1 and s[0].relation == "group_member"


def test_shared_registry_phone_note_is_not_a_group_signal():
    assert not V.GROUPISH_RX.search("registered website with the company name, but no registry corroboration (phone, "
                                    "e-mail, address, legal name) not shared with other organisation numbers (phone "
                                    "92449337 also filed by 996736415)")
    assert V.GROUPISH_RX.search("group site: www.tyr.no is also filed by sister entity TYR AS (999999999)")


def _one(tmp_path, orgnr, rec, url, html_text):
    host = url.split("/")[2]
    return run_declared(make_ctx(tmp_path), orgnr, {url: httpx.Response(200, text=html_text, headers=HTML)}, rec, (host,))


def test_dead_site_is_never_declared(tmp_path):
    rec = {"navn": "PHYSIOTECH AS", "organisasjonsform": {"kode": "AS"}, "hjemmeside": "physiotech.no"}
    c = _one(tmp_path, "888489142", rec, "https://physiotech.no/",
             page("Physiotech", "<p>Dette nettstedet er lagt ned. Ta kontakt: eb@physiotech.no</p>"))
    assert claims(c, "official_website") == []


def test_forwarding_to_a_site_that_never_names_the_company(tmp_path):
    rec = {"navn": "33KONSEPT AS", "organisasjonsform": {"kode": "AS"}, "hjemmeside": "33konsept.no"}
    gagn = page("Gagn – rådgivning", "<h1>Gagn</h1><p>" + "Vi hjelper virksomheter med strategi og ledelse. " * 8 + "</p>")
    c = run_declared(make_ctx(tmp_path), "933059235", {
        "https://33konsept.no/": httpx.Response(301, headers={"location": "https://www.gagn.no/"}),
        "https://www.gagn.no/": httpx.Response(200, text=gagn, headers=HTML),
    }, rec, ("33konsept.no", "www.gagn.no"))
    assert claims(c, "official_website") == []


def test_affiliate_site_printing_another_number_without_the_company_name(tmp_path):
    rec = {"navn": "ERSTAD & LEKVEN HELSE AS", "organisasjonsform": {"kode": "AS"}, "hjemmeside": "www.erstad-lekven.no"}
    html_text = page("Erstad & Lekven", "<h1>Erstad & Lekven</h1><p>" + "Vi utvikler eiendom i Bergen. " * 8 +
                     "</p><footer>© Erstad & Lekven Utbygging AS · Org.nr 974 279 363</footer>")
    c = _one(tmp_path, "880023772", rec, "https://www.erstad-lekven.no/", html_text)
    assert claims(c, "official_website") == []


def test_site_printing_its_own_number_is_self_despite_a_cofiler(tmp_path):
    rec = {"navn": "KAN RENHOLD OSLO AS", "organisasjonsform": {"kode": "AS"}, "hjemmeside": "kanrenhold.no"}
    html_text = page("Kan Renhold", "<h1>Kan Renhold</h1><p>" + "Renhold for bedrifter i Oslo. " * 8 +
                     "</p><footer>Kan renhold Oslo as · Org.nr 977 040 892</footer>")
    d = V.declared_site(_verdict(html_text, rec, "977040892", "https://kanrenhold.no/", shared=["999999999"]),
                        "https://kanrenhold.no/")
    assert d is not None and d["group"] is False


def test_copyright_owner_is_this_company_means_self(tmp_path):
    rec = {"navn": "WHITSON AS", "organisasjonsform": {"kode": "AS"}, "hjemmeside": "whitson.com"}
    html_text = page("whitson – PVT software", "<h1>whitson</h1><p>" + "Phase behavior software for engineers. " * 8 +
                     "</p><p>Our US affiliate Whitson USA LLC serves the Americas.</p>"
                     "<footer>Copyright © Whitson AS, Vegamot 8 A, Trondheim</footer>")
    d = V.declared_site(_verdict(html_text, rec, "948883422", "https://whitson.com/"), "https://whitson.com/")
    assert d is not None and d["group"] is False


def _verdict(html_text, rec, target, url, shared=()):
    p = V.Page(url=url, final_url=url, status=200, content_type="text/html", html=html_text, kind="home",
               retrieved_at="2026-10-14T00:00:00Z", sha256="0" * 64)
    v = V.verify([p], rec, target, lead_kind="registry_website", start_url=url, shared_orgnrs=shared)
    if v.level != "ambiguous":
        v.level = "ambiguous"                   # force the declared path for the label check
    return v
