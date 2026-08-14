from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo


LOCAL_TZ = ZoneInfo("Asia/Shanghai")


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
        readonly: bool = False,
    ):
        self.path = path
        self.timeout_seconds = max(0.001, float(timeout_seconds))
        self.max_retries = max(1, int(max_retries))
        path.parent.mkdir(parents=True, exist_ok=True)
        # Collectors and hooks may write at the same time.  SQLite WAL handles
        # that well as long as short-lived writers wait for one another rather
        # than failing immediately when the usage sampler owns the lock.
        self.readonly = readonly
        self.db = sqlite3.connect(
            f"file:{path.resolve()}?mode=ro" if readonly else str(path),
            uri=readonly,
            timeout=self.timeout_seconds,
        )
        self.db.row_factory = sqlite3.Row
        if not readonly:
            self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute(f"PRAGMA busy_timeout={max(1, int(self.timeout_seconds * 1000))}")
        if readonly:
            self.db.execute("PRAGMA query_only=ON")
        else:
            self.db.execute("PRAGMA synchronous=NORMAL")
            self._schema()
        # The ledger contains local paths, resource processes and usage totals.
        # Keep it private even when the repository itself lives in a shared
        # group-readable workspace.
        for private_path in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
            if private_path.exists():
                os.chmod(private_path, 0o600)

    def close(self) -> None:
        self.db.close()

    def _schema(self) -> None:
        from .migrations import migrate

        migrate(self.db, self.path)

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

    def record_agent_activity(
        self, *, source: str, session_id: str, project_id: str | None,
        semantic_kind: str, failed: bool, duration_ms: int | float | None,
        occurred_at: str, activity_key: str,
    ) -> bool:
        """Update a compact projector instead of appending ordinary tool events."""
        try:
            stamp = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
            activity_day = stamp.astimezone(LOCAL_TZ).date().isoformat()
        except (TypeError, ValueError):
            activity_day = occurred_at[:10]
        try:
            duration = max(0, int(duration_ms or 0))
        except (TypeError, ValueError):
            duration = 0
        inserted = self.db.execute(
            "INSERT OR IGNORE INTO agent_activity_seen(activity_key,occurred_at) VALUES (?,?)",
            (activity_key, occurred_at),
        )
        if inserted.rowcount == 0:
            self.db.commit()
            return False
        self.db.execute(
            """INSERT INTO agent_activity_rollups
            (activity_day,source,session_id,project_key,semantic_kind,completed_count,
             failed_count,total_duration_ms,last_occurred_at)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(activity_day,source,session_id,project_key,semantic_kind) DO UPDATE SET
              completed_count=completed_count+excluded.completed_count,
              failed_count=failed_count+excluded.failed_count,
              total_duration_ms=total_duration_ms+excluded.total_duration_ms,
              last_occurred_at=MAX(last_occurred_at,excluded.last_occurred_at)""",
            (
                activity_day, source, session_id, project_id or "", semantic_kind,
                0 if failed else 1, 1 if failed else 0, duration, occurred_at,
            ),
        )
        self.db.commit()
        return True

    def retract(self, event: str, reason: str) -> str:
        row = self._find_event(event)
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
        if project_id:
            clauses.append("project_id=?"); args.append(project_id)
        if since:
            clauses.append("occurred_at>=?"); args.append(since)
        if until:
            clauses.append("occurred_at<?"); args.append(until)
        if event_types:
            marks = ",".join("?" for _ in event_types)
            clauses.append(f"event_type IN ({marks})"); args.extend(sorted(event_types))
        query = f"SELECT current.* FROM events current WHERE {' AND '.join(clauses)}"
        hot = self.db.execute(query, args).fetchall()
        from .cold_store import cold_rows

        cold = cold_rows(self.path, query, args)
        merged = {str(row["event_id"]): row for row in [*cold, *hot]}
        if not include_history:
            superseded = {
                str(row["supersedes"])
                for row in [
                    *cold_rows(self.path, "SELECT supersedes FROM events WHERE supersedes IS NOT NULL"),
                    *self.db.execute(
                        "SELECT supersedes FROM events WHERE supersedes IS NOT NULL",
                    ).fetchall(),
                ]
                if row["supersedes"]
            }
            merged = {
                key: row for key, row in merged.items()
                if row["event_type"] not in {"event_retracted", "event_superseded"}
                and key not in superseded
            }
        return sorted(
            merged.values(),
            key=lambda row: (row["occurred_at"], row["ingested_at"], row["event_id"]),
        )

    def correct_project(
        self,
        event: str,
        project_id: str | None,
        reason: str,
        *,
        repo_path: str | None = None,
    ) -> str:
        """Append a project-label correction while preserving the original row."""
        row = self._find_event(event)
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
        return self.event_evidence_many([event_id_value]).get(event_id_value, [])

    def event_evidence_many(self, event_ids: Iterable[str]) -> dict[str, list[sqlite3.Row]]:
        from .cold_store import cold_rows

        identifiers = list(dict.fromkeys(str(value) for value in event_ids))
        output: dict[str, dict[str, sqlite3.Row]] = {value: {} for value in identifiers}
        for offset in range(0, len(identifiers), 500):
            batch = identifiers[offset:offset + 500]
            if not batch:
                continue
            marks = ",".join("?" for _ in batch)
            query = f"SELECT * FROM evidence WHERE event_id IN ({marks}) ORDER BY evidence_id"
            hot = self.db.execute(query, batch).fetchall()
            cold = cold_rows(self.path, query, batch)
            for row in [*cold, *hot]:
                output[str(row["event_id"])][str(row["evidence_id"])] = row
        return {key: list(values.values()) for key, values in output.items()}

    def _find_event(self, event: str) -> sqlite3.Row | None:
        row = self.db.execute("SELECT * FROM events WHERE event_id=?", (event,)).fetchone()
        if row is not None:
            return row
        from .cold_store import cold_rows

        rows = cold_rows(self.path, "SELECT * FROM events WHERE event_id=?", (event,))
        return rows[0] if rows else None

    def scalar(self, query: str, args: tuple[Any, ...] = ()) -> Any:
        row = self.db.execute(query, args).fetchone()
        return None if row is None else row[0]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()
