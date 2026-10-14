"""Regression tests from the fourth adversarial audit (out/audit300b) and its decisive policies H-O:
minimal HTML reproductions of the run's own snapshots."""

from __future__ import annotations

from nordic_company_agent.web import extract as X
from nordic_company_agent.web import verify as V

ZW = "​‌‍﻿"        # Sanity "stega" style zero-width characters


def pg(html: str, url: str, kind: str = "home") -> V.Page:
    return V.Page(kind, url, url, 200, html, "sha-" + kind + url, "2026-10-14T00:00:00Z")


def doc(title: str, body: str, head: str = "") -> str:
    filler = "<p>" + "Vi leverer tjenester av høy kvalitet til kunder i hele landet. " * 6 + "</p>"
    return f"<html lang='no'><head><title>{title}</title>{head}</head><body>{filler}{body}</body></html>"


def run(pages, rec, orgnr, *, lead="registry_website", start=None):
    v = V.verify(pages, rec, orgnr, lead_kind=lead, start_url=start or pages[0].url)
    return v, V.collect_facts(v, rec)


def vals(facts, field):
    return [f.value for f in facts if f.field == field]


def hide(s: str) -> str:
    """Interleave zero-width characters between every character (what hid Seashore's group list)."""
    return ZW.join(s)


# --------------------------------------------------------------------------- H: exclusivity
def test_zero_width_hidden_group_list_is_seen():
    html = doc("Kontakt oss – Seashore", "<p>" + hide("Konsernet Bremnes Seashore AS - org.nr. 933 852 873 Seashore Land "
               "AS - org.nr. 931 375 261") + " Seashore Shipping AS - org.nr. 914 955 874</p>")
    rec = {"navn": "SEASHORE SHIPPING AS", "organisasjonsform": {"kode": "AS"}, "hjemmeside": "www.seashore.no"}
    v, facts = run([pg(html, "https://www.seashore.no/kontakt-oss", "contact")], rec, "914955874",
                   start="https://www.seashore.no/")
    assert v.level == "ambiguous" and "other entities" in v.reason and facts == []


def test_unlabelled_other_number_blocks_but_third_party_context_does_not():
    rec = {"navn": "EKOM AS", "organisasjonsform": {"kode": "AS"}}
    plain = doc("Ekom AS", "<footer>Ekom AS · Org.nr 912 342 344 · Partner 920 209 319</footer>")
    v, _ = run([pg(plain, "https://www.ekom.no/")], rec, "912342344")
    assert v.level == "ambiguous"
    credited = doc("Ekom AS", "<footer>Ekom AS · Org.nr 912 342 344 · Nettside levert av Webbyrå AS, org.nr 920 209 319"
                   "</footer>")
    v2, _ = run([pg(credited, "https://www.ekom.no/")], rec, "912342344")
    assert v2.level == "L1"


def test_target_number_inside_third_party_context_is_not_proof():
    html = doc("Kunde AS", "<footer>Kunde AS · Nettside levert av Ekom AS, org.nr 912 342 344</footer>")
    v, _ = run([pg(html, "https://www.kunde.no/")], {"navn": "EKOM AS", "organisasjonsform": {"kode": "AS"}}, "912342344",
               lead="name_joined_no")
    assert not v.published


# --------------------------------------------------------------------------- I: brand alignment
def test_son_spa_on_group_site_not_aligned_and_spa_is_part_of_the_name():
    assert X.entity_words("SON SPA AS") == ["son", "spa"]
    html = doc("Personvernerklæring – NHG", "<p>Behandlingsansvarlig er Nordic Hospitality Group AS. Son Spa AS – "
               "991 377 891 – Son Spa</p>")
    rec = {"navn": "SON SPA AS", "organisasjonsform": {"kode": "AS"}, "hjemmeside": "www.sonspa.no"}
    v, facts = run([pg(html, "https://nhg.no/personvernerklaering", "privacy")], rec, "991377891",
                   start="https://www.sonspa.no/")
    assert v.level == "ambiguous" and facts == []
    assert not V._words_seq_in(["son"], "Personvernerklæring")      # token boundary, not a raw substring


