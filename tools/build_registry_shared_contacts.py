#!/usr/bin/env python3
"""Build the declared cache `cache/registry_shared_contacts.json.gz` from the Enhetsregisteret bulk file.

A registry phone or street address that sister companies share (a group switchboard, the group head office)
proves nothing about which of them a group website belongs to, so the website identity gate (web/verify.py)
does not count it as corroboration for any of them.

Output: {"phones": {"<8-digit national number>": [orgnr, ...]},
         "addresses": {"<postnummer>|<first address line, lower-case>": [orgnr, ...]}}
Phones: every number filed by two or more organisation numbers (a switchboard shared with a sister company,
an association secretariat or an accountant never identifies one of them). Addresses: only "siblings" - the
organisation numbers whose names share a distinctive name token (ALUHAK DRIFT AS / ALUHAK GRUPPEN AS) with
another organisation number on the same key, since office buildings are shared by strangers.

Also: "domains" - every registrable domain filed as a website (hjemmeside), with all its filers
[[orgnr, name], ...] (a franchise chain's domain filed by the chain, a group domain filed by sister companies);
"names" - names of organisation numbers that the discovery cache (site_discovery) found on a host shared with
another organisation number; "places" - Norwegian post towns and municipality names (single folded tokens), which
never align a website's brand with a company name on their own ("Spenst Otta" vs AKTIV OTTA AS).

Usage:  uv run python tools/build_registry_shared_contacts.py [--bulk data/bulk/enheter.csv.gz]
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from urllib.parse import urlsplit  # noqa: E402

from nordic_company_agent.registry_csv import iter_snapshot  # noqa: E402
from nordic_company_agent.web import extract as X  # noqa: E402
from nordic_company_agent.web.candidates import normalize_registry_website  # noqa: E402
from nordic_company_agent.web.verify import _nat8, address_key, fold, name_profile, squash  # noqa: E402


def name_tokens(name: str, form: str | None) -> frozenset[str]:
    prof = name_profile(name or "", form)
    return frozenset(t for t in (squash(x) for x in prof.required) if len(t) >= 3 and not t.isdigit())


def siblings(orgs: set[str], tokens: dict[str, frozenset[str]]) -> list[str]:
    out = set()
    lst = sorted(orgs)
    for i, a in enumerate(lst):
        ta = tokens.get(a, frozenset())
        for b in lst[i + 1:]:
            if ta & tokens.get(b, frozenset()):
                out |= {a, b}
    return sorted(out)


def discovery_shared_orgs(cache_dir: Path) -> set[str]:
    """Organisation numbers the discovery cache found on a host that another organisation number also has."""
    path = cache_dir / "site_discovery.jsonl.gz"
    if not path.exists():
        return set()
    by_host: dict[str, set[str]] = defaultdict(set)
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            for ld in row.get("leads") or []:
                u = ld.get("final_url") or ld.get("start") or ""
                host = (urlsplit(u if "://" in u else "https://" + u).hostname or "").lower().removeprefix("www.")
                if host:
                    by_host[host].add(str(row.get("organisation_number")))
                    by_host[X.reg_domain(host) or host].add(str(row.get("organisation_number")))
    return {o for orgs in by_host.values() if len(orgs) > 1 for o in orgs}


def build(bulk: Path, out_dir: Path) -> dict:
    phones: dict[str, set[str]] = defaultdict(set)
    addresses: dict[str, set[str]] = defaultdict(set)
    tokens: dict[str, frozenset[str]] = {}
    domains: dict[str, list[list[str]]] = defaultdict(list)
    names: dict[str, str] = {}
    places: set[str] = set()
    want_names = discovery_shared_orgs(out_dir)
    n = 0
    for rec in iter_snapshot(bulk):
        org = str(rec.get("organisasjonsnummer") or "")
        if not org:
            continue
        n += 1
        name = rec.get("navn") or ""
        tokens[org] = name_tokens(name, (rec.get("organisasjonsform") or {}).get("kode"))
        if org in want_names:
            names[org] = name
        u = normalize_registry_website(rec.get("hjemmeside")) if rec.get("hjemmeside") else None
        dom = X.reg_domain((urlsplit(u).hostname or "").lower()) if u else None
        if dom:
            domains[dom].append([org, name])
        for k in ("forretningsadresse", "postadresse"):
            a = rec.get(k) or {}
            if (a.get("landkode") or "NO") != "NO":
                continue
            for pk in ("poststed", "kommune"):
                for t in fold(a.get(pk) or "").replace("-", " ").split():
                    if len(t) >= 3 and t not in ("og", "i", "på"):
                        places.add(t)
        for k in ("telefon", "mobil"):
            p = _nat8(rec.get(k))
            if p:
                phones[p].add(org)
        for k in ("forretningsadresse", "postadresse"):
            a = rec.get(k) or {}
            lines = a.get("adresse") or []
            key = address_key(a.get("postnummer"), lines[0] if lines else None)
            if key:
                addresses[key].add(org)
    data: dict = {"phones": {}, "addresses": {}, "domains": dict(sorted(domains.items())),
                  "names": dict(sorted(names.items())), "places": sorted(places)}
    # phones: every number filed by >= 2 organisation numbers (a shared switchboard never identifies one of them);
    # addresses: only sibling organisations (office buildings and accountants' addresses are shared by strangers)
    for k, v in sorted(phones.items()):
        if len(v) > 1:
            data["phones"][k] = sorted(v)
    for k, v in sorted(addresses.items()):
        if len(v) > 1:
            sib = siblings(v, tokens) if len(v) <= 400 else []
            if len(sib) > 1:
                data["addresses"][k] = sib
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / "registry_shared_contacts.json.gz.tmp"
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
    tmp.replace(out_dir / "registry_shared_contacts.json.gz")
    meta = {"name": "registry_shared_contacts",
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source": f"Enhetsregisteret bulk file {bulk.name} (telefon, mobil, forretningsadresse, postadresse), NLOD 2.0",
            "entities": n, "shared_phones": len(data["phones"]), "shared_addresses": len(data["addresses"]),
            "website_domains": len(data["domains"]), "names": len(data["names"]), "places": len(data["places"]),
            "description": "Registry phone numbers filed by two or more organisation numbers, and street addresses "
                           "shared by sister organisations. The website identity gate never counts them as "
                           "corroboration and never publishes such a phone. Also every filed website domain with "
                           "its filers (chain/group domains), names of organisation numbers sharing a discovered "
                           "host, and Norwegian place names (which never align a site's brand on their own)."}
    (out_dir / "registry_shared_contacts.meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    return meta


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bulk", default=str(ROOT / "data" / "bulk" / "enheter.csv.gz"))
    ap.add_argument("--out", default=str(ROOT / "cache"))
    a = ap.parse_args()
    print(json.dumps(build(Path(a.bulk), Path(a.out)), indent=2))


if __name__ == "__main__":
    main()
