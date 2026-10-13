"""GLEIF LEI lookup (batch module, tier ``batch_external``).

One GET per 100 organisation numbers: ``/lei-records?filter[entity.registeredAs]=<both formats>``.
GLEIF stores ``registeredAs`` either spaced (``"923 609 016"``, ~85 %) or plain, so both forms are
sent and results are mapped back by the digits of ``registeredAs``. A record is accepted only when
those digits equal the organisation number and the entity is Norwegian (legal address country NO,
or registered at Enhetsregisteret ``RA000472``). Parent LEIs are followed only when the record's
``relationships`` say a parent record exists.
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any
from urllib.parse import urlencode

from ..envelope import (CC0_GLEIF, OFFICIAL_LEI, Claim, CompanyResult, Evidence, ModuleOutcome,
                        utc_iso)

MODULE = "gleif"
VERSION = "gleif@1"
TIER = "batch_external"
BASE = "https://api.gleif.org/api/v1"
SOURCE_NAME = "GLEIF LEI records API"
BATCH = 100
ACCEPT = {"Accept": "application/vnd.api+json"}
BUDGET_NOTE = "not checked: request budget reserved for higher-priority sources"
NO_REGISTRATION_AUTHORITIES = ("RA000472", "RA000473")  # Enhetsregisteret, Foretaksregisteret
STATUS_RANK = {"ISSUED": 0, "PENDING_TRANSFER": 1, "PENDING_ARCHIVAL": 1, "LAPSED": 2, "RETIRED": 3,
               "MERGED": 3, "TRANSFERRED": 3}
DROP_STATUSES = ("ANNULLED", "DUPLICATE")


def record_url(lei: str) -> str:
    return f"{BASE}/lei-records/{lei}"


def page_url(lei: str) -> str:
    return f"https://search.gleif.org/#/record/{lei}"


def spaced(orgnr: str) -> str:
    return f"{orgnr[:3]} {orgnr[3:6]} {orgnr[6:]}"


def batch_url(orgnrs: list[str]) -> str:
    vals: list[str] = []
    for o in orgnrs:
        vals += [o, spaced(o)]
    return f"{BASE}/lei-records?" + urlencode({"filter[entity.registeredAs]": ",".join(vals), "page[size]": "200"})


def _digits(s: Any) -> str:
    return re.sub(r"\D", "", str(s or ""))


def _norm_name(s: str | None) -> str:
    s = unicodedata.normalize("NFKC", s or "").casefold()
    return re.sub(r"[^0-9a-zæøåäöüé]+", " ", s).strip()


def _span(**kv: Any) -> str:
    return ",".join(f"{json.dumps(k)}:{json.dumps(v, ensure_ascii=False)}" for k, v in kv.items())[:300]


def _eligible(company: CompanyResult) -> bool:
    ident = company.data.get("identity") or {}
    return ident.get("match_status") in (None, "exact")


def _legal_name(company: CompanyResult) -> str | None:
    reg = company.data.get("registry") or {}
    return reg.get("navn") or company.first_value("legal_name")


def _accept(attrs: dict[str, Any], orgnr: str) -> bool:
    ent = attrs.get("entity") or {}
    if _digits(ent.get("registeredAs")) != orgnr:
        return False
    country = ((ent.get("legalAddress") or {}).get("country") or "").upper()
    ra = (ent.get("registeredAt") or {}).get("id")
    return country == "NO" or ra == "RA000472"


def _date(s: str | None) -> str | None:
    return s[:10] if isinstance(s, str) and len(s) >= 10 else None


def _evidence(*, lei: str, res_sha: str, retrieved_at: str, locator: str, span: str, via: str | None,
              source_url: str | None = None, effective_at: str | None = None, status: int | None = 200,
              method: str = VERSION) -> Evidence:
    return Evidence(source_url=source_url or record_url(lei), source_class=OFFICIAL_LEI, source_name=SOURCE_NAME,
                    retrieved_at=retrieved_at, content_sha256=res_sha, claim_span=span, locator=locator,
                    extraction_method=method, page_url=page_url(lei), effective_at=effective_at,
                    http_status=status, licence=CC0_GLEIF, retrieved_via=via)


def _fail_all(companies: list[CompanyResult], availability: str, note: str, url: str | None = None,
              status: int | None = None) -> None:
    for c in companies:
        if availability == "failed":
            c.error(MODULE, "failed", note, url)
        c.outcome(ModuleOutcome(MODULE, availability, note=note, source_url=url, http_status=status))


def _existing_names(company: CompanyResult) -> set[str]:
    names = {_norm_name(_legal_name(company))}
    for c in company.claims:
        if c.field in ("former_name", "legal_name") and c.availability == "available":
            v = c.value
            if isinstance(v, dict):
                v = v.get("name")
            names.add(_norm_name(v if isinstance(v, str) else None))
    names.discard("")
    return names


def _parent_value(attrs: dict[str, Any]) -> dict[str, Any]:
    ent = attrs.get("entity") or {}
    return {"lei": attrs.get("lei"), "name": (ent.get("legalName") or {}).get("name"),
            "country": (ent.get("legalAddress") or {}).get("country"), "registered_as": ent.get("registeredAs")}


async def _fetch_parent(ctx, company: CompanyResult, lei: str, kind: str) -> tuple[list[Claim], int]:
    """kind: 'direct-parent' | 'ultimate-parent'. Returns (claims, requests_used)."""
    url = f"{BASE}/lei-records/{lei}/{kind}"
    relation = "parent" if kind == "direct-parent" else "ultimate_parent"
    if not ctx.allow(TIER, 1):
        return [], 0
    before = ctx.client.used
    try:
        res = await ctx.get(url, tier=TIER, source=MODULE, headers=ACCEPT)
    except Exception as exc:  # noqa: BLE001 - modules never raise
        company.error(MODULE, "failed", f"{kind} lookup failed: {type(exc).__name__}: {exc}", url)
        return [], ctx.client.used - before
    used = ctx.client.used - before
    if not res.ok:
        if res.status != 404:
            company.error(MODULE, "failed", f"{kind} lookup HTTP {res.status}", url)
        return [], used
    retrieved_at = utc_iso(res.retrieved_at)
    sha = ctx.snapshots.save_result(res, retrieved_at)
    try:
        doc = res.json()
    except ValueError:
        company.error(MODULE, "failed", f"{kind} lookup returned non-JSON", url)
        return [], used
    data = doc.get("data")
    if isinstance(data, list):
        data = data[0] if len(data) == 1 else None
    if not isinstance(data, dict):
        return [], used
    attrs = data.get("attributes") or {}
    val = _parent_value(attrs)
    if not val["lei"]:
        return [], used
    parent_orgnr = _digits(val["registered_as"]) if (val["country"] or "").upper() == "NO" else ""
    note = f"{'Direct' if relation == 'parent' else 'Ultimate'} accounting-consolidation parent reported to GLEIF by LEI {lei}"
    if len(parent_orgnr) == 9:
        note += f"; parent registeredAs {val['registered_as']} (Norwegian organisation number {parent_orgnr})"
    ent = attrs.get("entity") or {}
    span = _span(lei=val["lei"], legalName={"name": val["name"]}, registeredAs=ent.get("registeredAs"))
    ev = _evidence(lei=val["lei"], res_sha=sha, retrieved_at=retrieved_at, locator="/data/attributes",
                   span=span, via=None, source_url=url,
                   effective_at=_date((attrs.get("registration") or {}).get("lastUpdateDate")))
    return [Claim(field="lei_parent", value=val, evidence=[ev], qualifier="direct" if relation == "parent" else "ultimate",
                  confidence=0.95, relation=relation, method="gleif_relationship_record",
                  match_basis="lei_relationship", note=note,
                  display=f"{val['name']} (LEI {val['lei']}{', ' + val['country'] if val['country'] else ''})")], used


async def run_batch(ctx, companies: list[CompanyResult]) -> None:
    try:
        await _run_batch(ctx, companies)
    except Exception as exc:  # noqa: BLE001 - modules never raise
        for c in companies:
            if MODULE not in c.modules:
                c.error(MODULE, "failed", f"{type(exc).__name__}: {exc}")
                c.outcome(ModuleOutcome(MODULE, "failed", note=f"internal error: {type(exc).__name__}"))


async def _run_batch(ctx, companies: list[CompanyResult]) -> None:
    targets: dict[str, list[CompanyResult]] = {}
    for c in companies:
        if not _eligible(c):
            c.outcome(ModuleOutcome(MODULE, "not_applicable",
                                    note="identity not confirmed as an exact organisation-number match"))
            continue
        targets.setdefault(c.organisation_number, []).append(c)
    orgnrs = sorted(targets)
    for i in range(0, len(orgnrs), BATCH):
        chunk = orgnrs[i:i + BATCH]
        chunk_companies = [c for o in chunk for c in targets[o]]
        url = batch_url(chunk)
        if not ctx.allow(TIER, 1):
            _fail_all(chunk_companies, "failed", BUDGET_NOTE)
            continue
        try:
            res = await ctx.get(url, tier=TIER, source=MODULE, headers=ACCEPT)
        except Exception as exc:  # noqa: BLE001
            _fail_all(chunk_companies, "failed", f"GLEIF batch request failed: {type(exc).__name__}: {exc}", url)
            continue
        if res.status in (403, 429):
            _fail_all(chunk_companies, "blocked", f"GLEIF refused the request (HTTP {res.status})", url, res.status)
            continue
        if not res.ok:
            _fail_all(chunk_companies, "failed", f"GLEIF batch request HTTP {res.status}", url, res.status)
            continue
        retrieved_at = utc_iso(res.retrieved_at)
        sha = ctx.snapshots.save_result(res, retrieved_at)
        try:
            doc = res.json()
        except ValueError:
            _fail_all(chunk_companies, "failed", "GLEIF returned non-JSON", url, res.status)
            continue
        records = list(enumerate(doc.get("data") or []))
        total = ((doc.get("meta") or {}).get("pagination") or {}).get("total")
        truncated = isinstance(total, int) and total > len(records)
        publish = ((doc.get("meta") or {}).get("goldenCopy") or {}).get("publishDate")
        by_org: dict[str, list[tuple[int, dict[str, Any]]]] = {}
        for idx, rec in records:
            attrs = (rec or {}).get("attributes") or {}
            o = _digits((attrs.get("entity") or {}).get("registeredAs"))
            if o in targets and _accept(attrs, o):
                by_org.setdefault(o, []).append((idx, rec))
        for o in chunk:
            for company in targets[o]:
                await _emit(ctx, company, by_org.get(o, []), url=url, sha=sha, retrieved_at=retrieved_at,
                            publish=publish, status=res.status, truncated=truncated)


async def _emit(ctx, company: CompanyResult, hits: list[tuple[int, dict[str, Any]]], *, url: str, sha: str,
                retrieved_at: str, publish: str | None, status: int, truncated: bool) -> None:
    hits = [(i, r) for i, r in hits
            if ((r.get("attributes") or {}).get("registration") or {}).get("status") not in DROP_STATUSES]
    if not hits:
        avail = "failed" if truncated else "not_available"
        note = ("GLEIF result page truncated; this organisation number may not have been covered" if truncated else
                f"no LEI record whose registeredAs equals {company.organisation_number} "
                f"(GLEIF golden copy {publish or 'n/a'}; both '{company.organisation_number}' and "
                f"'{spaced(company.organisation_number)}' searched)")
        company.outcome(ModuleOutcome(MODULE, avail, note=note, source_url=url, retrieved_at=retrieved_at,
                                      http_status=status))
        return
    hits.sort(key=lambda t: (STATUS_RANK.get(((t[1].get("attributes") or {}).get("registration") or {})
                                             .get("status"), 9), t[1].get("id") or ""))
    if len(hits) > 1:
        r0 = STATUS_RANK.get(hits[0][1]["attributes"]["registration"].get("status"), 9)
        r1 = STATUS_RANK.get(hits[1][1]["attributes"]["registration"].get("status"), 9)
        if r0 == r1:
            leis = sorted(h[1]["attributes"]["lei"] for h in hits)
            company.data["gleif"] = {"ambiguous_leis": leis}
            company.outcome(ModuleOutcome(MODULE, "ambiguous", source_url=url, retrieved_at=retrieved_at,
                                          http_status=status,
                                          note=f"{len(hits)} LEI records carry this organisation number: {', '.join(leis)}"))
            return
    idx, rec = hits[0]
    attrs = rec.get("attributes") or {}
    ent = attrs.get("entity") or {}
    reg = attrs.get("registration") or {}
    lei = attrs.get("lei") or rec.get("id")
    ra = ent.get("registeredAs")
    ra_id = (ent.get("registeredAt") or {}).get("id")
    gleif_name = (ent.get("legalName") or {}).get("name")
    brreg_name = _legal_name(company)
    names_match = bool(brreg_name) and _norm_name(brreg_name) == _norm_name(gleif_name)
    base = f"/data/{idx}/attributes"
    basis_note = f"GLEIF registeredAs '{ra}' at {ra_id} has the digits of this organisation number"
    if brreg_name and not names_match:
        basis_note += f"; GLEIF legal name '{gleif_name}' differs from the registry name (often a rename lag)"
    effective = _date(reg.get("lastUpdateDate"))
    claims: list[Claim] = []

    ev_lei = _evidence(lei=lei, res_sha=sha, retrieved_at=retrieved_at, locator=f"{base}/lei", via=url,
                       span=_span(lei=lei, registeredAs=ra), effective_at=_date(publish), status=status)
    claims.append(Claim(field="lei", value=lei, evidence=[ev_lei], confidence=0.99 if names_match else 0.95,
                        display=lei, method="gleif_registeredAs_digits", note=basis_note,
                        effective_at=_date(publish)))

    status_val = {"entity_status": ent.get("status"), "registration_status": reg.get("status"),
                  "next_renewal": _date(reg.get("nextRenewalDate")), "managing_lou": reg.get("managingLou"),
                  "last_update": _date(reg.get("lastUpdateDate"))}
    st_note = None
    if reg.get("status") == "LAPSED":
        st_note = ("LAPSED means the LEI registration was not renewed on time; it does not mean the company "
                   "is inactive")
    ev_status = _evidence(lei=lei, res_sha=sha, retrieved_at=retrieved_at, locator=f"{base}/registration",
                          via=url, effective_at=effective, status=status,
                          span=_span(status=reg.get("status"), nextRenewalDate=reg.get("nextRenewalDate"),
                                     managingLou=reg.get("managingLou")))
    claims.append(Claim(field="lei_status", value=status_val, evidence=[ev_status],
                        confidence=0.99 if names_match else 0.95, effective_at=effective, note=st_note,
                        display=f"LEI {reg.get('status')}, entity {ent.get('status')}, next renewal "
                                f"{status_val['next_renewal'] or 'n/a'}",
                        method="gleif_registration"))

    have = _existing_names(company)
    former: list[str] = []
    for j, other in enumerate(ent.get("otherNames") or []):
        if (other or {}).get("type") != "PREVIOUS_LEGAL_NAME":
            continue
        name = (other.get("name") or "").strip()
        if not name or _norm_name(name) in have or name in former:
            continue
        former.append(name)
        ev = _evidence(lei=lei, res_sha=sha, retrieved_at=retrieved_at, locator=f"{base}/entity/otherNames/{j}",
                       via=url, effective_at=effective, status=status,
                       span=_span(name=name, type="PREVIOUS_LEGAL_NAME"))
        claims.append(Claim(field="former_name", value={"name": name, "from": None, "to": None}, evidence=[ev],
                            qualifier=f"lei:{_norm_name(name)}",
                            confidence=0.9, display=name, method="gleif_otherNames_PREVIOUS_LEGAL_NAME",
                            note="previous legal name as registered in the LEI record (not dated by GLEIF)"))

    rels = rec.get("relationships") or {}
    parents: list[dict[str, Any]] = []
    for kind in ("direct-parent", "ultimate-parent"):
        links = (rels.get(kind) or {}).get("links") or {}
        if "lei-record" not in links:
            continue
        pcl, _used = await _fetch_parent(ctx, company, lei, kind)
        claims.extend(pcl)
        parents.extend({**c.value, "relation": c.relation} for c in pcl)

    company.add(claims)
    company.data["gleif"] = {
        "lei": lei, "legal_name": gleif_name, "registered_as": ra, "registered_at": ra_id,
        "registration_status": reg.get("status"), "entity_status": ent.get("status"),
        "legal_form": (ent.get("legalForm") or {}).get("id"), "creation_date": _date(ent.get("creationDate")),
        "names_match_registry": names_match, "parents": parents,
        "parent_reporting_exception": "reporting-exception" in ((rels.get("direct-parent") or {}).get("links") or {}),
        "golden_copy": publish,
    }
    company.outcome(ModuleOutcome(MODULE, "available", source_url=record_url(lei), retrieved_at=retrieved_at,
                                  http_status=status,
                                  note=f"LEI {lei} ({reg.get('status')}); matched by registeredAs digits"))
