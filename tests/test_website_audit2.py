"""Regression tests from the second adversarial audit (out/audit250): minimal HTML reproductions of the run's
own snapshots for 3 wrong websites, 2 doubtful websites and 8 wrong contact/social facts."""

from __future__ import annotations

from nordic_company_agent.web import extract as X
from nordic_company_agent.web import verify as V


def pg(html: str, url: str, kind: str = "home", final: str | None = None, redirects=()) -> V.Page:
    return V.Page(kind, url, final or url, 200, html, "sha-" + kind, "2026-10-14T00:00:00Z", list(redirects))


def doc(title: str, body: str, head: str = "") -> str:
    filler = "<p>" + "Vi leverer tjenester av høy kvalitet til kunder i hele landet. " * 6 + "</p>"
    return f"<html lang='no'><head><title>{title}</title>{head}</head><body>{filler}{body}</body></html>"


def run(pages, rec, orgnr, *, lead="registry_website", start=None, shared=()):
    v = V.verify(pages, rec, orgnr, lead_kind=lead, start_url=start or pages[0].url, shared_orgnrs=shared)
    return v, V.collect_facts(v, rec)


def vals(facts, field):
    return [f.value for f in facts if f.field == field]


# --------------------------------------------------------------------------- WRONG websites
NEST_FOOTER = "<footer>Design: fbStudios - 2023 | Alt innhold © Nesthood Norge AS | Org.nr. 925 051 284 Ring 91 66 33 00</footer>"


def test_nesthood_listed_subsidiary_on_parent_site():
    home = pg(doc("Nesthood – Skadedyrkontroll", "<a href='/kundeservice/'>Kundeservice</a>" + NEST_FOOTER),
              "https://www.nesthood.no/")
    ks = pg(doc("Kundeservice – Nesthood", "<p>Kontaktinformasjon E-post: post@nesthood.no</p><p>Selskapene Nesthood Oslo AS "
                "– org.nr. 927 433 672 Nesthood Vestfold AS – org.nr. 925 178 322 Nesthood Norge AS (morselskap) – org.nr. "
                "925 051 284</p>" + NEST_FOOTER), "https://www.nesthood.no/kundeservice/", "contact")
    rec = {"navn": "NESTHOOD OSLO AS", "organisasjonsform": {"kode": "AS"}, "epostadresse": "post@nesthood.no"}
    v, facts = run([home, ks], rec, "927433672", start="https://www.nesthood.no/",
                   shared=["925051284", "925178322", "927433672"])
    assert v.level == "ambiguous" and facts == []        # policy H: other org numbers on the site
    hit = next(h for i in v.infos for h in i.hits)
    assert hit["list"] is True


def test_permian_privacy_list_and_owner_before_copyright():
    home = pg(doc("Permian", "<footer>Legal Disclosures Privacy Notice Permian AS © 2025 | Made by Oase</footer>"),
              "https://www.permian.no/")
    priv = pg(doc("Privacy notice", "<p>This privacy notice applies to the following companies: Permian AS (reg. no. "
                  "892176132), Permian Business Partners AS (reg. no. 999226094), Permian Fund Services AS (reg. no. "
                  "916467079) Permian AIF Depositary AS (reg. no. 997318250).</p><footer>Permian AS © 2025</footer>"),
              "https://www.permian.no/privacy-notice", "privacy")
    rec = {"navn": "PERMIAN FUND SERVICES AS", "organisasjonsform": {"kode": "AS"}}
    v, facts = run([home, priv], rec, "916467079", start="https://www.permian.no/", shared=["892176132"])
    assert v.level == "ambiguous" and facts == []


def test_isovator_number_printed_under_another_name():
    html = doc("SRG – Stiftelsen ReturGass", "<h2>Isovator AS avd Hokksund</h2><a href='https://www.isovator.no/'>Gå til "
               "Isovator Hokksunds nettside</a><footer>Gå til Energilogistikk nettside Stiftelsen ReturGass Org.nr.:974 273 845 "
               "Horgenveien 227, 3303 Hokksund Tlf: 915 40 073 E-post: post@returgass.no</footer>")
    rec = {"navn": "ISOVATOR AS", "organisasjonsform": {"kode": "AS"}, "epostadresse": "post@returgass.no"}
    v, facts = run([pg(html, "https://srg.no/")], rec, "974273845", lead="email_domain")
    assert v.level == "ambiguous" and facts == []
    assert v.site_links == ["https://www.isovator.no/"]          # followed as an L1-only lead by sources/website.py


