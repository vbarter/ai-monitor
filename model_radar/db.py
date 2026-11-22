"""SQLite schema, snapshots, and alert rows.

Snapshots are replaced only by the integrator after a non-empty success.
This module does not interpret adapter payloads.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY,
  adapter TEXT NOT NULL,
  provider TEXT NOT NULL,
  url TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  last_ok_at TEXT,
  last_error TEXT,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  last_etag TEXT,
  cursor TEXT,
  last_snapshot_hash TEXT,
  health_notified INTEGER NOT NULL DEFAULT 0,
  recovery_pending TEXT
);

CREATE TABLE IF NOT EXISTS snapshots (
  source_id TEXT PRIMARY KEY REFERENCES sources(id),
  body_json TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  content_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS seen_items (
  fingerprint TEXT PRIMARY KEY,
  canonical_id TEXT,
  provider TEXT NOT NULL,
  model_id TEXT NOT NULL,
  version TEXT,
  kind TEXT NOT NULL,
  title TEXT,
  official_url TEXT,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  first_source TEXT,
  notified TEXT,
  raw_hash TEXT,
  first_party INTEGER NOT NULL DEFAULT 0,
  upgraded INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS aliases (
  alias TEXT PRIMARY KEY,
  fingerprint TEXT NOT NULL REFERENCES seen_items(fingerprint)
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  fingerprint TEXT NOT NULL REFERENCES seen_items(fingerprint),
  detected_at TEXT NOT NULL,
  source_id TEXT NOT NULL,
  payload_json TEXT,
  alert_id INTEGER
);

CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  channel TEXT NOT NULL,
  status TEXT NOT NULL,
  body_json TEXT,
  error TEXT
);

CREATE INDEX IF NOT EXISTS idx_seen_provider_model ON seen_items(provider, model_id);
CREATE INDEX IF NOT EXISTS idx_events_fingerprint ON events(fingerprint);
CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts(status);
"""


