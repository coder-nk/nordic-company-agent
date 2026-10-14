"""Regression tests from the sixth adversarial audit (out/audit600): blunt identity and fact rules - sub-path
publication needs the registrable-domain label, ownership spellings, chain brands, NUF group signals, fetched URLs
only, contacts filed by no other org number, registry-equal addresses, CMS defaults, news spam/demo/undated."""

from __future__ import annotations

from nordic_company_agent.sources.website import parse_feed, parse_news_page
from nordic_company_agent.web import verify as V

FILLER = "<p>" + "Vi leverer tjenester av høy kvalitet til kunder i hele landet. " * 6 + "</p>"


def pg(html: str, url: str, kind: str = "home", final: str | None = None) -> V.Page:
    return V.Page(kind, url, final or url, 200, html, "sha-" + kind + url, "2026-10-14T00:00:00Z")


def doc(title: str, body: str, head: str = "") -> str:
    return f"<html lang='no'><head><title>{title}</title>{head}</head><body>{FILLER}{body}</body></html>"


def rec_(name, **kw):
    return {"navn": name, "organisasjonsform": {"kode": name.rsplit(" ", 1)[-1]}, **kw}


def run(pages, rec, orgnr, *, lead="registry_website", start=None, filers=None, contacts=None):
    v = V.verify(pages, rec, orgnr, lead_kind=lead, start_url=start or pages[0].url, contact_filers=filers,
                 shared_contacts=contacts)
    return v, V.collect_facts(v, rec)


def vals(facts, field):
    return [f.value for f in facts if f.field == field]


# --------------------------------------------------------------------------- 1/4: sub-paths and ownership
def test_subpath_needs_the_registrable_domain_label():
    html = doc("Miljø", "<p>NLM Miljø AS · Org.nr 988 997 943</p><footer>Gjenbruken · Org. nr. 889391162</footer>")
    v, facts = run([pg(html, "https://nlmmiljo.no/", final="https://gjenbruken.no/miljo/")], rec_("NLM MILJØ AS"),
                   "988997943", start="https://nlmmiljo.no/")
    assert not v.published and facts == []


def test_ownership_spellings_are_decisive():
    p = V.name_profile("ATLAS EIENDOM AS")
    assert V._acquisition_phrases("Cookies & personvern Del av Atlas Gruppen Selge eiendom", p)
    assert V._acquisition_phrases("© 2026 Sol Seok part of Grounded Works", V.name_profile("SOL SEOK AS"))
    assert V._acquisition_phrases("Bilskadesenteret Tromsø er en del av Biltrend. Se alle steder",
                                  V.name_profile("BILSKADESENTERET TROMSØ AS"))
    assert V._acquisition_phrases("Nettstedet driftes av Interflora Norge SA.", V.name_profile("ASKER BLOMSTER AS"))
    assert V._acquisition_phrases("Interflora Asker Blomster AS er stolt medlem av verdens største formidlingstjeneste",
                                  V.name_profile("ASKER BLOMSTER AS"))
    assert V._acquisition_phrases("Selskapet er 100 % eiet av Hansen Holding AS", V.name_profile("EKOM AS"))
    # figurative / defined-term / acronym uses are not owners
    assert not V._acquisition_phrases("has also become a big part of STG, alongside", V.name_profile("SYSTEM TECH GROUP AS"))
    assert not V._acquisition_phrases("eiendom tidligere eiet av Bruker, og", V.name_profile("EIGAR AS"))


def test_en_del_av_followed_by_a_logo_link():
    html = doc("Future Housing", "<footer><p>Org nr 932 944 499</p><p><strong>En del av&nbsp;</strong></p></div>\n\t\t"
               "<div class='img'>\n\t\t<a href='https://futuregreengroup.no'><img src='/ffg.png' alt=''></a></footer>")
    v, facts = run([pg(html, "https://future-housing.no/")], rec_("FUTURE HOUSING AS"), "932944499")
    assert v.level == "ambiguous" and facts == []


# --------------------------------------------------------------------------- 2: chain brands
def test_hotel_by_chain_brand_is_a_group_site():
    html = doc("Vøringfoss Hotel by Fjordtind", "<footer>Vøringfoss Hotel AS · Org.nr 918 670 300 · "
               "<a href='mailto:booking@fjordtindhotels.no'>booking@fjordtindhotels.no</a></footer>")
    v, _ = run([pg(html, "https://voringfoss-hotel.no/")], rec_("VØRINGFOSS HOTEL AS"), "918670300")
    assert v.level == "ambiguous"


