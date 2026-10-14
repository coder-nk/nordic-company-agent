"""Static HTML viewer: searchable index, per-company pages with per-fact source disclosure,
side-by-side compare. Works from file:// (no server, no external assets) and on mobile."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

FAMILY_TITLES = [
    ("legal_identity", "Company record"),
    ("corporate_structure", "Group & ownership"),
    ("annual_accounts", "Financials"),
    ("leadership", "People"),
    ("workplaces", "Locations"),
    ("website", "Company website"),
    ("social_profiles", "Company-linked profiles"),
    ("hiring", "Hiring"),
    ("public_activity", "Dated public activity"),
    ("other", "Other"),
]
DIMENSIONS = [("company", ("legal_identity",)), ("financials", ("annual_accounts",)),
              ("people_places", ("leadership", "workplaces")), ("website", ("website", "social_profiles")),
              ("footprint", ())]
# External footprint = signals beyond the registry: profiles, jobs, news, grants, encyclopedic identity, LEI.
FOOTPRINT_FIELDS = {"social_profile", "job_posting", "news_item", "state_aid_grant", "encyclopedia_entry",
                    "wikidata_item", "lei", "stock_listing", "careers_page"}

CSS = """
:root{--bg:#f7f7f5;--card:#fff;--ink:#1d1f21;--mute:#666b70;--line:#e3e3df;--acc:#0b6e4f;--warn:#a15c00;--bad:#9b1c1c;--chip:#eef4f1}
*{box-sizing:border-box}body{margin:0;font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--ink)}
a{color:var(--acc)}header{background:var(--card);border-bottom:1px solid var(--line);padding:14px 20px;position:sticky;top:0;z-index:5}
header h1{font-size:18px;margin:0}header p{margin:2px 0 0;color:var(--mute);font-size:13px}
main{max-width:1100px;margin:0 auto;padding:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:0 0 14px}
.card h2{font-size:16px;margin:0 0 10px}.muted{color:var(--mute)}.small{font-size:12.5px}
input[type=search]{width:100%;padding:10px 12px;border:1px solid var(--line);border-radius:8px;font-size:15px}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:12px;text-transform:uppercase;letter-spacing:.03em;color:var(--mute);cursor:pointer;white-space:nowrap}
.pill{display:inline-block;padding:1px 8px;border-radius:99px;background:var(--chip);font-size:12px;margin:0 4px 4px 0;white-space:nowrap}
.pill.good{background:#e3f4ea;color:var(--acc)}a.pill{text-decoration:none;color:inherit}.pill.off{background:#f1f1ee;color:#9a9a96}.pill.amb{background:#fff4e0;color:var(--warn)}.pill.bad{background:#fde8e8;color:var(--bad)}
.dims{white-space:nowrap}.dim{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:2px;background:#d9d9d4}.dim.on{background:var(--acc)}
.fact{display:grid;grid-template-columns:minmax(140px,220px) 1fr;gap:4px 14px;padding:7px 0;border-bottom:1px dashed var(--line)}
.fact:last-child{border-bottom:0}.fact .k{color:var(--mute);font-size:13.5px}.fact .v{word-break:break-word}
details.src{margin-top:3px}details.src summary{cursor:pointer;color:var(--acc);font-size:12.5px}
details.src div{font-size:12.5px;color:var(--mute);background:#fafaf8;border:1px solid var(--line);border-radius:6px;padding:6px 8px;margin-top:4px}
code{font-size:12px;background:#f3f3f0;padding:0 4px;border-radius:4px}.summary p{margin:0 0 8px}
sup a{text-decoration:none;font-size:11px}.cmp{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(260px,1fr))}
.toolbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:10px 0}.btn{border:1px solid var(--acc);background:var(--acc);color:#fff;border-radius:7px;padding:7px 12px;cursor:pointer;font-size:14px}
.btn[disabled]{opacity:.4;cursor:default}footer{color:var(--mute);font-size:12px;text-align:center;padding:20px}
@media (max-width:640px){.fact{grid-template-columns:1fr}.hide-sm{display:none}main{padding:10px}}
"""

INDEX_JS = """
const rows=window.__INDEX||[];const tb=document.getElementById('tb');const q=document.getElementById('q');
const sel=new Set();let sortK='name',asc=true;
function dims(r){return r.dims.map(d=>`<span class="dim ${d[1]?'on':''}" title="${d[0]}"></span>`).join('')}
function render(){const s=q.value.trim().toLowerCase();let xs=rows.filter(r=>!s||r.search.includes(s));
xs.sort((a,b)=>{let x=a[sortK],y=b[sortK];if(typeof x==='number')return asc?x-y:y-x;return asc?String(x).localeCompare(String(y)):String(y).localeCompare(String(x))});
tb.innerHTML=xs.map(r=>`<tr><td><input type=checkbox data-o="${r.org}" ${sel.has(r.org)?'checked':''} aria-label="select for compare"></td>
<td><a href="company/${r.org}.html">${r.name||r.org}</a><div class="small muted">${r.org} · ${r.form||''}</div></td>
<td class="hide-sm">${r.muni||''}</td><td class="hide-sm">${r.industry||''}</td><td class="dims">${dims(r)} <span class="small muted">${r.score}/5</span></td>
<td class="hide-sm small">${r.web?'<a href="'+r.web+'" rel="noopener nofollow">site</a>':'—'}</td><td class="small">${r.changes||''}</td></tr>`).join('');
document.getElementById('n').textContent=xs.length;}
q.addEventListener('input',render);tb.addEventListener('change',e=>{const o=e.target.dataset.o;if(!o)return;e.target.checked?sel.add(o):sel.delete(o);
document.getElementById('cmp').disabled=sel.size<2;});
document.querySelectorAll('th[data-k]').forEach(th=>th.onclick=()=>{const k=th.dataset.k;asc=sortK===k?!asc:true;sortK=k;render();});
document.getElementById('cmp').onclick=()=>{location.href='compare.html#'+[...sel].slice(0,4).join(',')};render();
"""

COMPARE_JS = """
const ids=(location.hash||'').slice(1).split(',').filter(Boolean);const box=document.getElementById('box');
const F=[['legal_form','Legal form'],['registry_status','Status'],['business_address','Address'],['industry','Industry'],['employees','Employees'],
['revenue','Revenue (latest)'],['net_profit','Net result (latest)'],['equity','Equity (latest)'],['ceo','CEO'],['chair','Chair'],
['registered_workplace_count','Workplaces'],['official_website','Website'],['social_profile','Profiles'],['active_job_count','Active job ads'],['lei','LEI']];
window.__C={};let left=ids.length;ids.forEach(o=>{const s=document.createElement('script');s.src='company/'+o+'.js';s.onload=s.onerror=()=>{if(--left===0)draw()};document.body.appendChild(s)});
function draw(){box.innerHTML=ids.map(o=>{const c=window.__C[o];if(!c)return `<div class=card>${o}: not found</div>`;
return `<div class=card><h2><a href="company/${o}.html">${c.name||o}</a></h2><div class="small muted">${o}</div>`+
F.map(([f,l])=>`<div class=fact><div class=k>${l}</div><div class=v>${(c.f[f]||'—')}</div></div>`).join('')+'</div>'}).join('')}
if(!ids.length)box.innerHTML='<div class=card>Select 2–4 companies on the index page.</div>';
"""


def _e(x: Any) -> str:
    return html.escape("" if x is None else str(x))


def _fmt_value(c: dict[str, Any]) -> str:
    if c.get("display"):
        return _e(c["display"])
    v = c.get("value")
    if isinstance(v, dict):
        if "name" in v:
            s = v["name"]
            if v.get("role"):
                s += f" — {v['role']}"
            return _e(s)
        if "url" in v and "platform" in v:
            return f'<a href="{_e(v["url"])}" rel="noopener nofollow">{_e(v["platform"])}: {_e(v["url"])}</a>'
        if "title" in v and "url" in v:
            return f'<a href="{_e(v["url"])}" rel="noopener nofollow">{_e(v["title"])}</a>' + (
                f' <span class="muted small">{_e(str(v.get("published") or v.get("date") or "")[:10])}</span>')
        return _e(", ".join(f"{k}: {val}" for k, val in v.items() if val not in (None, "", [], {})))
    if isinstance(v, list):
        return _e(", ".join(map(str, v)))
    if isinstance(v, str) and v.startswith("http"):
        return f'<a href="{_e(v)}" rel="noopener nofollow">{_e(v)}</a>'
    return _e(v)


def _evidence_html(c: dict[str, Any], ev: dict[str, dict[str, Any]]) -> str:
    parts = []
    for eid in c.get("evidence_ids", []):
        e = ev.get(eid)
        if not e:
            continue
        link = e.get("page_url") or e.get("source_url")
        bits = [f'<b>{_e(e.get("source_name") or e.get("source_class"))}</b>',
                f'<a href="{_e(link)}" rel="noopener nofollow">{_e(link)}</a>']
        if e.get("page_url") and e.get("source_url") != e.get("page_url"):
            bits.append(f'data: <a href="{_e(e["source_url"])}" rel="noopener nofollow">{_e(e["source_url"])}</a>')
        bits.append(f'retrieved {_e(e.get("retrieved_at"))}')
        if e.get("effective_at"):
            bits.append(f'as of {_e(e["effective_at"])}')
        if e.get("claim_span"):
            bits.append(f'quote: <code>{_e(e["claim_span"][:300])}</code>')
        if e.get("locator"):
            bits.append(f'at <code>{_e(e["locator"])}</code>')
        if e.get("content_sha256"):
            bits.append(f'sha256 <code>{_e(e["content_sha256"][:16])}…</code>')
        parts.append(" · ".join(bits))
    meta = []
    if c.get("confidence") is not None:
        meta.append(f"confidence {c['confidence']}")
    if c.get("match_basis"):
        meta.append(f"match: {c['match_basis']}")
    if c.get("relation") and c["relation"] != "self":
        meta.append(f"relation: {c['relation']}")
    if c.get("note"):
        meta.append(_e(c["note"]))
    if c.get("stale"):
        meta.append("STALE (carried forward)")
    body = "<br>".join(parts) + ("<br>" if parts and meta else "") + _e(" · ".join(meta)) if (parts or meta) else ""
    return f'<details class="src"><summary>View source</summary><div id="{_e(c["id"])}">{body}</div></details>' if body else ""


def _company_page(env: dict[str, Any]) -> str:
    org = env["organisation_number"]
    name = env.get("legal_name") or org
    ev = {e["id"]: e for e in env.get("evidence", [])}
    claims = env.get("claims", [])
    fams: dict[str, list[dict[str, Any]]] = {}
    for c in claims:
        fams.setdefault(c.get("family", "other"), []).append(c)
    out = [f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
           f'<title>{_e(name)} · {org}</title><style>{CSS}</style></head><body>',
           f'<header><h1>{_e(name)}</h1><p>Org. no. {_e(env.get("identity", {}).get("organisation_number_formatted") or org)} · '
           f'match: {_e(env.get("identity", {}).get("match_status"))} · checked {_e(env.get("run", {}).get("completed_at"))} · '
           f'<a href="../index.html">all companies</a></p></header><main>']
    summ = env.get("summary") or {}
    out.append('<section class="card summary"><h2>Summary</h2>')
    sig = summ.get("signals") or []
    if sig:
        cls = {"risk": "bad", "warn": "amb", "positive": "good"}
        out.append('<p class="signals">' + "".join(
            f'<a class="pill {cls.get(x.get("polarity"), "")}" href="#{_e((x.get("claim_ids") or [""])[0])}">{_e(x["text"])}</a>'
            for x in sig) + '</p>')
    for s in summ.get("sentences", []):
        if s.get("topic") == "signals":
            continue
        refs = "".join(f'<sup><a href="#{_e(cid)}" title="evidence">[{i + 1}]</a></sup>'
                       for i, cid in enumerate(s.get("claim_ids", [])[:4]) if cid)
        out.append(f'<p>{_e(s["text"])} {refs}</p>')
    out.append('<p class="small muted">Generated deterministically from the facts below; each sentence links to its supporting facts.</p></section>')
    if env.get("changes"):
        out.append('<section class="card"><h2>Changes since previous check</h2>')
        for ch in env["changes"]:
            out.append(f'<div class="fact"><div class="k">{_e(ch["field"].replace("_", " "))} '
                       f'<span class="pill {"amb" if ch.get("materiality") == "material" else ""}">{_e(ch.get("change_type"))}</span></div>'
                       f'<div class="v">{_e(ch.get("old_display") or ch.get("old_value") or "—")} → {_e(ch.get("new_display") or ch.get("new_value") or "—")}'
                       f'<div class="small muted">detected {_e(ch.get("detected_at"))}; previous check {_e(ch.get("previous_checked_at"))}'
                       f'{(" · effective " + _e(ch.get("effective_at"))) if ch.get("effective_at") else ""}</div></div></div>')
        out.append('</section>')
    for fam, title in FAMILY_TITLES:
        cs = fams.get(fam)
        if not cs:
            continue
        out.append(f'<section class="card"><h2>{_e(title)}</h2>')
        for c in cs:
            label = c["field"].replace("_", " ")
            if c.get("period"):
                p = c["period"]
                label += f' ({p.get("year")}, {p.get("scope")})'
            elif c.get("key", "").count("|") and c["field"] in ("industry", "former_name"):
                label += f' {c["key"].split("|", 1)[1]}'
            if c["availability"] == "available":
                val = _fmt_value(c)
            else:
                cls = "amb" if c["availability"] == "ambiguous" else ("bad" if c["availability"] in ("failed", "blocked") else "off")
                val = f'<span class="pill {cls}">{_e(c["availability"].replace("_", " "))}</span> <span class="small muted">{_e(c.get("note") or "")}</span>'
            out.append(f'<div class="fact" id="f-{_e(c["id"])}"><div class="k">{_e(label)}</div><div class="v">{val}{_evidence_html(c, ev)}</div></div>')
        out.append('</section>')
    mods = env.get("modules") or {}
    out.append('<section class="card"><h2>Sources checked</h2>')
    for m, st in mods.items():
        cls = {"available": "", "not_available": "off", "not_applicable": "off", "ambiguous": "amb"}.get(st.get("availability"), "bad")
        out.append(f'<span class="pill {cls}">{_e(m)}: {_e(st.get("availability"))}</span>')
    ops = env.get("operations") or {}
    out.append(f'<p class="small muted">Requests for this company: {_e(ops.get("requests"))} · third-party cost ${_e(ops.get("third_party_cost_usd"))} · '
               f'licences: {_e("; ".join((env.get("licences") or {}).values() if isinstance(env.get("licences"), dict) else env.get("licences") or []))}</p></section>')
    out.append('</main><footer>nordic-company-agent · deterministic, no LLM · data from Brønnøysundregistrene (NLOD 2.0), '
               'GLEIF (CC0), Wikidata (CC0), NAV Arbeidsplassen, company-owned websites</footer></body></html>')
    return "".join(out)


def _compact(env: dict[str, Any]) -> dict[str, Any]:
    f: dict[str, str] = {}
    av = [c for c in env.get("claims", []) if c["availability"] == "available"]

    def first(field, pred=lambda c: True):
        xs = [c for c in av if c["field"] == field and pred(c)]
        return xs

    for fld in ("legal_form", "registry_status", "business_address", "employees", "ceo", "chair", "registered_workplace_count",
                "official_website", "active_job_count", "lei"):
        xs = first(fld)
        if xs:
            f[fld] = _fmt_value(xs[0])
    ind = sorted(first("industry"), key=lambda c: (c.get("value") or {}).get("rank", 9))
    if ind:
        f["industry"] = _fmt_value(ind[0])
    soc = first("social_profile")
    if soc:
        f["social_profile"] = ", ".join(_e((c["value"] or {}).get("platform")) for c in soc)
    for fld in ("revenue", "net_profit", "equity"):
        xs = sorted(first(fld, lambda c: (c.get("period") or {}).get("scope") == "company"),
                    key=lambda c: (c.get("period") or {}).get("year") or 0, reverse=True)
        if xs:
            f[fld] = _fmt_value(xs[0]) + f' <span class="small muted">({(xs[0].get("period") or {}).get("year")})</span>'
    return {"name": env.get("legal_name"), "f": f}


def build_site(envelopes: list[dict[str, Any]], out: Path, report: dict[str, Any] | None = None) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "company").mkdir(exist_ok=True)
    index = []
    for env in envelopes:
        org = env["organisation_number"]
        (out / "company" / f"{org}.html").write_text(_company_page(env), encoding="utf-8")
        (out / "company" / f"{org}.js").write_text(
            f"window.__C=window.__C||{{}};window.__C[{json.dumps(org)}]={json.dumps(_compact(env), ensure_ascii=False)};",
            encoding="utf-8")
        av = [c for c in env.get("claims", []) if c["availability"] == "available"]
        fams = {c.get("family") for c in av}
        flds = {c.get("field") for c in av}
        dims = [(d, bool(flds & FOOTPRINT_FIELDS) if d == "footprint" else any(f in fams for f in fs))
                for d, fs in DIMENSIONS]
        get = lambda fld: next((c for c in av if c["field"] == fld), None)
        form = (get("legal_form") or {}).get("value") or {}
        muni = ((get("municipality") or {}).get("value") or {}).get("name")
        ind = sorted([c for c in av if c["field"] == "industry"], key=lambda c: (c.get("value") or {}).get("rank", 9))
        web = (get("official_website") or {}).get("value")
        name = env.get("legal_name") or ""
        index.append({"org": org, "name": name, "form": form.get("code") if isinstance(form, dict) else form,
                      "muni": (muni or "").title(), "industry": ((ind[0]["value"] or {}).get("description") if ind else "") or "",
                      "dims": dims, "score": sum(1 for _, on in dims if on), "web": web,
                      "changes": len(env.get("changes", [])) or "",
                      "search": f"{name} {org} {muni or ''} {(ind[0]['value'] or {}).get('description') if ind else ''}".lower()})
    cov = ""
    if report:
        ops = report.get("operations", {})
        cov = (f'{report.get("emitted_envelopes")} companies · {ops.get("requests")} requests · '
               f'${ops.get("third_party_cost_usd")} · run {_e(report.get("run_id"))}')
    page = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>Company profiles</title><style>{CSS}</style></head><body><header><h1>Norwegian company profiles</h1>'
            f'<p>{cov}</p></header><main><div class="card"><input type="search" id="q" placeholder="Search name, org. number, municipality, industry…" autofocus>'
            f'<div class="toolbar"><span class="small muted"><span id="n"></span> shown · coverage squares: company, financials, people &amp; places, website, footprint</span>'
            f'<button class="btn" id="cmp" disabled>Compare selected</button></div>'
            f'<table><thead><tr><th></th><th data-k="name">Company</th><th class="hide-sm" data-k="muni">Municipality</th>'
            f'<th class="hide-sm" data-k="industry">Industry</th><th data-k="score">Coverage</th><th class="hide-sm">Web</th><th data-k="changes">Changes</th></tr></thead>'
            f'<tbody id="tb"></tbody></table></div></main><footer>Every fact links to its source, retrieval time and content hash.</footer>'
            f'<script>window.__INDEX={json.dumps(index, ensure_ascii=False)};</script><script>{INDEX_JS}</script></body></html>')
    (out / "index.html").write_text(page, encoding="utf-8")
    cmp_page = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                f'<title>Compare</title><style>{CSS}</style></head><body><header><h1>Compare companies</h1>'
                f'<p><a href="index.html">back to all companies</a></p></header><main><div class="cmp" id="box"></div></main>'
                f'<script>{COMPARE_JS}</script></body></html>')
    (out / "compare.html").write_text(cmp_page, encoding="utf-8")
