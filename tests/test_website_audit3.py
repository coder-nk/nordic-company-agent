"""Regression tests from the third adversarial audit (out/audit300) and its structural precision policies A-G:
minimal HTML reproductions of the run's own snapshots."""

from __future__ import annotations

from nordic_company_agent.web import verify as V


def pg(html: str, url: str, kind: str = "home", final: str | None = None, redirects=()) -> V.Page:
    return V.Page(kind, url, final or url, 200, html, "sha-" + kind + url, "2026-10-14T00:00:00Z", list(redirects))


def doc(title: str, body: str, head: str = "") -> str:
    filler = "<p>" + "Vi leverer tjenester av høy kvalitet til kunder i hele landet. " * 6 + "</p>"
    return f"<html lang='no'><head><title>{title}</title>{head}</head><body>{filler}{body}</body></html>"


def run(pages, rec, orgnr, *, lead="registry_website", start=None, shared=(), contacts=None):
    v = V.verify(pages, rec, orgnr, lead_kind=lead, start_url=start or pages[0].url, shared_orgnrs=shared,
                 shared_contacts=contacts)
    return v, V.collect_facts(v, rec)


def vals(facts, field):
    return [f.value for f in facts if f.field == field]


# --------------------------------------------------------------------------- WRONG / DOUBTFUL websites
def test_lk_systems_swedish_group_market_site():
    home = pg(doc("LK Systems – Vannbåren varme", "<p>LK Group er et familieeid konsern.</p>"
                  "<a href='https://www.lksystems.se/no/'>Sverige</a><a href='https://www.lksystems.fi/fi/'>Suomi</a>"
                  "<a href='/no/personvernerklaring/'>Personvern</a><footer><h4>LK Systems AS</h4> info@lksystems.no</footer>"),
              "https://www.lksystems.no/no/")
    priv = pg(doc("Personvernerklæring", "<p>Behandlingsansvarlig Lagerstedt & Krantz AB, organisasjonsnummer "
                  "556507-9844</p><footer><h4>LK Systems AS</h4> info@lksystems.no</footer>"),
              "https://www.lksystems.no/no/personvernerklaring/", "privacy")
    rec = {"navn": "LK SYSTEMS AS", "organisasjonsform": {"kode": "AS"}, "epostadresse": "info@lksystems.no",
           "hjemmeside": "www.lksystems.no"}
    v, facts = run([home, priv], rec, "937627696", start="https://www.lksystems.no/")
    assert v.level == "ambiguous" and facts == []
    assert any("foreign" in s or "same brand" in s for s in v.group_signals)


def test_today_mobility_filed_language_path_and_country_offices():
    html = doc("Today Mobility", "<p>KONTAKT Sverige Pipers väg 28 Solna, 170 73 +46 775 58 33 33 info@todaymobility.se "
               "Tyskland +49 1511 4197169 info@todaymobility.de Spania +34 626 453 445 info@todaymobility.es "
               "Norge +47 481 11 026 info@todaymobility.no</p>")
    rec = {"navn": "TODAY MOBILITY NORWAY AS", "organisasjonsform": {"kode": "AS"},
           "epostadresse": "yf.lin@todaymobility.com", "hjemmeside": "todaymobility.com/no/"}
    v, facts = run([pg(html, "https://todaymobility.com/no/")], rec, "930981990", start="https://todaymobility.com/no/")
    assert v.level == "ambiguous" and facts == []
    assert V.site_base("https://todaymobility.com/no/", "https://todaymobility.com/no/")[0] == "https://todaymobility.com/no/"
    assert "norway" in V.name_profile("TODAY MOBILITY NORWAY AS", "AS").required


def test_hotel_chain_page_is_not_the_hotels_website():
    html = doc("Thon Hotel Sandnes | Thon Hotels", "<p>Roald Amundsensgate 115, 4307 Sandnes Kontakt oss "
               "Org. nummer: NO887100462MVA</p>", "<meta property='og:site_name' content='Thon Hotels'>")
    url = "https://www.thonhotels.no/hoteller/norge/sandnes/thon-hotel-sandnes/"
    rec = {"navn": "SANDNES HOTELL AS", "organisasjonsform": {"kode": "AS"}, "hjemmeside": "www.thonhotels.no/sandnes"}
    v, facts = run([pg(html, "https://www.thonhotels.no/sandnes", final=url)], rec, "887100462",
                   start="https://www.thonhotels.no/sandnes", shared=["912345678"])
    assert v.level == "ambiguous" and facts == []


def test_foreign_parent_multilanguage_site_section():
    html = doc("Workforce Management Software - tamigo", "<a href='https://www.tamigo.com/da/'>Dansk</a>"
               "<a href='https://www.tamigo.com/de/'>Deutsch</a><a href='https://www.tamigo.com/no/'>Norsk</a>"
               "<footer>tamigo Norge AS supportno@tamigo.no Fridtjof Nansens vei 1 1366 Lysaker Norge "
               "Org nr.: NO 987 029 781 MVA ©️ tamigo 2026</footer>")
    rec = {"navn": "TAMIGO NORGE AS", "organisasjonsform": {"kode": "AS"}, "hjemmeside": "www.tamigo.no"}
    v, facts = run([pg(html, "https://www.tamigo.no/", final="https://www.tamigo.com/no/",
                       redirects=["https://www.tamigo.no/"])], rec, "987029781", start="https://www.tamigo.no/")
    assert v.level == "ambiguous" and facts == []