# --------------------------------------------------------------------------- DOUBTFUL websites
def test_skagerak_site_titled_with_sister_organisation():
    home = pg(doc("Skagerak International School – Maximising Potential of Students", "",
                  "<meta property='og:site_name' content='Skagerak International School – Maximising Potential of Students'>"),
              "https://www.skagerak.org/")
    contact = pg(doc("Contact Us – Skagerak International School – Maximising Potential of Students",
                     "<p>ADDRESS Come Visit Us Kindergarten Skagerak International School, Kindergarten Framnesveien 7 3222 "
                     "Sandefjord Organizational number / MVA number : 998 246 652 Skagerak International School Skagerak "
                     "International School Framnesveien 7 3222 Sandefjord Organizational number / MVA number : 871 496 722</p>"
                     "<p>EMAIL admin@skagerak.org PHONE Main switchboard phone number for all schools : +47 33 45 65 00</p>"),
                 "https://www.skagerak.org/contact-us/", "contact")
    rec = {"navn": "STIFTELSEN SKAGERAK INTERNATIONAL SCHOOL, KINDERGARTEN", "organisasjonsform": {"kode": "STI"},
           "epostadresse": "admin@skagerak.org", "telefon": "33 45 65 00"}
    v, facts = run([home, contact], rec, "998246652", start="https://www.skagerak.org/")
    assert v.level == "ambiguous" and "871496722" in v.reason and facts == []


def test_vibbo_platform_and_login_error_page():
    url = "https://vibbo.no/solrik-skogro/om?error=true&message=login_required&status=500&referenceId=srv-5d49"
    html = doc("Solrik Skogro sameie | Vibbo", "<p>Selskapsopplysninger Selskapsform: Sameie Organisasjonsnummer: 925579025</p>")
    rec = {"navn": "SOLRIK SKOGRO SAMEIE", "organisasjonsform": {"kode": "ESEK"}}
    v, facts = run([pg(html, "https://vibbo.no/solrik-skogro/om", final=url)], rec, "925579025",
                   start="https://vibbo.no/solrik-skogro/om")
    assert v.level in ("ambiguous", "not_available") and facts == []
    assert V.ERROR_URL_RX.search(url)


# --------------------------------------------------------------------------- wrong contact / social facts
def test_scankab_block_stops_at_danish_parent():
    html = doc("Scankab Cables Norge", "<div><div>Scankab Cables AS - SVG Luramyrveien 15 NO-4313 Sandnes Tlf:+47 5163 7020 "
               "E-mail: scankab@scankab.no Org. nr.: 987 147 229</div><div>Scankab Cables A/S - HQ Malervænget 3 DK-5560 "
               "Aarup Telefon: +45 7020 3455 E-mail: scankab@scankab.dk CVR nr.: 20973277</div></div>")
    rec = {"navn": "SCANKAB CABLES AS", "organisasjonsform": {"kode": "AS"}, "epostadresse": "jj@scankab.dk"}
    v, facts = run([pg(html, "https://scankab.no/kontakt/", "contact")], rec, "987147229",
                   start="https://www.scankab.no/")
    # audit 5: a foreign parent on the page makes it a group site - never published, no block rescue
    assert v.level == "ambiguous" and "group/foreign" in v.reason and facts == []
    assert "+4570203455" not in str(facts) and "+4770203455" not in str(facts)


def test_foreign_tel_link_never_becomes_norwegian():
    html = doc("MyCruise Norge", "<p>MyCruise Norge AS Org nr: 929545087 Prinsens Gate 18, 0152 Oslo Telefon: "
               "<a href='tel:+4723960924'>+47 23 96 09 24</a></p><div>Du er meget velkommen til at ringe til os på tlf. "
               "<a href=tel:+4570225959> 70 22 59 59 </a></div>")
    rec = {"navn": "MYCRUISE NORGE AS", "organisasjonsform": {"kode": "AS"}}
    v, facts = run([pg(html, "https://www.mycruise.no/kontakt", "contact")], rec, "929545087",
                   start="https://www.mycruise.no/")
    assert "+4770225959" not in vals(facts, "contact_phone") and "+4570225959" not in vals(facts, "contact_phone")


def test_stale_tel_href_dropped():
    html = doc("Ekra Glass", "<footer><a href='mailto:post@ekraglass.no'>post@ekraglass.no</a>"
               "<a href='tel:57884350'>37 02 41 88</a> Strømsbuveien 61, 4836 Arendal © Ekra Glass AS 2024 Org.nr. "
               "982 829 577</footer>")
    rec = {"navn": "EKRA GLASS AS", "organisasjonsform": {"kode": "AS"}, "telefon": "37 02 41 88"}
    v, facts = run([pg(html, "https://www.ekraglass.no/")], rec, "982829577", lead="name_joined_no")
    assert v.level == "L1"
    assert vals(facts, "contact_phone") == ["+4737024188"]


