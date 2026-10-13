"""Read Brønnøysund bulk snapshots (CSV from /enheter/lastned/csv, or the JSON bulk file) and
convert rows to the same nested shape as the live `/enheter/{orgnr}` API, so every module
consumes one record format regardless of where identity came from."""

from __future__ import annotations

import csv
import gzip
import io
import json
from pathlib import Path
from typing import Any, Iterable, Iterator

# CSV column names that differ in case from the API's JSON keys.
_KEY_FIX = {
    "registreringsdatoenhetsregisteret": "registreringsdatoEnhetsregisteret",
    "registrertIMvaRegisteret": "registrertIMvaregisteret",
    "registreringsdatoantallansatteNAVAaregisteret": "registreringsdatoAntallAnsatteNAVAaregisteret",
}
_BOOL_KEYS = {
    "harRegistrertAntallAnsatte", "registrertIMvaregisteret", "registrertIFrivillighetsregisteret",
    "registrertIForetaksregisteret", "registrertIStiftelsesregisteret", "registrertIPartiregisteret", "konkurs",
    "underAvvikling", "underTvangsavviklingEllerTvangsopplosning", "erIKonsern",
}
_INT_KEYS = {"antallAnsatte", "kapital.antallAksjer"}
_LIST_KEYS = {"vedtektsfestetFormaal", "aktivitet", "frivilligMvaRegistrertBeskrivelser"}


def _open_text(path: Path):
    raw = path.open("rb")
    head = raw.read(2)
    raw.seek(0)
    if head == b"\x1f\x8b":
        return io.TextIOWrapper(gzip.GzipFile(fileobj=raw), encoding="utf-8", newline="")
    return io.TextIOWrapper(raw, encoding="utf-8", newline="")


def csv_row_to_enhet(row: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for col, val in row.items():
        if col is None or val is None or val == "":
            continue
        key = _KEY_FIX.get(col, col)
        if key in _BOOL_KEYS:
            v: Any = val.strip().lower() == "true"
        elif key in _INT_KEYS:
            try:
                v = int(val)
            except ValueError:
                v = val
        elif key in _LIST_KEYS:
            v = [x for x in val.split("\n") if x.strip()]
        elif key.endswith(".adresse"):
            v = [x for x in val.split("\n") if x.strip()]
        elif key == "paategninger":
            if val.strip().lower() in ("false", "true", ""):
                continue
            v = [{"tekst": x} for x in val.split("\n") if x.strip()]
        else:
            v = val
        parts = key.split(".")
        cur = out
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
            if not isinstance(cur, dict):
                break
        else:
            cur[parts[-1]] = v
    return out


def iter_snapshot(path: str | Path) -> Iterator[dict[str, Any]]:
    """Yield API-shaped entity dicts from a CSV(.gz) or JSON(.gz) Brreg bulk snapshot."""
    path = Path(path)
    fh = _open_text(path)
    first = fh.read(1)
    rest = fh
    if first in ("[", "{"):
        data = json.loads(first + rest.read())
        rows = data if isinstance(data, list) else data.get("_embedded", {}).get("enheter", [])
        yield from rows
        return
    reader = csv.DictReader(_prepend(first, rest))
    for row in reader:
        yield csv_row_to_enhet(row)


def _prepend(first: str, fh) -> Iterable[str]:
    line = fh.readline()
    yield first + line
    for line in fh:
        yield line


def load_snapshot(path: str | Path, wanted: set[str] | None = None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for rec in iter_snapshot(path):
        org = str(rec.get("organisasjonsnummer") or "")
        if wanted is None or org in wanted:
            out[org] = rec
            if wanted is not None and len(out) == len(wanted):
                break
    return out