class Database:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        if path != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")

    def init(self) -> None:
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()

    def ensure_source(self, source_id: str, adapter: str, provider: str, url: str) -> dict[str, Any]:
        self.conn.execute(
            """
            INSERT INTO sources (id, adapter, provider, url, enabled)
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(id) DO UPDATE SET
              adapter=excluded.adapter,
              provider=excluded.provider,
              url=excluded.url,
              enabled=1
            """,
            (source_id, adapter, provider, url),
        )
        return self.get_source(source_id)

    def get_source(self, source_id: str) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        if row is None:
            raise KeyError(source_id)
        return dict(row)

    def mark_ok(
        self,
        source_id: str,
        *,
        now: str,
        etag: str | None = None,
        cursor: str | None = None,
        snapshot_hash: str | None = None,
    ) -> None:
        current = self.get_source(source_id)
        recovery = current.get("recovery_pending")
        if current.get("health_notified"):
            recovery = f"{source_id} recovered at {now}"
        self.conn.execute(
            """
            UPDATE sources
            SET last_ok_at = ?,
                last_error = NULL,
                consecutive_failures = 0,
                last_etag = COALESCE(?, last_etag),
                cursor = COALESCE(?, cursor),
                last_snapshot_hash = COALESCE(?, last_snapshot_hash),
                health_notified = 0,
                recovery_pending = ?
            WHERE id = ?
            """,
            (now, etag, cursor, snapshot_hash, recovery, source_id),
        )

    def mark_failure(self, source_id: str, error: str) -> None:
        self.conn.execute(
            """
            UPDATE sources
            SET last_error = ?,
                consecutive_failures = consecutive_failures + 1
            WHERE id = ?
            """,
            (error[:300], source_id),
        )

    def set_health_notified(self, source_id: str, value: int) -> None:
        self.conn.execute(
            "UPDATE sources SET health_notified = ? WHERE id = ?",
            (value, source_id),
        )

    def clear_recovery(self, source_ids: list[str]) -> None:
        for source_id in source_ids:
            self.conn.execute(
                "UPDATE sources SET recovery_pending = NULL WHERE id = ?",
                (source_id,),
            )

    def get_snapshot(self, source_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT body_json, content_hash, fetched_at FROM snapshots WHERE source_id = ?",
            (source_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "body": json.loads(row["body_json"]),
            "hash": row["content_hash"],
            "fetched_at": row["fetched_at"],
        }

    def save_snapshot(self, source_id: str, body: dict[str, Any], content_hash: str, fetched_at: str) -> None:
        payload = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        self.conn.execute(
            """
            INSERT INTO snapshots (source_id, body_json, fetched_at, content_hash)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
              body_json = excluded.body_json,
              fetched_at = excluded.fetched_at,
              content_hash = excluded.content_hash
            """,
            (source_id, payload, fetched_at, content_hash),
        )

    def get_seen(self, fp: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM seen_items WHERE fingerprint = ?", (fp,)).fetchone()
        return dict(row) if row else None

    def insert_seen(self, row: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO seen_items (
              fingerprint, canonical_id, provider, model_id, version, kind, title,
              official_url, first_seen_at, last_seen_at, first_source, notified,
              raw_hash, first_party, upgraded
            ) VALUES (
              :fingerprint, :canonical_id, :provider, :model_id, :version, :kind, :title,
              :official_url, :first_seen_at, :last_seen_at, :first_source, :notified,
              :raw_hash, :first_party, :upgraded
            )
            """,
            row,
        )

    def touch_seen(self, fp: str, now: str) -> None:
        self.conn.execute(
            "UPDATE seen_items SET last_seen_at = ? WHERE fingerprint = ?",
            (now, fp),
        )

    def update_kind(self, fp: str, kind: str, *, upgraded: int | None = None) -> None:
        if upgraded is None:
            self.conn.execute("UPDATE seen_items SET kind = ? WHERE fingerprint = ?", (kind, fp))
        else:
            self.conn.execute(
                "UPDATE seen_items SET kind = ?, upgraded = ? WHERE fingerprint = ?",
                (kind, upgraded, fp),
            )

    def set_notified(self, fp: str, notified: str, *, only_if_null: bool = False) -> None:
        if only_if_null:
            self.conn.execute(
                "UPDATE seen_items SET notified = ? WHERE fingerprint = ? AND notified IS NULL",
                (notified, fp),
            )
        else:
            self.conn.execute(
                "UPDATE seen_items SET notified = ? WHERE fingerprint = ?",
                (notified, fp),
            )

    def remember_alias(self, alias: str, fp: str) -> None:
        self.conn.execute(
            """
            INSERT INTO aliases (alias, fingerprint) VALUES (?, ?)
            ON CONFLICT(alias) DO NOTHING
            """,
            (alias, fp),
        )

    def insert_event(self, fp: str, source_id: str, now: str, payload: dict[str, Any]) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO events (fingerprint, detected_at, source_id, payload_json)
            VALUES (?, ?, ?, ?)
            """,
            (fp, now, source_id, json.dumps(payload, sort_keys=True, ensure_ascii=False)),
        )
        return int(cursor.lastrowid)

    def has_event_from(self, fp: str, source_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM events WHERE fingerprint = ? AND source_id = ? LIMIT 1",
            (fp, source_id),
        ).fetchone()
        return row is not None

    def list_immediate_pending(self, *, router_catalog_immediate: bool) -> list[dict[str, Any]]:
        flag = 1 if router_catalog_immediate else 0
        rows = self.conn.execute(
            """
            SELECT * FROM seen_items
            WHERE notified IS NULL
              AND (
                kind IN ('official_release', 'weights_drop')
                OR (kind = 'catalog_add' AND (first_party = 1 OR ? = 1))
              )
            ORDER BY first_seen_at, model_id
            """,
            (flag,),
        ).fetchall()
        return [dict(row) for row in rows]

    def insert_alert(self, *, created_at: str, channel: str, status: str, body: dict[str, Any]) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO alerts (created_at, channel, status, body_json)
            VALUES (?, ?, ?, ?)
            """,
            (created_at, channel, status, json.dumps(body, sort_keys=True, ensure_ascii=False)),
        )
        return int(cursor.lastrowid)

    def list_open_alerts(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT * FROM alerts
            WHERE status IN ('pending', 'failed')
            ORDER BY id
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def set_alert(self, alert_id: int, status: str, error: str | None) -> None:
        self.conn.execute(
            "UPDATE alerts SET status = ?, error = ? WHERE id = ?",
            (status, error, alert_id),
        )

    def set_events_alert(self, event_ids: list[int], alert_id: int) -> None:
        for event_id in event_ids:
            self.conn.execute("UPDATE events SET alert_id = ? WHERE id = ?", (alert_id, event_id))

    def covered_fingerprints(self) -> set[str]:
        found: set[str] = set()
        rows = self.conn.execute(
            "SELECT body_json FROM alerts WHERE status IN ('pending', 'failed', 'sent')"
        ).fetchall()
        for row in rows:
            if not row["body_json"]:
                continue
            body = json.loads(row["body_json"])
            for fp in body.get("fingerprints") or []:
                found.add(fp)
        return found

    def open_health_source_ids(self) -> set[str]:
        found: set[str] = set()
        rows = self.conn.execute(
            "SELECT body_json FROM alerts WHERE status IN ('pending', 'failed')"
        ).fetchall()
        for row in rows:
            if not row["body_json"]:
                continue
            body = json.loads(row["body_json"])
            if body.get("health") and body.get("source_id"):
                found.add(body["source_id"])
        return found

    def digest_seen(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT * FROM seen_items
            WHERE notified IS NULL
              AND (
                kind IN ('changelog_note', 'catalog_update')
                OR (kind = 'catalog_add' AND first_party = 0)
              )
            ORDER BY first_seen_at, model_id
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def digest_updates(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM events WHERE alert_id IS NULL ORDER BY id"
        ).fetchall()
        found = []
        for row in rows:
            payload = json.loads(row["payload_json"] or "{}")
            if payload.get("kind") == "catalog_update":
                item = dict(row)
                item["payload"] = payload
                found.append(item)
        return found

    def pending_recoveries(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT id, recovery_pending FROM sources
            WHERE recovery_pending IS NOT NULL AND recovery_pending != ''
            ORDER BY id
            """
        ).fetchall()
        return [dict(row) for row in rows]


def connect(path: str) -> Database:
    database = Database(path)
    database.init()
    return database
