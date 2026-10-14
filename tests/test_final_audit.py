"""Regression tests from the final audit of the deliverables: registry-filed contacts on a group / chain / accountant /
sister domain are labelled related (domain co-filers, and the website gate's own verdict carried over), and news
posts presenting other companies are dropped."""

from __future__ import annotations

from types import SimpleNamespace

from nordic_company_agent.contact_sharing import label_by_website, label_shared
from nordic_company_agent.envelope import Claim, CompanyResult
from nordic_company_agent.sources.website import parse_feed

CACHES = {
    "registry_contact_filers": {
        "phones": {},
        "emails": {"kiwi.vigeland@ngbutikk.net": ["991192190"], "spar.x@ngbutikk.net": ["916325363"],
                   "post@vikrekneskap.no": ["979868626"], "per@vikrekneskap.no": ["930670219"],
                   "ola@gmail.com": ["930670219"], "kari@gmail.com": ["912345678"],
                   "post@eget.no": ["930670219"]},
    },
    "registry_shared_contacts": {"domains": {}, "names": {"979868626": "VIK REKNESKAP AS"}},
    "registry_website_domains": {"rema.no": ["982254604", "814063402"]},
}


def ctx():
    return SimpleNamespace(shared={}, caches=SimpleNamespace(json=lambda name: CACHES.get(name)))


def company(orgnr, *claims, website=None, name="TEST AS"):
    c = CompanyResult(orgnr)
    c.add([Claim(field="legal_name", value=name), *claims])
    if website is not None:
        c.data["website"] = website
    return c


def rel(c, field):
    return [x.relation for x in c.claims if x.field == field]


def test_email_on_a_domain_other_organisations_file_is_related():
    c = company("930670219", Claim(field="registry_email", value="per@vikrekneskap.no"))
    label_shared(ctx(), [c])
    cl = c.get("registry_email")[0]
    assert cl.relation == "related" and cl.confidence <= 0.6 and "vikrekneskap.no" in cl.note


def test_chain_mailbox_domain_and_chain_website_email_are_related():
    a = company("991192190", Claim(field="registry_email", value="kiwi.vigeland@ngbutikk.net"))
    b = company("988560618", Claim(field="registry_email", value="svelvik@rema.no"))
    label_shared(ctx(), [a, b])
    assert rel(a, "registry_email") == ["related"] and rel(b, "registry_email") == ["related"]


def test_freemail_and_unshared_own_domain_stay_self():
    c = company("930670219", Claim(field="registry_email", value="ola@gmail.com"),
                Claim(field="registry_website", value="www.eget.no"))
    label_shared(ctx(), [c])
    assert rel(c, "registry_email") == ["self"] and rel(c, "registry_website") == ["self"]


def test_website_verdict_carries_over_to_registry_contacts():
    web = {"state": "ambiguous", "leads": [{"kind": "registry_website", "start": "https://www.energeia.no/",
                                            "state": "ambiguous",
                                            "reason": "https://www.energeia.no/: group/foreign site (owner statement "
                                                      "names Energeia AS; sister entity: Energeia AS)"}]}
    c = company("922275076", Claim(field="registry_website", value="www.energeia.no"),
                Claim(field="registry_email", value="jarl@energeia.no"),
                Claim(field="registry_email", value="jarl@gmail.com", qualifier="2"), website=web)
    assert label_by_website(c) == 2
    assert rel(c, "registry_website") == ["related"] and rel(c, "registry_email") == ["related", "self"]


def test_no_proof_or_published_site_does_not_carry_over():
    no_proof = {"state": "ambiguous", "leads": [{"start": "https://www.firma.no/", "state": "ambiguous",
                                                 "reason": "no visible labelled organisation number"}]}
    c = company("912345678", Claim(field="registry_website", value="www.firma.no"), website=no_proof)
    assert label_by_website(c) == 0 and rel(c, "registry_website") == ["self"]
    published = {"state": "available", "url": "https://firma.no/no/",
                 "leads": [{"start": "https://firma.no/", "state": "ambiguous", "reason": "group site: x"}]}
    c = company("912345678", Claim(field="registry_website", value="www.firma.no"), website=published)
    assert label_by_website(c) == 0


def test_property_company_on_an_operating_brand_site_is_related():
    web = {"state": "ambiguous", "leads": [{"start": "https://www.hobbyboat.no/", "state": "ambiguous",
                                            "reason": "company name not prominent on the site (missing: eiendom)"}]}
    c = company("899220412", Claim(field="registry_website", value="www.hobbyboat.no"), website=web,
                name="HOBBY BOAT EIENDOM AS")
    assert label_by_website(c) == 1
    c = company("912345678", Claim(field="registry_website", value="www.hobbyboat.no"), website=web,
                name="HOBBY BOAT AS")
    assert label_by_website(c) == 0


FEED = """<?xml version="1.0"?><rss><channel>
<item><title><![CDATA[PS TRAFIKKSKOLE AS]]></title><link>https://www.smaa.no/post/ps</link>
<category><![CDATA[PRESENTASJON AV MEDLEMMER]]></category><pubDate>Tue, 23 Sep 2026 07:59:30 GMT</pubDate></item>
<item><title><![CDATA[ØSTFOLD TRYKKERI]]></title><link>https://www.smaa.no/post/trykk</link>
<category><![CDATA[PRESENTASJON AV MEDLEMMER]]></category><pubDate>Wed, 29 Apr 2026 05:37:03 GMT</pubDate></item>
<item><title><![CDATA[HØSTMARKED 2026]]></title><link>https://www.smaa.no/post/marked</link>
<category><![CDATA[AKTIVITETER I SPYDEBERG]]></category><pubDate>Thu, 16 Apr 2026 12:24:44 GMT</pubDate></item>
</channel></rss>""".encode()


def test_member_presentation_posts_are_not_news():
    items = parse_feed(FEED, "https://www.smaa.no/blog-feed.xml", lambda u: "smaa.no" in u)
    assert [i["title"] for i in items] == ["HØSTMARKED 2026"]


def test_domain_label_yields_to_a_verified_own_site():
    caches = dict(CACHES, registry_contact_filers={"phones": {}, "emails": {"post@kontram.no": ["984227949"],
                                                                            "lager@kontram.no": ["912345678"]}})
    cx = SimpleNamespace(shared={}, caches=SimpleNamespace(json=lambda name: caches.get(name)))
    c = company("984227949", Claim(field="registry_website", value="www.kontram.no"),
                Claim(field="registry_email", value="post@kontram.no"))
    label_shared(cx, [c])
    assert rel(c, "registry_website") == ["related"] and rel(c, "registry_email") == ["related"]
    c.data["website"] = {"state": "available", "url": "https://www.kontram.no/", "leads": []}
    label_by_website(c)
    assert rel(c, "registry_website") == ["self"] and rel(c, "registry_email") == ["self"]
    assert all(x.note is None and x.confidence == 1.0 for x in c.claims)
