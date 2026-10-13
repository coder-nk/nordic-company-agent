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

FIELDS = {"registry_website", "registry_email", "registry_phone", "registry_mobile"}


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
    out = {"phones": filers.get("phones") or {}, "emails": filers.get("emails") or {}, "domains": domains,
           "names": shared.get("names") or {}}
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
                others = [o for o in idx["domains"].get(_host(cl.value), []) if o != me]
                what = "website host"
            elif cl.field == "registry_email":
                others = [o for o in idx["emails"].get(cl.value.strip().lower(), []) if o != me]
                what = "e-mail address"
            else:
                d = re.sub(r"\D", "", cl.value)[-8:]
                others = [o for o in idx["phones"].get(d, []) if o != me] if len(d) == 8 else []
                what = "phone number"
            if not others:
                continue
            example = idx["names"].get(others[0]) or others[0]
            cl.relation = "related"
            cl.confidence = min(cl.confidence, 0.6)
            cl.note = (f"filed in Enhetsregisteret for this entity, but the same {what} is also filed by "
                       f"{len(others)} other organisation number(s) (e.g. {example}): a shared property-manager, "
                       "accountant, group or chain contact, not specific to this entity")
            n += 1
    return n
