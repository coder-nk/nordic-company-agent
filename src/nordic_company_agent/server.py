"""Minimal stdlib HTTP API (the challenge's preferred routes) on top of the batch pipeline.

GET  /companies/{orgnr}[?refresh=1]   -> envelope (served from the local store if checked < 24 h ago)
POST /research   {"organisation_numbers": [...]} -> {"envelopes": [...]}
POST /refresh    {"organisation_numbers": [...]} (default: all stored) -> {"changes": [...], "envelopes": [...]}
GET  /           -> static viewer of the stored envelopes
GET  /health
"""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .context import RunConfig
from .orgnr import InvalidOrgNumber, normalize


class Store:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = root / "envelopes.jsonl"
        self.lock = threading.Lock()
        self.data: dict[str, dict] = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    e = json.loads(line)
                    self.data[e["organisation_number"]] = e

    def save(self, envs: list[dict]) -> None:
        with self.lock:
            for e in envs:
                self.data[e["organisation_number"]] = e
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text("".join(json.dumps(e, ensure_ascii=False, default=str) + "\n" for e in self.data.values()),
                           encoding="utf-8")
            tmp.replace(self.path)
        try:
            from .viewer import build_site
            build_site(list(self.data.values()), self.root / "site")
        except Exception:
            pass


def _research(store: Store, orgnrs: list[str], max_requests: int | None = None) -> list[dict]:
    from .pipeline import run_batch
    run_id = "api-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    cfg = RunConfig.from_env(run_id=run_id, out_dir=store.root,
                             **({"max_requests": max_requests} if max_requests else
                                {"max_requests": max(60, 25 * len(orgnrs))}),
                             time_budget_s=min(40 * 60, 120 + 15 * len(orgnrs)))
    prev = {o: store.data[o] for o in orgnrs if o in store.data}
    out = asyncio.run(run_batch(orgnrs, cfg, previous=prev))
    store.save(out.envelopes)
    return out.envelopes


def make_handler(store: Store):
    class H(BaseHTTPRequestHandler):
        server_version = "nordic-company-agent"

        def _send(self, code: int, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype + ("; charset=utf-8" if "json" in ctype or "html" in ctype else ""))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            try:
                return json.loads(self.rfile.read(n))
            except json.JSONDecodeError:
                return {}

        def _orgnrs(self, body: dict) -> tuple[list[str], list[str]]:
            ok, bad = [], []
            for v in body.get("organisation_numbers") or []:
                try:
                    o = normalize(str(v.get("organisation_number") if isinstance(v, dict) else v))
                    if o not in ok:
                        ok.append(o)
                except InvalidOrgNumber:
                    bad.append(str(v))
            return ok, bad

        def do_GET(self):
            u = urlsplit(self.path)
            parts = [p for p in u.path.split("/") if p]
            if u.path == "/health":
                return self._send(200, {"ok": True, "stored": len(store.data)})
            if len(parts) == 2 and parts[0] == "companies":
                try:
                    org = normalize(parts[1])
                except InvalidOrgNumber as exc:
                    return self._send(400, {"error": str(exc)})
                env = store.data.get(org)
                fresh = False
                if env:
                    try:
                        t = datetime.fromisoformat(env["run"]["completed_at"].replace("Z", "+00:00"))
                        fresh = datetime.now(timezone.utc) - t < timedelta(hours=24)
                    except Exception:
                        fresh = False
                if not env or not fresh or parse_qs(u.query).get("refresh"):
                    env = _research(store, [org])[0]
                return self._send(200, env)
            # static viewer
            rel = "/".join(parts) or "index.html"
            f = (store.root / "site" / rel).resolve()
            if str(f).startswith(str((store.root / "site").resolve())) and f.is_file():
                ctype = "text/html" if f.suffix == ".html" else ("application/javascript" if f.suffix == ".js" else "text/plain")
                return self._send(200, f.read_bytes(), ctype)
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            u = urlsplit(self.path)
            body = self._body()
            if u.path == "/research":
                ok, bad = self._orgnrs(body)
                if not ok:
                    return self._send(400, {"error": "no valid organisation_numbers", "invalid": bad})
                envs = _research(store, ok, body.get("max_requests"))
                return self._send(200, {"envelopes": envs, "invalid": bad})
            if u.path == "/refresh":
                ok, bad = self._orgnrs(body)
                ok = ok or sorted(store.data)
                if not ok:
                    return self._send(200, {"changes": [], "envelopes": []})
                envs = _research(store, ok, body.get("max_requests"))
                changes = [dict(ch, organisation_number=e["organisation_number"]) for e in envs for ch in e.get("changes", [])]
                return self._send(200, {"changes": changes, "envelopes": envs, "invalid": bad})
            return self._send(404, {"error": "not found"})

        def log_message(self, fmt, *args):  # quieter logs
            pass

    return H


def serve(root: Path, host: str = "127.0.0.1", port: int = 8080) -> None:
    store = Store(root)
    httpd = ThreadingHTTPServer((host, port), make_handler(store))
    print(f"nordic-company-agent API on http://{host}:{port}  (store: {root})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
