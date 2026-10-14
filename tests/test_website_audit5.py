"""Regression tests from the fifth adversarial audit (out/audit500): no rescue for group/foreign/sister signals,
chain/franchise domains, group co-filers, «vi» definitions, foreign invoicing lists, theme demo content, shared
phones, careers nouns, verbatim proof spans and place-word social handles (minimal HTML reproductions)."""

from __future__ import annotations

from nordic_company_agent.sources.website import parse_feed, parse_news_page
from nordic_company_agent.web import verify as V

FILLER = "<p>" + "Vi leverer tjenester av høy kvalitet til kunder i hele landet. " * 6 + "</p>"


def pg(html: str, url: str, kind: str = "home") -> V.Page:
    return V.Page(kind, url, url, 200, html, "sha-" + kind + url, "2026-10-14T00:00:00Z")


def doc(title: str, body: str, head: str = "") -> str:
    return f"<html lang='no'><head><title>{title}</title>{head}</head><body>{FILLER}{body}</body></html>"


def run(pages, rec, orgnr, *, lead="registry_website", start=None, shared=(), contacts=None):
    v = V.verify(pages, rec, orgnr, lead_kind=lead, start_url=start or pages[0].url, shared_orgnrs=shared,
                 shared_contacts=contacts)
    return v, V.collect_facts(v, rec)


def vals(facts, field):
    return [f.value for f in facts if f.field == field]


def rec_(name, **kw):
    return {"navn": name, "organisasjonsform": {"kode": name.rsplit(" ", 1)[-1]}, **kw}


# --------------------------------------------------------------------------- no rescue for group signals
def test_group_signal_is_never_rescued_by_the_company_block():
    html = doc("Ergofast", "<footer>ERGOFAST AS Org.nr. 979 226 519 Mail: post@ergofast.no Tel. +47 67 18 60 60 "
               "| Ergofast AB Org.nr 556480-4770 Fakturaadress Ergofast AB, 654 44 Kungälv</footer>")
    v, facts = run([pg(html, "https://ergofast.no/")], rec_("ERGOFAST AS", telefon="67 18 60 60"), "979226519")
    assert v.level == "ambiguous" and "group/foreign" in v.reason and facts == []


def test_swedish_interface_strings_are_a_foreign_signal():
    html = doc("Ergofast", "<nav>Hem Produkter Sök i nyheter Läs mer</nav><footer>ERGOFAST AS Org.nr. 979 226 519"
               "</footer>")
    v, _ = run([pg(html, "https://ergofast.no/")], rec_("ERGOFAST AS"), "979226519")
    assert v.level == "ambiguous" and "interface" in v.reason


# --------------------------------------------------------------------------- 1: chain / franchise sub-domain
def test_franchise_subdomain_of_a_chain_domain_is_refused():
    contacts = {"domains": {"spenst.no": [["996565203", "SPENST NORGE AS"], ["997283651", "FETSUND TRENINGSENTER AS"]]}}
    html = doc("Spenst Otta", "<footer>Aktiv Otta AS · Org.nr 987 494 123 · Otta</footer>")
    page = V.Page("contact", "https://otta.spenst.no/om-oss/kontakt-oss/", "https://otta.spenst.no/om-oss/kontakt-oss/",
                  200, html, "sha", "t")
    home = pg(doc("Spenst Otta", "<p>Trening på Otta</p>"), "https://otta.spenst.no/")
    v, facts = run([home, page], rec_("AKTIV OTTA AS", hjemmeside="www.aktivotta.no"), "987494123",
                   start="https://www.aktivotta.no/", contacts=contacts, shared=["996565203", "997283651"])
    assert v.level == "ambiguous" and "chain/franchise" in v.reason and facts == []


def test_place_word_never_aligns_and_subdomain_redirect_needs_registrable_label():
    html = doc("Spenst Otta", "<footer>Aktiv Otta AS · Org.nr 987 494 123</footer>")
    v, _ = run([pg(html, "https://otta.spenst.no/")], rec_("AKTIV OTTA AS"), "987494123",
               start="https://www.aktivotta.no/", contacts={"places": ["otta"]})
    assert v.level == "ambiguous"
    assert "brand not aligned" in v.reason or "sub-domain" in v.reason


# --------------------------------------------------------------------------- 3: foreign invoicing list
def test_number_in_a_list_with_foreign_vat_ids_is_not_proof():
    html = doc("Contact us - Solteq", "<p>Invoicing: Solteq Plc VAT FI04904840 Solteq Denmark A/S DK12345678 Aponsa AB "
               "Peppol 0007:5566272513 Solteq Norway AS VAT No: NO995654830</p>")
    v, facts = run([pg(html, "https://www.solteq.com/en/contact-us", "contact")], rec_("SOLTEQ NORWAY AS"), "995654830",
                   lead="name_joined_com", start="https://www.solteq.com/")
    assert not v.published and facts == []


