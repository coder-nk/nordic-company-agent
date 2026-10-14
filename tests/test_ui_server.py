"""The interactive app's server (nca ui): library index over JSONL / .jsonl.gz, JSON API, static files, safety."""

from __future__ import annotations

import gzip
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from nordic_company_agent.ui_server import Jobs, Library, make_handler, summarize

ROOT = Path(__file__).resolve().parents[1]


def _env(org="912342344", name="FJORDLYS ELEKTRO AS"):
    per = {"year": 2025, "scope": "company", "start": "2025-01-01", "end": "2025-12-31"}
    per24 = {"year": 2024, "scope": "company", "start": "2024-01-01", "end": "2024-12-31"}

    def cl(i, field, value, **kw):
        return {"id": f"cl-{i}", "field": field, "family": kw.pop("family", "legal_identity"), "value": value,
                "availability": kw.pop("availability", "available"), "evidence_ids": ["ev-1"], **kw}
    return {"organisation_number": org, "legal_name": name,
            "identity": {"organisation_number_formatted": "912 342 344", "match_status": "exact"},
            "run": {"run_id": "t", "completed_at": "2026-10-14T00:00:00Z"},
            "claims": [cl(1, "legal_name", name), cl(2, "registry_status", "active"),
                       cl(7, "accounts_period", per, period=per, family="annual_accounts"),
                       cl(3, "revenue", 1200000, period=per, family="annual_accounts"),
                       cl(4, "revenue", 1000000, period=per24, family="annual_accounts"),
                       cl(5, "net_profit", -5000, period=per, family="annual_accounts"),
                       cl(6, "official_website", None, availability="not_available", family="website")],
            "evidence": [{"id": "ev-1", "source_url": "https://data.brreg.no/x", "content_sha256": "a" * 64,
                          "claim_span": '"sumDriftsinntekter":1200000'}],
            "changes": [], "modules": {}}


def test_summarize_row():
    r = summarize(_env(), "t")
    assert r["name"] == "FJORDLYS ELEKTRO AS" and r["fin"]["year"] == 2025 and r["fin"]["growth"] == 20.0
    assert r["website"] is None and r["facts"] == 6
    assert any(s["signal"] == "profitability" and s["polarity"] == "warn" for s in r["signals"])


@pytest.fixture()
def server(tmp_path):
    src = tmp_path / "data" / "envelopes.jsonl.gz"
    src.parent.mkdir()
    with gzip.open(src, "wt", encoding="utf-8") as fh:
        fh.write(json.dumps(_env()) + "\n")
        fh.write("not json\n")
    snaps = src.parent / "snapshots"
    snaps.mkdir()
    (snaps / ("a" * 64 + ".gz")).write_bytes(gzip.compress(b'{"x":1,"sumDriftsinntekter":1200000,"y":2}'))
    lib = Library([src], tmp_path / "store")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(lib, Jobs(lib)))
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", lib
    httpd.shutdown()


def get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.status, r.headers, r.read()


def test_api_index_company_snapshot(server):
    base, lib = server
    _, _, body = get(base + "/api/index")
    j = json.loads(body)
    assert [c["orgnr"] for c in j["companies"]] == ["912342344"] and j["stats"]["companies"] == 1
    assert {c["key"] for c in j["stats"]["coverage"]} >= {"website", "hiring", "accounts"}
    _, _, body = get(base + "/api/company/912%20342%20344")
    env = json.loads(body)
    assert env["organisation_number"] == "912342344" and "signals" in env["summary"]
    _, _, body = get(base + "/api/snapshot/" + "a" * 64 + "?q=" + urllib.request.quote('"sumDriftsinntekter":1200000'))
    snap = json.loads(body)
    assert snap["matched"] and snap["match"] == '"sumDriftsinntekter":1200000' and snap["before"].endswith('"x":1,')


def test_static_shell_and_safety(server):
    base, _ = server
    st, hdr, body = get(base + "/")
    assert st == 200 and b"Signalpost" in body and "script-src 'self'" in hdr["Content-Security-Policy"]
    st, hdr, _ = get(base + "/app.js")
    assert hdr["Content-Type"].startswith("application/javascript")
    st, _, body = get(base + "/../../pyproject.toml")          # traversal falls back to the app shell
    assert b"[project]" not in body
    with pytest.raises(urllib.error.HTTPError) as e:
        get(base + "/api/company/123")
    assert e.value.code == 400
    with pytest.raises(urllib.error.HTTPError) as e:
        get(base + "/api/company/923609016")
    assert e.value.code == 404
    req = urllib.request.Request(base + "/api/research", data=b'{"organisation_number":"123456789"}', method="POST")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=10)
    assert e.value.code == 400
    assert json.loads(get(base + "/api/snapshot/nothex")[2])["found"] is False


def test_researched_envelope_overrides(tmp_path):
    lib = Library([], tmp_path / "store")
    lib.add(_env(name="NEW NAME AS"))
    assert lib.rows["912342344"]["name"] == "NEW NAME AS" and lib.rows["912342344"]["dataset"] == "researched"
    again = Library([], tmp_path / "store")                       # persisted across restarts
    assert again.get("912342344")["legal_name"] == "NEW NAME AS"


def test_ui_assets_exist():
    ui = ROOT / "src" / "nordic_company_agent" / "ui"
    for f in ("index.html", "app.js", "app.css", "favicon.svg"):
        assert (ui / f).is_file()
    html = (ui / "index.html").read_text()
    assert "<script>" not in html and "http" not in html.split("<body>")[0].replace("http-equiv", "")


def test_static_export(tmp_path):
    from nordic_company_agent.ui_server import export_static
    snaps = tmp_path / "snapshots"
    snaps.mkdir()
    (snaps / ("a" * 64 + ".gz")).write_bytes(gzip.compress(b'{"x":1,"sumDriftsinntekter":1200000,"y":2}'))
    env = _env()
    env["claims"][2]["evidence_ids"] = ["ev-1"]
    export_static([env], tmp_path / "site", dataset="smoke", snapshot_dir=snaps)
    site = tmp_path / "site"
    html = (site / "index.html").read_text()
    assert '<script src="data/index.js"></script><script src="app.js"></script>' in html and 'href="app.css"' in html
    idx = (site / "data" / "index.js").read_text()
    assert idx.startswith("window.__SP_STATIC__={index:") and '"912342344"' in idx
    c = (site / "data" / "c" / "912342344.js").read_text()
    assert '"_excerpts"' in c and '"before": "{\\"x\\":1,"' in c
    assert (site / "app.js").read_bytes() == (ROOT / "src/nordic_company_agent/ui/app.js").read_bytes()
