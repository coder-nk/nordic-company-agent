"""Regression tests from the adversarial audit of the 1,000-company run (snapshots in tests/fixtures/website/audit).

Three wrong-company and three doubtful publications must no longer publish (or must publish only the company's
own block), and two recall cases must now pass with strong evidence.
"""

from __future__ import annotations

import json
from pathlib import Path

from nordic_company_agent.web import extract as X
from nordic_company_agent.web import verify as V

A = Path(__file__).parent / "fixtures" / "website" / "audit"
R = json.loads((A / "registry_records.json").read_text())
JONK_HOST = ["925170356", "925516139", "926876899", "929209842", "929209850", "930318663", "930363650", "932394081",
             "933029654"]                                   # registry_website_domains: jonkburger.no
OMEXOM_HOST = ["984277032", "984594453"]                    # registry_website_domains: omexom.no
ALUHAK_SHARED = {"phones": {"51425700": ["945675586", "999152090"]},
                 "addresses": {"4353|plogfabrikkvegen 5": ["945675586", "999152090"]}}


def page(kind: str, url: str, final: str, name: str, redirects=()) -> V.Page:
    return V.Page(kind, url, final, 200, (A / name).read_text(), name, "2026-10-13T00:00:00Z", list(redirects))


def run(orgnr: str, pages: list[V.Page], *, lead: str = "registry_website", start: str, shared=(), contacts=None):
    v = V.verify(pages, R[orgnr], orgnr, lead_kind=lead, start_url=start, shared_orgnrs=shared,
                 shared_contacts=contacts)
    return v, V.collect_facts(v, R[orgnr])


def vals(facts, field):
    return [f.value for f in facts if f.field == field]


# --------------------------------------------------------------------------- WRONG
def test_axflow_cross_domain_redirect_keeps_locale_base():
    pages = [page("home", "https://www.axflow.no/", "https://www.axflow.com/nb-no/", "axflow_nb-no.html",
                  ["https://www.axflow.no/"])]
    v, facts = run("918210997", pages, start="https://www.axflow.no/")
    # a section of the Swedish group's site (locale siblings, "Member of AxFlow Group"): policies B/C
    assert v.level == "ambiguous" and facts == []
    assert V.site_base("https://www.axflow.no/", "https://www.axflow.com/nb-no/") == ("https://www.axflow.com/nb-no/", True)
    assert V.site_base("https://www.firma.no/", "https://firma.no/no/") == ("https://firma.no/", False)


def test_hydreco_group_site_only_own_block():
    home = page("home", "https://www.hydreco.com/", "https://hydreco.com/", "hydreco_home.html")
    contact = page("contact", "https://hydreco.com/contact-us", "https://hydreco.com/contact-us", "hydreco_contact.html")
    v, facts = run("920217761", [home, contact], start="https://www.hydreco.com/")
    assert v.level == "ambiguous" and not v.published      # group site naming other entities: no L2
    if v.published:
        assert v.strict_block
        assert vals(facts, "contact_email") == ["post-no@hydreco.com"]
        assert vals(facts, "contact_phone") == ["+4722909410"]
        assert not [f for f in facts if f.field in ("social_profile", "careers_page", "website_title",
                                                     "website_description", "brand_name", "logo_url")]
    blob = json.dumps([f.value for f in facts])
    for bad in ("info-uk@", "sales-it@", "reception-wa@", "sales-au@", "sales-in@", "+44", "linkedin"):
        assert bad not in blob.lower()
    v2, facts2 = run("920217761", [home], start="https://www.hydreco.com/")
    assert v2.level == "ambiguous" and facts2 == []              # UK sister's site without the AS's own block


def test_yabimo_sister_entity_site():
    home = page("home", "https://www.yabimo.com/", "https://yabimo.com/", "yabimo_home.html")
    contact = page("contact", "https://yabimo.com/contact/", "https://yabimo.com/contact/", "yabimo_contact.html")
    v, facts = run("912500128", [home, contact], start="https://www.yabimo.com/")
    assert v.level == "ambiguous" and "sister/group" in v.reason and facts == []
    v2, _ = run("912500128", [home], start="https://www.yabimo.com/")
    assert v2.level == "ambiguous"                                # c/o accountant + one corroboration


def test_legal_name_needs_uppercase_form_token():
    rx = X.legal_name_rx("YABIMO AS")
    assert not rx.search("we strongly recommend Yabimo as a solid and reliable supplier")
    assert rx.search("Copyright YABIMO AS 2026") and rx.search("Yabimo AS er leverandør")
    corr = V.corroborate([V.analyse_page(V.Page("home", "https://y.no/", "https://y.no/", 200,
                         "<html><body><p>We recommend Yabimo as a solid supplier.</p></body></html>"), "912500128",
                         "YABIMO AS")], {"navn": "YABIMO AS"}, "y.no")
    assert corr["legal_name_full"] is False


# --------------------------------------------------------------------------- DOUBTFUL
def test_otera_redirect_to_multi_entity_brand_site():
    home = page("home", "https://www.otera.no/", "https://www.omexom.no/", "omexom_home.html", ["https://www.otera.no/"])
    contact = page("contact", "https://www.omexom.no/kontakt-oss-kontaktside-omexom-norge/",
                   "https://www.omexom.no/kontakt-oss-kontaktside-omexom-norge/", "omexom_contact.html")
    v, facts = run("995845067", [home, contact], start="https://www.otera.no/", shared=OMEXOM_HOST)
    assert v.level == "ambiguous" and facts == []


