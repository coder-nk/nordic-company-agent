from nordic_company_agent.sources.website import _find_date, _verbatim, parse_news_page

HTML = """<html><body><nav><a href="/nyheter">Nyheter</a></nav>
<main>
 <article><time datetime="2026-09-30T08:00:00+02:00">30. september 2026</time>
   <a href="/nyheter/ny-avtale-med-kommunen">Ny rammeavtale med Bergen kommune signert</a></article>
 <div class="post"><span>12.08.2026</span> <a href="https://www.firma.no/nyheter/vi-flytter">Vi flytter til nye lokaler i sentrum</a></div>
 <a href="/nyheter/side/2">Neste side med flere nyheter</a>
 <a href="https://annen.no/nyheter/x">Ekstern nyhet fra et annet nettsted her</a>
 <a href="/kontakt">Kontakt oss for mer informasjon</a>
</main></body></html>"""


def test_parse_news_page():
    items = parse_news_page(HTML, "https://www.firma.no/nyheter", lambda u: "firma.no" in u, "/nyheter")
    urls = [i["url"] for i in items]
    assert urls == ["https://www.firma.no/nyheter/ny-avtale-med-kommunen", "https://www.firma.no/nyheter/vi-flytter"]
    assert items[0]["published"] == "2026-09-30" and items[1]["published"] == "2026-08-12"
    for i in items:  # evidence spans are mapped back to the raw page source when evidence is built
        v = _verbatim(HTML, i["span"])
        assert v and v in HTML


def test_find_date_formats():
    assert _find_date("Publisert 3. mars 2025") == "2025-03-03"
    assert _find_date("2024-11-02") == "2024-11-02"
    assert _find_date("tlf 22 33 44 55") is None
