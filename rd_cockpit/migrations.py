"""Ordered SQLite migrations with a verified pre-upgrade snapshot."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path


LATEST_SCHEMA_VERSION = 6

MIGRATIONS: tuple[tuple[int, str], ...] = (
    (1, """
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
          provenance TEXT NOT NULL CHECK(provenance IN ('observed','reported','inferred')),
          verification TEXT NOT NULL DEFAULT 'unverified',
          payload_json TEXT NOT NULL DEFAULT '{}',
          dedup_key TEXT UNIQUE,
          supersedes TEXT,
          retraction_reason TEXT,
          schema_version INTEGER NOT NULL DEFAULT 1
        );
        CREATE INDEX IF NOT EXISTS idx_events_time ON events(occurred_at);
        CREATE INDEX IF NOT EXISTS idx_events_project ON events(project_id, occurred_at);
        CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, occurred_at);
        CREATE INDEX IF NOT EXISTS idx_events_commit ON events(commit_sha);
        CREATE INDEX IF NOT EXISTS idx_events_supersedes ON events(supersedes);
        CREATE TABLE IF NOT EXISTS evidence (
          evidence_id TEXT PRIMARY KEY,
          event_id TEXT NOT NULL REFERENCES events(event_id),
          evidence_type TEXT NOT NULL,
          path TEXT,
          sha256 TEXT,
          metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_evidence_event ON evidence(event_id);
        CREATE TABLE IF NOT EXISTS report_runs (
          report_id TEXT PRIMARY KEY,
          report_date TEXT NOT NULL,
          generated_at TEXT NOT NULL,
          output_json TEXT,
          output_markdown TEXT,
          output_html TEXT
        );
    """),
    (2, """
        CREATE TABLE IF NOT EXISTS current_session_usage (
          agent TEXT NOT NULL,
          session_id TEXT NOT NULL,
          source TEXT NOT NULL,
          project_id TEXT,
          repo_path TEXT,
          source_path TEXT,
          activity_day TEXT,
          occurred_at TEXT,
          updated_at TEXT NOT NULL,
          payload_json TEXT NOT NULL DEFAULT '{}',
          evidence_sha256 TEXT,
          settled_totals_json TEXT NOT NULL DEFAULT '{}',
          PRIMARY KEY (agent, session_id)
        );
        CREATE INDEX IF NOT EXISTS idx_current_usage_project
          ON current_session_usage(project_id, activity_day);
    """),
    (3, """
        CREATE TABLE IF NOT EXISTS model_runs (
          run_id TEXT PRIMARY KEY,
          stage TEXT NOT NULL,
          project_id TEXT,
          source_hash TEXT,
          requested_model TEXT,
          selected_model TEXT,
          provider TEXT,
          fallback_used INTEGER NOT NULL DEFAULT 0,
          cache_hit INTEGER NOT NULL DEFAULT 0,
          status TEXT NOT NULL,
          started_at TEXT NOT NULL,
          finished_at TEXT NOT NULL,
          duration_ms INTEGER NOT NULL DEFAULT 0,
          input_tokens INTEGER NOT NULL DEFAULT 0,
          output_tokens INTEGER NOT NULL DEFAULT 0,
          cached_tokens INTEGER NOT NULL DEFAULT 0,
          total_tokens INTEGER NOT NULL DEFAULT 0,
          reason TEXT,
          error TEXT,
          metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_model_runs_time ON model_runs(started_at);
        CREATE INDEX IF NOT EXISTS idx_model_runs_stage ON model_runs(stage, started_at);
        CREATE INDEX IF NOT EXISTS idx_model_runs_project ON model_runs(project_id, started_at);
    """),
    (4, """
        CREATE TABLE IF NOT EXISTS resource_rollups (
          bucket_kind TEXT NOT NULL CHECK(bucket_kind IN ('hour','day')),
          bucket_start TEXT NOT NULL,
          machine TEXT NOT NULL,
          gpu_index INTEGER NOT NULL,
          sample_count INTEGER NOT NULL,
          avg_utilization_pct REAL,
          avg_memory_used_mb REAL,
          max_memory_used_mb REAL,
          avg_temperature_c REAL,
          avg_power_w REAL,
          generated_at TEXT NOT NULL,
          PRIMARY KEY (bucket_kind, bucket_start, machine, gpu_index)
        );
        CREATE INDEX IF NOT EXISTS idx_resource_rollups_time
          ON resource_rollups(bucket_kind, bucket_start);
    """),
    (5, """
        CREATE TABLE IF NOT EXISTS agent_activity_rollups (
          activity_day TEXT NOT NULL,
          source TEXT NOT NULL,
          session_id TEXT NOT NULL,
          project_key TEXT NOT NULL,
          semantic_kind TEXT NOT NULL,
          completed_count INTEGER NOT NULL DEFAULT 0,
          failed_count INTEGER NOT NULL DEFAULT 0,
          total_duration_ms INTEGER NOT NULL DEFAULT 0,
          last_occurred_at TEXT NOT NULL,
          PRIMARY KEY (activity_day, source, session_id, project_key, semantic_kind)
        );
        CREATE INDEX IF NOT EXISTS idx_agent_activity_rollups_day
          ON agent_activity_rollups(activity_day, project_key);
    """),
    (6, """
        CREATE TABLE IF NOT EXISTS agent_activity_seen (
          activity_key TEXT PRIMARY KEY,
          occurred_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_agent_activity_seen_time
          ON agent_activity_seen(occurred_at);
    """),
)


def _has_user_schema(connection: sqlite3.Connection) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' LIMIT 1",
    ).fetchone() is not None


def _migration_backup(connection: sqlite3.Connection, path: Path, current: int) -> Path:
    root = path.parent / "migration-backups"
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = root / f"events-v{current}-to-v{LATEST_SCHEMA_VERSION}-{stamp}.sqlite"
    descriptor, temporary = tempfile.mkstemp(prefix=".migration-", suffix=".sqlite", dir=root)
    os.close(descriptor)
    temporary_path = Path(temporary)
    destination: sqlite3.Connection | None = None
    try:
        destination = sqlite3.connect(temporary_path)
        connection.backup(destination, pages=2048)
        destination.commit()
        destination.execute("PRAGMA journal_mode=DELETE")
        check = destination.execute("PRAGMA integrity_check").fetchone()
        if not check or check[0] != "ok":
            raise RuntimeError(f"pre-migration backup integrity check failed: {check}")
        destination.close()
        destination = None
        os.chmod(temporary_path, 0o600)
        temporary_path.replace(target)
        return target
    finally:
        if destination is not None:
            destination.close()
        temporary_path.unlink(missing_ok=True)


def migrate(connection: sqlite3.Connection, path: Path) -> int:
    current = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if current > LATEST_SCHEMA_VERSION:
        raise RuntimeError(
            f"database schema v{current} is newer than this application (v{LATEST_SCHEMA_VERSION})",
        )
    if current == LATEST_SCHEMA_VERSION:
        return current
    if _has_user_schema(connection):
        _migration_backup(connection, path, current)

    pending = [(version, sql) for version, sql in MIGRATIONS if version > current]
    script = ["BEGIN IMMEDIATE;"]
    for version, sql in pending:
        script.append(sql)
        script.append(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);",
        )
        applied_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("'", "''")
        script.append(
            f"INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES({version},'{applied_at}');",
        )
    script.append(f"PRAGMA user_version={LATEST_SCHEMA_VERSION};")
    script.append("COMMIT;")
    try:
        connection.executescript("\n".join(script))
    except Exception:
        connection.rollback()
        raise
    return LATEST_SCHEMA_VERSION
