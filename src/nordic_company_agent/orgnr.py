"""Norwegian organisation number (organisasjonsnummer) handling.

An organisation number is 9 digits; the last digit is a MOD11 check digit computed
over the first 8 digits with weights 3,2,7,6,5,4,3,2. A remainder that would give
check digit 10 means the number is invalid (Brønnøysundregistrene never issues it).
"""

from __future__ import annotations

import re

_WEIGHTS = (3, 2, 7, 6, 5, 4, 3, 2)
_STRIP = re.compile(r"[\s.\-  ]")


class InvalidOrgNumber(ValueError):
    pass


def check_digit(first8: str) -> int | None:
    total = sum(int(d) * w for d, w in zip(first8, _WEIGHTS))
    rem = 11 - (total % 11)
    if rem == 11:
        return 0
    if rem == 10:
        return None
    return rem


def is_valid(orgnr: str) -> bool:
    if not (len(orgnr) == 9 and orgnr.isdigit()):
        return False
    cd = check_digit(orgnr[:8])
    return cd is not None and cd == int(orgnr[8])


def normalize(raw: str) -> str:
    """Normalize user input such as 'NO 923 609 016 MVA' or '923.609.016' to '923609016'.

    Raises InvalidOrgNumber if the result is not a checksum-valid 9-digit number.
    """
    if raw is None:
        raise InvalidOrgNumber("empty organisation number")
    s = str(raw).strip().upper()
    s = re.sub(r"^NO", "", s)
    s = re.sub(r"MVA$", "", s)
    s = _STRIP.sub("", s)
    if not (len(s) == 9 and s.isdigit()):
        raise InvalidOrgNumber(f"{raw!r} is not a 9-digit organisation number")
    if not is_valid(s):
        raise InvalidOrgNumber(f"{raw!r} fails the MOD11 checksum")
    return s


def formatted(orgnr: str) -> str:
    """'923609016' -> '923 609 016' (the format printed on Norwegian documents)."""
    return f"{orgnr[0:3]} {orgnr[3:6]} {orgnr[6:9]}"


def text_patterns(orgnr: str) -> re.Pattern[str]:
    """Regex that finds this organisation number in free text in its common renderings:
    923609016, 923 609 016, 923.609.016, 923-609-016, NO923609016MVA, NO 923 609 016 MVA.
    Digit boundaries prevent matching inside a longer number (e.g. an account number).
    """
    a, b, c = orgnr[0:3], orgnr[3:6], orgnr[6:9]
    sep = r"[\s.\-  ]?"
    return re.compile(rf"(?<!\d){a}{sep}{b}{sep}{c}(?!\d)")


ANY_ORGNR_IN_TEXT = re.compile(
    r"(?:org(?:anisasjons)?[\s.\-]*(?:nr|nummer|no)\.?|foretaksregisteret|"
    r"business\s+reg(?:istration)?\s*(?:no|number)\.?|company\s+(?:reg(?:istration)?\s*)?(?:no|number)\.?|"
    r"\bNO(?=\s?\d{3}))"
    r"[\s:#. ]*(?:NO\s?)?(\d{3}[\s.\-  ]?\d{3}[\s.\-  ]?\d{3})(?!\d)",
    re.IGNORECASE,
)


def find_labelled_orgnrs(text: str) -> set[str]:
    """Return every checksum-valid org number that appears next to an org-number label
    ('Org.nr', 'Organisasjonsnummer', 'NO ... MVA', ...) in `text`."""
    found: set[str] = set()
    for m in ANY_ORGNR_IN_TEXT.finditer(text):
        digits = _STRIP.sub("", m.group(1))
        if is_valid(digits):
            found.add(digits)
    return found
