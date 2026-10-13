"""SQLite store: versioned profiles, detected changes, raw evidence snapshots, feed cursors."""

from __future__ import annotations

import gzip
import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS profiles (
    orgnr TEXT NOT NULL,
    version INTEGER NOT NULL,
    generated_at TEXT NOT NULL,
    status TEXT,
    name TEXT,
    profile_json BLOB NOT NULL,
    PRIMARY KEY (orgnr, version)
);
CREATE TABLE IF NOT EXISTS latest (
    orgnr TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    generated_at TEXT NOT NULL,
    status TEXT,
    name TEXT,
    registry_changed_at TEXT,
    next_refresh_due TEXT
);
CREATE TABLE IF NOT EXISTS changes (
    orgnr TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    field TEXT NOT NULL,
    kind TEXT NOT NULL,
    old_display TEXT,
    new_display TEXT,
    source_changed_at TEXT
);
CREATE INDEX IF NOT EXISTS changes_orgnr ON changes(orgnr);
CREATE TABLE IF NOT EXISTS snapshots (
    sha256 TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    content_type TEXT,
    body BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS cursors (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feed_events (
    orgnr TEXT NOT NULL,
    feed TEXT NOT NULL,
    event_id TEXT NOT NULL,
    event_time TEXT NOT NULL,
    kind TEXT,
    fields TEXT,
    PRIMARY KEY (feed, event_id)
);
CREATE INDEX IF NOT EXISTS feed_events_orgnr ON feed_events(orgnr);
"""


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        self._db.close()

    # ---------------------------------------------------------------- profiles
    def latest_profile(self, orgnr: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT p.profile_json FROM latest l JOIN profiles p ON p.orgnr=l.orgnr AND p.version=l.version "
                "WHERE l.orgnr=?", (orgnr,)).fetchone()
        return json.loads(gzip.decompress(row[0])) if row else None

    def save_profile(self, profile: dict[str, Any]) -> int:
        orgnr = profile["org_number"]
        fresh = profile.get("freshness", {})
        with self._lock:
            row = self._db.execute("SELECT MAX(version) FROM profiles WHERE orgnr=?", (orgnr,)).fetchone()
            version = (row[0] or 0) + 1
            profile.setdefault("freshness", {})["profile_version"] = version
            blob = gzip.compress(json.dumps(profile, ensure_ascii=False).encode())
            self._db.execute("BEGIN")
            self._db.execute(
                "INSERT INTO profiles VALUES (?,?,?,?,?,?)",
                (orgnr, version, profile["generated_at"], profile.get("status"), profile.get("name"), blob))
            self._db.execute(
                "INSERT OR REPLACE INTO latest VALUES (?,?,?,?,?,?,?)",
                (orgnr, version, profile["generated_at"], profile.get("status"), profile.get("name"),
                 _s(fresh.get("registry_last_changed_at")), _s(fresh.get("next_refresh_due"))))
            for ch in profile.get("changes", []):
                if ch.get("detected_at") == profile["generated_at"]:
                    self._db.execute(
                        "INSERT INTO changes VALUES (?,?,?,?,?,?,?)",
                        (orgnr, ch["detected_at"], ch["field"], ch["kind"], ch.get("old_display"),
                         ch.get("new_display"), _s(ch.get("source_changed_at"))))
            self._db.execute("COMMIT")
        return version

    def change_log(self, orgnr: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT detected_at, field, kind, old_display, new_display, source_changed_at FROM changes "
                "WHERE orgnr=? ORDER BY detected_at DESC LIMIT ?", (orgnr, limit)).fetchall()
        keys = ("detected_at", "field", "kind", "old_display", "new_display", "source_changed_at")
        return [dict(zip(keys, r)) for r in rows]

    def known_orgnrs(self) -> list[str]:
        with self._lock:
            return [r[0] for r in self._db.execute("SELECT orgnr FROM latest ORDER BY orgnr")]

    def latest_index(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT orgnr, version, generated_at, status, name, registry_changed_at, next_refresh_due "
                "FROM latest ORDER BY orgnr").fetchall()
        keys = ("orgnr", "version", "generated_at", "status", "name", "registry_changed_at", "next_refresh_due")
        return [dict(zip(keys, r)) for r in rows]

    def iter_latest_profiles(self) -> Iterable[dict[str, Any]]:
        for orgnr in self.known_orgnrs():
            p = self.latest_profile(orgnr)
            if p:
                yield p

    # --------------------------------------------------------------- snapshots
    def put_snapshot(self, sha256: str, url: str, retrieved_at: datetime, content_type: str | None,
                     body: bytes) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO snapshots VALUES (?,?,?,?,?)",
                (sha256, url, retrieved_at.isoformat(), content_type, gzip.compress(body)))

    def get_snapshot(self, sha256: str) -> bytes | None:
        with self._lock:
            row = self._db.execute("SELECT body FROM snapshots WHERE sha256=?", (sha256,)).fetchone()
        return gzip.decompress(row[0]) if row else None

    # ----------------------------------------------------------------- cursors
    def get_cursor(self, name: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT value FROM cursors WHERE name=?", (name,)).fetchone()
        return row[0] if row else None

    def set_cursor(self, name: str, value: str, now: datetime) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO cursors VALUES (?,?,?)", (name, value, now.isoformat()))

    # ------------------------------------------------------------- feed events
    def add_feed_events(self, events: Iterable[tuple[str, str, str, str, str | None, str | None]]) -> int:
        n = 0
        with self._lock:
            self._db.execute("BEGIN")
            for ev in events:
                cur = self._db.execute("INSERT OR IGNORE INTO feed_events VALUES (?,?,?,?,?,?)", ev)
                n += cur.rowcount
            self._db.execute("COMMIT")
        return n

    def feed_events_for(self, orgnr: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT feed, event_id, event_time, kind, fields FROM feed_events WHERE orgnr=? "
                "ORDER BY event_time DESC LIMIT ?", (orgnr, limit)).fetchall()
        keys = ("feed", "event_id", "event_time", "kind", "fields")
        return [dict(zip(keys, r)) for r in rows]


def _s(v: Any) -> str | None:
    if v is None:
        return None
    return v.isoformat() if hasattr(v, "isoformat") else str(v)
