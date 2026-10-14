"""`nca ui`: the interactive company-intelligence app (single command, stdlib only, works offline).

Serves the single-page app in `ui/` plus a small JSON API over every envelope file it is given (JSONL or .jsonl.gz,
e.g. smoke/envelopes.jsonl and profiles/envelopes.jsonl.gz). Companies researched from the app are written to
<store>/researched.jsonl and override older envelopes of the same organisation number.

GET  /api/index                 compact list of every company + dataset statistics
GET  /api/company/{orgnr}       full envelope (summary signals recomputed for envelopes from older runs)
GET  /api/snapshot/{sha}?q=...  excerpt of the archived source body around a verbatim evidence span
POST /api/research {"organisation_number": "..."}  -> {"job": id}; live research with the full pipeline
GET  /api/jobs/{id}             job state with per-module progress events
GET  /health
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import re
import threading
import time
import uuid
import webbrowser
from collections import Counter
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from .orgnr import InvalidOrgNumber, normalize

UI_DIR = Path(__file__).parent / "ui"
STATIC_TYPES = {".html": "text/html", ".js": "application/javascript", ".css": "text/css", ".svg": "image/svg+xml",
                ".json": "application/json", ".ico": "image/x-icon", ".png": "image/png", ".woff2": "font/woff2"}
FIN_FIELDS = ("revenue", "operating_profit", "net_profit", "equity", "total_assets", "total_liabilities")
INDEX_VERSION = 5


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------------- compact index row
def _num(v: Any) -> float | None:
    try:
        return float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def summarize(env: dict[str, Any], dataset: str) -> dict[str, Any]:
    claims = [c for c in env.get("claims") or [] if c.get("availability") == "available"]
    by: dict[str, list[dict]] = {}
    for c in claims:
        by.setdefault(c["field"], []).append(c)

    def one(f: str, rel: str = "self") -> Any:
        for c in by.get(f, []):
            if (c.get("relation") or "self") == rel:
                return c
        return None

    ident = env.get("identity") or {}
    form = (one("legal_form") or {}).get("value") or {}
    ind = sorted(by.get("industry", []), key=lambda c: (c.get("value") or {}).get("rank", 9))
    addr = (one("business_address") or {}).get("value") or {}
    emp = ((one("employees") or {}).get("value") or {})
    # latest company-scope financial year
    years: dict[int, dict[str, float]] = {}
    cur = "NOK"
    for f in FIN_FIELDS:
        for c in by.get(f, []):
            per = c.get("period") or {}
            if per.get("scope") != "company" or not per.get("year"):
                continue
            years.setdefault(int(per["year"]), {})[f] = _num(c.get("value"))
    for c in by.get("accounts_currency", []):
        cur = str(c.get("value") or cur)
    fin = {}
    if years:
        y = max(years)
        fin = {"year": y, **years[y], "currency": cur,
               "series": [{"year": k, "revenue": v.get("revenue"), "net_profit": v.get("net_profit")}
                          for k, v in sorted(years.items())]}
        prev = years.get(y - 1, {})
        if fin.get("revenue") and prev.get("revenue") and prev["revenue"] > 0 and fin["revenue"] > 0:
            fin["growth"] = round((fin["revenue"] - prev["revenue"]) / prev["revenue"] * 100, 1)
    site = one("official_website")
    ver = (one("website_verification") or {}).get("value") or {}
    summ = env.get("summary") or {}
    signals = summ.get("signals")
    if signals is None:
        signals = _recompute_summary(env).get("signals") or []
    fams = Counter(c.get("family") or "other" for c in claims)
    hosts = {re.sub(r"^www\.", "", urlsplit(e.get("source_url") or "").hostname or "") for e in env.get("evidence") or []}
    return {
        "orgnr": env.get("organisation_number"),
        "orgnr_fmt": ident.get("organisation_number_formatted"),
        "name": (one("legal_name") or {}).get("value") or env.get("legal_name") or env.get("organisation_number"),
        "match": ident.get("match_status"),
        "form": form.get("code"), "form_text": form.get("description"),
        "status": (one("registry_status") or {}).get("value"),
        "municipality": (addr.get("municipality") or addr.get("kommune") or "") if isinstance(addr, dict) else "",
        "industry": (ind[0].get("value") or {}).get("description") if ind else None,
        "industry_code": (ind[0].get("value") or {}).get("code") if ind else None,
        "employees": emp.get("count") if isinstance(emp, dict) else None,
        "founded": (one("founding_date") or {}).get("value"),
        "fin": fin,
        "website": site.get("value") if site else None, "website_level": ver.get("level"),
        "signals": [{"signal": s.get("signal"), "polarity": s.get("polarity"), "text": s.get("text")} for s in signals],
        "hiring": len(by.get("job_posting", [])), "grants": len(by.get("state_aid_grant", [])),
        "workplaces": len(by.get("registered_workplace", [])),
        "leaders": sum(len(by.get(f, [])) for f in ("ceo", "chair", "board_member", "owner", "business_manager")),
        "records": sum(len(by.get(f, [])) for f in ("tax_incentive_project", "research_grant", "food_inspection")),
        "group": sum(len(by.get(f, [])) for f in ("parent_company", "ultimate_parent", "group_subsidiary", "lei_parent")),
        "events": len(by.get("registry_event", [])) + len(by.get("role_change_event", [])),
        "socials": len(by.get("social_profile", [])), "news": len(by.get("news_item", [])),
        "facts": len(claims), "families": dict(fams), "sources": len(hosts - {""}),
        "changes": len(env.get("changes") or []),
        "checked_at": (env.get("run") or {}).get("completed_at"), "run_id": (env.get("run") or {}).get("run_id"),
        "dataset": dataset,
    }


def _recompute_summary(env: dict[str, Any]) -> dict[str, Any]:
    try:
        from .synthesis import build_summary
        return build_summary(env.get("claims") or [], env.get("modules") or {}, env.get("changes") or [],
                             env.get("identity") or {})
    except Exception:
        return env.get("summary") or {}


# --------------------------------------------------------------------------- data library
class Library:
    """Envelopes by organisation number, read from disk on demand (byte offsets), with a compact cached index."""

    def __init__(self, sources: list[Path], store: Path):
        self.store = store
        (store / "cache").mkdir(parents=True, exist_ok=True)
        self.researched = store / "researched.jsonl"
        self.lock = threading.Lock()
        self.loc: dict[str, tuple[Path, int, int]] = {}
        self.rows: dict[str, dict[str, Any]] = {}
        self.datasets: list[dict[str, Any]] = []
        self.snapshot_dirs: list[Path] = []
        for src in sources:
            self._load(src)
        if self.researched.exists():
            self._load(self.researched, dataset="researched")

    def _plain(self, src: Path) -> Path:
        if src.suffix != ".gz":
            return src
        st = src.stat()
        out = self.store / "cache" / f"{src.stem}-{hashlib.sha1(f'{src.resolve()}|{st.st_mtime_ns}|{st.st_size}'.encode()).hexdigest()[:10]}"
        if not out.exists():
            tmp = out.with_suffix(".tmp")
            with gzip.open(src, "rb") as fi, tmp.open("wb") as fo:
                while chunk := fi.read(1 << 20):
                    fo.write(chunk)
            tmp.replace(out)
        return out

    def _load(self, src: Path, dataset: str | None = None) -> None:
        if not src.exists():
            return
        dataset = dataset or (src.parent.name if src.parent.name not in ("", ".") else src.stem)
        plain = self._plain(src)
        st = plain.stat()
        key = hashlib.sha1(f"{plain.resolve()}|{st.st_mtime_ns}|{st.st_size}|{INDEX_VERSION}".encode()).hexdigest()[:12]
        cache = self.store / "cache" / f"index-{key}.json"
        rows: list[dict] | None = None
        if cache.exists():
            try:
                rows = json.loads(cache.read_text(encoding="utf-8"))
            except Exception:
                rows = None
        if rows is None:
            rows = []
            off = 0
            with plain.open("rb") as fh:
                for line in fh:
                    n = len(line)
                    if line.strip():
                        try:
                            env = json.loads(line)
                        except ValueError:
                            off += n
                            continue
                        if isinstance(env, dict) and env.get("organisation_number"):
                            r = summarize(env, dataset)
                            r["_off"], r["_len"] = off, n
                            rows.append(r)
                    off += n
            if dataset != "researched":
                cache.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        report = None
        rp = src.parent / "run-report.json"
        if rp.exists() and dataset != "researched":
            try:
                rj = json.loads(rp.read_text(encoding="utf-8"))
                ops = rj.get("operations") or {}
                report = {"requests": ops.get("requests"), "request_limit": ops.get("request_limit"),
                          "started_at": rj.get("started_at"), "completed_at": rj.get("completed_at"),
                          "website_levels": rj.get("website_verification_levels"),
                          "family_company_coverage": rj.get("family_company_coverage")}
            except Exception:
                report = None
        for r in rows:
            r["dataset"] = dataset
            self.loc[r["orgnr"]] = (plain, r.pop("_off"), r.pop("_len"))
            self.rows[r["orgnr"]] = r
        if (src.parent / "snapshots").is_dir():
            self.snapshot_dirs.append(src.parent / "snapshots")
        self.datasets = [d for d in self.datasets if d["name"] != dataset] + [
            {"name": dataset, "file": str(src), "companies": len(rows), "report": report}]

    def get(self, orgnr: str) -> dict[str, Any] | None:
        loc = self.loc.get(orgnr)
        if not loc:
            return None
        path, off, n = loc
        with path.open("rb") as fh:
            fh.seek(off)
            env = json.loads(fh.read(n))
        if env.get("summary") is None or "signals" not in (env.get("summary") or {}):
            env["summary"] = _recompute_summary(env)
        return env

    def add(self, env: dict[str, Any]) -> None:
        with self.lock:
            line = (json.dumps(env, ensure_ascii=False, default=str) + "\n").encode("utf-8")
            with self.researched.open("ab") as fh:
                off = fh.tell()
                fh.write(line)
            r = summarize(env, "researched")
            self.loc[r["orgnr"]] = (self.researched, off, len(line))
            self.rows[r["orgnr"]] = r
            ds = next((d for d in self.datasets if d["name"] == "researched"), None)
            if ds is None:
                self.datasets.append({"name": "researched", "file": str(self.researched), "companies": 0, "report": None})
                ds = self.datasets[-1]
            ds["companies"] = sum(1 for x in self.rows.values() if x["dataset"] == "researched")

    def stats(self) -> dict[str, Any]:
        return compute_stats(list(self.rows.values()), self.datasets)

    def snapshot_excerpt(self, sha: str, q: str) -> dict[str, Any]:
        if not re.fullmatch(r"[0-9a-f]{64}", sha):
            return {"found": False, "error": "bad hash"}
        for d in self.snapshot_dirs:
            p = d / f"{sha}.gz"
            if p.exists():
                try:
                    raw = gzip.decompress(p.read_bytes())
                except Exception:
                    return {"found": False, "error": "unreadable snapshot"}
                text = raw.decode("utf-8", errors="replace")
                i = text.find(q) if q else -1
                if i < 0 and q:
                    i = re.sub(r"\s+", " ", text).find(re.sub(r"\s+", " ", q))
                    if i >= 0:
                        text = re.sub(r"\s+", " ", text)
                if i < 0:
                    return {"found": True, "matched": False, "bytes": len(raw), "excerpt": text[:600]}
                a, b = max(0, i - 280), min(len(text), i + len(q) + 280)
                return {"found": True, "matched": True, "bytes": len(raw), "before": text[a:i],
                        "match": text[i:i + len(q)], "after": text[i + len(q):b]}
        return {"found": False}


def compute_stats(rows: list[dict[str, Any]], datasets: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows) or 1
    fams = Counter()
    for r in rows:
        for f in r["families"]:
            fams[f] += 1
    return {
        "companies": len(rows), "facts": sum(r["facts"] for r in rows),
        "websites": sum(1 for r in rows if r["website"]),
        "with_accounts": sum(1 for r in rows if r["fin"]),
        "hiring": sum(1 for r in rows if r["hiring"]),
        "risk": sum(1 for r in rows if any(s["polarity"] == "risk" for s in r["signals"])),
        "grants": sum(1 for r in rows if r["grants"]),
        "family_coverage": {f: round(c / n * 100, 1) for f, c in fams.most_common()},
        "coverage": [
            {"key": k, "label": lab, "desc": desc, "pct": round(sum(1 for r in rows if test(r)) / n * 100, 1)}
            for k, lab, desc, test in COVERAGE_DIMS],
        "sources": 12, "cost_usd": 0.0, "datasets": datasets,
    }


COVERAGE_DIMS = (
    ("identity", "Legal identity", "Name, form, address, status, industry", lambda r: r["match"] == "exact"),
    ("accounts", "Annual accounts", "Revenue, results and balance sheet", lambda r: bool(r["fin"])),
    ("leadership", "Leadership", "General manager, board or owner", lambda r: r["leaders"] > 0),
    ("workplaces", "Workplaces", "Registered shops, offices and plants", lambda r: r["workplaces"] > 0),
    ("events", "Registry activity", "Dated updates in the last year", lambda r: r["events"] > 0),
    ("website", "Verified website", "Proven to be the company's own site", lambda r: bool(r["website"])),
    ("socials", "Social profiles", "Linked from the verified site", lambda r: r["socials"] > 0),
    ("news", "Company news", "Dated posts on the company's site", lambda r: r["news"] > 0),
    ("grants", "Public support", "Grants in Støtteregisteret", lambda r: r["grants"] > 0),
    ("records", "R&D and inspections", "SkatteFUNN, Research Council, Mattilsynet", lambda r: r["records"] > 0),
    ("group", "Group & ownership", "Parents and subsidiaries", lambda r: r["group"] > 0),
    ("hiring", "Hiring", "Active job ads on NAV", lambda r: r["hiring"] > 0),
)


# --------------------------------------------------------------------------- static export (no server)
def _excerpts(env: dict[str, Any], snapshot_dirs: list[Path], width: int = 170) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    cache: dict[str, str | None] = {}
    avail_ev = {e for c in env.get("claims") or [] if c.get("availability") == "available" for e in c.get("evidence_ids") or []}
    for ev in env.get("evidence") or []:
        sha, q = ev.get("content_sha256") or "", ev.get("claim_span") or ""
        if ev.get("id") not in avail_ev or not sha or not q or f"{sha}|{q}" in out:
            continue
        if sha not in cache:
            cache[sha] = None
            for d in snapshot_dirs:
                f = d / f"{sha}.gz"
                if f.exists():
                    try:
                        cache[sha] = gzip.decompress(f.read_bytes()).decode("utf-8", errors="replace")
                    except Exception:
                        pass
                    break
        text = cache[sha]
        if not text:
            continue
        i = text.find(q)
        if i < 0:
            continue
        out[f"{sha}|{q}"] = {"before": text[max(0, i - width):i], "match": q, "after": text[i + len(q):i + len(q) + width],
                             "bytes": len(text)}
    return out


def export_static(envelopes: list[dict[str, Any]], out: Path, dataset: str = "run", report: dict | None = None,
                  snapshot_dir: Path | None = None) -> None:
    """The app as a static folder (open index.html from disk): data/index.js + data/c/<orgnr>.js, no server."""
    out.mkdir(parents=True, exist_ok=True)
    (out / "data" / "c").mkdir(parents=True, exist_ok=True)
    rows = []
    snaps = [snapshot_dir] if snapshot_dir and snapshot_dir.is_dir() else []
    for env in envelopes:
        if not env.get("organisation_number"):
            continue
        if "signals" not in (env.get("summary") or {}):
            env = dict(env, summary=_recompute_summary(env))
        rows.append(summarize(env, dataset))
        payload = dict(env, _excerpts=_excerpts(env, snaps)) if snaps else env
        org = env["organisation_number"]
        (out / "data" / "c" / f"{org}.js").write_text(
            "window.__SP_C__=window.__SP_C__||{};window.__SP_C__[" + json.dumps(org) + "]=" +
            json.dumps(payload, ensure_ascii=False, default=str) + ";\n", encoding="utf-8")
    ops = (report or {}).get("operations") or {}
    ds = [{"name": dataset, "file": "envelopes.jsonl", "companies": len(rows),
           "report": {"requests": ops.get("requests"), "request_limit": ops.get("request_limit")} if report else None}]
    index = {"companies": sorted(rows, key=lambda r: str(r["name"])), "stats": compute_stats(rows, ds),
             "generated_at": _now()}
    (out / "data" / "index.js").write_text("window.__SP_STATIC__={index:" + json.dumps(index, ensure_ascii=False) + "};\n",
                                          encoding="utf-8")
    for f in ("app.js", "app.css", "favicon.svg"):
        (out / f).write_bytes((UI_DIR / f).read_bytes())
    html = (UI_DIR / "index.html").read_text(encoding="utf-8")
    html = html.replace('href="/app.css"', 'href="app.css"').replace('href="/favicon.svg"', 'href="favicon.svg"')
    html = html.replace('<script src="/app.js"></script>', '<script src="data/index.js"></script><script src="app.js"></script>')
    (out / "index.html").write_text(html, encoding="utf-8")


# --------------------------------------------------------------------------- research jobs
class Jobs:
    def __init__(self, lib: Library):
        self.lib = lib
        self.jobs: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()

    def start(self, orgnr: str) -> str:
        jid = uuid.uuid4().hex[:12]
        job = {"id": jid, "organisation_number": orgnr, "status": "running", "started": time.time(), "events": [],
               "error": None, "finished": None}
        with self.lock:
            self.jobs[jid] = job
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return jid

    def _run(self, job: dict[str, Any]) -> None:
        from .context import RunConfig
        from .pipeline import run_batch
        org = job["organisation_number"]

        def progress(o: str, module: str, availability: str | None, note: str | None) -> None:
            with self.lock:
                if not any(e["module"] == module for e in job["events"]):
                    job["events"].append({"module": module, "availability": availability, "note": (note or "")[:160],
                                          "t": round(time.time() - job["started"], 1)})

        try:
            cfg = RunConfig.from_env(run_id="ui-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
                                     out_dir=self.lib.store / "runs", max_requests=80, time_budget_s=300)
            prev = {}
            old = self.lib.get(org)
            if old:
                prev[org] = old
            out = asyncio.run(run_batch([org], cfg, previous=prev, progress=progress))
            env = out.envelopes[0]
            self.lib.add(env)
            job["status"] = "done"
        except Exception as exc:  # pragma: no cover - surfaced to the UI
            job["status"], job["error"] = "error", f"{type(exc).__name__}: {exc}"
        job["finished"] = time.time()

    def get(self, jid: str) -> dict[str, Any] | None:
        with self.lock:
            j = self.jobs.get(jid)
            return None if j is None else {**j, "events": list(j["events"]),
                                           "elapsed": round((j["finished"] or time.time()) - j["started"], 1)}


# --------------------------------------------------------------------------- HTTP
def make_handler(lib: Library, jobs: Jobs):
    class H(BaseHTTPRequestHandler):
        server_version = "signalpost-ui"

        def _send(self, code: int, body: Any, ctype: str = "application/json", cache: bool = False) -> None:
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith(("text/", "application/j")) else ""))
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "max-age=300" if cache else "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data: https:; "
                             "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            u = urlsplit(self.path)
            parts = [unquote(p) for p in u.path.split("/") if p]
            if u.path == "/health":
                return self._send(200, {"ok": True, "companies": len(lib.rows)})
            if parts[:2] == ["api", "index"]:
                return self._send(200, {"companies": sorted(lib.rows.values(), key=lambda r: str(r["name"])),
                                        "stats": lib.stats(), "generated_at": _now()})
            if len(parts) == 3 and parts[:2] == ["api", "company"]:
                try:
                    org = normalize(parts[2])
                except InvalidOrgNumber as exc:
                    return self._send(400, {"error": str(exc)})
                env = lib.get(org)
                return self._send(200, env) if env else self._send(404, {"error": "not researched yet",
                                                                         "organisation_number": org})
            if len(parts) == 3 and parts[:2] == ["api", "snapshot"]:
                q = (parse_qs(u.query).get("q") or [""])[0][:2000]
                return self._send(200, lib.snapshot_excerpt(parts[2], q))
            if len(parts) == 3 and parts[:2] == ["api", "jobs"]:
                j = jobs.get(parts[2])
                return self._send(200, j) if j else self._send(404, {"error": "unknown job"})
            rel = "/".join(parts) or "index.html"
            f = (UI_DIR / rel).resolve()
            if f.is_relative_to(UI_DIR.resolve()) and f.is_file():
                return self._send(200, f.read_bytes(), STATIC_TYPES.get(f.suffix, "application/octet-stream"),
                                  cache=f.suffix in (".woff2", ".png"))
            # client-side routes fall back to the app shell
            return self._send(200, (UI_DIR / "index.html").read_bytes(), "text/html")

        def do_POST(self) -> None:
            u = urlsplit(self.path)
            n = min(int(self.headers.get("Content-Length") or 0), 10_000)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                body = {}
            if u.path == "/api/research":
                try:
                    org = normalize(str(body.get("organisation_number") or ""))
                except InvalidOrgNumber as exc:
                    return self._send(400, {"error": str(exc)})
                running = [j for j in jobs.jobs.values() if j["status"] == "running"]
                if len(running) >= 3:
                    return self._send(429, {"error": "three researches are already running; try again shortly"})
                return self._send(200, {"job": jobs.start(org)})
            return self._send(404, {"error": "not found"})

        def log_message(self, fmt: str, *args: Any) -> None:
            pass

    return H


def default_sources(root: Path) -> list[Path]:
    out = []
    for p in ("profiles/envelopes.jsonl.gz", "profiles/envelopes.jsonl", "smoke/envelopes.jsonl"):
        if (root / p).exists():
            out.append(root / p)
    return out


def serve_ui(sources: list[Path], store: Path, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    t0 = time.time()
    print("Signalpost · loading company profiles …", flush=True)
    lib = Library(sources, store)
    jobs = Jobs(lib)
    httpd = None
    for p in range(port, port + 20):
        try:
            httpd = ThreadingHTTPServer((host, p), make_handler(lib, jobs))
            port = p
            break
        except OSError:
            continue
    if httpd is None:
        raise SystemExit(f"no free port in {port}-{port + 19}")
    url = f"http://{host}:{port}/"
    print(f"Signalpost · {len(lib.rows):,} companies ready in {time.time() - t0:.1f}s")
    print(f"Signalpost · open {url}   (Ctrl+C to stop)", flush=True)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nSignalpost · stopped")
