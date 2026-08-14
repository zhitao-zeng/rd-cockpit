"""Recoverable cold SQLite store for high-volume, low-value ledger events."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, TYPE_CHECKING

if TYPE_CHECKING:
    from .ledger import Ledger


COLD_DATABASE_NAME = "events-archive.sqlite"
COLD_EVENT_TYPES = {
    "agent_tool_completed", "agent_tool_failed", "agent_usage_observed",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  event_id TEXT PRIMARY KEY,
  occurred_at TEXT NOT NULL,
  ingested_at TEXT NOT NULL,
  event_type TEXT NOT NULL,
  project_id TEXT,
  task_id TEXT,
  session_id TEXT,
  source TEXT NOT NULL,
  machine TEXT,
  repo_path TEXT,
  commit_sha TEXT,
  dirty INTEGER,
  status TEXT,
  provenance TEXT NOT NULL,
  verification TEXT NOT NULL DEFAULT 'unverified',
  payload_json TEXT NOT NULL DEFAULT '{}',
  dedup_key TEXT UNIQUE,
  supersedes TEXT,
  retraction_reason TEXT,
  schema_version INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_cold_events_time ON events(occurred_at);
CREATE INDEX IF NOT EXISTS idx_cold_events_project ON events(project_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_cold_events_session ON events(session_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_cold_events_supersedes ON events(supersedes);
CREATE TABLE IF NOT EXISTS evidence (
  evidence_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL REFERENCES events(event_id),
  evidence_type TEXT NOT NULL,
  path TEXT,
  sha256 TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_cold_evidence_event ON evidence(event_id);
"""


def cold_path(database: Path) -> Path:
    return database.parent / COLD_DATABASE_NAME


def connect_cold(database: Path, *, readonly: bool = False) -> sqlite3.Connection | None:
    path = cold_path(database)
    if readonly and not path.is_file():
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        f"file:{path.resolve()}?mode=ro" if readonly else str(path),
        uri=readonly, timeout=30,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=30000")
    if readonly:
        connection.execute("PRAGMA query_only=ON")
    else:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        connection.executescript(_SCHEMA)
        connection.commit()
        os.chmod(path, 0o600)
    return connection


def cold_rows(database: Path, query: str, args: Iterable[Any] = ()) -> list[sqlite3.Row]:
    connection = connect_cold(database, readonly=True)
    if connection is None:
        return []
    try:
        return connection.execute(query, tuple(args)).fetchall()
    finally:
        connection.close()


def move_cold_events(
    ledger: "Ledger", home: Path, *, retention_days: int = 30,
) -> dict[str, Any]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, retention_days))).replace(
        hour=0, minute=0, second=0, microsecond=0,
    ).isoformat()
    marks = ",".join("?" for _ in COLD_EVENT_TYPES)
    rows = ledger.db.execute(
        f"SELECT * FROM events WHERE event_type IN ({marks}) AND occurred_at<? "
        "ORDER BY occurred_at,event_id",
        (*sorted(COLD_EVENT_TYPES), cutoff),
    ).fetchall()
    path = cold_path(ledger.path)
    if not rows:
        return {
            "path": str(path), "retention_days": retention_days, "cutoff": cutoff,
            "copied": 0, "removed_from_hot": 0, "cold_events": (
                len(cold_rows(ledger.path, "SELECT event_id FROM events")) if path.exists() else 0
            ),
            "integrity_check": "ok" if not path.exists() else _integrity(path),
        }

    event_ids = [str(row["event_id"]) for row in rows]
    evidence: list[sqlite3.Row] = []
    for offset in range(0, len(event_ids), 500):
        batch = event_ids[offset:offset + 500]
        batch_marks = ",".join("?" for _ in batch)
        evidence.extend(ledger.db.execute(
            f"SELECT * FROM evidence WHERE event_id IN ({batch_marks}) ORDER BY evidence_id",
            batch,
        ).fetchall())

    destination = connect_cold(ledger.path)
    assert destination is not None
    event_columns = list(rows[0].keys())
    evidence_columns = list(evidence[0].keys()) if evidence else []
    try:
        destination.execute("BEGIN IMMEDIATE")
        destination.executemany(
            f"INSERT OR IGNORE INTO events ({','.join(event_columns)}) "
            f"VALUES ({','.join('?' for _ in event_columns)})",
            [tuple(row[column] for column in event_columns) for row in rows],
        )
        if evidence:
            destination.executemany(
                f"INSERT OR IGNORE INTO evidence ({','.join(evidence_columns)}) "
                f"VALUES ({','.join('?' for _ in evidence_columns)})",
                [tuple(row[column] for column in evidence_columns) for row in evidence],
            )
        destination.commit()
        found = 0
        for offset in range(0, len(event_ids), 500):
            batch = event_ids[offset:offset + 500]
            batch_marks = ",".join("?" for _ in batch)
            found += int(destination.execute(
                f"SELECT COUNT(*) FROM events WHERE event_id IN ({batch_marks})", batch,
            ).fetchone()[0])
        if found != len(event_ids):
            raise RuntimeError(f"cold-store verification failed: copied {found}/{len(event_ids)}")
        check = destination.execute("PRAGMA integrity_check").fetchone()
        if not check or check[0] != "ok":
            raise RuntimeError(f"cold-store integrity check failed: {check}")
        total = int(destination.execute("SELECT COUNT(*) FROM events").fetchone()[0])
    finally:
        destination.close()

    # Copy-and-verify precedes removal. A crash between these transactions
    # leaves duplicates by event_id, which merged reads deliberately dedupe.
    ledger.db.execute("BEGIN IMMEDIATE")
    try:
        for offset in range(0, len(event_ids), 500):
            batch = event_ids[offset:offset + 500]
            batch_marks = ",".join("?" for _ in batch)
            ledger.db.execute(f"DELETE FROM evidence WHERE event_id IN ({batch_marks})", batch)
            ledger.db.execute(f"DELETE FROM events WHERE event_id IN ({batch_marks})", batch)
        ledger.db.commit()
    except BaseException:
        ledger.db.rollback()
        raise
    os.chmod(path, 0o600)
    return {
        "path": str(path), "retention_days": retention_days, "cutoff": cutoff,
        "copied": len(event_ids), "removed_from_hot": len(event_ids),
        "cold_events": total, "integrity_check": "ok", "bytes": path.stat().st_size,
    }


def _integrity(path: Path) -> str:
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as connection:
        row = connection.execute("PRAGMA integrity_check").fetchone()
    return str(row[0]) if row else "unknown"