def test_densiq_group_section_and_glued_ab_handle():
    home = pg(doc("DENSIQ – Tetningsteknikk", "<p>Vi tilhører nå DENSIQ Group, som har styrket vår posisjon. Selskapet er "
                  "en del av Latour Industries AB.</p><a href='https://densiq.com/sv/'>SV</a><a href='https://densiq.com/fi/'>FI</a>"
                  "<a href='https://www.facebook.com/densiqab'>fb</a>"),
              "https://www.densiq.no/", final="https://densiq.com/no/")
    rec = {"navn": "DENSIQ AS", "organisasjonsform": {"kode": "AS"}, "hjemmeside": "www.densiq.no"}
    contact = pg(doc("Kontakt – DENSIQ", "<p>DENSIQ AS – Sandnes Luramyrveien 13 4313 Sandnes Org. no: 997 495 365 "
                     "+47 53 00 97 70 norge@densiq.com</p>"), "https://densiq.com/no/kontakt-no/", "contact")
    v, facts = run([home, contact], rec, "997495365", start="https://www.densiq.no/")
    assert v.level == "ambiguous" and facts == []
    assert not V.social_handle_ok("densiqab", V.name_profile("DENSIQ AS", "AS"), "densiq")


def test_ownership_phrase_and_href_only_proof():
    html = doc("FranskBolig", "<p>Europimmo Consulting eier det kommersielle merkenavnet FranskBolig gjennom sin norske "
               "filial <a href='https://w2.brreg.no/enhet/sok/detalj.jsp?orgnr=914530903'>World Properties Consulting "
               "Scandinavia</a>.</p>")
    rec = {"navn": "WORLD PROPERTIES CONSULTING SCANDINAVIA AS", "organisasjonsform": {"kode": "AS"},
           "hjemmeside": "www.franskbolig.no"}
    v, facts = run([pg(html, "https://franskbolig.no/generelle-betingelser/", "terms")], rec, "914530903",
                   start="https://franskbolig.no/")
    assert v.level == "ambiguous" and facts == []


def test_site_branded_as_another_legal_entity():
    html = doc("Park Amusement – Cube Zone, Lasertag, Prison Island", "<p>Park Amusement AS har over 30 års erfaring.</p>"
               "<footer>Kontakt Park Amusement Drift AS Org.nr: 917916314 MVA Oslo, Norge © 2026 Park Amusement AS</footer>")
    rec = {"navn": "PARK AMUSEMENT DRIFT AS", "organisasjonsform": {"kode": "AS"}}
    v, facts = run([pg(html, "https://parkamusement.no/")], rec, "917916314", lead="name_joined_no")
    assert v.level == "ambiguous" and facts == []


def test_org_number_only_in_html_comment():
    html = doc("Pro Arkitekt", "<div class='logo-wrap'><!-- <a href='http://sgdata.dibk.no/enterprises/914194423'>"
               "<img src='x'></a> --></div><footer>post@proarkitekt.no</footer>")
    rec = {"navn": "PRO ARKITEKT AS", "organisasjonsform": {"kode": "AS"}, "epostadresse": "post@proarkitekt.no",
           "hjemmeside": "www.proarkitekt.no"}
    v, facts = run([pg(html, "https://proarkitekt.no/")], rec, "914194423", start="https://proarkitekt.no/")
    # never L1 from an invisible number; the registry-filed own-name domain is L2r (live-checked 2026-10-14: the
    # site is titled "PRO Arkitekt AS"), with registry-equal contacts only
    assert v.level == "L2r" and v.restricted
    assert all(f.value == "post@proarkitekt.no" for f in facts if f.field == "contact_email")


# --------------------------------------------------------------------------- WRONG / DOUBTFUL facts
def test_postbox_postcode_is_not_a_phone():
    html = doc("Svipp", "<p>Fakturaadresse: Postboks 3222 7439 Trondheim</p><footer>© Svipp | Org.nr.: 828956612 | "
               "<a href='tel:51599999'>51 59 99 99</a></footer>")
    rec = {"navn": "SVIPP AS", "organisasjonsform": {"kode": "AS"}, "telefon": "51 59 99 99"}
    v, facts = run([pg(html, "https://www.svipp.no/")], rec, "828956612")
    assert v.level == "L1"
    assert "+4732227439" not in vals(facts, "contact_phone")