def test_footer_links_to_sister_hotels_and_foreign_mailboxes():
    html = doc("Fjordhotellet", "<footer>Fjord Hotell AS · Org.nr 912 342 344 · <a href='https://www.stalheim-hotell.no/'>"
               "Stalheim</a> <a href='https://www.utne-hotell.no/'>Utne</a></footer>")
    v, _ = run([pg(html, "https://www.fjord-hotell.no/")], rec_("FJORD HOTELL AS"), "912342344")
    assert v.level == "ambiguous" and "sister sites" in v.reason
    html2 = doc("Stord Hydrogen", "<footer>Stord Hydrogen AS · Org.nr 912 342 344 · ola@hyds.no kari@hyds.no "
                "post@hyds.no</footer>")
    v2, _ = run([pg(html2, "https://stordhydrogen.no/")], rec_("STORD HYDROGEN AS"), "912342344")
    assert v2.level == "ambiguous" and "another domain" in v2.reason


# --------------------------------------------------------------------------- 3: only fetched URLs
def test_builder_page_is_published_not_a_constructed_filed_domain():
    html = doc("NF Maskin", "<p>NF Maskin AS · Org.nr 926 563 289</p>")
    v, _ = run([pg(html, "https://nfmaskin.no/", final="https://nf-maskin-as.webnode.page/")], rec_("NF MASKIN AS"),
               "926563289", lead="name_joined_no", start="https://nfmaskin.no/")
    assert v.level == "L1" and v.site_url == "https://nf-maskin-as.webnode.page/"


# --------------------------------------------------------------------------- 5: NUF
def test_nuf_group_signals_apply():
    html = doc("Axlab", "<footer>Axlab NUF · Org.nr 932 168 626 · <a href='https://axlab.dk/'>DK</a> "
               "<a href='https://axlab.se/'>SE</a></footer>")
    v, _ = run([pg(html, "https://axlab.no/")], rec_("AXLAB NUF"), "932168626")
    assert v.level == "ambiguous"


# --------------------------------------------------------------------------- 6/7: contacts and addresses
def test_contacts_filed_by_another_org_number_are_never_published():
    html = doc("Rob Arnesen", "<footer>Rob Arnesen AS · Org.nr 821 862 752 · Telefon <a href='tel:+4767162000'>"
               "67 16 20 00</a> · <a href='mailto:ordre@robarnesen.no'>ordre@robarnesen.no</a></footer>")
    filers = {"phones": {"67162000": ["821862752", "999999999"]}, "emails": {"ordre@robarnesen.no": ["821862752"]}}
    v, facts = run([pg(html, "https://robarnesen.no/")], rec_("ROB ARNESEN AS", telefon="67 16 20 00"), "821862752",
                   filers=filers)
    assert v.level == "L1"
    assert vals(facts, "contact_phone") == [] and vals(facts, "contact_email") == ["ordre@robarnesen.no"]


def test_phone_labelled_as_another_office_is_dropped():
    html = doc("Vest Helse", "<p>Kontakt Vest helse og trening AS Baker Østbys vei 26 1351 Rud Tlf. 67 150 150 "
               "post@vesthelse.no Org.nr 994 267 183 Ønsker du å komme i kontakt med legesenteret, vennligst klikk "
               "her eller ring 67 18 02 70.</p>")
    v, facts = run([pg(html, "https://www.vesthelse.no/kontakt", "contact")],
                   rec_("VEST HELSE OG TRENING AS", telefon="67 15 01 50"), "994267183", start="https://www.vesthelse.no/")
    assert "+4767180270" not in vals(facts, "contact_phone")


def test_address_only_when_equal_to_the_registry():
    html = doc("Tannlege Dramdal", "<p>Tannlege Bent Dramdal AS · Org.nr 912 066 274 · Adresse: Myntgata 3, 3616 "
               "Kongsberg</p>")
    rec = rec_("TANNLEGE BENT DRAMDAL AS", forretningsadresse={"adresse": ["Myntgata 5"], "postnummer": "3616"})
    v, facts = run([pg(html, "https://tannlegebentdramdal.no/")], rec, "912066274")
    assert v.level == "L1" and vals(facts, "website_address") == []
    rec2 = rec_("TANNLEGE BENT DRAMDAL AS", forretningsadresse={"adresse": ["Myntgata 3"], "postnummer": "3616"})
    _, facts2 = run([pg(html, "https://tannlegebentdramdal.no/")], rec2, "912066274")
    assert vals(facts2, "website_address") == ["Myntgata 3, 3616 Kongsberg"]


