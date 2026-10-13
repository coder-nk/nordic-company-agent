"""Group structure from `GET /enhetsregisteret/api/konsernstruktur/{orgnr}` (only when erIKonsern).

The endpoint returns the whole tree rooted at the ultimate (Norwegian-registered) parent, whichever
member is queried. This module finds the company's own node and publishes: its direct parent
(`parent_company`, relation "parent", ownership basis `grunnlag` and date `dato` as given), the tree
root when that is not the company itself (`ultimate_parent`), and its direct children
(`group_subsidiary` [orgnr], relation "related").
"""

from __future__ import annotations

import json
from typing import Any

from ..context import RunContext
from ..envelope import NLOD, OFFICIAL_GROUP, Claim, CompanyResult, Evidence, ModuleOutcome, utc_iso
from ..orgnr import formatted
from .registry import (ACCEPT_JSON, BASE, BUDGET_NOTE, array_elements, decode_text, enhet_page, members_span,
                       outcome_state_for_status, pointer)

MODULE = "group"
VERSION = "1"
METHOD = f"{MODULE}@{VERSION}"
TIER = "group"
SOURCE_NAME = "Brønnøysundregistrene, Enhetsregisteret group structure API (konsernstruktur)"
MAX_SUBSIDIARY_CLAIMS = 200
MAX_BYTES = 24_000_000


def group_url(orgnr: str) -> str:
    return f"{BASE}/konsernstruktur/{orgnr}"


def find_path(root: dict[str, Any], orgnr: str) -> list[tuple[str, dict[str, Any]]] | None:
    """[(json_pointer, node), ...] from the root to the node whose organisasjonsnummer == orgnr."""
    stack: list[tuple[str, dict[str, Any], list[tuple[str, dict[str, Any]]]]] = [("", root, [])]
    while stack:
        ptr, node, path = stack.pop()
        if not isinstance(node, dict):
            continue
        here = path + [(ptr, node)]
        if str(node.get("organisasjonsnummer") or "") == orgnr:
            return here
        kids = node.get("children") or []
        for i in range(len(kids) - 1, -1, -1):
            stack.append((ptr + pointer("children", i), kids[i], here))
    return None


def _count_nodes(node: Any) -> int:
    if not isinstance(node, dict):
        return 0
    return 1 + sum(_count_nodes(k) for k in node.get("children") or [])


def _link(node: dict[str, Any]) -> str | None:
    kf = node.get("knytningsform")
    if isinstance(kf, dict):
        return kf.get("beskrivelse") or kf.get("kode")
    return None


async def run(ctx: RunContext, company: CompanyResult) -> None:
    try:
        await _run(ctx, company)
    except Exception as exc:
        company.error(MODULE, "failed", f"{type(exc).__name__}: {exc}")
        company.outcome(ModuleOutcome(MODULE, "failed", note=f"module error: {type(exc).__name__}"))