def test_number_after_sister_company_name_not_published():
    html = doc("Fana Blikk", "<div><p>Kontakt oss Fana Blikk AS Fanavegen 39 5221 Nesttun Org. nr. 965 523 057 Tlf kontor: "
               "<a href='tel:55116969'>55 11 69 69</a> post@fanablikk.no</p><p>Butikk – Tak og Ventilasjon AS Fanavegen 41 "
               "Tlf butikk: <a href='tel:55116963'>55 11 69 63</a></p></div>")
    rec = {"navn": "FANA BLIKK AS", "organisasjonsform": {"kode": "AS"}, "telefon": "55 11 69 69"}
    v, facts = run([pg(html, "https://fanablikk.no/")], rec, "965523057")
    assert "+4755116963" not in vals(facts, "contact_phone")


def test_number_of_an_affiliate_with_its_own_domain():
    html = doc("Kontakt oss – Forskerforbundet", "<p>Forskerforbundet Organisasjonsnummer 971 422 505 Telefon "
               "<a href='tel:+4721023400'>+47 21 02 34 00</a> E-post post@forskerforbundet.no</p><p>Forskerforbundets "
               "forsikringskontor Postadresse Postboks 553, 1327 Lysaker Telefon <a href='tel:+4723113578'>+47 23 11 35 78</a> "
               "E-post post@forskerforsikring.no</p>")
    rec = {"navn": "FORSKERFORBUNDET", "organisasjonsform": {"kode": "FLI"}, "telefon": "21 02 34 00"}
    v, facts = run([pg(html, "https://www.forskerforbundet.no/kontakt-oss", "contact")], rec, "971422505",
                   start="https://www.forskerforbundet.no/")
    assert "+4723113578" not in vals(facts, "contact_phone")


def test_agency_credit_and_chain_boilerplate_descriptions():
    rec = {"navn": "TRIO BYGG AS", "organisasjonsform": {"kode": "AS"}}
    for desc in ("web design: profil-trøkk.no", "Interflora - blomster levert over hele landet, en del av kjeden",
                 "Kort tekst"):
        html = doc("Trio Bygg", "<footer>Trio Bygg AS Org.nr 987 620 552</footer>", f"<meta name='description' content='{desc}'>")
        v, facts = run([pg(html, "https://www.trio-bygg.no/")], rec, "987620552")
        assert v.level == "L1" and vals(facts, "website_description") == []


def test_shared_switchboard_jsonld_and_invisible_tel():
    html = doc("Norsk Betongforening", "<script type='application/ld+json'>{\"@type\":\"Organization\",\"name\":"
               "\"Norsk Betongforening\",\"telephone\":\"+47 22 94 75 00\"}</script>"
               "<h1>Norsk Betongforening</h1><p>" + "Om betong og forskning. " * 8 + "</p>"
               "<footer>Org.nr 912 342 344 <a href='tel:22947500'></a> Tlf 22 94 75 00</footer>")
    rec = {"navn": "NORSK BETONGFORENING", "organisasjonsform": {"kode": "FLI"}, "telefon": "22 94 75 00"}
    shared = {"phones": {"22947500": ["912342344", "912345670", "970000000"]}}
    v, facts = run([pg(html, "https://betong.net/")], rec, "912342344", contacts=shared)
    assert v.level == "L1"
    assert vals(facts, "contact_phone") == []          # shared registry number; tel link has no visible text


def test_person_mobile_in_block_and_href_text_mismatch():
    html = doc("Aspecto Marine", "<footer>Aspecto Marine AS Org.nr 912 342 344 Vaktmester Ola Nordmann 912 34 567 "
               "<a href='mailto:post@aspectomarine.no'>post@aspecto.no</a> <a href='mailto:info@aspectomarine.no'>"
               "info@aspectomarine.no</a></footer>")
    rec = {"navn": "ASPECTO MARINE AS", "organisasjonsform": {"kode": "AS"}}
    v, facts = run([pg(html, "https://aspectomarine.no/")], rec, "912342344")
    assert vals(facts, "contact_phone") == []
    assert vals(facts, "contact_email") == ["info@aspectomarine.no"]


def test_generic_social_handles_rejected():
    prof = V.name_profile("GULLSMED MOE AS", "AS")
    assert not V.social_handle_ok("@gullsmedm", prof, None)
    assert not V.social_handle_ok("ridesenteret", V.name_profile("HALLINGDAL RIDESENTER AS", "AS"), None)
    assert V.social_handle_ok("hallingdalridesenter", V.name_profile("HALLINGDAL RIDESENTER AS", "AS"), None)


def test_every_fact_span_contains_its_value():
    html = doc("Ekom AS", "<footer>Ekom AS Industriveien 9B 2021 Skedsmokorset Org.nr 912 342 344 Telefon "
               "<a href='tel:+4763840000'>+47 63 84 00 00</a> <a href='mailto:post@ekom.no'>post@ekom.no</a> "
               "<a href='https://www.facebook.com/ekomas'>Facebook</a></footer>",
               "<meta name='description' content='Ekom AS leverer rådgivende elektroprosjektering i hele Norge.'>")
    rec = {"navn": "EKOM AS", "organisasjonsform": {"kode": "AS"}}
    v, facts = run([pg(html, "https://www.ekom.no/")], rec, "912342344")
    assert facts
    for f in facts:
        assert V._span_has(f, f.span), (f.field, f.value, f.span)