# --------------------------------------------------------------------------- 8: descriptions and titles
def test_cms_defaults_contacts_and_other_entities_in_descriptions_dropped():
    for title, desc in (("Home | Grav", "Grav is an easy to use, yet powerful, open source flat-file CMS"),
                        ("Classic Shop - Morning star", "This is a Page excerpt. It will be displayed for search results"),
                        ("Engevik Byggsenter", "Engevik Byggsenter Tlf 53 41 77 70 selger fliser fra butikk på Stord"),
                        ("Ekom", "Ekom leverer elektro i samarbeid med Norsk Bygg AS over hele landet."),
                        ("Tromselektro", "Tromselektro AS #antonmikalsen #hydraulikk og elektromotorer i Tromsø")):
        head = f"<meta name='description' content='{desc}'>"
        html = doc(title, "<footer>Ekom AS · Org.nr 912 342 344</footer>", head)
        v, facts = run([pg(html, "https://www.ekom.no/")], rec_("EKOM AS"), "912342344")
        assert v.level == "L1"
        assert vals(facts, "website_description") == [], desc
    v, facts = run([pg(doc("Home | Grav", "<footer>Ekom AS · Org.nr 912 342 344</footer>"), "https://www.ekom.no/")],
                   rec_("EKOM AS"), "912342344")
    assert vals(facts, "website_title") == []


def test_own_name_parts_and_acronyms_are_not_other_entities():
    for name, text in (("HAUER & KJEVE AS", "Hauer & Kjeve AS | Tak- og blikkarbeider"),
                       ("ÅSANE BYGGMESTERFORRETNING AS", "Åsane Byggmesterforretning AS (ÅBF AS) har 25 års erfaring")):
        assert V._names_other_entity(text, V.name_profile(name), V.X.entity_words(name)) is None


# --------------------------------------------------------------------------- 9: news
def _feed(items: list[tuple[str, str, str]]) -> bytes:
    body = "".join(f"<item><title>{t}</title><link>https://isotakbygg.no/{slug}/</link><guid>https://isotakbygg.no/?p={i}"
                   f"</guid><pubDate>Mon, 08 Sep 2026 10:00:00 +0000</pubDate><description>{d}</description></item>"
                   for i, (t, slug, d) in enumerate(items))
    return f"<?xml version='1.0'?><rss version='2.0'><channel><title>x</title>{body}</channel></rss>".encode()


def test_one_spam_or_filler_item_drops_the_whole_feed():
    good = ("Nytt tak på skolen i Skien", "nytt-tak", "Vi har levert nytt tak.")
    assert len(parse_feed(_feed([good]), "https://isotakbygg.no/feed/", lambda u: True)) == 1
    for bad in (("Gratis Kasino Kreditt Uten Innskudd", "kasino", "Spill nå"),
                ("Vårt nye prosjekt", "prosjekt", "Quisque donec curabitur vestibulum."),
                ("First, Love Yourself", "love", "Write about something you know."),
                ("Jobb1", "jobb1", "x")):
        assert parse_feed(_feed([good, bad]), "https://isotakbygg.no/feed/", lambda u: True) == [], bad[0]


def test_news_page_needs_dates_and_skips_product_links():
    raw = ("<div><a href='/blogg/nytt-tak'><h2>Nytt tak på skolen i Skien</h2><time datetime='2026-09-08'>8. sep"
           "</time></a><a href='/blogg/udatert-artikkel'><h2>En artikkel uten dato i det hele tatt</h2></a>"
           "<a href='/blogg/products/vinterjakke'><h2>Univern Vinterjakke 8256-0401 Protec</h2><time datetime="
           "'2026-09-01'>1. sep</time></a><a href='/blogg/nytt-tak'>Les mer</a></div>")
    items = parse_news_page(raw, "https://www.allpro.no/blogg", lambda u: True, "/blogg")
    assert [it["title"] for it in items] == ["Nytt tak på skolen i Skien"]


# --------------------------------------------------------------------------- 10: careers
def test_service_page_link_is_not_careers():
    html = doc("Ansett", "<nav><a href='/tjenester/rekruttering/'>Mer om rekruttering</a></nav><footer>Ansett AS · "
               "Org.nr 912 342 344</footer>")
    v, facts = run([pg(html, "https://www.ansett.no/")], rec_("ANSETT AS"), "912342344")
    assert v.level == "L1" and vals(facts, "careers_page") == []