async def _run(ctx: RunContext, company: CompanyResult) -> None:
    orgnr = company.organisation_number
    status = (company.data.get("identity") or {}).get("match_status")
    rec = company.data.get("registry") or {}
    if status != "exact":
        company.outcome(ModuleOutcome(MODULE, "not_applicable", note=f"identity not an active main unit ({status})"))
        return
    if rec.get("erIKonsern") is not True:
        company.outcome(ModuleOutcome(MODULE, "not_applicable",
                                      note="not registered as part of a group (erIKonsern is not true)"))
        return
    url = group_url(orgnr)
    if not ctx.allow(TIER, 1):
        company.outcome(ModuleOutcome(MODULE, "failed", note=BUDGET_NOTE, source_url=url))
        return
    try:
        res = await ctx.get(url, tier=TIER, source=MODULE, headers=ACCEPT_JSON, max_bytes=MAX_BYTES)
    except Exception as exc:
        msg = f"group lookup failed: {type(exc).__name__}: {exc}"[:300]
        company.error(MODULE, "failed", msg, url)
        company.outcome(ModuleOutcome(MODULE, "failed", note=msg, source_url=url, requests=1))
        return
    retrieved_at = utc_iso(res.retrieved_at)
    if res.status == 404:
        company.outcome(ModuleOutcome(MODULE, "not_available", note="no group structure registered (HTTP 404)",
                                      source_url=url, retrieved_at=retrieved_at, http_status=404, requests=1))
        return
    if res.status != 200 or res.truncated:
        state = outcome_state_for_status(res.status) if res.status != 200 else "failed"
        company.outcome(ModuleOutcome(MODULE, state, note=f"group lookup returned HTTP {res.status}", source_url=url,
                                      retrieved_at=retrieved_at, http_status=res.status, requests=1))
        return
    sha = ctx.snapshots.save_result(res, retrieved_at)
    text = decode_text(res.content)
    try:
        tree = json.loads(text)
    except ValueError as exc:
        company.outcome(ModuleOutcome(MODULE, "failed", note=f"unparseable group structure: {exc}"[:300], source_url=url,
                                      retrieved_at=retrieved_at, http_status=200, requests=1))
        return
    path = find_path(tree, orgnr) if isinstance(tree, dict) else None
    if not path:
        company.outcome(ModuleOutcome(MODULE, "ambiguous",
                                      note="company not found in the returned group tree; nothing published",
                                      source_url=url, retrieved_at=retrieved_at, http_status=200, requests=1))
        return

    def ev(span: str | None, locator: str, effective_at: str | None = None) -> Evidence:
        return Evidence(source_url=url, source_class=OFFICIAL_GROUP, source_name=SOURCE_NAME, retrieved_at=retrieved_at,
                        content_sha256=sha, claim_span=span, locator=locator or "/", extraction_method=METHOD,
                        page_url=enhet_page(orgnr), effective_at=effective_at, http_status=200, licence=NLOD)

    node_ptr, node = path[-1]
    root_ptr, root = path[0]
    claims: list[Claim] = []
    info: dict[str, Any] = {"root": {"organisation_number": str(root.get("organisasjonsnummer") or ""),
                                     "name": root.get("navn")},
                            "is_root": len(path) == 1, "level": node.get("nivaa", len(path) - 1),
                            "node_count": _count_nodes(tree), "parent": None, "subsidiaries": []}
    if len(path) >= 2:
        _pptr, pnode = path[-2]
        p_org = str(node.get("parentOrganisasjonsnummer") or pnode.get("organisasjonsnummer") or "")
        if p_org and p_org == str(pnode.get("organisasjonsnummer") or ""):
            pv = {"organisation_number": p_org, "name": node.get("parentNavn") or pnode.get("navn"),
                  "ownership": node.get("grunnlag"), "since": node.get("dato"), "link_type": _link(node)}
            info["parent"] = pv
            evs = [ev(members_span(text, node_ptr, ["organisasjonsnummer", "parentNavn", "parentOrganisasjonsnummer"]),
                      node_ptr + "/parentOrganisasjonsnummer", node.get("dato")),
                   ev(members_span(text, node_ptr, ["dato", "grunnlag", "knytningsform"]), node_ptr + "/grunnlag",
                      node.get("dato"))]
            disp = f"{pv['name']} ({formatted(p_org)})" + (f", {pv['ownership']}" if pv.get("ownership") else "")
            claims.append(Claim(field="parent_company", value=pv, evidence=evs, relation="parent", display=disp,
                                effective_at=node.get("dato"), method=METHOD))
        if root is not node:
            r_org = str(root.get("organisasjonsnummer") or "")
            if r_org and r_org != orgnr:
                uv = {"organisation_number": r_org, "name": root.get("navn")}
                claims.append(Claim(field="ultimate_parent", value=uv, relation="ultimate_parent",
                                    evidence=[ev(members_span(text, root_ptr, ["navn", "organisasjonsform",
                                                                                "organisasjonsnummer"]),
                                                 root_ptr + "/organisasjonsnummer")],
                                    display=f"{uv['name']} ({formatted(r_org)})", method=METHOD,
                                    note=f"root of the registered group tree; this company is at level "
                                         f"{node.get('nivaa', len(path) - 1)}"))
    else:
        claims.append(Claim(field="parent_company", value=None, availability="not_applicable", method=METHOD,
                            evidence=[ev(members_span(text, root_ptr, ["navn", "organisasjonsform", "organisasjonsnummer"]),
                                         root_ptr + "/organisasjonsnummer")],
                            note="the company is the top entity (root) of its registered group"))

    subs: list[tuple[dict[str, Any], str, str]] = []
    child_ptr = node_ptr + "/children"
    for i, (s, e) in enumerate(array_elements(text, child_ptr)):
        ctext = text[s:e]
        child = json.loads(ctext)
        c_org = str(child.get("organisasjonsnummer") or "")
        if not c_org:
            continue
        if str(child.get("parentOrganisasjonsnummer") or orgnr) != orgnr:
            continue  # echo check: only direct children of this company
        sv = {"organisation_number": c_org, "name": child.get("navn"), "ownership": child.get("grunnlag"),
              "since": child.get("dato"), "link_type": _link(child)}
        subs.append((sv, ctext, child_ptr + pointer(i)))
    subs.sort(key=lambda t: t[0]["organisation_number"])
    info["subsidiaries"] = [sv for sv, _t, _p in subs]
    for sv, ctext, cptr in subs[:MAX_SUBSIDIARY_CLAIMS]:
        span = members_span(ctext, "", ["grunnlag", "knytningsform", "navn", "nivaa", "organisasjonsform",
                                        "organisasjonsnummer", "parentNavn", "parentOrganisasjonsnummer"])
        disp = f"{sv['name']} ({formatted(sv['organisation_number'])})" + (f", {sv['ownership']}" if sv.get("ownership") else "")
        claims.append(Claim(field="group_subsidiary", value=sv, qualifier=sv["organisation_number"], relation="related",
                            evidence=[ev(span, cptr, sv.get("since"))], display=disp, effective_at=sv.get("since"),
                            method=METHOD))
    company.data["group"] = info
    company.add(claims)
    n_avail = sum(1 for c in claims if c.availability == "available")
    note = (f"parent {info['parent']['organisation_number']}" if info["parent"] else "top of group") + \
        f"; {len(subs)} direct subsidiar{'y' if len(subs) == 1 else 'ies'}"
    if len(subs) > MAX_SUBSIDIARY_CLAIMS:
        note += f" (claims limited to {MAX_SUBSIDIARY_CLAIMS})"
    company.outcome(ModuleOutcome(MODULE, "available" if n_avail else "not_available", note=note, source_url=url,
                                  retrieved_at=retrieved_at, http_status=200, requests=1))
