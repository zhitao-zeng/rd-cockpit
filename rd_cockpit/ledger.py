from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def utc_now() -> str:
    # Keep sub-second ordering for append-only corrections and fast command events.
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def event_id() -> str:
    return f"evt_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:10]}"


class Ledger:
    def __init__(
        self,
        path: Path,
        *,
        timeout_seconds: float = 30.0,
        max_retries: int = 5,
    ):
        self.path = path
        self.timeout_seconds = max(0.001, float(timeout_seconds))
        self.max_retries = max(1, int(max_retries))
        path.parent.mkdir(parents=True, exist_ok=True)
        # Collectors and hooks may write at the same time.  SQLite WAL handles
        # that well as long as short-lived writers wait for one another rather
        # than failing immediately when the usage sampler owns the lock.
        self.db = sqlite3.connect(path, timeout=self.timeout_seconds)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute(f"PRAGMA busy_timeout={max(1, int(self.timeout_seconds * 1000))}")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self._schema()

    def close(self) -> None:
        self.db.close()

    def _schema(self) -> None:
        self.db.executescript(
            """
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
            """
        )
        self.db.commit()

    def append(
        self,
        *,
        event_type: str,
        source: str,
        project_id: str | None = None,
        task_id: str | None = None,
        session_id: str | None = None,
        machine: str | None = None,
        repo_path: str | None = None,
        commit_sha: str | None = None,
        dirty: bool | None = None,
        status: str | None = None,
        provenance: str = "observed",
        verification: str = "unverified",
        payload: dict[str, Any] | None = None,
        dedup_key: str | None = None,
        occurred_at: str | None = None,
        evidence: Iterable[dict[str, Any]] = (),
        supersedes: str | None = None,
        retraction_reason: str | None = None,
    ) -> str:
        if provenance not in {"observed", "reported", "inferred"}:
            raise ValueError(f"invalid provenance: {provenance}")
        eid = event_id()
        evidence_items = list(evidence)
        for attempt in range(self.max_retries):
            try:
                self.db.execute(
                    """INSERT INTO events
                    (event_id, occurred_at, ingested_at, event_type, project_id, task_id,
                     session_id, source, machine, repo_path, commit_sha, dirty, status,
                     provenance, verification, payload_json, dedup_key, supersedes,
                     retraction_reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        eid, occurred_at or utc_now(), utc_now(), event_type, project_id, task_id,
                        session_id, source, machine, repo_path, commit_sha,
                        None if dirty is None else int(dirty), status, provenance, verification,
                        json.dumps(payload or {}, ensure_ascii=False, sort_keys=True), dedup_key,
                        supersedes, retraction_reason,
                    ),
                )
                for item in evidence_items:
                    self.db.execute(
                        "INSERT INTO evidence (evidence_id,event_id,evidence_type,path,sha256,metadata_json) VALUES (?,?,?,?,?,?)",
                        (
                            f"ev_{uuid.uuid4().hex}", eid, item.get("type", "file"), item.get("path"),
                            item.get("sha256"), json.dumps(item.get("metadata", {}), ensure_ascii=False),
                        ),
                    )
                self.db.commit()
                return eid
            except sqlite3.IntegrityError as exc:
                self.db.rollback()
                if dedup_key and "UNIQUE" in str(exc).upper():
                    row = self.db.execute("SELECT event_id FROM events WHERE dedup_key=?", (dedup_key,)).fetchone()
                    if row:
                        return str(row[0])
                raise
            except sqlite3.OperationalError as exc:
                self.db.rollback()
                if (attempt == self.max_retries - 1
                        or not any(word in str(exc).lower() for word in ("locked", "busy"))):
                    raise
                time.sleep(0.05 * (2 ** attempt))
        raise RuntimeError("unreachable ledger append retry state")

    def retract(self, event: str, reason: str) -> str:
        row = self.db.execute("SELECT * FROM events WHERE event_id=?", (event,)).fetchone()
        if not row:
            raise ValueError(f"unknown event: {event}")
        return self.append(
            event_type="event_retracted", source="rd_cli", project_id=row["project_id"],
            task_id=row["task_id"], session_id=row["session_id"], machine=row["machine"],
            provenance="observed", verification="user_confirmed", payload={"reason": reason},
            supersedes=event, retraction_reason=reason,
        )

    def events(self, *, project_id: str | None = None, since: str | None = None, until: str | None = None,
               event_types: set[str] | None = None, include_history: bool = False) -> list[sqlite3.Row]:
        clauses, args = ["1=1"], []
        if not include_history:
            clauses.extend([
                "current.event_type NOT IN ('event_retracted','event_superseded')",
                "NOT EXISTS (SELECT 1 FROM events correction "
                "WHERE correction.supersedes=current.event_id)",
            ])
        if project_id:
            clauses.append("project_id=?"); args.append(project_id)
        if since:
            clauses.append("occurred_at>=?"); args.append(since)
        if until:
            clauses.append("occurred_at<?"); args.append(until)
        if event_types:
            marks = ",".join("?" for _ in event_types)
            clauses.append(f"event_type IN ({marks})"); args.extend(sorted(event_types))
        return self.db.execute(
            f"SELECT current.* FROM events current WHERE {' AND '.join(clauses)} "
            "ORDER BY current.occurred_at, current.ingested_at, current.event_id", args
        ).fetchall()

    def correct_project(
        self,
        event: str,
        project_id: str | None,
        reason: str,
        *,
        repo_path: str | None = None,
    ) -> str:
        """Append a project-label correction while preserving the original row."""
        row = self.db.execute("SELECT * FROM events WHERE event_id=?", (event,)).fetchone()
        if not row:
            raise ValueError(f"unknown event: {event}")
        if row["project_id"] == project_id:
            return str(row["event_id"])
        payload = json.loads(row["payload_json"] or "{}")
        payload["project_assignment_correction"] = {
            "from": row["project_id"], "to": project_id, "reason": reason,
        }
        evidence = []
        for item in self.event_evidence(str(row["event_id"])):
            evidence.append({
                "type": item["evidence_type"], "path": item["path"], "sha256": item["sha256"],
                "metadata": json.loads(item["metadata_json"] or "{}"),
            })
        return self.append(
            event_type=row["event_type"], source=row["source"], project_id=project_id,
            task_id=row["task_id"], session_id=row["session_id"], machine=row["machine"],
            repo_path=repo_path, commit_sha=None, dirty=None, status=row["status"],
            provenance=row["provenance"], verification=row["verification"], payload=payload,
            dedup_key=f"project-correction:{row['event_id']}:{project_id or 'unassigned'}",
            occurred_at=row["occurred_at"], evidence=evidence, supersedes=row["event_id"],
            retraction_reason=reason,
        )

    def event_evidence(self, event_id_value: str) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM evidence WHERE event_id=?", (event_id_value,)).fetchall()

    def scalar(self, query: str, args: tuple[Any, ...] = ()) -> Any:
        row = self.db.execute(query, args).fetchone()
        return None if row is None else row[0]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()
