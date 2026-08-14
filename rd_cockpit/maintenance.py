"""Crash-safe local database backup, archival and resource compaction."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import sqlite3
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .artifact_cache import atomic_write_json
from .ledger import Ledger


LEGACY_NOISE_TYPES = {
    "agent_tool_completed", "agent_tool_failed", "agent_usage_observed",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verified_sqlite_backup(source_path: Path, target: Path) -> bool:
    """Create one immutable daily recovery copy; return True when reused."""
    if target.is_file():
        with sqlite3.connect(target) as existing:
            existing.execute("PRAGMA journal_mode=DELETE")
            check = existing.execute("PRAGMA integrity_check").fetchone()
        if check and check[0] == "ok":
            return True
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{target.stem}-", suffix=".sqlite.tmp", dir=target.parent,
    )
    os.close(descriptor)
    temporary_path = Path(temporary)
    source = destination = None
    try:
        source = sqlite3.connect(source_path, timeout=30)
        destination = sqlite3.connect(temporary_path)
        source.backup(destination, pages=2048)
        destination.commit()
        destination.execute("PRAGMA journal_mode=DELETE")
        check = destination.execute("PRAGMA integrity_check").fetchone()
        if not check or check[0] != "ok":
            raise RuntimeError(f"backup integrity check failed: {check}")
        destination.close()
        destination = None
        os.chmod(temporary_path, 0o600)
        temporary_path.replace(target)
        return False
    finally:
        if destination is not None:
            destination.close()
        if source is not None:
            source.close()
        temporary_path.unlink(missing_ok=True)


def online_backup(home: Path, *, retention_days: int = 14) -> dict[str, Any]:
    database = home / ".rd-cockpit" / "events.sqlite"
    if not database.is_file():
        raise FileNotFoundError(f"ledger database does not exist: {database}")
    backup_dir = home / ".rd-cockpit" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(backup_dir, 0o700)
    target = backup_dir / f"events-{datetime.now(timezone.utc).date().isoformat()}.sqlite"
    reused = _verified_sqlite_backup(database, target)

    from .cold_store import cold_path
    cold_source = cold_path(database)
    cold_backup: dict[str, Any] | None = None
    if cold_source.is_file():
        cold_target = backup_dir / f"events-archive-{datetime.now(timezone.utc).date().isoformat()}.sqlite"
        cold_reused = _verified_sqlite_backup(cold_source, cold_target)
        cold_backup = {
            "path": cold_target.name, "bytes": cold_target.stat().st_size,
            "sha256": _sha256(cold_target), "integrity_check": "ok",
            "reused_daily_backup": cold_reused,
        }

    removed = []
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=max(1, retention_days))
    for path in backup_dir.glob("events-????-??-??.sqlite"):
        try:
            day = datetime.strptime(path.stem.removeprefix("events-"), "%Y-%m-%d").date()
        except ValueError:
            continue
        if day < cutoff:
            path.unlink()
            removed.append(path.name)
    for path in backup_dir.glob("events-archive-????-??-??.sqlite"):
        try:
            day = datetime.strptime(path.stem.removeprefix("events-archive-"), "%Y-%m-%d").date()
        except ValueError:
            continue
        if day < cutoff:
            path.unlink()
            removed.append(path.name)
    manifest = {
        "generated_at": _now(), "path": target.name, "bytes": target.stat().st_size,
        "sha256": _sha256(target), "integrity_check": "ok", "retention_days": retention_days,
        "reused_daily_backup": reused,
        "cold_store_backup": cold_backup,
        "removed_backups": removed,
    }
    atomic_write_json(backup_dir / "latest.json", manifest)
    os.chmod(backup_dir / "latest.json", 0o600)
    return manifest


def _event_objects(ledger: Ledger, rows: Iterable[Any]) -> Iterable[dict[str, Any]]:
    row_list = list(rows)
    evidence_by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    event_ids = [str(row["event_id"]) for row in row_list]
    for event_id, evidence_rows in ledger.event_evidence_many(event_ids).items():
        for item in evidence_rows:
            evidence = dict(item)
            try:
                evidence["metadata"] = json.loads(evidence.pop("metadata_json") or "{}")
            except json.JSONDecodeError:
                evidence["metadata"] = {}
                evidence.pop("metadata_json", None)
            evidence_by_event[event_id].append(evidence)
    for row in row_list:
        value = dict(row)
        try:
            value["payload"] = json.loads(value.pop("payload_json") or "{}")
        except json.JSONDecodeError:
            value["payload"] = {}
        value["evidence"] = evidence_by_event.get(str(row["event_id"]), [])
        yield value


def _write_archive(path: Path, values: Iterable[dict[str, Any]]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(descriptor)
    temporary_path = Path(temporary)
    count = 0
    try:
        with gzip.open(temporary_path, "wt", encoding="utf-8", compresslevel=6) as handle:
            for value in values:
                handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True))
                handle.write("\n")
                count += 1
        os.chmod(temporary_path, 0o600)
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return {"path": str(path), "events": count, "bytes": path.stat().st_size,
            "sha256": _sha256(path)}


def archive_legacy_noise(ledger: Ledger, home: Path) -> dict[str, Any]:
    rows = ledger.events(event_types=LEGACY_NOISE_TYPES, include_history=True)
    groups: dict[str, list[Any]] = defaultdict(list)
    for row in rows:
        month = str(row["occurred_at"] or "unknown")[:7]
        try:
            datetime.strptime(month, "%Y-%m")
        except ValueError:
            month = "unknown"
        groups[month].append(row)
    root = home / ".rd-cockpit" / "archive" / "legacy-events"
    previous: dict[str, Any] = {}
    try:
        value = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        previous = {str(item.get("month")): item for item in value.get("archives") or []}
    except (OSError, json.JSONDecodeError):
        pass
    archives = []
    for month, values in sorted(groups.items()):
        fingerprint = hashlib.sha256("\n".join(
            f"{row['event_id']}|{row['ingested_at']}" for row in values
        ).encode()).hexdigest()
        prior = previous.get(month) or {}
        archive_path = root / f"{month}.jsonl.gz"
        if (
            prior.get("source_fingerprint") == fingerprint and archive_path.is_file()
            and prior.get("sha256") == _sha256(archive_path)
        ):
            item = {**prior, "path": str(archive_path), "reused": True}
        else:
            item = _write_archive(archive_path, _event_objects(ledger, values))
            item.update({"month": month, "source_fingerprint": fingerprint, "reused": False})
        archives.append(item)
    manifest = {"generated_at": _now(), "event_types": sorted(LEGACY_NOISE_TYPES),
                "events": len(rows), "archives": archives, "source_rows_deleted": 0}
    atomic_write_json(root / "manifest.json", manifest)
    os.chmod(root / "manifest.json", 0o600)
    return manifest


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bucket(value: str, kind: str) -> str:
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    if kind == "hour":
        stamp = stamp.replace(minute=0, second=0, microsecond=0)
    else:
        stamp = stamp.replace(hour=0, minute=0, second=0, microsecond=0)
    return stamp.isoformat()


def _aggregate_resource_rows(ledger: Ledger, rows: list[Any]) -> int:
    values: dict[tuple[str, str, str, int], dict[str, Any]] = {}
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            continue
        for gpu in payload.get("gpus") or []:
            if not isinstance(gpu, dict):
                continue
            try:
                gpu_index = int(gpu.get("index"))
            except (TypeError, ValueError):
                continue
            for kind in ("hour", "day"):
                try:
                    bucket_start = _bucket(str(row["occurred_at"]), kind)
                except ValueError:
                    continue
                key = (kind, bucket_start, str(row["machine"] or "local"), gpu_index)
                item = values.setdefault(key, {"samples": 0, "util": [], "memory": [], "temperature": [], "power": []})
                item["samples"] += 1
                for field, target in (("utilization_pct", "util"), ("memory_used_mb", "memory"),
                                      ("temperature_c", "temperature"), ("power_w", "power")):
                    if (number := _number(gpu.get(field))) is not None:
                        item[target].append(number)

    generated = _now()
    for (kind, start, machine, gpu_index), item in values.items():
        avg = lambda data: (sum(data) / len(data)) if data else None
        ledger.db.execute(
            """INSERT INTO resource_rollups
            (bucket_kind,bucket_start,machine,gpu_index,sample_count,avg_utilization_pct,
             avg_memory_used_mb,max_memory_used_mb,avg_temperature_c,avg_power_w,generated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(bucket_kind,bucket_start,machine,gpu_index) DO UPDATE SET
              sample_count=excluded.sample_count,avg_utilization_pct=excluded.avg_utilization_pct,
              avg_memory_used_mb=excluded.avg_memory_used_mb,max_memory_used_mb=excluded.max_memory_used_mb,
              avg_temperature_c=excluded.avg_temperature_c,avg_power_w=excluded.avg_power_w,
              generated_at=excluded.generated_at""",
            (kind, start, machine, gpu_index, item["samples"], avg(item["util"]),
             avg(item["memory"]), max(item["memory"]) if item["memory"] else None,
             avg(item["temperature"]), avg(item["power"]), generated),
        )
    ledger.db.commit()
    return len(values)


def compact_resource_samples(
    ledger: Ledger, home: Path, *, retention_days: int = 30, prune: bool = True,
) -> dict[str, Any]:
    # Align the cutoff to a UTC day. Otherwise two maintenance runs can split
    # one day bucket and a later partial aggregate would overwrite the first.
    cutoff_at = (datetime.now(timezone.utc) - timedelta(days=max(1, retention_days))).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    cutoff = cutoff_at.isoformat()
    rows = ledger.db.execute(
        "SELECT * FROM events WHERE event_type='resource_snapshot' AND occurred_at<? "
        "ORDER BY occurred_at,event_id", (cutoff,),
    ).fetchall()
    rollups = _aggregate_resource_rows(ledger, rows)
    root = home / ".rd-cockpit" / "archive" / "resource-samples"
    archives = []
    if rows:
        through = cutoff.replace("-", "").replace(":", "").replace("+", "_").replace(".", "")
        archives.append(_write_archive(
            root / f"through-{through}.jsonl.gz", _event_objects(ledger, rows),
        ))
    deleted = 0
    if prune and rows:
        event_ids = [str(row["event_id"]) for row in rows]
        for offset in range(0, len(event_ids), 500):
            batch = event_ids[offset:offset + 500]
            marks = ",".join("?" for _ in batch)
            ledger.db.execute(f"DELETE FROM evidence WHERE event_id IN ({marks})", batch)
            ledger.db.execute(f"DELETE FROM events WHERE event_id IN ({marks})", batch)
        ledger.db.commit()
        deleted = len(event_ids)
    manifest = {"generated_at": _now(), "cutoff": cutoff, "retention_days": retention_days,
                "source_events": len(rows), "rollup_rows": rollups, "archives": archives,
                "source_rows_deleted": deleted}
    atomic_write_json(root / "manifest.json", manifest)
    if (root / "manifest.json").exists():
        os.chmod(root / "manifest.json", 0o600)
    return manifest


def prune_agent_activity_dedup(ledger: Ledger, *, retention_days: int = 30) -> dict[str, Any]:
    """Bound compact hook replay keys; rollups themselves remain available."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, retention_days))).isoformat()
    cursor = ledger.db.execute(
        "DELETE FROM agent_activity_seen WHERE occurred_at<?", (cutoff,),
    )
    ledger.db.commit()
    return {"cutoff": cutoff, "retention_days": retention_days, "removed": cursor.rowcount}


def maintain(
    home: Path, *, backup_retention_days: int = 14, resource_retention_days: int = 30,
    prune_resources: bool = True, cold_retention_days: int = 30,
) -> dict[str, Any]:
    home = home.expanduser().resolve()
    # A verified backup is deliberately first; compaction is not attempted if
    # backup creation or integrity validation fails.
    backup = online_backup(home, retention_days=backup_retention_days)
    ledger = Ledger(home / ".rd-cockpit" / "events.sqlite")
    try:
        legacy = archive_legacy_noise(ledger, home)
        from .cold_store import move_cold_events
        cold = move_cold_events(ledger, home, retention_days=cold_retention_days)
        activity = prune_agent_activity_dedup(
            ledger, retention_days=max(30, cold_retention_days),
        )
        resources = compact_resource_samples(
            ledger, home, retention_days=resource_retention_days, prune=prune_resources,
        )
        ledger.db.execute("PRAGMA wal_checkpoint(PASSIVE)")
    finally:
        ledger.close()
    from .view_cache import prune_view_cache
    views = prune_view_cache(
        home,
        retention_days=int(os.environ.get("RD_VIEW_CACHE_RETENTION_DAYS", "14")),
        max_bytes=int(float(os.environ.get("RD_VIEW_CACHE_MAX_MB", "100")) * 1024 * 1024),
    )
    result = {"generated_at": _now(), "backup": backup, "legacy_archive": legacy,
              "cold_store": cold, "agent_activity": activity,
              "resources": resources, "view_cache": views}
    atomic_write_json(home / ".rd-cockpit" / "maintenance-status.json", result)
    os.chmod(home / ".rd-cockpit" / "maintenance-status.json", 0o600)
    return result
