"""SkatteFUNN projects, Research Council applications/grants and Mattilsynet food-safety inspections
(batch module, tier ``batch_external``, **0 HTTP requests**).

Input: the declared cache ``cache/public_records.jsonl.gz`` (+ ``.meta.json``) built offline by
``tools/build_public_records_cache.py`` from three public files:

* SkatteFUNN application lists (Norges forskningsråd, xlsx; NLOD 2.0 per data.norge.no),
* Forskningsrådet open data "Søknader v2" (CSV on GitHub; NLOD 2.0 per data.norge.no),
* Mattilsynet smilefjes inspections (CSV; CC BY 4.0 per data.norge.no). Its ``orgnummer`` is usually
  the inspected location (underenhet); the builder maps it to the parent enhet via Enhetsregisteret.

Claims (one per record, the 10 most recent per kind are in the cache):

* ``tax_incentive_project`` [record_id] {project_number, application_number?, title, status, date,
  decision_date, period, summary}
* ``research_grant`` [record_id] {project_number, title, status, phase, date, amount (Decimal), currency,
  period, programme}
* ``food_inspection`` [record_id] {date, grade, smiley, location_name, location_organisation_number,
  address}; ``relation="subunit"`` when the inspected location is a sub-unit of the company.

Precision: a SkatteFUNN / Research Council record is published only when its own organisation-number
cell equals the company's number. An inspection is published only when its location number is the
company itself or one of its registered sub-units (the run's sub-unit list when present; otherwise the
cached Enhetsregisteret mapping, which is noted and given lower confidence).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from .. import envelope
from ..envelope import OFFICIAL_GRANTS, Claim, CompanyResult, Evidence, ModuleOutcome, utc_iso

MODULE = "public_records"
VERSION = "public_records@1"
TIER = "batch_external"
CACHE_NAME = "public_records"
CACHE_FILE = f"cache/{CACHE_NAME}.jsonl.gz"
BUILDER = "tools/build_public_records_cache.py"

# Mattilsynet's inspection register is not a grants source: own source class (kept local to this module).
OFFICIAL_FOOD_INSPECTIONS = "official_food_inspections"

KINDS = ("skattefunn", "research_grant", "food_inspection")
FIELD = {"skattefunn": "tax_incentive_project", "research_grant": "research_grant",
         "food_inspection": "food_inspection"}
SOURCE_CLASS = {"skattefunn": OFFICIAL_GRANTS, "research_grant": OFFICIAL_GRANTS,
                "food_inspection": OFFICIAL_FOOD_INSPECTIONS}
SOURCE_NAME = {
    "skattefunn": "Norges forskningsråd, SkatteFUNN application list",
    "research_grant": "Norges forskningsråd open data, applications and grants (Søknader v2)",
    "food_inspection": "Mattilsynet, smilefjes food-safety inspections",
}
LABEL = {"skattefunn": "SkatteFUNN", "research_grant": "Research Council", "food_inspection": "food-inspection"}
SKATTEFUNN_PAGE = "https://www.forskningsradet.no/skattefunn/suksesshistorier/"
NFR_DATASET_PAGE = "https://github.com/Forskningsradet/open-data/tree/main/datasets/soknader2"
SMILEFJES_PAGE = "https://smilefjes.mattilsynet.no/"

LICENCE_SKATTEFUNN = ("SkatteFUNN application lists, Norges forskningsråd. Norwegian Licence for Open Government Data "
                      "(NLOD) 2.0 (https://data.norge.no/nlod/en/2.0) as registered in the national data catalogue "
                      "(https://data.norge.no/datasets/f82d3d4a-b0f8-3220-83ef-15fbd854c54f); the download page "
                      "states no licence.")
LICENCE_NFR = ("Forskningsrådet open data, Søknader v2 (https://github.com/Forskningsradet/open-data). Norwegian Licence "
               "for Open Government Data (NLOD) 2.0 (https://data.norge.no/nlod/en/2.0) as registered in the national "
               "data catalogue (https://data.norge.no/datasets/349766ba-b5fd-3a04-8d82-77ec8928d6f2).")
LICENCE_MATTILSYNET = ("Mattilsynet smilefjes inspection data (Smilefjestilsyn på serveringssteder). Creative Commons "
                       "Attribution 4.0 (https://creativecommons.org/licenses/by/4.0/) as registered in the national "
                       "data catalogue (https://data.norge.no/datasets/288aa74c-e3d3-492e-9ede-e71503b3bfd9).")
LICENCE = {"skattefunn": LICENCE_SKATTEFUNN, "research_grant": LICENCE_NFR, "food_inspection": LICENCE_MATTILSYNET}
# Evidence carries a short licence id; the envelope lists the full text once per id.
for _text, _id in ((LICENCE_SKATTEFUNN, "NLOD-2.0-SkatteFUNN"), (LICENCE_NFR, "NLOD-2.0-Forskningsradet"),
                   (LICENCE_MATTILSYNET, "CC-BY-4.0-Mattilsynet")):
    envelope.LICENCE_IDS.setdefault(_text, _id)
    envelope.LICENCE_TEXTS.setdefault(_id, _text)
del _text, _id

CONFIDENCE = 0.95                 # exact organisation number, read from a declared cache of the official file
CONFIDENCE_CACHED_MAPPING = 0.85  # inspection at a location whose parent comes only from the cached brreg mapping
FOOD_FRESH_DAYS = 2               # Mattilsynet withdraws invalidated inspections and asks users to refresh daily
SPAN_MAX = 300

GRADE_TEXT = {"0": "no breaches found (smiling face)", "1": "minor breaches, no follow-up (smiling face)",
              "2": "breaches requiring follow-up (straight mouth)", "3": "serious breaches (sad mouth)"}
STATUS_TEXT = {"approved": "approved", "rejected": "rejected", "no_decision_recorded": "no decision recorded",
               "granted": "granted", "completed": "completed (granted)", "under_review": "under review",
               "received": "received"}


# ----------------------------------------------------------------------------- helpers
def _eligible(company: CompanyResult) -> bool:
    return (company.data.get("identity") or {}).get("match_status") in (None, "exact")


def _digits(v: Any) -> str:
    return "".join(ch for ch in str(v or "") if ch.isdigit())


def _subunits(company: CompanyResult) -> tuple[dict[str, str] | None, bool]:
    """({sub-unit orgnr: name} or None when no list was provided this run, list complete?)."""
    raw = company.data.get("subunits")
    if raw is None:
        return None, False
    if isinstance(raw, dict):
        raw = raw.get("items") or raw.get("underenheter") or raw.get("subunits") or []
    out: dict[str, str] = {}
    for s in raw or []:
        if not isinstance(s, dict):
            continue
        o = str(s.get("organisasjonsnummer") or s.get("organisation_number") or "")
        if len(o) == 9 and o.isdigit():
            out[o] = s.get("navn") or s.get("name") or ""
    complete = bool((company.data.get("subunits_meta") or {}).get("complete", True))
    return out, complete


def _parse_ts(s: Any) -> datetime | None:
    if not isinstance(s, str) or not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _span(cells: dict[str, Any], order: list[str]) -> str | None:
    """Verbatim cell values as `"header":"value"` members, the identifying cells first (<= 300 chars)."""
    keys = [k for k in order if k in cells] + sorted(k for k in cells if k not in order)
    parts = [f"{json.dumps(k, ensure_ascii=False)}:{json.dumps(str(cells[k]), ensure_ascii=False)}" for k in keys]
    s = ",".join(parts)
    return s[:SPAN_MAX] or None


def _decimal(s: Any) -> Decimal | None:
    if s is None or s == "":
        return None
    try:
        d = Decimal(str(s))
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def _fmt_amount(d: Decimal, currency: str | None) -> str:
    q = d.quantize(Decimal(1)) if d == d.to_integral_value() else d
    txt = f"{q:,}".replace(",", " ")
    return f"{currency} {txt}" if currency else txt


def _period_text(p: Any) -> str:
    if not isinstance(p, dict):
        return ""
    a, b = p.get("from"), p.get("to")
    if a and b:
        return f"{a}" if a == b else f"{a}-{b}"
    return str(a or b or "")


def _row_orgnr_cell(row: dict[str, Any]) -> str:
    cells = row.get("raw_cells") or {}
    for k in ("Organisasjonsnr", "Organisasjonsnummer", "organisasjonsnummer", "orgnummer"):
        if k in cells:
            return _digits(cells[k])
    return ""


# ----------------------------------------------------------------------------- entry point
async def run_batch(ctx, companies: list[CompanyResult]) -> None:
    try:
        _run_batch(ctx, companies)
    except Exception as exc:  # noqa: BLE001 - modules never raise
        for c in companies:
            if MODULE not in c.modules:
                c.error(MODULE, "failed", f"{type(exc).__name__}: {exc}")
                c.outcome(ModuleOutcome(MODULE, "failed", note=f"internal error: {type(exc).__name__}"))


def _run_batch(ctx, companies: list[CompanyResult]) -> None:
    targets: list[CompanyResult] = []
    for c in companies:
        if _eligible(c):
            targets.append(c)
        else:
            c.outcome(ModuleOutcome(MODULE, "not_applicable",
                                    note="identity not confirmed as an exact organisation-number match"))
    if not targets:
        return
    meta = ctx.caches.meta(CACHE_NAME)
    index = ctx.caches.jsonl_index(CACHE_NAME)
    if meta is None and not index:
        msg = f"not checked: declared cache {CACHE_FILE} is missing (build it with {BUILDER})"
        for c in targets:
            c.error(MODULE, "failed", msg)
            c.add(_placeholders(None, "failed", msg, {}))
            c.outcome(ModuleOutcome(MODULE, "failed", note=msg))
        return
    meta = meta or {}
    built_at = meta.get("built_at")
    files = {f.get("url"): f for f in meta.get("files") or [] if f.get("url")}
    # Source files cited by evidence: list them once in the run's snapshot index (bytes not stored).
    for f in files.values():
        if f.get("sha256"):
            ctx.snapshots.index.setdefault(f["sha256"], {
                "sha256": f["sha256"], "url": f["url"], "retrieved_at": f.get("fetched_at") or built_at,
                "bytes": f.get("bytes"), "last_modified": f.get("last_modified"),
                "declared_cache": f"{CACHE_FILE} (built {built_at}); the file itself is not stored in the run"})
    by_location: dict[str, list[dict[str, Any]]] = {}
    for rows in index.values():
        for r in rows:
            if r.get("kind") == "food_inspection" and r.get("location_organisation_number"):
                by_location.setdefault(str(r["location_organisation_number"]), []).append(r)
    now = datetime.now(timezone.utc)
    built_dt = _parse_ts(built_at)
    age_days = (now - built_dt).total_seconds() / 86400 if built_dt else None
    for c in targets:
        _emit(ctx, c, index.get(c.organisation_number, []), by_location, meta, files, built_at, age_days)


def _evidence(row: dict[str, Any], kind: str, built_at: str | None) -> Evidence:
    cells = row.get("raw_cells") or {}
    if kind == "skattefunn":
        order = ["Organisasjonsnr", "Organisasjonsnummer", "Prosjektnummer", "Søknadsnummer", "Godkjent",
                 "Søknad godkjent", "Søknad avslått", "Søknadsdato", "Innsendt dato", "Vedtaksdato", "Prosjekttittel"]
        page = SKATTEFUNN_PAGE
    elif kind == "research_grant":
        order = ["organisasjonsnummer", "prosjektnummer", "prosjektfase", "tildelt_belop", "soknadsdato",
                 "prosjekttittel"]
        pn = row.get("project_number")
        page = f"https://prosjektbanken.forskningsradet.no/project/FORISS/{pn}" if pn else NFR_DATASET_PAGE
    else:
        order = ["orgnummer", "navn", "dato", "total_karakter", "tilsynid"]
        page = SMILEFJES_PAGE
    return Evidence(
        source_url=row.get("source_url") or "", source_class=SOURCE_CLASS[kind], source_name=SOURCE_NAME[kind],
        retrieved_at=built_at or utc_iso(), content_sha256=row.get("source_sha256"),
        claim_span=_span(cells, order), locator=row.get("row_key"), extraction_method=f"{VERSION}:cache",
        page_url=page, effective_at=row.get("decision_date") or row.get("date"), licence=LICENCE[kind],
        retrieved_via=f"declared cache {CACHE_FILE} (built {built_at} by {BUILDER})")


def _file_evidence(kind: str, files: dict[str, dict[str, Any]], built_at: str | None) -> list[Evidence]:
    """Evidence for 'checked, nothing there': the source file(s) of this kind in the cache."""
    out = []
    for url, f in sorted(files.items()):
        if f.get("kind") != kind:
            continue
        out.append(Evidence(source_url=url, source_class=SOURCE_CLASS[kind], source_name=SOURCE_NAME[kind],
                            retrieved_at=built_at or utc_iso(), content_sha256=f.get("sha256"),
                            locator=f"cache:{CACHE_NAME}.meta.json#files", extraction_method=f"{VERSION}:cache",
                            page_url={"skattefunn": SKATTEFUNN_PAGE, "research_grant": NFR_DATASET_PAGE}.get(
                                kind, SMILEFJES_PAGE),
                            licence=LICENCE[kind],
                            retrieved_via=f"declared cache {CACHE_FILE} (built {built_at} by {BUILDER})"))
    return out


def _placeholders(kinds: list[str] | None, state: str, note: str, files: dict[str, dict[str, Any]],
                  built_at: str | None = None) -> list[Claim]:
    return [Claim(field=FIELD[k], value=None, availability=state, evidence=_file_evidence(k, files, built_at),
                  confidence=1.0, method=f"{VERSION}:absent", note=note)
            for k in (kinds if kinds is not None else list(KINDS))]


def _emit(ctx, company: CompanyResult, rows: list[dict[str, Any]], by_location: dict[str, list[dict[str, Any]]],
          meta: dict[str, Any], files: dict[str, dict[str, Any]], built_at: str | None, age_days: float | None) -> None:
    o = company.organisation_number
    subs, subs_complete = _subunits(company)
    built_day = (built_at or "")[:10] or "an unknown date"
    claims: dict[str, list[Claim]] = {k: [] for k in KINDS}
    totals: dict[str, int] = {}      # records of this kind in the source file (before the cache's cap)
    cached: dict[str, int] = {}      # records of this kind kept in the cache for this company
    dropped: dict[str, int] = {}

    def drop(reason: str) -> None:
        dropped[reason] = dropped.get(reason, 0) + 1

    # Food inspections at the company's *current* sub-units filed under another parent in the cache
    # (sub-unit moved since the cache was built) are also considered; the run's list proves the relation.
    cand = [r for r in rows if r.get("kind") in KINDS]
    seen_ids = {r.get("record_id") for r in cand}
    for s in sorted(subs or {}):
        for r in by_location.get(s, []):
            if r.get("record_id") not in seen_ids:
                cand.append(r)
                seen_ids.add(r.get("record_id"))

    food_stale = age_days is not None and age_days > FOOD_FRESH_DAYS
    for r in cand:
        kind = r.get("kind")
        if kind in KINDS and str(r.get("organisation_number")) == o:
            cached[kind] = cached.get(kind, 0) + 1
            totals[kind] = max(totals.get(kind, 0), int(r.get("company_kind_total") or 1))
        if kind in ("skattefunn", "research_grant"):
            # The source row's own organisation-number cell must be exactly this company.
            if str(r.get("organisation_number")) != o or _row_orgnr_cell(r) != o:
                drop("organisation_number_mismatch")
                continue
            claims[kind].append(_grant_claim(r, kind, built_at))
        elif kind == "food_inspection":
            loc = str(r.get("location_organisation_number") or "")
            if not loc or _row_orgnr_cell(r) != loc:
                drop("location_number_mismatch")
                continue
            if loc == o:
                relation, basis, conf, note = "self", "organisation_number_exact", CONFIDENCE, None
            elif subs is not None and loc in subs:
                relation, basis, conf = "subunit", "location_subunit_of_organisation_number", CONFIDENCE
                note = f"inspection at registered sub-unit (underenhet) {loc} {subs.get(loc) or ''}".rstrip()
            elif str(r.get("organisation_number")) == o and r.get("parent_basis") == "underenhet_overordnetEnhet" \
                    and (subs is None or not subs_complete):
                relation, basis, conf = "subunit", "location_subunit_of_organisation_number_cached", \
                    CONFIDENCE_CACHED_MAPPING
                note = (f"inspection at sub-unit (underenhet) {loc} {r.get('location_registry_name') or ''}".rstrip()
                        + f"; overordnetEnhet per Enhetsregisteret when the cache was built ({built_day}), "
                          "not re-checked against this run's sub-unit list")
            else:
                # The run's complete sub-unit list does not contain this location: not provably the company's.
                drop("location_not_a_registered_subunit")
                continue
            if food_stale:
                conf = round(conf - 0.1, 3)
                stale = (f"inspection cache is {age_days:.0f} days old; Mattilsynet withdraws invalidated "
                         "inspections and asks users to refresh daily")
                note = f"{note}; {stale}" if note else stale
            claims[kind].append(_inspection_claim(r, built_at, relation, basis, conf, note))
        else:
            drop("unknown_kind")

    out: list[Claim] = []
    absent: list[str] = []
    for kind in KINDS:
        cl = sorted(claims[kind], key=lambda c: (c.effective_at or "", c.qualifier or ""), reverse=True)
        if cl:
            out.extend(cl)
        else:
            absent.append(kind)
    published = {FIELD[k]: len(claims[k]) for k in KINDS}
    note_absent = (f"checked the declared public-records cache built {built_day}: no "
                   + " or ".join(LABEL[k] for k in absent) + " records for this organisation number")
    if out:
        out.extend(_placeholders(absent, "not_available", note_absent, files, built_at))
    company.add(out)
    company.data[MODULE] = {
        "published": published,
        "source_totals": {FIELD[k]: totals.get(k, 0) for k in KINDS},
        "cached": {FIELD[k]: cached.get(k, 0) for k in KINDS},
        "approved_tax_incentive_projects": sum(1 for c in claims["skattefunn"] if c.value.get("status") == "approved"),
        "granted_research_projects": sum(1 for c in claims["research_grant"]
                                         if c.value.get("status") in ("granted", "completed")),
        "inspected_locations": len({c.value.get("location_organisation_number") for c in claims["food_inspection"]}),
        "latest_inspection": next((c.value for c in sorted(claims["food_inspection"],
                                                           key=lambda c: (c.effective_at or "", c.qualifier or ""),
                                                           reverse=True)), None),
        "dropped": dropped,
        "subunits_known": subs is not None,
        "cache_built_at": built_at,
    }
    dropped_txt = ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in sorted(dropped.items()))
    if not out:
        msg = ("checked the declared public-records cache built " + built_day
               + ": no SkatteFUNN, Research Council or food-inspection records")
        if dropped:
            msg += f" (records not published: {dropped_txt})"
        company.add(_placeholders(None, "not_available", msg, files, built_at))
        company.outcome(ModuleOutcome(MODULE, "not_available", note=msg, retrieved_at=built_at))
        return
    parts = []
    for kind in KINDS:
        n, tot = len(claims[kind]), totals.get(kind, 0)
        if not n:
            continue
        word = {"skattefunn": "SkatteFUNN project application", "research_grant": "Research Council application",
                "food_inspection": "food-safety inspection"}[kind]
        txt = f"{n} {word}{'s' if n != 1 else ''}"
        if tot > cached.get(kind, 0):
            txt += f" (the cache keeps the {cached.get(kind, 0)} most recent of {tot})"
        parts.append(txt)
    note = "; ".join(parts) + f" in the declared public-records cache built {built_day}"
    if dropped:
        note += f"; not published: {dropped_txt}"
    company.outcome(ModuleOutcome(MODULE, "available", note=note, retrieved_at=built_at))


def _grant_claim(r: dict[str, Any], kind: str, built_at: str | None) -> Claim:
    rid = str(r.get("record_id"))
    title = r.get("title")
    status = r.get("status")
    ev = _evidence(r, kind, built_at)
    if kind == "skattefunn":
        value: dict[str, Any] = {"project_number": r.get("project_number"), "title": title, "status": status,
                                 "date": r.get("date"), "decision_date": r.get("decision_date"),
                                 "period": r.get("period"), "summary": r.get("summary")}
        if r.get("application_number"):
            value["application_number"] = r["application_number"]
        if r.get("summary_truncated"):
            value["summary_truncated"] = True
        per = _period_text(r.get("period"))
        display = (f'SkatteFUNN {STATUS_TEXT.get(status, status)}: "{title}"'
                   + (f" ({per})" if per else "") + (f", applied {r['date']}" if r.get("date") else ""))
        return Claim(field="tax_incentive_project", value=value, evidence=[ev], qualifier=rid, confidence=CONFIDENCE,
                     display=display, effective_at=r.get("decision_date") or r.get("date"),
                     method="skattefunn_list_orgnr_exact")
    amount = _decimal(r.get("amount"))
    currency = r.get("currency") if amount is not None else None
    value = {"project_number": r.get("project_number"), "title": title, "status": status, "phase": r.get("phase"),
             "date": r.get("date"), "amount": amount, "currency": currency, "period": r.get("period"),
             "programme": r.get("programme")}
    per = _period_text(r.get("period"))
    display = (f'Research Council {STATUS_TEXT.get(status, status)}: "{title}"'
               + (f", {_fmt_amount(amount, currency)}" if amount is not None else "")
               + (f" ({per})" if per else ""))
    note = ("amount is the total awarded for the whole project period (tildelt_belop); currency NOK by "
            "Forskningsrådet convention (not stated in the file)") if amount is not None else None
    return Claim(field="research_grant", value=value, evidence=[ev], qualifier=rid, confidence=CONFIDENCE,
                 display=display, effective_at=r.get("date"), method="forskningsradet_soknader_orgnr_exact", note=note)


def _inspection_claim(r: dict[str, Any], built_at: str | None, relation: str, basis: str, conf: float,
                      note: str | None) -> Claim:
    grade = r.get("grade")
    value = {"date": r.get("date"), "grade": grade, "smiley": r.get("smiley"), "location_name": r.get("location_name"),
             "location_organisation_number": r.get("location_organisation_number"), "address": r.get("address")}
    display = (f"{r.get('date')}: smilefjes grade {grade}"
               + (f" - {GRADE_TEXT[grade]}" if grade in GRADE_TEXT else "")
               + f" at {r.get('location_name')}" + (f", {r['address']}" if r.get("address") else ""))
    return Claim(field="food_inspection", value=value, evidence=[_evidence(r, "food_inspection", built_at)],
                 qualifier=str(r.get("record_id")), confidence=conf, display=display, effective_at=r.get("date"),
                 relation=relation, match_basis=basis, method="mattilsynet_smilefjes_location_orgnr", note=note)