# --------------------------------------------------------------------------- 4/5: «vi» definitions, co-filers
def test_we_defined_as_several_entities_is_a_group_site():
    priv = doc("Personvernerklæring", "<p>Hos Eiendomshuset Sandia Eiendom (Sandia Megling AS, Sandia Holding AS, Sandia "
               "Eiendomsforvaltning AS) «Sandia», «vi» eller «oss» vil vi behandle alle personopplysninger. Sandia "
               "Megling AS, org.nr 971 240 091.</p>")
    v, facts = run([pg(doc("Sandia", "<p>Velkommen</p>"), "https://sandia.no/"),
                    pg(priv, "https://sandia.no/personvern/", "privacy")], rec_("SANDIA MEGLING AS"), "971240091")
    assert v.level == "ambiguous" and facts == []


def test_we_defined_as_a_foreign_entity_is_a_group_site():
    priv = doc("Personvernerklæring", "<p>1.1. eSmiley A/S er leverandør av IT-løsninger. eSmiley A/S er heretter benevnt "
               "«eSmiley», «vi», «vår» og «oss». eSmiley AS, org.nr 916 205 147.</p>")
    v, _ = run([pg(doc("eSmiley", "<p>Mattrygghet</p>"), "https://www.esmiley.no/"),
                pg(priv, "https://www.esmiley.no/personvernerklaering/", "privacy")], rec_("ESMILEY AS"), "916205147")
    assert v.level == "ambiguous"
    assert V._we_definitions("Behandlingsansvarlig er Ekom AS, org.nr 912 342 344 (heretter «vi»).") == \
        [("Behandlingsansvarlig er Ekom AS, org.nr 912 342 344 (heretter «vi»).", ["Ekom AS"])]


def test_host_cofiled_by_a_sister_entity_is_a_group_site():
    contacts = {"domains": {"sandia.no": [["971240091", "SANDIA MEGLING AS"], ["977343356", "SANDIA HOLDING AS"]]}}
    html = doc("Sandia", "<footer>Sandia Megling AS · Org.nr 971 240 091</footer>")
    v, facts = run([pg(html, "https://sandia.no/")], rec_("SANDIA MEGLING AS"), "971240091", contacts=contacts,
                   shared=["977343356"])
    assert v.level == "ambiguous" and "SANDIA HOLDING AS" in v.reason and facts == []


def test_supplier_and_other_footer_segment_are_not_owners():
    html = doc("Subsys", "<p>Om Subsys AS: Blix Solutions AS og Hetzner Online GmbH leverer linjer og colocation.</p>"
               "<footer>Subsys AS Orgnr.: 998 289 203 MVA © Subsys Privacy Policy | Mint Media AS</footer>")
    v, _ = run([pg(html, "https://subsys.no/kontakt/", "contact")], rec_("SUBSYS AS"), "998289203",
               lead="name_joined_no", start="https://subsys.no/")
    assert v.level == "L1", v.reason


# --------------------------------------------------------------------------- 6/8: demo copy, alliance boilerplate
def test_theme_demo_description_title_and_feed_items_dropped():
    head = ("<meta name='description' content='Discover the ultimate handyman prebuilt website with Avada Website "
            "Builder. Showcase services, projects, and client testimonials.'>")
    html = doc("GeoRisiko AS – Just another WordPress site", "<footer>GeoRisiko AS · Org.nr 920 235 026</footer>", head)
    v, facts = run([pg(html, "https://www.georisiko.no/")], rec_("GEORISIKO AS"), "920235026")
    assert v.level == "L1"
    assert vals(facts, "website_description") == [] and vals(facts, "website_title") == []
    feed = (b"<?xml version='1.0'?><rss version='2.0'><channel><title>Gento</title>"
            b"<item><title>A Simple Guide to Design Thinking</title><link>https://www.gento.no/a-simple-guide/</link>"
            b"<guid>https://demosites.io/web-agency-gb/?p=12</guid><pubDate>Mon, 06 Oct 2025 10:00:00 +0000</pubDate>"
            b"<description>Readymade godard brooklyn kogi.</description></item>"
            b"<item><title>Ny CNC-fres levert til kunde</title><link>https://www.gento.no/ny-cnc-fres/</link>"
            b"<guid>https://www.gento.no/?p=40</guid><pubDate>Mon, 06 Oct 2025 10:00:00 +0000</pubDate>"
            b"<description>Vi har levert en ny fres.</description></item></channel></rss>")
    # audit 6: one demo / filler item drops the whole feed
    assert parse_feed(feed, "https://www.gento.no/feed/", lambda u: True) == []
    clean = feed.replace(feed[feed.index(b"<item>"):feed.index(b"</item>") + 7], b"")
    items = parse_feed(clean, "https://www.gento.no/feed/", lambda u: True)
    assert [it["title"] for it in items] == ["Ny CNC-fres levert til kunde"]
    assert "Ny CNC-fres levert til kunde" in items[0]["span"]


def test_alliance_boilerplate_description_dropped():
    head = "<meta name='description' content='Norefjell - Nettalliansen'>"
    html = doc("Velkommen til Norefjell Nett AS | Norefjell", "<footer>Norefjell Nett AS Org.nr 824 701 482 MVA</footer>",
               head)
    v, facts = run([pg(html, "https://norefjell-nett.no/")], rec_("NOREFJELL NETT AS"), "824701482")
    assert v.level == "L1" and vals(facts, "website_description") == []


