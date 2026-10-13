"""Wikidata (P2333 = Norwegian organisation number) batch module, tier ``batch_external``.

The declared cache ``cache/wikidata_p2333.json.gz`` ({orgnr: [QIDs]}, built by
``tools/build_wikidata_index.py``) pre-selects the batch organisation numbers that have items, so a
batch without Wikidata companies costs zero requests. Otherwise one SPARQL GET (per <=150 orgnrs)
reads, in long format, the statements we use. Statements with an end time (pq:P582) or deprecated
rank are excluded; where a property has preferred-rank statements only those are used.

Published: ``wikidata_item``, ``encyclopedia_entry`` (no/nn/en Wikipedia), ``stock_listing``, and
social profiles whose handle matches the company's legal-name tokens. Never published: financials,
employee counts, official website (P856 is only passed on as a lead in
``company.data["wikidata"]["website"]``).
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any
from urllib.parse import urlencode, urlsplit

from ..envelope import (CC0_WIKIDATA, OPEN_KNOWLEDGE_BASE, Claim, CompanyResult, Evidence, ModuleOutcome,
                        utc_iso)
from ..web.extract import name_core_tokens, reg_domain, social_handle, social_matches_company

MODULE = "wikidata"
VERSION = "wikidata@1"
TIER = "batch_external"
ENDPOINT = "https://query.wikidata.org/sparql"
SOURCE_NAME = "Wikidata Query Service"
CHUNK = 150
MAX_RETRY_AFTER = 30.0
BUDGET_NOTE = "not checked: request budget reserved for higher-priority sources"
SOCIAL_NOTE = "linked from the Wikidata item that carries this organisation number"

PROPS = ("P856", "P571", "P159", "P749", "P414", "P946", "P1278", "P2013", "P2003", "P4264", "P2002", "P2397")
SOCIAL_PROPS = {  # property -> (platform, URL template)
    "P2013": ("facebook", "https://www.facebook.com/{}"),
    "P2003": ("instagram", "https://www.instagram.com/{}"),
    "P4264": ("linkedin", "https://www.linkedin.com/company/{}"),
    "P2002": ("twitter", "https://x.com/{}"),
    "P2397": ("youtube", "https://www.youtube.com/channel/{}"),
}
WIKIS = {"https://no.wikipedia.org/": "nb", "https://nn.wikipedia.org/": "nn", "https://en.wikipedia.org/": "en"}
WIKI_NAMES = {"nb": "Norwegian (Bokmål) Wikipedia", "nn": "Norwegian (Nynorsk) Wikipedia", "en": "English Wikipedia"}
PREFERRED = "http://wikiba.se/ontology#PreferredRank"
# Leftover handle text (after removing the company-name tokens) that does not point to another entity.
ALLOWED_RESIDUE = ("official", "offisiell", "gruppen", "group", "konsern", "norge", "norway", "asa", "as", "no",
                   "hq", "sa")

QUERY_TEMPLATE = """SELECT ?orgnr ?item ?itemLabel ?itemDescription ?prop ?st ?rank ?value ?valueLabel ?extra WHERE {
 VALUES ?orgnr { %s }
 ?item p:P2333 ?os . ?os ps:P2333 ?orgnr .
 FILTER NOT EXISTS { ?os wikibase:rank wikibase:DeprecatedRank }
 FILTER NOT EXISTS { ?os pq:P582 ?osEnd }
 OPTIONAL {
  {
   VALUES (?prop ?p ?ps) { %s }
   ?item ?p ?st . ?st ?ps ?value ; wikibase:rank ?rank .
   FILTER(?rank != wikibase:DeprecatedRank)
   FILTER NOT EXISTS { ?st pq:P582 ?stEnd }
   OPTIONAL { ?st pq:P249 ?ticker }
   OPTIONAL { ?value wdt:P2333 ?vorg }
   BIND(COALESCE(?ticker, ?vorg) AS ?extra)
  } UNION {
   VALUES ?wiki { %s }
   ?value schema:about ?item ; schema:isPartOf ?wiki ; schema:name ?extra .
   BIND("sitelink" AS ?prop)
  }
 }
 SERVICE wikibase:label { bd:serviceParam wikibase:language "nb,nn,en". }
}"""


def build_query(orgnrs: list[str]) -> str:
    q = QUERY_TEMPLATE % (" ".join(f'"{o}"' for o in orgnrs),
                          " ".join(f'("{p}" p:{p} ps:{p})' for p in PROPS),
                          " ".join(f"<{w}>" for w in WIKIS))
    return re.sub(r"\s+", " ", q).strip()


def query_url(orgnrs: list[str]) -> str:
    return ENDPOINT + "?" + urlencode({"format": "json", "query": build_query(orgnrs)})


def item_url(qid: str) -> str:
    return f"https://www.wikidata.org/wiki/{qid}"


def _qid(uri: str | None) -> str:
    return (uri or "").rsplit("/", 1)[-1]


def _eligible(company: CompanyResult) -> bool:
    ident = company.data.get("identity") or {}
    return ident.get("match_status") in (None, "exact")


def _legal_name(company: CompanyResult) -> str:
    reg = company.data.get("registry") or {}
    return reg.get("navn") or company.first_value("legal_name") or ""


def _registry_domain(company: CompanyResult) -> str:
    site = ((company.data.get("registry") or {}).get("hjemmeside") or "").strip()
    if not site:
        return ""
    host = urlsplit(site if "//" in site else "http://" + site).hostname or ""
    return reg_domain(host)


def _translit(s: str) -> str:
    return s.replace("æ", "ae").replace("ø", "o").replace("å", "a").replace("ä", "a").replace("ö", "o")


def handle_matches_company(url: str, legal_name: str, domain: str = "") -> bool:
    """A Wikidata social handle is publishable only when it is made of the company's own name tokens
    (or its registry-website domain label) plus at most a neutral suffix such as 'asa' / 'group'.
    This rejects e.g. 'telenorsverige' (Telenor Sweden) for Telenor ASA, or 'Equinor_UK'."""
    handle = social_handle(url)
    if len(handle) < 3:
        return False
    toks = [re.sub(r"[^a-z0-9æøå]", "", t) for t in name_core_tokens(legal_name or "")]
    label = re.sub(r"[^a-z0-9]", "", (domain or "").split(".")[0])
    cands = {t for t in toks if len(t) >= 2} | ({label} if len(label) >= 3 else set())
    cands |= {_translit(t) for t in cands}
    h = _translit(handle)
    strong = [c for c in cands if len(c) >= 3 and c in h]
    if not strong:
        return False
    rest = h
    for c in sorted(cands, key=len, reverse=True):
        rest = rest.replace(c, "")
    for suffix in sorted(ALLOWED_RESIDUE, key=len, reverse=True):
        rest = rest.replace(suffix, "")
    if rest:
        return False
    # The shared website-module heuristic must agree too, unless the handle is exactly a short name token.
    return social_matches_company(url, legal_name or "", domain or "") or h in cands


def label_matches(label: str, legal_name: str) -> bool:
    """True when the item label shares a distinctive (>= 3 chars) name token with the registry name."""
    a = {_translit(t) for t in name_core_tokens(label or "") if len(t) >= 3}
    b = {_translit(t) for t in name_core_tokens(legal_name or "") if len(t) >= 3}
    return bool(a & b) or not b


def _span(binding: dict[str, Any]) -> str:
    keep = {k: binding[k] for k in ("orgnr", "item", "prop", "st", "value", "valueLabel", "extra") if k in binding}
    return json.dumps(keep, ensure_ascii=False, separators=(",", ":"))[:300]


def _outcome_all(companies: list[CompanyResult], availability: str, note: str, url: str | None = None,
                 status: int | None = None) -> None:
    for c in companies:
        if availability == "failed":
            c.error(MODULE, "failed", note, url)
        c.outcome(ModuleOutcome(MODULE, availability, note=note, source_url=url, http_status=status))


async def _query(ctx, orgnrs: list[str]):
    """Returns (FetchResult | None, availability_on_failure, note, url)."""
    url = query_url(orgnrs)
    headers = {"Accept": "application/sparql-results+json"}
    for attempt in (1, 2):
        if not ctx.allow(TIER, 1):
            return None, "failed", BUDGET_NOTE, url
        try:
            res = await ctx.get(url, tier=TIER, source=MODULE, headers=headers, retries=0)
        except Exception as exc:  # noqa: BLE001 - modules never raise
            if attempt == 1 and ctx.time_left() > 60:
                await asyncio.sleep(2.0)
                continue
            return None, "failed", f"Wikidata query failed: {type(exc).__name__}: {exc}", url
        if res.status in (429, 503) and attempt == 1:
            ra = res.headers.get("retry-after", "")
            wait = min(float(ra), MAX_RETRY_AFTER) if ra.replace(".", "", 1).isdigit() else 5.0
            if ctx.time_left() > wait + 30:
                await asyncio.sleep(wait)
                continue
        if res.status in (403, 429):
            return None, "blocked", (f"Wikidata Query Service refused the query (HTTP {res.status}, "
                                     f"retry-after {res.headers.get('retry-after', 'n/a')})"), url
        if not res.ok:
            return None, "failed", f"Wikidata query HTTP {res.status}", url
        return res, "available", "", url
    return None, "failed", "Wikidata query failed", url


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
    index = ctx.caches.json("wikidata_p2333")
    if isinstance(index, dict) and isinstance(index.get("map"), dict):
        index = index["map"]
    meta = ctx.caches.meta("wikidata_p2333") or {}
    if isinstance(index, dict):
        wanted = sorted(o for o in targets if o in index)
        built = meta.get("built_at", "unknown date")
        for o in sorted(set(targets) - set(wanted)):
            for c in targets[o]:
                c.outcome(ModuleOutcome(MODULE, "not_available",
                                        note=f"no Wikidata item carries P2333 = {o} (declared index of "
                                             f"{len(index)} organisation numbers, built {built})"))
    else:
        wanted = sorted(targets)
    for i in range(0, len(wanted), CHUNK):
        chunk = wanted[i:i + CHUNK]
        chunk_cos = [c for o in chunk for c in targets[o]]
        res, avail, note, url = await _query(ctx, chunk)
        if res is None:
            _outcome_all(chunk_cos, avail, note, url)
            continue
        retrieved_at = utc_iso(res.retrieved_at)
        sha = ctx.snapshots.save_result(res, retrieved_at)
        try:
            bindings = res.json()["results"]["bindings"]
        except (ValueError, KeyError, TypeError):
            _outcome_all(chunk_cos, "failed", "Wikidata returned an unexpected body", url, res.status)
            continue
        by_org: dict[str, dict[str, list[tuple[int, dict[str, Any]]]]] = {}
        for idx, b in enumerate(bindings):
            o = (b.get("orgnr") or {}).get("value")
            q = _qid((b.get("item") or {}).get("value"))
            if o in targets and q.startswith("Q"):
                by_org.setdefault(o, {}).setdefault(q, []).append((idx, b))
        for o in chunk:
            for c in targets[o]:
                _emit(c, by_org.get(o, {}), url=url, sha=sha, retrieved_at=retrieved_at, status=res.status)


def _ev(qid: str, *, url: str, sha: str, retrieved_at: str, idx: int, binding: dict[str, Any],
        status: int, page_url: str | None = None) -> Evidence:
    return Evidence(source_url=item_url(qid), source_class=OPEN_KNOWLEDGE_BASE, source_name=SOURCE_NAME,
                    retrieved_at=retrieved_at, content_sha256=sha, claim_span=_span(binding),
                    locator=f"/results/bindings/{idx}", extraction_method=VERSION, page_url=page_url or item_url(qid),
                    http_status=status, licence=CC0_WIKIDATA, retrieved_via=url)


def _label(rows: list[tuple[int, dict[str, Any]]], qid: str) -> str:
    for _i, b in rows:
        v = (b.get("itemLabel") or {}).get("value")
        if v:
            return v
    return qid


def _truthy(rows: list[tuple[int, dict[str, Any]]]) -> list[tuple[int, dict[str, Any]]]:
    """Wikidata 'truthy' semantics: preferred-rank statements win over normal-rank ones."""
    pref = [r for r in rows if (r[1].get("rank") or {}).get("value") == PREFERRED]
    return pref or rows


def _emit(company: CompanyResult, items: dict[str, list[tuple[int, dict[str, Any]]]], *, url: str, sha: str,
          retrieved_at: str, status: int) -> None:
    o = company.organisation_number
    if not items:
        company.outcome(ModuleOutcome(MODULE, "not_available", source_url=url, retrieved_at=retrieved_at,
                                      http_status=status,
                                      note=f"no current, non-deprecated P2333 = {o} statement on Wikidata"))
        return
    kw = dict(url=url, sha=sha, retrieved_at=retrieved_at, status=status)
    qids = sorted(items, key=lambda q: int(q[1:]) if q[1:].isdigit() else 0)
    if len(qids) > 1:
        claims = []
        for q in qids:
            idx, b = items[q][0]
            claims.append(Claim(field="wikidata_item", value={"id": q, "url": item_url(q), "label": _label(items[q], q)},
                                evidence=[_ev(q, idx=idx, binding=b, **kw)], qualifier=q, confidence=0.9,
                                display=f"{_label(items[q], q)} ({q})", method="wikidata_P2333",
                                note=(f"organisation number {o} is carried by {len(qids)} Wikidata items "
                                      f"({', '.join(qids)}: brand/related/predecessor items); no other Wikidata "
                                      "facts are published")))
        company.add(claims)
        company.data["wikidata"] = {"items": [c.value for c in claims], "ambiguous": True}
        company.outcome(ModuleOutcome(MODULE, "ambiguous", source_url=url, retrieved_at=retrieved_at,
                                      http_status=status,
                                      note=f"{len(qids)} Wikidata items carry P2333 = {o}: {', '.join(qids)}"))
        return

    qid = qids[0]
    rows = items[qid]
    label = _label(rows, qid)
    desc = next(((b.get("itemDescription") or {}).get("value") for _i, b in rows
                 if (b.get("itemDescription") or {}).get("value")), None)
    by_prop: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for idx, b in rows:
        p = (b.get("prop") or {}).get("value")
        if p and (b.get("value") or {}).get("value") is not None:
            by_prop.setdefault(p, []).append((idx, b))

    def vals(p: str, truthy: bool = True) -> list[tuple[int, dict[str, Any]]]:
        rs = by_prop.get(p, [])
        rs = _truthy(rs) if truthy else rs
        seen, out = set(), []
        for r in sorted(rs, key=lambda r: (r[1]["value"]["value"], (r[1].get("extra") or {}).get("value", ""))):
            key = (r[1]["value"]["value"], (r[1].get("extra") or {}).get("value"))
            if key not in seen:
                seen.add(key)
                out.append(r)
        return out

    claims: list[Claim] = []
    first_idx, first_b = rows[0]
    legal_name = _legal_name(company)
    label_ok = label_matches(label, legal_name)
    label_note = None if label_ok else (f"the Wikidata label '{label}' differs from the registry name "
                                        f"'{legal_name}' (the item may use a former or brand name)")
    claims.append(Claim(field="wikidata_item", value={"id": qid, "url": item_url(qid), "label": label},
                        evidence=[_ev(qid, idx=first_idx, binding=first_b, **kw)], qualifier=qid,
                        confidence=0.95 if label_ok else 0.9, display=f"{label} ({qid})", method="wikidata_P2333",
                        note=f"Wikidata item whose P2333 (Norwegian organisation number) statement is {o}"
                             + (f"; {label_note}" if label_note else "")))

    for idx, b in vals("sitelink", truthy=False):
        wiki_url = b["value"]["value"]
        host = f"https://{urlsplit(wiki_url).hostname}/"
        lang = WIKIS.get(host)
        if not lang:
            continue
        title = (b.get("extra") or {}).get("value")
        claims.append(Claim(field="encyclopedia_entry", value={"language": lang, "url": wiki_url, "title": title},
                            evidence=[_ev(qid, idx=idx, binding=b, page_url=wiki_url, **kw)], qualifier=lang,
                            confidence=0.9 if label_ok else 0.8, display=f"{WIKI_NAMES[lang]}: {title}",
                            method="wikidata_sitelink",
                            note=f"Wikipedia article linked from Wikidata item {qid}"
                                 + (f"; {label_note}" if label_note else "")))

    isins = [b["value"]["value"] for _i, b in vals("P946")]
    listings = vals("P414")
    tickers = [((b.get("extra") or {}).get("value"), _qid(b["value"]["value"])) for _i, b in listings]
    for idx, b in listings:
        ticker = (b.get("extra") or {}).get("value")
        exch_qid = _qid(b["value"]["value"])
        exch = (b.get("valueLabel") or {}).get("value") or exch_qid
        qual = ticker or exch_qid
        if ticker and sum(1 for t, _e in tickers if t == ticker) > 1:
            qual = f"{ticker}:{exch_qid}"
        val = {"ticker": ticker, "exchange": exch, "isin": isins[0] if len(isins) == 1 else None}
        claims.append(Claim(field="stock_listing", value=val, evidence=[_ev(qid, idx=idx, binding=b, **kw)],
                            qualifier=qual, confidence=0.85,
                            display=f"{exch}: {ticker or 'listed'}" + (f" (ISIN {val['isin']})" if val["isin"] else ""),
                            method="wikidata_P414_P249",
                            note=f"stock exchange (P414) statement with ticker qualifier (P249) on {qid}"))

    domain = _registry_domain(company)
    dropped: list[dict[str, str]] = []
    socials: list[Claim] = []
    for prop, (platform, tmpl) in SOCIAL_PROPS.items():
        for idx, b in vals(prop):
            handle = b["value"]["value"].strip()
            if not handle:
                continue
            surl = tmpl.format(handle)
            if not handle_matches_company(surl, legal_name, domain):
                dropped.append({"platform": platform, "handle": handle, "reason": "handle does not match legal name"})
                continue
            socials.append(Claim(field="social_profile", value={"platform": platform, "url": surl, "handle": handle},
                                 evidence=[_ev(qid, idx=idx, binding=b, page_url=surl, **kw)],
                                 qualifier=f"{platform}|{surl}", confidence=0.8, display=f"{platform}: {surl}",
                                 method="wikidata_P2333", match_basis="wikidata_P2333_item_and_handle_name",
                                 note=SOCIAL_NOTE))
    claims.extend(socials)

    websites_all = vals("P856", truthy=False)
    preferred_sites = {b["value"]["value"] for _i, b in _truthy(by_prop.get("P856", []))}
    websites = [b["value"]["value"] for _i, b in websites_all]

    def site_rank(u: str) -> tuple:
        host = (urlsplit(u).hostname or "").lower()
        return (0 if domain and reg_domain(host) == domain else 1, 0 if u in preferred_sites else 1,
                0 if host.endswith(".no") else 1, len(u), u)

    company.add(claims)
    company.data["wikidata"] = {
        "id": qid, "url": item_url(qid), "label": label, "description": desc, "label_matches_registry": label_ok,
        "website": min(websites, key=site_rank) if websites else None,
        "websites": websites,
        "inception": [b["value"]["value"][:10] for _i, b in vals("P571")],
        "headquarters": [(b.get("valueLabel") or {}).get("value") for _i, b in vals("P159")],
        "parents": [{"id": _qid(b["value"]["value"]), "label": (b.get("valueLabel") or {}).get("value"),
                     "organisation_number": (b.get("extra") or {}).get("value")} for _i, b in vals("P749")],
        "lei": [b["value"]["value"] for _i, b in vals("P1278")],
        "isin": isins,
        "socials_dropped": dropped,
        "retrieved_at": retrieved_at,
        "source_url": item_url(qid),
        "website_note": "P856 official website from Wikidata: a lead only, must pass the website identity gate",
    }
    company.outcome(ModuleOutcome(MODULE, "available", source_url=item_url(qid), retrieved_at=retrieved_at,
                                  http_status=status, note=f"Wikidata item {qid} carries P2333 = {o}"))
