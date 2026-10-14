"""`nca bench`: score a run against Builderr's public reference profiles (the 100-company Signalpost product sample).

Builderr scores recall per external field family as 70% company recall + 30% individual-claim recall against its
verified reference pool. The public sample is a slice of that pool, so this is the closest local proxy of the
official recall score. It also lists, per family, every miss (with our gate's reason), every conflict (we
published a different value) and every extra (a value we found that the reference lacks: a candidate new
contribution to the pool, or a precision risk to review).

Families compared (the external families the public sample carries):
  website  - registrable domain of `web.value` (reference) vs `official_website` (ours)
  social   - approved company handles (`external.handles`, rightsStatus approved) vs `social_profile` (ours)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from .web import extract as X
from .web import verify as V

FAMILIES = ("website", "social")
PLATFORM_ALIASES = {"x": "twitter"}


def _domain(url: str | None) -> str | None:
    if not url:
        return None
    host = urlsplit(url if "://" in url else "https://" + url).hostname or ""
    return X.reg_domain(host) or None


def _social_key(platform: str, url: str) -> tuple[str, str] | None:
    platform = PLATFORM_ALIASES.get(platform, platform)
    can = V.canonical_social(platform, url) if platform in V.SOCIAL_PLATFORMS else None
    handle = can[1] if can else urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    handle = unquote(handle or "").lower().lstrip("@").strip("/")
    return (platform, handle) if handle else None


def reference_values(profile: dict[str, Any]) -> dict[str, set]:
    web = profile.get("web") or {}
    val = web.get("value") or {}
    dom = (val.get("registered_domain") or _domain(val.get("final_url") or val.get("requested_url"))) \
        if web.get("status") == "available" else None
    handles = set()
    for h in (profile.get("external") or {}).get("handles") or []:
        if h.get("rightsStatus") == "approved" and h.get("platform") and h.get("url"):
            k = _social_key(str(h["platform"]).lower(), h["url"])
            if k:
                handles.add(k)
    return {"website": {dom} if dom else set(), "social": handles}


def our_values(env: dict[str, Any]) -> tuple[dict[str, set], dict[str, str]]:
    out: dict[str, set] = {"website": set(), "social": set()}
    why: dict[str, str] = {}
    for c in env.get("claims") or []:
        f, av = c.get("field"), c.get("availability")
        if f == "official_website":
            if av == "available":
                d = _domain(str(c.get("value") or ""))
                if d:
                    out["website"].add(d)
            else:
                why["website"] = f"{av}: {(c.get('note') or '')[:160]}"
        elif f == "social_profile" and av == "available" and isinstance(c.get("value"), dict):
            k = _social_key(str(c["value"].get("platform", "")).lower(), str(c["value"].get("url", "")))
            if k:
                out["social"].add(k)
    return out, why


def bench(envelopes: list[dict[str, Any]], reference: list[dict[str, Any]]) -> dict[str, Any]:
    ref = {str(p.get("org") or p.get("organisation_number")): p for p in reference}
    ours = {e["organisation_number"]: e for e in envelopes}
    common = [o for o in ref if o in ours]
    fam: dict[str, dict[str, Any]] = {f: {"ref_companies": 0, "hit_companies": 0, "ref_claims": 0, "hit_claims": 0,
                                          "our_claims": 0, "misses": [], "conflicts": [], "extras": []}
                                      for f in FAMILIES}
    for org in common:
        rv = reference_values(ref[org])
        ov, why = our_values(ours[org])
        name = ref[org].get("name") or ""
        for f in FAMILIES:
            r, o, s = rv[f], ov[f], fam[f]
            s["our_claims"] += len(o)
            if r:
                s["ref_companies"] += 1
                s["ref_claims"] += len(r)
                s["hit_companies"] += bool(r & o)
                s["hit_claims"] += len(r & o)
                if not r & o:
                    item = {"org": org, "name": name, "reference": sorted(map(_fmt, r)), "ours": sorted(map(_fmt, o))}
                    if o and f == "website":
                        s["conflicts"].append(item)
                    else:
                        s["misses"].append({**item, "why": why.get(f, "")})
            extra = o - r
            if extra:
                s["extras"].append({"org": org, "name": name, "ours": sorted(map(_fmt, extra))})
    for f, s in fam.items():
        cr = s["hit_companies"] / s["ref_companies"] if s["ref_companies"] else None
        ir = s["hit_claims"] / s["ref_claims"] if s["ref_claims"] else None
        s["company_recall"], s["claim_recall"] = cr, ir
        s["coverage_score"] = round(0.7 * cr + 0.3 * ir, 4) if cr is not None and ir is not None else None
    scored = [s["coverage_score"] for s in fam.values() if s["coverage_score"] is not None]
    return {"companies_compared": len(common), "reference_companies": len(ref), "families": fam,
            "mean_coverage_score": round(sum(scored) / len(scored), 4) if scored else None,
            "recall_points_proxy": round(50 * sum(scored) / len(scored), 2) if scored else None}


def _fmt(v: Any) -> str:
    return f"{v[0]}:{v[1]}" if isinstance(v, tuple) else str(v)


def render(res: dict[str, Any], show: int = 10) -> str:
    lines = [f"compared {res['companies_compared']} of {res['reference_companies']} reference companies",
             "family    ref cos  hit cos  ref claims  hit claims  ours   score (0.7 company + 0.3 claim)"]
    for f, s in res["families"].items():
        sc = "n/a" if s["coverage_score"] is None else f"{100 * s['coverage_score']:.1f}%"
        lines.append(f"{f:<9} {s['ref_companies']:>7}  {s['hit_companies']:>7}  {s['ref_claims']:>10}  "
                     f"{s['hit_claims']:>10}  {s['our_claims']:>4}   {sc}")
    lines.append(f"mean family coverage {100 * (res['mean_coverage_score'] or 0):.1f}%  ->  recall proxy "
                 f"{res['recall_points_proxy']} / 50")
    for f, s in res["families"].items():
        for key in ("conflicts", "misses", "extras"):
            items = s[key]
            if not items:
                continue
            lines.append(f"\n{f} {key} ({len(items)}):")
            for it in items[:show]:
                lines.append(f"  {it['org']} {it['name'][:32]:<32} ref={','.join(it.get('reference', [])) or '-'}"
                             f" ours={','.join(it.get('ours', [])) or '-'}"
                             + (f"  [{it['why']}]" if it.get("why") else ""))
            if len(items) > show:
                lines.append(f"  ... {len(items) - show} more (--json for all)")
    return "\n".join(lines)


def load_envelopes(path: str | Path) -> list[dict[str, Any]]:
    import gzip
    p = Path(path)
    op = gzip.open if p.suffix == ".gz" else open
    with op(p, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]