def test_long_description_value_fits_in_its_span():
    long = "Vietnamesisk kaffe i Norge! " + "Vi tilbyr naturlige kaffeprodukter og opplevelser for alle. " * 8
    html = doc("Phin. Coffee Norway", "<footer>Phin AS Org.nr 934 438 728</footer>",
               f"<meta name='description' content='{long}'>")
    v, facts = run([pg(html, "https://phin.no/")], rec_("PHIN AS"), "934438728", lead="name_joined_no")
    d = [f for f in facts if f.field == "website_description"]
    assert d and len(d[0].value) <= 281 and d[0].value.endswith("…")
    assert d[0].span in html and len(d[0].span) <= 300 and V._span_has(d[0], d[0].span)


# --------------------------------------------------------------------------- 7: shared phones
def test_phone_the_registry_lists_for_another_org_number_is_never_published():
    html = doc("Floorlift", "<footer>Floorlift Norge AS · Org.nr 994 444 824 · Telefon: <a href='tel:+4722697730'>"
               "22 69 77 30</a></footer>")
    contacts = {"phones": {"22697730": ["950691131", "994444824"]}}
    v, facts = run([pg(html, "https://www.floorlift.no/kontakt", "contact")],
                   rec_("FLOORLIFT NORGE AS", telefon="22 69 77 30"), "994444824", contacts=contacts,
                   start="https://www.floorlift.no/")
    assert v.level == "L1" and vals(facts, "contact_phone") == []


# --------------------------------------------------------------------------- 9: careers nouns
def test_careers_page_needs_a_careers_noun():
    html = doc("Jarlsberg Bygg", "<nav><a href='/slik-jobber-vi/'>Slik jobber vi</a> <a href='/om-oss/'>Ledige "
               "stillinger</a> <a href='/vi-jobber-med'>Vi jobber med</a></nav><footer>Jarlsberg Bygg AS Org.nr "
               "912 342 344</footer>")
    v, facts = run([pg(html, "https://www.jarlsbergbygg.no/")], rec_("JARLSBERG BYGG AS"), "912342344",
                   lead="name_joined_no")
    assert v.level == "L1" and vals(facts, "careers_page") == []
    html2 = html.replace("</nav>", " <a href='/karriere'>Karriere</a></nav>")
    v2, facts2 = run([pg(html2, "https://www.jarlsbergbygg.no/")], rec_("JARLSBERG BYGG AS"), "912342344",
                     lead="name_joined_no")
    assert vals(facts2, "careers_page") == ["https://www.jarlsbergbygg.no/karriere"]


# --------------------------------------------------------------------------- 10: verbatim spans
def test_jsonld_proof_span_is_verbatim_with_spaced_number():
    ld = ('<script type="application/ld+json">{"@context":"https://schema.org","@type":"TravelAgency",'
          '"name":"Al-Injaaz","identifier":"932 231 999"}</script>')
    html = doc("Al-Injaaz | Hajj, Umrah & Experiences fra Norge", "<p>Reis med oss</p>", ld)
    v, _ = run([pg(html, "https://al-injaaz.no/")], rec_("AL-INJAAZ AS"), "932231999", lead="name_hyphen_no")
    assert v.level == "L1" and v.proof["span"] in html and "932 231 999" in v.proof["span"]
    assert V.raw_window("<p>Org. nr. 983 47 2583.</p>", "983472583") == "<p>Org. nr. 983 47 2583.</p>"


def test_news_card_title_from_title_class_and_span_shows_own_title():
    raw = ('<div><a href="/blogg/kaffereise"><div class="cardContent"><div class="title_eN4rQ textheading3">Kaffereise: '
           'Oppdag Vietnamesisk Kaffe</div><div class="description_YLQ">Oppdag de unike smakene av kaffe!</div></div>'
           '<div class="date">21. mars 2025</div></a><a href="/blogg/iskaffe"><div class="title_x">Slik lager du '
           'vietnamesisk iskaffe</div><div class="date">5. april 2025</div></a></div>')
    items = parse_news_page(raw, "https://phin.no/blogg/", lambda u: True, "/blogg")
    by = {it["title"]: it for it in items}
    assert set(by) == {"Kaffereise: Oppdag Vietnamesisk Kaffe", "Slik lager du vietnamesisk iskaffe"}
    for t, it in by.items():
        assert it["span"] in raw and V._span_has(V.Fact("news_item", it, 0, "a[href]", ""), it["span"])


# --------------------------------------------------------------------------- 11: place-word social handles
def test_region_word_plus_form_residue_is_not_a_handle_match():
    prof = V.name_profile("POLARIS NORDMØRE AS", "AS")
    assert not V.social_handle_ok("nordmoresa", prof, None, "AS")
    assert V.social_handle_ok("polarisnordmore", prof, None, "AS")
