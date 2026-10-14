"""Label registry-filed contacts that are shared with other organisation numbers.

Enhetsregisteret records the website, e-mail and phone each entity filed. Many entities file their property
manager's, accountant's, group's or chain's contacts (e.g. thousands of housing co-operatives file
www.usbl.no). Those values are true *registry facts* about the filing, but they are not the entity's own
contact channels. Shared values are therefore published with relation="related", lower confidence and an
explicit note, so they are never presented as company-specific.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from .envelope import CompanyResult
from .web.extract import reg_domain

FIELDS = {"registry_website", "registry_email", "registry_phone", "registry_mobile"}

# Freemail / consumer-ISP domains: an address there is personal to its holder, so other filers on the same
# *domain* say nothing (exactly shared addresses are still labelled).
FREEMAIL = frozenset("""
gmail.com googlemail.com gmail.no hotmail.com hotmail.no hotmail.co.uk hotmail.se hotmail.dk live.no live.com live.se
live.dk outlook.com outlook.no msn.com msn.no yahoo.com yahoo.no yahoo.co.uk yahoo.se ymail.com icloud.com me.com
mac.com aol.com mail.com gmx.com gmx.net gmx.de protonmail.com proton.me pm.me inbox.lv wp.pl online.no start.no
frisurf.no broadpark.no c2i.net getmail.no lyse.net altibox.no epost.no tele2.no netcom.no chello.no telia.com
telia.no ntebb.no bbnett.no haugnett.no enivest.net bluezone.no tussa.com vikenfiber.no ebnett.no halden.net
tiscali.no knett.no adsl.no sf-nett.no roros.net oppdal.com loqal.no nktv.no sensewave.com tdcadsl.dk
""".split())

# Website-gate reasons that name another entity as the site's owner/user (verify.py wording).
OTHER_ENTITY_RX = re.compile(
    r"group site|group/foreign|parent/group|sister/group|sister entit|sister sites|chain/franchise|chain-branded|"
    r"a chain brand|foreign legal entity|foreign registration|same brand on a foreign domain|ownership statement|"
    r"owner statement|acquisition|© owner|another owner|another legal entity|another entity|"
    r"controller/seller/owner|other organisation numbers|organisation numbers of other entities|"
    r"property-manager/community/directory platform|hijacked or expired", re.I)
NAME_MISSING_RX = re.compile(r"company name not prominent", re.I)
# Property / holding entities whose filed site carries an operating brand without their name (HOBBY BOAT EIENDOM AS
# filing the boat brand's site of its sister HOBBY BOAT AS).
HOLDING_NAME_RX = re.compile(r"\b(?:eiendom\w*|holding|invest\w*|property|properties)\b", re.I)


def _host(v: str) -> str:
    v = v.strip()
    if "://" not in v:
        v = "https://" + v
    return (urlsplit(v).hostname or "").lower().removeprefix("www.")


def _load(ctx) -> dict[str, Any]:
    cached = ctx.shared.get("_contact_sharing")
    if cached is not None:
        return cached
    filers = ctx.caches.json("registry_contact_filers") or {}
    shared = ctx.caches.json("registry_shared_contacts") or {}
    domains: dict[str, list[str]] = {}
    for host, rows in (shared.get("domains") or {}).items():
        domains[str(host).lower().removeprefix("www.")] = [str(r[0]) if isinstance(r, list) else str(r) for r in rows]
    for host, orgs in (ctx.caches.json("registry_website_domains") or {}).items():
        domains.setdefault(str(host).lower().removeprefix("www."), [str(o) for o in orgs])
    email_domains: dict[str, set[str]] = {}
    for addr, orgs in (filers.get("emails") or {}).items():
        dom = str(addr).rsplit("@", 1)[-1].lower() if "@" in str(addr) else ""
        if dom and dom not in FREEMAIL:
            email_domains.setdefault(dom, set()).update(str(o) for o in orgs)
    out = {"phones": filers.get("phones") or {}, "emails": filers.get("emails") or {}, "domains": domains,
           "email_domains": email_domains, "names": shared.get("names") or {}}
    ctx.shared["_contact_sharing"] = out
    return out


def label_shared(ctx, companies: list[CompanyResult]) -> int:
    idx = _load(ctx)
    n = 0
    for c in companies:
        me = c.organisation_number
        for cl in c.claims:
            if cl.field not in FIELDS or cl.availability != "available" or not isinstance(cl.value, str):
                continue
            if cl.field == "registry_website":
                host = _host(cl.value)
                others = [o for o in idx["domains"].get(host, []) if o != me]
                what = "website host"
                if not others:
                    others, what = _domain_filers(idx, reg_domain(host), me)
            elif cl.field == "registry_email":
                addr = cl.value.strip().lower()
                others = [o for o in idx["emails"].get(addr, []) if o != me]
                what = "e-mail address"
                if not others and "@" in addr:
                    others, what = _domain_filers(idx, reg_domain(addr.rsplit("@", 1)[-1]), me)
            else:
                d = re.sub(r"\D", "", cl.value)[-8:]
                others = [o for o in idx["phones"].get(d, []) if o != me] if len(d) == 8 else []
                what = "phone number"
            if not others:
                continue
            if what.startswith("domain"):
                cl._domain_label = (reg_domain(_host(cl.value) if cl.field == "registry_website"  # type: ignore[attr-defined]
                                               else cl.value.rsplit("@", 1)[-1]), cl.confidence, cl.note)
            example = idx["names"].get(others[0]) or others[0]
            cl.relation = "related"
            cl.confidence = min(cl.confidence, 0.6)
            cl.note = (f"filed in Enhetsregisteret for this entity, but the same {what} is also filed by "
                       f"{len(others)} other organisation number(s) (e.g. {example}): a shared property-manager, "
                       "accountant, group or chain contact, not specific to this entity")
            n += 1
    return n


def _domain_filers(idx: dict[str, Any], dom: str, me: str) -> tuple[list[str], str]:
    """Other organisation numbers using the same (non-freemail) registrable domain in a filed e-mail address or
    website: the domain of a group, chain, property manager, accountant or sister entity."""
    if not dom or dom in FREEMAIL:
        return [], ""
    others = set(idx["email_domains"].get(dom, ())) | set(idx["domains"].get(dom, ()))
    others.discard(me)
    return sorted(others), f"domain ({dom}, in e-mail addresses or websites)"


def finalize_declared(company: CompanyResult) -> str | None:
    """Settle the relation of a declared (L3d) website once the group module has run: a group / sister site is the
    company's own when the company heads its registered group (it has subsidiaries and no parent); otherwise it
    stays `group_member`. Returns the final relation, or None without a declared website."""
    data = company.data.get("website") or {}
    decl = data.get("declared")
    if not isinstance(decl, dict):
        return None
    avail = [c for c in company.claims if c.availability == "available"]
    subs = sum(1 for c in avail if c.field == "group_subsidiary")
    parent = any(c.field in ("parent_company", "ultimate_parent") for c in avail)
    rel = "self" if not decl.get("group") or (subs and not parent) else "group_member"
    for c in company.claims:
        if c.method.endswith(":L3d"):                    # the declared site, its verification and socials
            c.relation = rel
            if c.field == "official_website" and rel == "self" and decl.get("group"):
                c.note = (f"{c.note}; the company heads its registered group ({subs} subsidiar"
                          f"{'y' if subs == 1 else 'ies'}), so the group's site is its own")[:700]
    decl["relation"] = rel
    return rel


def label_by_website(company: CompanyResult) -> int:
    """After the website gate: registry-filed websites and e-mails on a domain the gate refused as another entity's
    site (group, parent, sister, chain, foreign, acquirer, platform, other org numbers) are labelled related.
    A declared (L3d) website whose relation is `self` counts as the entity's own domain."""
    data = company.data.get("website") or {}
    decl = data.get("declared") if finalize_declared(company) == "self" else None
    name = next((str(c.value) for c in company.claims if c.field == "legal_name" and c.relation == "self"
                 and c.availability == "available"), "")
    refused: dict[str, str] = {}
    for lead in data.get("leads") or []:
        reason = str(lead.get("reason") or "")
        if lead.get("state") != "ambiguous" or not lead.get("start"):
            continue
        if OTHER_ENTITY_RX.search(reason) or (NAME_MISSING_RX.search(reason) and HOLDING_NAME_RX.search(name)):
            dom = reg_domain(_host(str(lead["start"])))
            if dom:
                refused.setdefault(dom, reason)
    own_dom = reg_domain(_host(str(data["url"]))) if data.get("state") == "available" and data.get("url") else ""
    if not own_dom and decl:
        own_dom = reg_domain(_host(str(decl.get("url") or "")))
    refused.pop(own_dom, None)                                   # never contradict a published own site
    n = 0
    for cl in company.claims:
        lab = getattr(cl, "_domain_label", None)
        if own_dom and lab and lab[0] == own_dom and cl.relation == "related":
            # the gate verified this domain as the entity's own exclusive site: co-filers on the domain are its
            # subsidiaries / holding, not evidence that the filed contact belongs to someone else
            cl.relation, cl.confidence, cl.note = "self", lab[1], lab[2]
    for cl in company.claims:
        if cl.field not in ("registry_website", "registry_email") or cl.availability != "available" \
                or cl.relation != "self" or not isinstance(cl.value, str):
            continue
        v = cl.value.strip().lower()
        dom = reg_domain(v.rsplit("@", 1)[-1] if cl.field == "registry_email" else _host(v))
        if dom not in refused:
            continue
        reason = re.sub(r"\s+", " ", refused[dom])[:240]
        cl.relation = "related"
        cl.confidence = min(cl.confidence, 0.6)
        cl.note = (f"filed in Enhetsregisteret for this entity, but the website identity check refused {dom} as this "
                   f"entity's own site ({reason}): a group, chain, sister or other entity's domain, not specific "
                   "to this entity")
        n += 1
    return n

