"""SQLite 저장소. 스키마는 스펙 8.2를 따르며, 기기 인증용 `devices` 테이블을 더한다."""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id     TEXT PRIMARY KEY,
    source       TEXT NOT NULL,
    app          TEXT,
    type         TEXT NOT NULL,
    captured_at  TEXT NOT NULL,
    received_at  TEXT NOT NULL,
    device_id    TEXT NOT NULL,
    payload      TEXT NOT NULL,
    text         TEXT,
    file_ref     TEXT,
    dedupe_key   TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'queued'
);
CREATE INDEX IF NOT EXISTS events_dedupe ON events (dedupe_key);
CREATE INDEX IF NOT EXISTS events_status ON events (status);

CREATE TABLE IF NOT EXISTS heartbeats (
    device_id    TEXT NOT NULL,
    at           TEXT NOT NULL,
    app_version  TEXT,
    listener_ok  INTEGER NOT NULL,
    queue_len    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS heartbeats_device ON heartbeats (device_id, at);

CREATE TABLE IF NOT EXISTS devices (
    device_id    TEXT PRIMARY KEY,
    token_hash   TEXT NOT NULL UNIQUE,
    created_at   TEXT NOT NULL,
    revoked_at   TEXT
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


# ── 기기 ────────────────────────────────────────────────────────────────


def add_device(conn: sqlite3.Connection, device_id: str) -> str:
    """기기를 등록하고 평문 토큰을 돌려준다. 토큰은 해시로만 저장하므로 다시 볼 수 없다."""
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO devices (device_id, token_hash, created_at) VALUES (?, ?, ?)",
        (device_id, hash_token(token), now()),
    )
    return token


def revoke_device(conn: sqlite3.Connection, device_id: str) -> bool:
    cur = conn.execute(
        "UPDATE devices SET revoked_at = ? WHERE device_id = ? AND revoked_at IS NULL",
        (now(), device_id),
    )
    return cur.rowcount > 0


def list_devices(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT device_id, created_at, revoked_at FROM devices ORDER BY created_at").fetchall()


def device_for_token(conn: sqlite3.Connection, token: str) -> str | None:
    row = conn.execute(
        "SELECT device_id FROM devices WHERE token_hash = ? AND revoked_at IS NULL",
        (hash_token(token),),
    ).fetchone()
    return row["device_id"] if row else None


# ── 이벤트 ──────────────────────────────────────────────────────────────


def find_duplicate(conn: sqlite3.Connection, event_id: str, dedupe_key: str) -> str | None:
    """같은 event_id 또는 dedupe_key로 이미 받은 이벤트의 event_id."""
    row = conn.execute(
        "SELECT event_id FROM events WHERE event_id = ? OR dedupe_key = ? LIMIT 1",
        (event_id, dedupe_key),
    ).fetchone()
    return row["event_id"] if row else None


def insert_event(conn: sqlite3.Connection, row: dict) -> None:
    columns = ", ".join(row)
    placeholders = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO events ({columns}) VALUES ({placeholders})", tuple(row.values()))


def insert_heartbeat(conn: sqlite3.Connection, device_id: str, app_version: str | None,
                     listener_ok: bool, queue_len: int) -> None:
    conn.execute(
        "INSERT INTO heartbeats (device_id, at, app_version, listener_ok, queue_len) VALUES (?, ?, ?, ?, ?)",
        (device_id, now(), app_version, int(listener_ok), queue_len),
    )


def stats(conn: sqlite3.Connection) -> dict:
    queued = conn.execute("SELECT COUNT(*) FROM events WHERE status = 'queued'").fetchone()[0]
    last = conn.execute("SELECT MAX(received_at) FROM events").fetchone()[0]
    return {"queue_len": queued, "last_received_at": last}