def test_jonk_shared_host_jsonld_only_hit_refused():
    pages = [page("home", "https://jonkburger.no/", "https://www.jonkburger.no/", "jonk_home.html")]
    v, facts = run("925516139", pages, start="https://jonkburger.no/", shared=JONK_HOST)
    assert v.level == "ambiguous" and facts == []


def test_aluhak_sibling_group_site_and_shared_contacts():
    home = page("home", "https://www.aluhak.no/", "https://www.aluhak.no/", "aluhak_home.html")
    kontakt = page("contact", "https://www.aluhak.no/kontakt-oss", "https://www.aluhak.no/kontakt-oss", "aluhak_kontakt.html")
    v, facts = run("999152090", [home, kontakt], start="https://www.aluhak.no/", contacts=ALUHAK_SHARED)
    assert v.level == "ambiguous" and facts == []
    v2, _ = run("999152090", [home], start="https://www.aluhak.no/", contacts=ALUHAK_SHARED)
    assert v2.level == "ambiguous"


# --------------------------------------------------------------------------- e-mail policy
def test_personal_registry_email_not_published_and_region_mailboxes_rejected():
    prof = V.name_profile("JONNY LARSEN RØRLEGGERBEDRIFT AS", "AS")
    assert V.email_kind("rune@jonnylarsen.no", prof, "jonnylarsen") != "role"
    assert V.REGION_LOCAL_RX.search("info-uk") and V.REGION_LOCAL_RX.search("reception-wa")
    assert not V.REGION_LOCAL_RX.search("post-no") and not V.REGION_LOCAL_RX.search("post")


# --------------------------------------------------------------------------- recall
def test_star_elektro_js_shell_with_head_evidence():
    pages = [page("home", "https://star-elektro.no/", "https://star-elektro.no/", "star_home.html")]
    v, facts = run("984447647", pages, start="https://star-elektro.no/")
    # policy D: the full legal name must be in visible footer/contact text and a legal page must be checked
    assert v.level == "ambiguous" and facts == []


def test_olav_olsen_registry_email_domain_lead_l2e():
    pages = [page("home", "https://olavolsen.no/", "https://www.olavolsen.no/", "olsen_home.html")]
    v, facts = run("981124626", pages, lead="email_domain", start="https://olavolsen.no/")
    # policy D(iii): without a fetched privacy/terms/imprint page an L2e is not awarded
    assert v.level == "ambiguous"         # (also: "owned by the Artelia Group" is a group signal)
    # the same page as a name-slug guess (not the registry e-mail domain) must not publish
    v2, _ = run("981124626", pages, lead="name_joined_no", start="https://olavolsen.no/")
    assert v2.level == "ambiguous"


def test_hydreco_strict_proof_is_own_block():
    home = page("home", "https://www.hydreco.com/", "https://hydreco.com/", "hydreco_home.html")
    contact = page("contact", "https://hydreco.com/contact-us", "https://hydreco.com/contact-us", "hydreco_contact.html")
    v, _ = run("920217761", [home, contact], start="https://www.hydreco.com/")
    if v.published:
        assert "Hydreco Hydraulics Norway AS" in v.proof["span"] and v.proof["url"].endswith("/contact-us")


def test_theme_credit_and_foreign_form_entities():
    ents = X.legal_entities("Powered by WordPress. Theme by Think Up Themes Ltd. © Infrabeam AS")
    assert [e["name"] for e in ents] == ["Infrabeam AS"]
    # a foreign legal form never names the Norwegian-form company with the same brand
    assert not X.same_entity(X.entity_key("YABIMO Sp. z o.o."), X.entity_key("YABIMO AS"), True, False)
    assert X.same_entity(X.entity_key("Norway Hydreco Hydraulics Norway AS"), X.entity_key("HYDRECO HYDRAULICS NORWAY AS"),
                         False, False)


def test_shared_contacts_only_for_siblings():
    sc = {"phones": {"51425700": ["945675586", "999152090"]}}
    assert V._shared(sc, "phones", "51425700", "999152090") == ["945675586"]
    assert V._shared(sc, "phones", "51425700", "912345678") == []          # not a sibling of the target
    assert V.address_key("4353", "Plogfabrikkvegen 5") == "4353|plogfabrikkvegen 5"
    assert V.address_key("0278", "Postboks 98") is None


def test_company_label_mailbox_allowed():
    html = ("<html><head><title>TYR - Norsk Tyrkisk Foreningsliv</title></head><body><p>" + "Om foreningen. " * 30 +
            "</p><footer>TYR · Org.nr 970 142 223 · <a href='mailto:tyr@tyr.no'>tyr@tyr.no</a> · "
            "<a href='mailto:ola.nordmann@tyr.no'>ola.nordmann@tyr.no</a></footer></body></html>")
    pages = [V.Page("home", "https://www.tyr.no/", "https://www.tyr.no/", 200, html, "s", "t")]
    rec = {"navn": "TYR", "organisasjonsform": {"kode": "FLI"}, "epostadresse": "ola.nordmann@tyr.no"}
    v = V.verify(pages, rec, "970142223", lead_kind="registry_website", start_url="https://www.tyr.no/")
    emails = [f.value for f in V.collect_facts(v, rec) if f.field == "contact_email"]
    assert emails == []                             # policy L: only generic role mailboxes; personal withheld
