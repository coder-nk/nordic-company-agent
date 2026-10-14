"""Live candidate domains: leads only (a live candidate is published only with the org number printed on it)."""

from nordic_company_agent.web import candidates as C


def kinds(rec):
    return dict(C.candidates(rec))


def test_leading_initials_form_one_label():
    assert kinds({"navn": "P.E. GAARUD AS"})["name_initials_no"] == "pe-gaarud.no"


def test_trailing_post_town_and_region_dropped():
    k = kinds({"navn": "SJØ-SPORT MANDAL AS", "forretningsadresse": {"poststed": "MANDAL", "kommune": "LINDESNES"}})
    assert k["name_short_hyphen_no"] == "sjo-sport.no" and k["name_short_no"] == "sjosport.no"
    assert kinds({"navn": "NETSOLUTION VIKEN AS"})["name_short_no"] == "netsolution.no"


def test_and_co_dropped():
    assert kinds({"navn": "LAPSEN & CO AS"})["name_short_no"] == "lapsen.no"


def test_no_short_variant_for_single_or_short_names():
    k = kinds({"navn": "SLIPETEKNIKK AS", "forretningsadresse": {"poststed": "BERGEN"}})
    assert "name_short_no" not in k and "name_initials_no" not in k
    assert "name_short_no" not in kinds({"navn": "BIL OSLO AS", "forretningsadresse": {"poststed": "OSLO"}})