def test_authority_mailbox_from_privacy_page_not_published():
    html = doc("Personvern – Myrkdalen Vest", "<p>E-post: pk@myrkdalenvest.no Telefon: 977 23450 Organisasjonsnr.: "
               "987160810. Dersom du mener at vår behandling ikke er i orden, kan du klage til Datatilsynet på "
               "postkasse@datatilsynet.no</p>")
    rec = {"navn": "MYRKDALEN VEST AS", "organisasjonsform": {"kode": "AS"}}
    v, facts = run([pg(html, "https://myrkdalenvest.no/side/28/personvern", "privacy")], rec, "987160810",
                   start="https://www.myrkdalenvest.no/")
    assert "postkasse@datatilsynet.no" not in vals(facts, "contact_email")


def test_next_company_mailbox_not_in_block():
    html = doc("Kontakt oss – Rene Trapper", "<section><h2>Kontakt oss</h2><p>Rene Trapper AS Org.nr: 989 269 631 MVA C/O Sven "
               "Aars-Rynning NO-0252 Oslo Telefon: <a href='tel:45230111'>452 30 111</a> kontakt@renetrapper.no</p>"
               "<p>Rene Bygårder AS Telefon: <a href='tel:48850001'>488 50 001</a> kontakt@renebygarder.no</p></section>")
    rec = {"navn": "RENE TRAPPER AS", "organisasjonsform": {"kode": "AS"}}
    v, facts = run([pg(html, "https://www.renetrapper.no/kontakt-oss/", "contact")], rec, "989269631",
                   lead="name_joined_no", start="https://www.renetrapper.no/")
    # audit 5: a sister entity (Rene Bygårder AS) on the page makes it a group site - never published
    assert v.level == "ambiguous" and facts == []
    assert "+4748850001" not in vals(facts, "contact_phone")


def test_foreign_parent_linkedin_suffix_rejected():
    html = doc("Balco – Innglassing av balkonger", "<p>Balco AS, Sandstuveien 60A, 1184 Oslo, Selskapsregistreringsnr. "
               "979 458 398</p><a href='https://www.linkedin.com/company/balco-ab?trk=x'>in</a>"
               "<a href='https://www.facebook.com/balcobalkong'>fb</a>")
    rec = {"navn": "BALCO AS", "organisasjonsform": {"kode": "AS"}}
    v, facts = run([pg(html, "https://www.balco.no/")], rec, "979458398")
    urls = [s["url"] for s in vals(facts, "social_profile")]
    assert not any("balco-ab" in u for u in urls)


def test_non_ascii_facebook_handle_not_truncated():
    url = "https://www.facebook.com/Hestn%C3%A6s-Trappefabrikk-As-338580586172780/"
    assert X.SOCIAL_RX["facebook"].search(url).group(1) == "Hestn%C3%A6s-Trappefabrikk-As-338580586172780"
    assert X.SOCIAL_RX["facebook"].search("https://www.facebook.com/Hestnæstrapp").group(1) == "Hestnæstrapp"
    html = doc("Hestnæs Trappefabrikk", f"<p>Org nr 972 412 503</p><a href='{url}'>Facebook</a>")
    rec = {"navn": "HESTNÆS TRAPPEFABRIKK AS", "organisasjonsform": {"kode": "AS"}}
    v, facts = run([pg(html, "https://hestnestrapp.no/")], rec, "972412503", lead="email_domain")
    urls = [s["url"] for s in vals(facts, "social_profile")]
    assert "https://www.facebook.com/hestn" not in urls
    assert any(u.startswith("https://www.facebook.com/Hestnæs-Trappefabrikk") for u in urls)


def test_invoice_intake_mailbox_not_published():
    html = doc("Hermansen Maskin", "<p>Hermansen Maskin AS Org: 990 710 430 Brekka 4 3077 SANDE I VESTFOLD Kontaktinfo "
               "Telefon: 906 89 244 E-post: arild@hermansenmaskin.no Faktura: EHF eller e-post: "
               "hermansenmaskin@faktura.poweroffice.net</p>")
    rec = {"navn": "HERMANSEN MASKIN AS", "organisasjonsform": {"kode": "AS"}, "mobil": "90689244"}
    v, facts = run([pg(html, "https://hermansenmaskin.no/")], rec, "990710430", lead="name_joined_no")
    assert v.level == "L1"
    assert vals(facts, "contact_email") == []
    assert vals(facts, "contact_phone") == ["+4790689244"]


def test_fax_and_foreign_prefixed_block_numbers():
    assert X.block_phones("Telefon: 22 33 44 55 Telefax: 22 33 44 56 Danmark: +45 7020 3455") == ["22334455"]


def test_builder_host_redirect_publishes_filed_domain():
    html = doc("Firma Test", "<footer>Firma Test AS · Org.nr 912 342 344</footer>")
    pages = [pg(html, "https://www.firmatest.no/", final="https://firmatest.wixsite.com/firma", redirects=["https://www.firmatest.no/"])]
    v, _ = run(pages, {"navn": "FIRMA TEST AS"}, "912342344", start="https://www.firmatest.no/")
    assert v.level == "L1" and v.site_url == "https://www.firmatest.no/"
