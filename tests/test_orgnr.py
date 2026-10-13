import pytest

from nordic_company_agent import orgnr


@pytest.mark.parametrize("n", ["923609016", "984851006", "974760673"])
def test_valid(n):
    assert orgnr.is_valid(n)


@pytest.mark.parametrize("n", ["923609017", "12345678", "abcdefghi", "000000001"])
def test_invalid(n):
    assert not orgnr.is_valid(n)


@pytest.mark.parametrize("raw", ["923 609 016", "NO 923 609 016 MVA", "923.609.016", "no923609016mva", " 923-609-016 "])
def test_normalize(raw):
    assert orgnr.normalize(raw) == "923609016"


def test_normalize_rejects_bad_checksum():
    with pytest.raises(orgnr.InvalidOrgNumber):
        orgnr.normalize("923609017")


def test_text_patterns():
    pat = orgnr.text_patterns("923609016")
    assert pat.search("Org.nr: 923 609 016")
    assert pat.search("NO923609016MVA")
    assert not pat.search("Konto 1923609016")


def test_find_labelled():
    txt = "Equinor ASA, Org.nr. 923 609 016 | Webdesign: Byrå AS org nr 984851006"
    assert orgnr.find_labelled_orgnrs(txt) == {"923609016", "984851006"}
    assert orgnr.find_labelled_orgnrs("Foretaksregisteret NO 923 609 016 MVA") == {"923609016"}
