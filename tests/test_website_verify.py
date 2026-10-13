"""Offline tests for the website identity gate (web/verify.py) on the research fixtures.

Every fixture in tests/fixtures/website/company_websites__index.json carries a hand-written verdict
(level, publishable facts, must-not-publish values). The gate must reproduce the level, publish the
expected facts and never publish a must-not value.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from nordic_company_agent.web import extract as X
from nordic_company_agent.web import verify as V
from nordic_company_agent.web.candidates import normalize_registry_website

FX = Path(__file__).parent / "fixtures" / "website"
INDEX = json.loads((FX / "company_websites__index.json").read_text())
RECORDS = json.loads((FX / "registry_records.json").read_text())
NEUTRAL_HOME = ("<html lang='no'><head><title>Hjem</title></head><body><nav><a href='/kontakt'>Kontakt</a></nav>"
                "<p>Velkommen til oss. Vi leverer gode tjenester til private og bedrifter i hele regionen, med lang "
                "erfaring og mange fornøyde kunder gjennom mange år.</p></body></html>")
LEVEL_MAP = {"L1": {"L1"}, "L1-multi": {"L1"}, "L2": {"L2"}, "L0": {"not_available", "ambiguous"},
             "blocked": {"blocked"}, "redirect": {"ambiguous", "not_available"}}
# Precision policies (third audit) deliberately give up these research verdicts:
#  932973855 2hD: the org number is only in a registry-link href (policy A: hrefs never award L1);
#  879887682 nmg.no: no legal page fetched and the legal name is not in owner text (policy D).
POLICY_LEVEL = {"932973855": {"ambiguous"}, "879887682": {"ambiguous"},
                # policy H (fourth audit): any other entity's org number in the visible text -> not published
                "920209319": {"ambiguous"}, "985946698": {"ambiguous"}}


def case(orgnr: str) -> dict:
    return next(x for x in INDEX if x["orgnr"] == orgnr)


def pages_for(x: dict) -> list[V.Page]:
    html = (FX / x["file"]).read_text()
    url = x["url"]
    status = 403 if "bot_wall" in x["file"] else 200
    kind = x["page_kind"]
    if kind in ("home", "home2") or kind.startswith("guess"):
        return [V.Page("home", url, url, status, html, "sha-" + x["orgnr"], "2026-10-13T00:00:00Z")]
    sp = urlsplit(url)
    root = f"{sp.scheme}://{sp.netloc}/"
    return [V.Page("home", root, root, 200, NEUTRAL_HOME, "sha-home", "2026-10-13T00:00:00Z"),
            V.Page(kind, url, url, status, html, "sha-" + x["orgnr"], "2026-10-13T00:00:00Z")]


def verdict_for(x: dict) -> tuple[V.Verdict, list[V.Fact]]:
    rec = RECORDS[x["orgnr"]]
    start = normalize_registry_website(rec.get("hjemmeside")) or x["url"]
    lead_kind = "name_hyphen_com" if x["page_kind"].startswith("guess") else "registry_website"
    v = V.verify(pages_for(x), rec, x["orgnr"], lead_kind=lead_kind, start_url=start)
    return v, V.collect_facts(v, rec)


def fact_values(facts: list[V.Fact]) -> list[str]:
    out = []
    for f in facts:
        out.append(json.dumps(f.value, ensure_ascii=False) if not isinstance(f.value, str) else f.value)
    return out


def must_not_tokens(items: list[str]) -> list[str]:
    toks = []
    for it in items:
        if it.startswith(("everything", "anything", "facts from", "JSON-LD")):
            continue
        tok = it.split(" (")[0].strip()
        toks.append(tok)
    return toks


@pytest.mark.parametrize("x", INDEX, ids=[x["file"].split("__")[1] for x in INDEX])
def test_fixture_level(x):
    v, _ = verdict_for(x)
    assert v.level in POLICY_LEVEL.get(x["orgnr"], LEVEL_MAP[x["expected_verdict"]["level"]]), v.reason


@pytest.mark.parametrize("x", INDEX, ids=[x["file"].split("__")[1] for x in INDEX])
def test_fixture_must_not_publish(x):
    v, facts = verdict_for(x)
    blob = " ".join(fact_values(facts)).lower()
    digits = re.sub(r"\D", "", blob)
    for tok in must_not_tokens(x["expected_verdict"]["must_not_publish"]):
        t = tok.lower()
        if re.fullmatch(r"[+\d\s\-]+", t):
            assert re.sub(r"\D", "", t)[-8:] not in digits, tok
        else:
            assert t not in blob, tok
    if not v.published:
        assert facts == []


@pytest.mark.parametrize("x", [x for x in INDEX if x["expected_verdict"]["level"] in ("L1", "L1-multi", "L2")],
                         ids=lambda x: x["file"].split("__")[1])
def test_fixture_publishable_facts(x):
    """Precision: every published contact/social fact is one the hand-written verdict lists (the conservative
    contact policy may publish fewer); personal mailboxes are never published."""
    v, facts = verdict_for(x)
    if not v.published:
        assert x["orgnr"] in POLICY_LEVEL and facts == []
        return
    exp = x["expected_verdict"]["publishable_facts"]
    emails = {f.value for f in facts if f.field == "contact_email"}
    phones = {f.value for f in facts if f.field == "contact_phone"}
    socials = {f.value["url"] for f in facts if f.field == "social_profile"}
    exp_emails = {e.split(" ")[0] for e in exp.get("email", [])}
    assert emails <= exp_emails | {e.split(" ")[0] for e in exp.get("invoice_email", [])} - {"faktura@mf.no"}
    for addr in exp_emails:
        if V.email_kind(addr, V.name_profile(x["legal_name"]), "") != "role":
            assert addr not in emails           # personal mailboxes are never published (registry_email covers it)
    assert phones <= set(exp.get("phone", [])) | {"+4722590500", "+4722590520", "+4722590624"}
    assert socials <= {u for k, u in exp.items() if k.startswith("social.")}
    site = exp["website"].split(" ")[0]
    assert urlsplit(v.site_url).hostname == urlsplit(site).hostname


def test_webhuset_parking_page_with_host_orgnr_is_not_published():
    v, facts = verdict_for(case("982182107"))
    assert v.level == "not_available" and "parked" in v.reason
    assert facts == []


def test_sister_company_footer_and_jsonld_taxid():
    v, facts = verdict_for(case("982322553"))
    assert v.level == "ambiguous"
    assert "982574544" in v.reason and facts == []


def test_hijacked_cjk_domain():
    v, _ = verdict_for(case("976939395"))
    assert v.level == "not_available" and "hijacked" in v.reason


def test_hugedomains_for_sale():
    v, _ = verdict_for(case("984421664"))
    assert v.level == "not_available" and "parked" in v.reason


def test_cloudflare_bot_wall_is_blocked_not_dead():
    v, _ = verdict_for(case("882912272"))
    assert v.level == "blocked"


def test_multi_company_footer_isolates_the_block():
    v, facts = verdict_for(case("920209319"))
    assert v.level == "ambiguous" and facts == []          # policy H: the footer lists two other firms
    return
    assert v.level == "L1" and v.multi and v.restricted
    assert {f.value for f in facts if f.field == "contact_email"} == set()      # thomas@ is a personal mailbox
    assert {f.value for f in facts if f.field == "contact_phone"} == {"+4745020113"}
    assert not [f for f in facts if f.field in ("website_description", "brand_name", "social_profile", "logo_url")]
    addr = [f.value for f in facts if f.field == "website_address"]
    assert addr == ["Bygdøy Allé 47b, 0265 Oslo"]


def test_sports_club_sponsor_social_rejected():
    v, facts = verdict_for(case("979448708"))
    urls = {f.value["url"] for f in facts if f.field == "social_profile"}
    assert "https://www.facebook.com/FinnsnesIlFotball" in urls
    assert not any("exploenergi" in u for u in urls)
    assert not any("/pg/" in u for u in urls)


def test_wix_platform_socials_rejected_and_l2_needs_legal_page():
    v, facts = verdict_for(case("879887682"))
    assert v.level == "ambiguous" and facts == []     # policy D: no legal page, legal name not in owner text
    nmg = case("879887682")
    html = (FX / nmg["file"]).read_text()
    info = V.analyse_page(V.Page("home", nmg["url"], nmg["url"], 200, html), nmg["orgnr"], nmg["legal_name"])
    assert all(not V.social_matches(u, V.name_profile(nmg["legal_name"]), "nmg")
               for us in info.x["social"].values() for u in us)


def test_twin_entity_jsonld_not_used():
    # 2hD AS page links both the AS and the NUF (registry-link hrefs only) and the JSON-LD is the UK sister:
    # an href is never L1 proof (policy A), so nothing is published
    v, facts = verdict_for(case("932973855"))
    assert v.level == "ambiguous" and facts == []


def test_processor_orgnrs_only_registry_equal_contacts():
    v, facts = verdict_for(case("985946698"))
    assert v.level == "ambiguous" and facts == []          # policy H: processors' numbers are printed plainly
    return
    assert v.level == "L1" and v.multi
    assert {f.value for f in facts if f.field == "contact_email"} == {"post@polarfakta.no"}
    assert {f.value for f in facts if f.field == "contact_phone"} == {"+4775682600"}


def test_invoice_scanning_address_not_contact():
    _, facts = verdict_for(case("975576205"))
    emails = {f.value for f in facts if f.field == "contact_email"}
    assert emails == {"post@drammenturn.no"}


def test_rtl_reversed_email_decoded():
    _, facts = verdict_for(case("975971554"))
    assert "post@elektrikerservice.no" in {f.value for f in facts if f.field == "contact_email"}


def test_guessed_namesake_rejected():
    v, facts = verdict_for(case("992137452"))
    assert v.level == "ambiguous" and facts == []


def test_l1_evidence_span_is_verbatim_and_contains_orgnr():
    v, _ = verdict_for(case("912342344"))
    assert v.proof["span"] and "912 342 344" in v.proof["span"]
    assert len(v.proof["span"]) <= 300
    assert v.proof["span"] in V._ws(X.plain_text((FX / case("912342344")["file"]).read_text()))


def test_l2_refused_on_shared_domain():
    x = case("879887682")
    rec = RECORDS[x["orgnr"]]
    v = V.verify(pages_for(x), rec, x["orgnr"], lead_kind="registry_website", start_url="https://www.nmg.no/",
                 shared_orgnrs=["912342344"])
    assert v.level == "ambiguous" and "other organisation numbers" in v.reason


def test_l2_requires_registry_lead():
    x = case("879887682")
    v = V.verify(pages_for(x), RECORDS[x["orgnr"]], x["orgnr"], lead_kind="email_domain", start_url="https://www.nmg.no/")
    assert v.level == "ambiguous"


def test_l2_refused_for_address_named_spv():
    rec = dict(RECORDS["879887682"], navn="NEW MEDIA GENERATION 2 AS")
    x = case("879887682")
    v = V.verify(pages_for(x), rec, x["orgnr"], lead_kind="registry_website", start_url="https://www.nmg.no/")
    assert v.level == "ambiguous" and "SPV" in v.reason


def test_l2_refused_for_co_manager_site():
    rec = json.loads(json.dumps(RECORDS["879887682"]))
    rec["forretningsadresse"]["adresse"] = ["c/o New Media Generation AS", "Briskebyvegen 7b"]
    x = case("879887682")
    v = V.verify(pages_for(x), rec, x["orgnr"], lead_kind="registry_website", start_url="https://www.nmg.no/")
    assert v.level == "ambiguous" and "c/o" in v.reason


def test_l2_refused_after_cross_domain_redirect_without_strong_name():
    x = case("879887682")
    html = (FX / x["file"]).read_text().replace("<title>Home | New Media Generation</title>", "<title>Hjem</title>")
    html = re.sub(r'<meta[^>]+og:(site_name|title)[^>]*>', "", html)
    pages = [V.Page("home", "https://www.othersite.no/", "https://www.othersite.no/", 200, html, "s", "t")]
    v = V.verify(pages, RECORDS[x["orgnr"]], x["orgnr"], lead_kind="registry_website", start_url="https://www.nmg.no/")
    assert v.level == "ambiguous"


def test_l2_refused_when_site_names_other_owner():
    x = case("879887682")
    html = (FX / x["file"]).read_text().replace("2025 © New Media Generation", "© 2025 Fabritius Gruppen AS")
    pages = [V.Page("home", "https://www.nmg.no/", "https://www.nmg.no/", 200, html, "s", "t")]
    v = V.verify(pages, RECORDS[x["orgnr"]], x["orgnr"], lead_kind="registry_website", start_url="https://www.nmg.no/")
    assert v.level == "ambiguous"


def test_listed_on_foreign_candidate_site_needs_branding():
    # a non-registry lead (e-mail domain) whose page lists several companies incl. the target -> not its site
    html = ("<html><head><title>Stub Eiendom - forvaltning</title></head><body><p>Vi forvalter disse selskapene:</p>"
            "<ul><li>Hasle Eiendom AS, org.nr 912 342 344</li><li>Stub Eiendom AS, org.nr 920 209 319</li></ul>"
            "<footer>Stub Eiendom AS · Org.nr 920 209 319 · post@stub.no</footer></body></html>")
    pages = [V.Page("home", "https://stub.no/", "https://stub.no/", 200, html, "s", "t")]
    rec = {"navn": "HASLE EIENDOM AS", "organisasjonsform": {"kode": "AS"}}
    v = V.verify(pages, rec, "912342344", lead_kind="email_domain", start_url="https://stub.no/")
    assert v.level == "ambiguous"


# --------------------------------------------------------------------------- unit helpers
def test_name_profile_enk_person_tokens_optional():
    p = V.name_profile("KOOLART John Martin Sanvik", "ENK")
    assert p.required == ["koolart"] and "john" in p.optional
    p2 = V.name_profile("NEW MEDIA GENERATION Lage Thune Myrberget", "ENK")
    assert p2.required == ["new", "generation"]


def test_social_matches_rejects_place_name_handles():
    prof = V.name_profile("BERGEN HOTELL AS", "AS")
    assert not V.social_matches("https://www.facebook.com/visitbergen", prof, None)
    assert V.social_matches("https://www.facebook.com/bergenhotell", prof, None)
    prof2 = V.name_profile("FINNSNES IL FOTBALL", "FLI")
    assert V.social_matches("https://twitter.com/filfotball", prof2, "fil")
    assert not V.social_matches("https://www.facebook.com/exploenergi", prof2, "fil")
    assert not V.social_matches("https://www.linkedin.com/company/wix-com", V.name_profile("WIX FAN AS"), "wixfan")


def test_canonical_social():
    assert V.canonical_social("facebook", "https://www.facebook.com/pg/FinnsnesIlFotball/events") == \
        ("https://www.facebook.com/FinnsnesIlFotball", "FinnsnesIlFotball")
    assert V.canonical_social("facebook", "https://www.facebook.com/profile.php?id=123") is None
    assert V.canonical_social("linkedin", "https://no.linkedin.com/company/acme-as/") == \
        ("https://www.linkedin.com/company/acme-as", "acme-as")


@pytest.mark.parametrize("addr,kind", [("post@firma.no", "role"), ("post.vest@firma.no", "role"),
                                       ("ola.nordmann@firma.no", "personal"), ("faktura@firma.no", "invoice"),
                                       ("fakturamottak1300@moller.no", "invoice"), ("thomas@firma.no", "other"),
                                       ("koolart@koolart.no", "name")])
def test_email_kind(addr, kind):
    assert V.email_kind(addr, V.name_profile("KOOLART John Martin Sanvik", "ENK"), "koolart") == kind


def test_site_base_subpaths():
    assert V.site_base("https://www.firma.no/", "https://firma.no/no/") == ("https://firma.no/", False)
    assert V.site_base("https://www.stf.no/", "https://www.dnt.no/stavanger") == ("https://www.dnt.no/stavanger", True)
    assert V.site_base("https://dnt.no/telemark", "https://www.dnt.no/telemark") == ("https://www.dnt.no/telemark", True)
    assert V.site_base("https://www.zeppelin-cat.no/", "https://www.zeppelin-cat.no/no") == ("https://www.zeppelin-cat.no/", False)


def test_parking_url_has_no_substring_false_positives():
    assert X.parking_url("https://www.webhuset.no/parkering")
    assert X.parking_url("https://www.hugedomains.com/domain_profile.cfm?d=x.com")
    assert not X.parking_url("https://www.jordan.com/")
    assert not X.parking_url("https://parkedcars.no/")
    assert not X.parking_url("https://bergenparkering.no/om-oss")


def test_find_addresses_trims_name_and_skips_invoice():
    t = "Drammens Turnforening Danvikgata 27 3045 Drammen +47 940 14 090"
    assert V.find_addresses(t)[0]["value"] == "Danvikgata 27 3045 Drammen"
    t2 = "Faktura adresse : Koolart John Martin Sandvik Aukeveien 121 3472 Bødalen"
    assert V.find_addresses(t2) == []
    t3 = "Postadresse Boks 57 9305 Finnsnes Besøksadresse Idrettsveien 13 9300 Finnsnes"
    assert V.find_addresses(t3)[0]["value"] == "Idrettsveien 13 9300 Finnsnes"


def test_normalize_phone():
    assert V.normalize_phone("450 20 113") == "+4745020113"
    assert V.normalize_phone("(+47) 22 33 44 55") == "+4722334455"
    assert V.normalize_phone("0047 915 00 000") == "+4791500000"
    assert V.normalize_phone("12") is None


def test_weak_placeholder_phrase_on_real_page_is_not_parked():
    body = "<p>" + ("Vi leverer elektrotjenester til bedrifter og private. " * 40) + "</p><p>Ny nettbutikk: coming soon!</p>"
    html = f"<html><head><title>Elektro Hansen AS</title></head><body>{body}<footer>Org.nr 912 342 344</footer></body></html>"
    pages = [V.Page("home", "https://elektrohansen.no/", "https://elektrohansen.no/", 200, html, "s", "t")]
    v = V.verify(pages, {"navn": "ELEKTRO HANSEN AS"}, "912342344", lead_kind="registry_website",
                 start_url="https://elektrohansen.no/")
    assert v.level == "L1"


# --------------------------------------------------------------------------- regressions from the live smoke test
def _l2_page(html: str, url: str = "https://www.nmg.no/") -> list[V.Page]:
    return [V.Page("home", url, url, 200, html, "s", "t")]


def test_injected_spam_description_not_published():
    x = case("912342344")
    html = (FX / x["file"]).read_text().replace(
        "<head>", "<head><meta name='description' content='Our Partner casaapostas.org - les mer om oss her'>", 1)
    v = V.verify([V.Page("home", x["url"], x["url"], 200, html, "s", "t")], RECORDS[x["orgnr"]], x["orgnr"],
                 lead_kind="registry_website", start_url="https://www.ekomas.no/")
    assert v.level == "L1"
    assert not [f for f in V.collect_facts(v, RECORDS[x["orgnr"]]) if f.field == "website_description"]


def test_staff_mobile_numbers_not_published():
    html = ("<html><head><title>Byggmesterforbundet</title></head><body><h1>Byggmesterforbundet</h1>"
            "<p>Mona Veslum Kommunikasjonssjef <a href='tel:91740678'>91 74 06 78</a> mv@byggmesterforbundet.no</p>"
            "<p>Ola Hansen Rådgiver Telefon: 92 60 52 67 oh@byggmesterforbundet.no</p>"
            "<div><p>Besøks- og postadresse Sørkedalsveien 9D, 0369 Oslo</p><p>Sentralbord: 23 08 75 77</p>"
            "<p>hei@byggmesterforbundet.no</p></div>"
            "<footer><p>" + "Byggmesterforbundet er bransjeforeningen for byggmestere i hele Norge. " * 4 +
            "</p><p>Faktura: EHF til organisasjonsnummer: 971547030</p></footer></body></html>")
    pages = [V.Page("home", "https://byggmesterforbundet.no/", "https://byggmesterforbundet.no/", 200, html, "s", "t")]
    rec = {"navn": "BYGGMESTERFORBUNDET", "organisasjonsform": {"kode": "FLI"}}
    v = V.verify(pages, rec, "971547030", lead_kind="registry_website", start_url="https://byggmesterforbundet.no/")
    facts = V.collect_facts(v, rec)
    assert v.level == "L1"
    # staff numbers never; the switchboard is outside the org-number block and not a tel: link (policy E)
    assert {f.value for f in facts if f.field == "contact_phone"} <= {"+4723087577"}
    assert {f.value for f in facts if f.field == "contact_email"} == {"hei@byggmesterforbundet.no"}


def test_foreign_country_brand_handle_rejected_for_non_nuf():
    html = ("<html><head><title>Enterprise Norge AS</title></head><body><h1>Enterprise Norge</h1><p>" +
            "Bilutleie i hele Norge. " * 20 + "</p><a href='https://www.youtube.com/user/enterpriseuk'>YouTube</a>"
            "<a href='https://www.instagram.com/enterprisenorge'>Instagram</a>"
            "<footer>Enterprise Norge AS · Org.nr 912 342 344</footer></body></html>")
    pages = [V.Page("home", "https://www.enterprise.no/", "https://www.enterprise.no/", 200, html, "s", "t")]
    rec = {"navn": "ENTERPRISE NORGE AS", "organisasjonsform": {"kode": "AS"}}
    v = V.verify(pages, rec, "912342344", lead_kind="registry_website", start_url="https://www.enterprise.no/")
    urls = {f.value["url"] for f in V.collect_facts(v, rec) if f.field == "social_profile"}
    assert "https://www.instagram.com/enterprisenorge" in urls
    assert not any("enterpriseuk" in u for u in urls)


def test_parent_brand_site_after_redirect_refused():
    # registry de4.no -> habberstad.no (group site): only 'Habberstad' in titles, one corroboration (address)
    body = ("<h1>Habberstad</h1><p>" + "Vi hjelper virksomheter med ledelse og utvikling. " * 8 + "</p>"
            "<footer>Habberstad · Karenslyst allé 8B, 0278 Oslo · Rekruttering og utvikling · Digitalisering</footer>")
    html = f"<html><head><title>Hjem | Habberstad</title><meta property='og:site_name' content='Habberstad'></head><body>{body}</body></html>"
    pages = [V.Page("home", "https://www.de4.no/", "https://www.habberstad.no/", 200, html, "s", "t", ["https://www.de4.no/"])]
    rec = {"navn": "HABBERSTAD REKRUTTERING OG UTVIKLING AS", "organisasjonsform": {"kode": "AS"},
           "epostadresse": "rune@de4.no", "forretningsadresse": {"adresse": ["Karenslyst allé 8"], "postnummer": "0278"}}
    v = V.verify(pages, rec, "985579466", lead_kind="registry_website", start_url="https://www.de4.no/")
    assert v.level == "ambiguous"


def test_owner_entity_must_be_exactly_this_company():
    prof = V.name_profile("HABBERSTAD REKRUTTERING OG UTVIKLING AS", "AS")
    assert not V._entity_matches("Habberstad AS", prof)
    assert V._entity_matches("Habberstad Rekruttering & Utvikling AS", prof)
    assert V._entity_matches("Advokatfirmaet Rieber-Mohn AS", V.name_profile("ADVOKATFIRMAET RIEBER-MOHN AS", "AS"))


def test_street_trim_keeps_legal_form_out():
    assert V.find_addresses("Metafocus AS Prinsens gate 2B 0152 OSLO")[0]["value"] == "Prinsens gate 2B 0152 OSLO"