# --------------------------------------------------------------------------- J: acquisition / ownership
def test_acquisition_page_and_formless_copyright_owner():
    html = doc("Advicia – nå en del av Questback", "<p>Advicia . Nå en del av Questback Viktig nyhet Advicia er blitt en "
               "del av Questback Advicia AS er kjøpt opp av Questback, Nordens ledende plattform.</p>"
               "<footer>Advicia AS · Org.nr: 992 058 501 · En del av Questback © 2026 Questback. Alle rettigheter "
               "reservert. <a href='tel:+4721027070'>+47 21 02 70 70</a></footer>")
    rec = {"navn": "ADVICIA AS", "organisasjonsform": {"kode": "AS"}, "hjemmeside": "www.advicia.no",
           "telefon": "22 43 08 09"}
    v, facts = run([pg(html, "https://www.advicia.no/")], rec, "992058501")
    assert v.level == "ambiguous" or (v.strict_block and vals(facts, "contact_phone") == [])
    assert any("Questback" in s for s in v.group_signals)
    assert vals(facts, "contact_phone") == []


def test_controller_on_privacy_page_applies_to_l1():
    home = pg(doc("Hotell Son", "<footer>Hotell Son AS · Org.nr 912 342 344</footer>"), "https://www.hotellson.no/")
    priv = pg(doc("Personvern", "<p>Behandlingsansvarlig er Nordic Hospitality Group AS, Oslo.</p>"),
              "https://www.hotellson.no/personvern", "privacy")
    v, _ = run([home, priv], {"navn": "HOTELL SON AS", "organisasjonsform": {"kode": "AS"}}, "912342344")
    assert v.level == "ambiguous" and "controller" in v.reason


# --------------------------------------------------------------------------- K: template pages
def test_template_company_page_is_blocking():
    html = doc("Bobilnorge", "<p>Juridisk Informasjon Firmanavn : BOBILNORGE AS Juridisk form : (AS) Organisasjonsnummer : "
               "932 481 294 Registreringsdato : 11/18/2023 Stiftelsesdato : 10/25/2023 Téléphone : +0 Email : </p>")
    v, facts = run([pg(html, "https://www.bobilnorge.com/no")], {"navn": "BOBILNORGE AS",
                                                                 "organisasjonsform": {"kode": "AS"}}, "932481294",
                   lead="name_joined_com")
    assert v.level == "not_available" and "template" in v.reason and facts == []


# --------------------------------------------------------------------------- L/M: contact facts and logos
def test_sister_office_phones_not_published():
    html = doc("Kai Hansen", "<p>Stavanger Kontorveien 12, 4033 post@kai-hansen.no (+47) 51 90 66 00 Kontaktpersoner "
               "Kristiansand Kristian IVs gate 23, 4612 post@kai-hansen.no (+47) 38 00 30 50 Kontaktpersoner Haugesund "
               "Longhammarvegen 19, 5536 post@kai-hansen.no (+47) 52 70 33 70</p><footer>©2026 Kai Hansen AS, Kontorveien "
               "12, 4033 Stavanger. Telefon: 51 90 66 00. Foretaksregisteret: NO 940036437 MVA.</footer>")
    rec = {"navn": "KAI HANSEN AS", "organisasjonsform": {"kode": "AS"}, "telefon": "51 90 66 00",
           "hjemmeside": "kai-hansen.no"}
    v, facts = run([pg(html, "https://www.kai-hansen.no/")], rec, "940036437", start="https://kai-hansen.no/")
    assert v.level == "L1"
    assert vals(facts, "contact_phone") == ["+4751906600"]


def test_brand_owner_logo_on_shop_cdn_not_published():
    head = "<meta property='og:image' content='https://cdn.shopify.com/s/files/1/0398/files/logo.jpg?v=1644590841'>"
    html = doc("Mei & Moy", "<footer>© 2026, Mei og Moy AS / Lydia Dainow Norge (org nr 927116928)</footer>", head)
    v, facts = run([pg(html, "https://www.meiogmoy.no/")], {"navn": "MEI & MOY AS", "organisasjonsform": {"kode": "AS"}},
                   "927116928", lead="name_joined_no")
    assert vals(facts, "logo_url") == []


def test_returns_address_c_o_another_entity_not_published():
    html = doc("Kontakt – Bokashi Norge", "<p>Bokashi Norge, Org.nr.: 923 456 570</p><p>Adresse for retur av varer: "
               "Bokashi Norge AS c/o Remonter AS Hellvikveien 10 1459 Nesodden</p>")
    v, facts = run([pg(html, "https://www.bokashinorge.no/kontakt", "contact")],
                   {"navn": "BOKASHI NORGE AS", "organisasjonsform": {"kode": "AS"}}, "923456570",
                   lead="name_joined_no", start="https://www.bokashinorge.no/")
    assert vals(facts, "website_address") == []


