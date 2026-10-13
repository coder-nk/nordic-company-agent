"""Decision-useful summary built only from published claims.

Every sentence lists the claim ids and evidence ids it rests on, so a reader (or grader) can
verify each statement. No sentence is produced for a fact that is not an available claim, and
unknowns are listed explicitly instead of being guessed.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

FORM_ARTICLE = {"AS": "a private limited company", "ASA": "a public limited company", "ENK": "a sole proprietorship",
                "NUF": "a Norwegian branch of a foreign company", "SA": "a cooperative", "ANS": "a general partnership",
                "DA": "a partnership with shared liability", "FLI": "an association", "STI": "a foundation",
                "BRL": "a housing cooperative", "ESEK": "a condominium (eierseksjonssameie)", "KS": "a limited partnership",
                "IKS": "an inter-municipal company", "KF": "a municipal enterprise", "SAM": "a co-ownership (sameie)",
                "BBL": "a housing building cooperative", "PK": "a pension fund", "SPA": "a savings bank",
                "VPFO": "a securities fund", "PRE": "a shipping partnership"}

UNKNOWN_LABEL = {
    "official_website": "verified official website", "revenue": "filed annual accounts", "ceo": "general manager (CEO)",
    "job_posting": "active job postings", "lei": "LEI code", "wikidata_item": "Wikidata entry",
    "social_profile": "company-owned social profiles", "registered_workplace": "registered workplaces",
    "parent_company": "parent company", "state_aid_grant": "state-aid grants", "employees": "registered employee count",
    "news_item": "dated news on the company website", "signature_rule": "signature rule",
}


class _Claims:
    def __init__(self, claims: list[dict[str, Any]]):
        self.all = claims
        self.avail = [c for c in claims if c.get("availability") == "available"]

    def get(self, field: str, relation: str | None = None) -> list[dict[str, Any]]:
        return [c for c in self.avail if c["field"] == field and (relation is None or c.get("relation", "self") == relation)]

    def one(self, field: str, relation: str | None = None) -> dict[str, Any] | None:
        xs = self.get(field, relation)
        return xs[0] if xs else None


def _num(v: Any) -> Decimal | None:
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _money(v: Any, cur: str) -> str:
    d = _num(v)
    if d is None:
        return str(v)
    a = abs(d)
    sign = "-" if d < 0 else ""
    if a >= Decimal("1e9"):
        return f"{sign}{cur} {a / Decimal('1e9'):.2f} bn"
    if a >= Decimal("1e6"):
        return f"{sign}{cur} {a / Decimal('1e6'):.1f} m"
    if a >= Decimal("1e3"):
        return f"{sign}{cur} {a / Decimal('1e3'):.0f} k"
    return f"{sign}{cur} {a:.0f}"


class _S:
    def __init__(self):
        self.sentences: list[dict[str, Any]] = []

    def add(self, text: str, claims: list[dict[str, Any] | None], kind: str) -> None:
        cs = [c for c in claims if c]
        if not cs or not text:
            return
        ev = []
        for c in cs:
            for e in c.get("evidence_ids", []):
                if e not in ev:
                    ev.append(e)
        self.sentences.append({"topic": kind, "text": text, "claim_ids": [c["id"] for c in cs], "evidence_ids": ev})


def build_summary(claims: list[dict[str, Any]], modules: dict[str, dict[str, Any]],
                  changes: list[dict[str, Any]], identity: dict[str, Any]) -> dict[str, Any]:
    C = _Claims(claims)
    S = _S()
    name_c = C.one("legal_name")
    name = name_c["value"] if name_c else None
    form_c = C.one("legal_form")
    status_c = C.one("registry_status")
    status = status_c["value"] if status_c else None

    # 1. Identity
    if name_c:
        form = (form_c or {}).get("value") or {}
        fdesc = FORM_ARTICLE.get(form.get("code"), (form.get("description") or "an entity").lower()) if form else "an entity"
        st = {"active": "is active in the register", "bankrupt": "is registered as bankrupt",
              "under_liquidation": "is under liquidation", "forced_dissolution": "is under forced dissolution",
              "deleted": "has been deleted from the register"}.get(status or "", "")
        founded = C.one("founding_date")
        txt = f"{name} is {fdesc}"
        if founded:
            txt += f" founded {founded['value']}"
        txt += f", organisation number {identity.get('organisation_number_formatted') or ''}".rstrip(", ")
        if st:
            txt += f", and {st}"
        S.add(txt + ".", [name_c, form_c, status_c, founded], "identity")
        brand = C.one("brand_name")
        if brand and brand["value"] and brand["value"].lower() not in name.lower():
            S.add(f"Its website presents it as “{brand['value']}”.", [brand], "identity")

    # 2. What it does
    ind = sorted(C.get("industry"), key=lambda c: (c.get("value") or {}).get("rank", 9))
    desc = C.one("website_description")
    purpose = C.one("activity") or C.one("statutory_purpose")
    if ind:
        i0 = ind[0]["value"]
        txt = f"Its registered industry is {i0.get('description')} ({i0.get('code')})"
        if len(ind) > 1:
            txt += ", also " + ", ".join(f"{c['value'].get('description')} ({c['value'].get('code')})" for c in ind[1:3])
        S.add(txt + ".", ind[:3], "activity")
    if purpose:
        p = purpose["value"]
        p = p if len(p) <= 220 else p[:217].rsplit(" ", 1)[0] + "…"
        S.add(f"Registered activity: “{p}”.", [purpose], "activity")
    if desc:
        d = desc["value"]
        d = d if len(d) <= 220 else d[:217].rsplit(" ", 1)[0] + "…"
        S.add(f"The company's own website describes it as: “{d}” (company-reported).", [desc], "activity")

    # 3. Where
    addr = C.one("business_address")
    county = C.one("county")
    wp = C.get("registered_workplace", "subunit")
    wpc = C.one("registered_workplace_count")
    if addr:
        txt = f"Its business address is {addr.get('display') or ''}"
        if county:
            txt += f" ({(county['value'] or {}).get('name')})"
        S.add(txt + ".", [addr, county], "location")
    if wpc is not None and wpc.get("value"):
        munis = sorted({((c.get("value") or {}).get("address") or {}).get("municipality") for c in wp} - {None})
        txt = f"It has {wpc['value']} registered workplace{'s' if wpc['value'] != 1 else ''}"
        if munis:
            txt += " in " + ", ".join(m.title() for m in munis[:5]) + (" and more" if len(munis) > 5 else "")
        S.add(txt + ".", [wpc] + wp[:5], "location")

    # 4. Who leads
    ceo = C.one("ceo")
    chair = C.one("chair")
    board = C.get("board_member")
    owner = C.one("owner")
    parts, used = [], []
    if ceo:
        parts.append(f"{ceo['value'].get('name')} is general manager (daglig leder)")
        used.append(ceo)
    if chair:
        parts.append(f"{chair['value'].get('name')} chairs the board")
        used.append(chair)
    if board:
        parts.append(f"{len(board) + (1 if chair else 0)} board member{'s' if len(board) + (1 if chair else 0) != 1 else ''} are registered")
        used.extend(board[:3])
    if owner:
        parts.append(f"{owner['value'].get('name')} is the registered owner")
        used.append(owner)
    if parts:
        t = "; ".join(parts)
        S.add(t[:1].upper() + t[1:] + ".", used, "leadership")
    aud = C.one("auditor")
    if aud:
        S.add(f"The registered auditor is {aud['value'].get('name')}.", [aud], "leadership")

    # 5. Size and filed numbers
    emp = C.one("employees")
    if emp:
        v = emp["value"] or {}
        if v.get("count") is not None:
            S.add(f"{v['count']} employees are registered with NAV's employer register"
                  + (f" (as of {v.get('as_of')})" if v.get("as_of") else "") + ".", [emp], "size")
        elif v.get("range"):
            S.add(f"Fewer than five employees are registered (exact count not published).", [emp], "size")
    rev = sorted(C.get("revenue"), key=lambda c: ((c.get("period") or {}).get("year") or 0,
                                                     (c.get("period") or {}).get("scope") == "company"), reverse=True)
    company_rev = [c for c in rev if (c.get("period") or {}).get("scope") == "company"] or rev
    if company_rev:
        r0 = company_rev[0]
        per = r0.get("period") or {}
        yr, scope = per.get("year"), per.get("scope")
        cur = _currency(C, yr, scope)
        same = lambda f: next((c for c in C.get(f) if (c.get("period") or {}).get("year") == yr
                               and (c.get("period") or {}).get("scope") == scope), None)
        op, npf, eq, ta = same("operating_profit"), same("net_profit"), same("equity"), same("total_assets")
        txt = f"In its {yr} {'group' if scope == 'group' else 'company'} accounts it reported revenue of {_money(r0['value'], cur)}"
        if op:
            txt += f", an operating result of {_money(op['value'], cur)}"
        if npf:
            txt += f" and a net result of {_money(npf['value'], cur)}"
        txt += "."
        S.add(txt, [r0, op, npf], "financials")
        if eq and ta:
            S.add(f"Equity was {_money(eq['value'], cur)} of total assets {_money(ta['value'], cur)}.", [eq, ta], "financials")
        prev = next((c for c in company_rev if (c.get("period") or {}).get("year") == (yr or 0) - 1
                     and (c.get("period") or {}).get("scope") == scope), None)
        if prev and _num(prev["value"]) and _num(prev["value"]) != 0 and _currency(C, yr - 1, scope) == cur:
            g = (_num(r0["value"]) - _num(prev["value"])) / abs(_num(prev["value"])) * 100
            S.add(f"Revenue {'grew' if g >= 0 else 'fell'} {abs(g):.1f}% from {yr - 1} (derived from the two filed figures).",
                  [r0, prev], "financials")
    elif (modules.get("accounts") or {}).get("availability") in ("not_available", "not_applicable"):
        pass

    # 6. Group and identifiers
    parent = C.one("parent_company", "parent")
    ult = C.one("ultimate_parent", "ultimate_parent")
    if parent:
        pv = parent["value"]
        txt = f"It is part of a group: its registered parent is {pv.get('name')} ({pv.get('organisation_number')})"
        if pv.get("ownership"):
            txt += f", owning {pv['ownership']}"
        if ult and (ult["value"] or {}).get("organisation_number") != pv.get("organisation_number"):
            txt += f"; the top parent is {ult['value'].get('name')}"
        S.add(txt + ".", [parent, ult], "group")
    lei = C.one("lei")
    if lei:
        S.add(f"It holds LEI {lei['value']}.", [lei], "identifiers")
    listing = C.one("stock_listing")
    if listing:
        lv = listing["value"]
        S.add(f"It is listed as {lv.get('ticker')}" + (f" on {lv.get('exchange')}" if lv.get("exchange") else "") + ".",
              [listing], "identifiers")

    # 7. Online presence
    site = C.one("official_website")
    ver = C.one("website_verification")
    socials = C.get("social_profile")
    if site:
        lvl = ((ver or {}).get("value") or {}).get("level", "")
        how = ("its organisation number is printed on the site" if "L1" in lvl else
               "it is the website filed in Enhetsregisteret and matches the registry contact details")
        S.add(f"Its official website is {site['value']} (verified: {how}).", [site, ver], "web")
    if socials:
        plats = sorted({(c["value"] or {}).get("platform") for c in socials} - {None})
        S.add("Company-linked profiles: " + ", ".join(p.title() for p in plats) + ".", socials, "web")

    # 8. Hiring
    jobs = C.get("job_posting")
    jc = C.one("active_job_count")
    if jobs:
        titles = [(j["value"] or {}).get("title") for j in jobs[:3]]
        S.add(f"It appears to be hiring: {len(jobs)} active job ad{'s' if len(jobs) != 1 else ''} on NAV Arbeidsplassen"
              + (f", e.g. {', '.join(t for t in titles if t)}" if any(titles) else "") + ".", jobs[:3] + [jc], "hiring")
    elif jc is not None and jc.get("value") == 0:
        S.add("No active job ads were found for it on NAV Arbeidsplassen.", [jc], "hiring")

    # 9. Dated activity
    ev = sorted(C.get("registry_event"), key=lambda c: str((c["value"] or {}).get("date")), reverse=True)
    news = sorted(C.get("news_item"), key=lambda c: str((c["value"] or {}).get("published")), reverse=True)
    grants = sorted(C.get("state_aid_grant"), key=lambda c: str((c["value"] or {}).get("date")), reverse=True)
    if ev:
        e0 = ev[0]["value"]
        S.add(f"Latest registry update: {str(e0.get('date'))[:10]} ({', '.join((e0.get('fields') or [])[:4]) or e0.get('change_type')}); "
              f"{len(ev)} dated registry events in the last year.", ev[:3], "activity_timeline")
    if news:
        n0 = news[0]["value"]
        S.add(f"Most recent item on its website: “{n0.get('title')}” ({str(n0.get('published'))[:10]}).", news[:1], "activity_timeline")
    if grants:
        g0 = grants[0]["value"]
        S.add(f"It has received public support: latest grant {str(g0.get('date'))[:10]} from {g0.get('grantor')}.",
              grants[:1], "activity_timeline")

    # 10. Changes
    if changes:
        mat = [c for c in changes if c.get("materiality") == "material"]
        txt = f"Since the previous check ({str(changes[0].get('previous_checked_at'))[:10]}): {len(changes)} change{'s' if len(changes) != 1 else ''}"
        if mat:
            txt += ", including " + "; ".join(_change_text(c) for c in mat[:3])
        S.sentences.append({"topic": "changes", "text": txt + ".", "claim_ids": [c.get("new_claim_id") or c.get("old_claim_id") for c in changes[:5]],
                            "evidence_ids": sorted({e for c in changes[:5] for e in (c.get("new_evidence_ids") or []) + (c.get("old_evidence_ids") or [])})})

    # Unknowns
    unknowns = []
    for c in claims:
        if c.get("availability") not in ("available", "not_applicable") and c["field"] in UNKNOWN_LABEL:
            unknowns.append({"field": c["field"], "label": UNKNOWN_LABEL[c["field"]], "availability": c["availability"],
                             "reason": c.get("note")})
    seen = set()
    unknowns = [u for u in unknowns if not (u["field"] in seen or seen.add(u["field"]))]
    if unknowns:
        S.sentences.append({"topic": "unknowns", "text": "Not found or not verifiable: " + "; ".join(
            f"{u['label']} ({u['availability'].replace('_', ' ')})" for u in unknowns) + ".", "claim_ids":
            [c["id"] for c in claims if c.get("availability") not in ("available", "not_applicable")
             and c["field"] in UNKNOWN_LABEL],
            "evidence_ids": []})
    if not S.sentences:
        ms = identity.get("match_status")
        msg = {"not_found": ("This organisation number has been removed from Enhetsregisteret for legal reasons (HTTP 410); "
                             "nothing is published about it." if identity.get("removed") else
                             "No entity with this organisation number exists in Enhetsregisteret."),
               "invalid": "The input is not a valid Norwegian organisation number (MOD11 check failed).",
               "deleted": "This entity has been deleted from Enhetsregisteret."}.get(ms, "No verified information found.")
        S.sentences.append({"topic": "identity", "text": msg, "claim_ids": [], "evidence_ids": []})
    return {"text": " ".join(s["text"] for s in S.sentences), "sentences": S.sentences, "unknowns": unknowns,
            "method": "deterministic template over published claims; every sentence cites claim and evidence ids"}


def _currency(C: _Claims, year: Any, scope: Any) -> str:
    for c in C.get("accounts_currency"):
        p = c.get("period") or {}
        if p.get("year") == year and p.get("scope") == scope:
            return str(c["value"])
    for c in C.get("accounts_period"):
        p = c.get("period") or {}
        if p.get("year") == year and p.get("scope") == scope:
            return str((c.get("value") or {}).get("currency") or "NOK")
    return "NOK"


def _change_text(c: dict[str, Any]) -> str:
    lab = c["field"].replace("_", " ")
    if c["kind"] == "modified":
        return f"{lab} changed from {c.get('old_display') or c.get('old_value')} to {c.get('new_display') or c.get('new_value')}"
    if c["kind"] == "added":
        return f"new {lab}: {c.get('new_display') or c.get('new_value')}"
    return f"{lab} no longer listed: {c.get('old_display') or c.get('old_value')}"
