"""Municipality number (kommunenummer) -> county (fylke), 2024 county structure.

Since 1 January 2024 Norway has 15 counties. The first two digits of the four-digit
kommunenummer are the county number, so the county is derived from the municipality number
alone (no lookup, no request). Prefixes that belong to dissolved counties (e.g. 30 Viken,
38 Vestfold og Telemark, 54 Troms og Finnmark) and areas outside the county structure
(21 Svalbard, 22 Jan Mayen) deliberately return None: an unknown county is never guessed.
"""

from __future__ import annotations

METHOD = "kommunenummer_prefix_county_2024"

COUNTIES_2024: dict[str, str] = {
    "03": "Oslo",
    "11": "Rogaland",
    "15": "Møre og Romsdal",
    "18": "Nordland",
    "31": "Østfold",
    "32": "Akershus",
    "33": "Buskerud",
    "34": "Innlandet",
    "39": "Vestfold",
    "40": "Telemark",
    "42": "Agder",
    "46": "Vestland",
    "50": "Trøndelag",
    "55": "Troms",
    "56": "Finnmark",
}


def county_for_municipality(kommunenummer: str | None) -> dict[str, str] | None:
    """'1103' -> {'number': '11', 'name': 'Rogaland'}; None when the number is malformed or the
    prefix is not a 2024 county."""
    if not kommunenummer:
        return None
    k = str(kommunenummer).strip()
    if len(k) != 4 or not k.isdigit():
        return None
    prefix = k[:2]
    name = COUNTIES_2024.get(prefix)
    if name is None:
        return None
    return {"number": prefix, "name": name}