def test_only_generic_role_mailboxes_on_own_brand_domain():
    html = doc("Ekom AS", "<footer>Ekom AS · Org.nr 912 342 344 · <a href='mailto:post@ekom.no'>post@ekom.no</a> "
               "<a href='mailto:ola@ekom.no'>ola@ekom.no</a> <a href='mailto:info@webbyraa.no'>info@webbyraa.no</a>"
               "</footer>")
    v, facts = run([pg(html, "https://www.ekom.no/")], {"navn": "EKOM AS", "organisasjonsform": {"kode": "AS"}}, "912342344")
    assert vals(facts, "contact_email") == ["post@ekom.no"]


# --------------------------------------------------------------------------- N/O: spans and junk
def test_spans_are_verbatim_and_contain_the_value():
    html = ("<html lang='no'><head><title>Ekom AS – elektro</title>"
            "<meta name='description' content='Ekom AS leverer r&aring;dgivende elektroprosjektering i hele Norge.'>"
            "</head><body><p>" + "Om oss og v&aring;re tjenester. " * 10 + "</p><footer><p>Ekom AS<br>Org.nr 912 342 344"
            "</p><p>Telefon <a href=\"tel:+4763840000\">+47&nbsp;63&nbsp;84&nbsp;00&nbsp;00</a></p>"
            "<a href=\"mailto:post@ekom.no\">post@ekom.no</a> <a href=\"https://www.facebook.com/ekomas/\">Facebook</a>"
            "</footer></body></html>")
    rec = {"navn": "EKOM AS", "organisasjonsform": {"kode": "AS"}, "telefon": "63 84 00 00"}
    v, facts = run([pg(html, "https://www.ekom.no/")], rec, "912342344")
    assert facts
    for f in facts:
        assert f.span in html, (f.field, f.span)
        assert V._span_has(f, f.span), (f.field, f.value, f.span)


def test_error_page_title_and_junk_news_dropped():
    from nordic_company_agent.sources.website import PLACEHOLDER_TITLE_RX
    for t in ("Hurtigvisning", "Quick view", "Les mer", "Read more", "Hei, verden!", "Hello world!"):
        assert PLACEHOLDER_TITLE_RX.search(t), t
    assert not PLACEHOLDER_TITLE_RX.search("Les mer om vår nye fabrikk i Sandnes")
    html = doc("Fant ikke siden", "<footer>Ekom AS · Org.nr 912 342 344</footer>")
    v, facts = run([pg(html, "https://www.ekom.no/")], {"navn": "EKOM AS", "organisasjonsform": {"kode": "AS"}}, "912342344")
    assert vals(facts, "website_title") == []


# --------------------------------------------------------------------------- replay follow-ups
def test_formless_copyright_owner_ignores_labels_credits_and_glued_menus():
    for t in ("© 2025 bottom of page", "© 2026 Nettløsning & foto: Aarstein Media", "© 2026 ArcHub Theme. All rights",
              "Copyright © 2026 Web levert av CustomPublish AS", "© 2026 Innholdet er beskyttet", "© 2026 Org.nr. 912",
              "Copyright 2026 © Plantehallen AS • Org.nr.: 826491132", "© 2026 PRIVACY POLICY"):
        assert V._formless_owners(t) == [], t
    assert V._formless_owners("Kontakt 2026 © PHM Group Ventilasjonsrens Enebolig") == ["PHM Group"]
    assert V._formless_owners("© 2026 Questback. Alle rettigheter reservert.") == ["Questback."]
    html = doc("Activepeople", "<footer>ActivePeople AS · Org.nr 912 342 344 · © 2024 – Aktivepeople | Personvern"
               "</footer>")
    v, _ = run([pg(html, "https://activepeople.no/")], {"navn": "ACTIVEPEOPLE AS", "organisasjonsform": {"kode": "AS"}},
               "912342344", lead="name_joined_no")
    assert v.level == "L1"


def test_ownership_phrases_about_programmes_persons_and_own_brand_do_not_count():
    assert V._acquisition_phrases("Butikken drives og eies av Susanne J Kvalvik og Marianne K Risløv.",
                                  V.name_profile("STEMPEL AS")) == []
    assert V._acquisition_phrases("Programmet eies av Kommunal- og distriktsdepartementet og forvaltes av",
                                  V.name_profile("SENTRUM NÆRINGSHAGE AS")) == []
    assert V._acquisition_phrases("Som en del av Os Caravan-familien får du", V.name_profile("OS CARAVAN & FRITID AS")) == []
    assert V._acquisition_phrases("Ekra Glass har vært en del av Arendals lokalsamfunn", V.name_profile("EKRA GLASS AS")) == []
    assert V._acquisition_phrases("Selskapet er i dag en del av Eurofins Scientific, verdens",
                                  V.name_profile("EUROFINS RADONLAB AS"))


def test_non_utf8_page_gets_ascii_only_spans():
    html = doc("Kai Hansen", "<footer><span>©2026 Kai Hansen AS,</span><span> Kontorveien 12, 4033 Stavanger.</span> "
               "Telefon: 51 90 66 00. Foretaksregisteret: NO 940036437 MVA. <a href='https://www.facebook.com/kaihansenas/'"
               " aria-label='Følg oss på Facebook'>f</a></footer>",
               "<meta name='description' content='I over 50 år har Kai Hansen vært din partner for trykksaker.'>")
    page = V.Page("home", "https://www.kai-hansen.no/", "https://www.kai-hansen.no/", 200, html, "sha", "t", utf8=False)
    rec = {"navn": "KAI HANSEN AS", "organisasjonsform": {"kode": "AS"}, "telefon": "51 90 66 00",
           "forretningsadresse": {"adresse": ["Kontorveien 12"], "postnummer": "4033", "poststed": "STAVANGER"}}
    v, facts = run([page], rec, "940036437")
    assert v.level == "L1" and facts
    assert all(f.span.isascii() and f.span in html and V._span_has(f, f.span) for f in facts)
    assert vals(facts, "website_description") == []          # needs 'å': no byte-verbatim ASCII span exists
    assert vals(facts, "website_address") == ["Kontorveien 12, 4033 Stavanger"]


def test_news_page_items_use_card_heading_and_verbatim_span():
    from nordic_company_agent.sources.website import parse_news_page
    raw = ('<div class="grid"><a class="kort" href="/Aktuelt/%c3%85pent-kurs%26%2363%3b"><div class="bilde"></div>'
           '<div class="dato">24.03.2026</div><h2 class="tittel">&Aring;pent kurs i beredskap og kriseledelse &ndash; '
           'hvordan responderer din virksomhet?</h2></a></div>')
    items = parse_news_page(raw, "https://www.example.no/Aktuelt", lambda u: True, "/Aktuelt")
    assert len(items) == 1
    it = items[0]
    assert it["title"].startswith("Åpent kurs i beredskap") and it["published"] == "2026-03-24"
    assert it["span"] in raw
    assert V._span_has(V.Fact("news_item", it, 0, "a[href]", ""), it["span"])


def test_relative_news_link_without_path_form_gets_no_span():
    page = pg(doc("Kontram", "<a href='nyheter'>Nyheter</a>"), "https://www.kontram.no/")
    assert V.span_for(page, V.Fact("news_page", "https://www.kontram.no/nyheter", 0, "a[href]", "")) is None
    page2 = pg(doc("Kontram", "<a href='/nyheter'>Nyheter</a>"), "https://www.kontram.no/")
    assert V.span_for(page2, V.Fact("news_page", "https://www.kontram.no/nyheter", 0, "a[href]", ""))


def test_copyright_act_prose_and_rating_badges_name_no_owner():
    assert V._formless_owners("the Personal Data Act, the Copyright Act, the Tax Act") == []
    assert V._formless_owners("Org nr 999 614 973 © Bisnode 2023.05.26 Studix was awarded") == []
    assert V._formless_owners("Kopperå Forvaltning © 2026 KUNDESERVICE SOSIALE MEDIER Facebook") == []


def test_whole_name_in_domain_label_aligns_brand():
    html = doc("Velkommen", "<footer>Nr 1 Catering AS · Org.nr 912 342 344</footer>")
    v, _ = run([pg(html, "https://nr1catering.no/")], {"navn": "NR 1 CATERING AS", "organisasjonsform": {"kode": "AS"}},
               "912342344", lead="name_joined_no")
    assert v.level == "L1"
