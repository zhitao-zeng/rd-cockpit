"""Content-addressed materialized views for expensive dashboard projections."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .artifact_cache import atomic_write_json, read_json
from .daily_source import report_directories
from .ledger import Ledger


VIEW_CACHE_VERSION = 4
ANALYTICS_EVENT_SQL = """(
    event_type IN (
      'agent_session_completed','git_snapshot','test_completed','benchmark_completed',
      'experiment_completed','plan_closed','agent_usage_observed'
    ) OR event_type LIKE 'decision_%'
)"""


@dataclass(frozen=True)
class CachedView:
    data: dict[str, Any]
    etag: str
    cache_hit: bool
    generated_at: str
    path: Path


def _file_state(paths: list[Path]) -> list[tuple[str, int, int]]:
    output: list[tuple[str, int, int]] = []
    for path in sorted(set(paths)):
        try:
            stat = path.stat()
        except OSError:
            continue
        output.append((str(path), stat.st_size, stat.st_mtime_ns))
    return output


def source_fingerprint(home: Path, *, scope: str = "reports") -> str:
    paths = [home / "config" / "projects.yaml", home / "config" / "projects.local.yaml"]
    for root in report_directories():
        paths.extend(root.glob("????-??-??.md"))
        data = root / "data"
        if data.is_dir():
            paths.extend(data.rglob("*.json"))
    database = home / ".rd-cockpit" / "events.sqlite"
    database_state: dict[str, Any] = {"scope": scope}
    if scope == "analytics" and database.exists():
        try:
            with sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True) as connection:
                database_state.update({
                    "events": connection.execute(
                        f"SELECT COUNT(*),MAX(ingested_at) FROM events WHERE {ANALYTICS_EVENT_SQL}",
                    ).fetchone(),
                    "usage": connection.execute(
                        "SELECT COUNT(*),MAX(updated_at) FROM current_session_usage",
                    ).fetchone(),
                    "agent_activity": connection.execute(
                        "SELECT COUNT(*),MAX(last_occurred_at),SUM(completed_count),"
                        "SUM(failed_count),SUM(total_duration_ms) FROM agent_activity_rollups",
                    ).fetchone(),
                    "schema": connection.execute("PRAGMA user_version").fetchone()[0],
                })
        except sqlite3.Error:
            database_state["unavailable"] = True
    cold = home / ".rd-cockpit" / "events-archive.sqlite"
    if scope == "analytics" and cold.exists():
        try:
            with sqlite3.connect(f"file:{cold.resolve()}?mode=ro", uri=True) as connection:
                database_state["cold"] = connection.execute(
                    f"SELECT COUNT(*),MAX(ingested_at) FROM events WHERE {ANALYTICS_EVENT_SQL}",
                ).fetchone()
        except sqlite3.Error:
            database_state["cold_unavailable"] = True
    payload = {
        "cache_version": VIEW_CACHE_VERSION,
        "files": _file_state(paths),
        "database": database_state,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _cache_path(home: Path, name: str, parameters: dict[str, Any]) -> Path:
    key = hashlib.sha256(
        json.dumps(parameters, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(),
    ).hexdigest()[:20]
    return home / ".rd-cockpit" / "views" / f"{name}-{key}.json"


def get_or_build(
    home: Path,
    name: str,
    parameters: dict[str, Any],
    builder: Callable[[], dict[str, Any]],
    *,
    force: bool = False,
    source_scope: str = "reports",
) -> CachedView:
    home = home.expanduser().resolve()
    path = _cache_path(home, name, parameters)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    lock_path = path.with_suffix(".lock")
    fingerprint = source_fingerprint(home, scope=source_scope)
    with lock_path.open("a+", encoding="utf-8") as lock:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        existing = read_json(path, {})
        if (
            not force
            and isinstance(existing, dict)
            and existing.get("schema_version") == VIEW_CACHE_VERSION
            and existing.get("source_fingerprint") == fingerprint
            and existing.get("parameters") == parameters
            and existing.get("source_scope") == source_scope
            and isinstance(existing.get("data"), dict)
        ):
            etag = str(existing.get("etag") or fingerprint)
            return CachedView(existing["data"], etag, True, str(existing.get("generated_at") or ""), path)

        data = builder()
        generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        etag = hashlib.sha256(
            f"{name}|{fingerprint}|{json.dumps(parameters, sort_keys=True)}".encode(),
        ).hexdigest()
        wrapper = {
            "schema_version": VIEW_CACHE_VERSION,
            "name": name,
            "parameters": parameters,
            "source_scope": source_scope,
            "source_fingerprint": fingerprint,
            "generated_at": generated_at,
            "etag": etag,
            "data": data,
        }
        atomic_write_json(path, wrapper)
        os.chmod(path, 0o600)
        return CachedView(data, etag, False, generated_at, path)


def prune_view_cache(
    home: Path, *, retention_days: int = 14, max_bytes: int = 100 * 1024 * 1024,
    keep_per_variant: int = 2,
) -> dict[str, Any]:
    """Bound derived view files without touching reports or ledger evidence."""
    root = home.expanduser().resolve() / ".rd-cockpit" / "views"
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max(1, int(retention_days)))
    removed: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    volatile = {"target_date", "offset", "baseline"}
    for path in root.glob("*.json") if root.is_dir() else []:
        value = read_json(path, {})
        if not isinstance(value, dict) or value.get("schema_version") != VIEW_CACHE_VERSION:
            size = path.stat().st_size
            path.unlink(missing_ok=True)
            removed.append({"path": path.name, "bytes": size, "reason": "obsolete_or_invalid"})
            continue
        parameters = value.get("parameters") if isinstance(value.get("parameters"), dict) else {}
        identity = json.dumps({
            "name": value.get("name"),
            "parameters": {key: item for key, item in parameters.items() if key not in volatile},
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        stamp = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        entries.append({"path": path, "bytes": path.stat().st_size, "stamp": stamp,
                        "identity": identity})

    groups: dict[str, list[dict[str, Any]]] = {}
    for item in entries:
        groups.setdefault(item["identity"], []).append(item)
    keep: set[Path] = set()
    for values in groups.values():
        values.sort(key=lambda item: item["stamp"], reverse=True)
        keep.update(item["path"] for item in values[:max(1, int(keep_per_variant))])
    for item in sorted(entries, key=lambda value: value["stamp"]):
        if item["path"] not in keep or item["stamp"] < cutoff:
            item["path"].unlink(missing_ok=True)
            removed.append({"path": item["path"].name, "bytes": item["bytes"],
                            "reason": "variant_limit" if item["path"] not in keep else "expired"})

    remaining = [item for item in entries if item["path"].exists()]
    total_bytes = sum(item["bytes"] for item in remaining)
    if total_bytes > max(1, int(max_bytes)):
        for item in sorted(remaining, key=lambda value: value["stamp"]):
            if total_bytes <= max_bytes:
                break
            item["path"].unlink(missing_ok=True)
            total_bytes -= item["bytes"]
            removed.append({"path": item["path"].name, "bytes": item["bytes"],
                            "reason": "size_limit"})

    # Locks are tiny, but stale orphan locks make diagnostics confusing.
    orphan_locks = 0
    for lock in root.glob("*.lock") if root.is_dir() else []:
        if lock.with_suffix(".json").exists():
            continue
        if datetime.fromtimestamp(lock.stat().st_mtime, timezone.utc) < now - timedelta(days=1):
            try:
                with lock.open("a+", encoding="utf-8") as handle:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    lock.unlink(missing_ok=True)
                    orphan_locks += 1
            except BlockingIOError:
                pass
    final = list(root.glob("*.json")) if root.is_dir() else []
    return {
        "retention_days": retention_days, "max_bytes": max_bytes,
        "removed": len(removed), "removed_bytes": sum(item["bytes"] for item in removed),
        "remaining": len(final), "remaining_bytes": sum(path.stat().st_size for path in final),
        "orphan_locks_removed": orphan_locks, "details": removed,
    }


def materialize_all(home: Path, *, force: bool = False, target: date | None = None) -> dict[str, Any]:
    from .development import (
        development_dashboard, development_global_view, development_summary_view,
    )
    from .intelligence import project_intelligence
    from .simple import analytics
    from .privacy import safe_value

    target = target or date.today()
    results: list[dict[str, Any]] = []
    database = home / ".rd-cockpit" / "events.sqlite"
    for days in (7, 30, 90):
        parameters = {"days": days}

        def build_analytics(days: int = days) -> dict[str, Any]:
            ledger = Ledger(database, readonly=True)
            try:
                return analytics(ledger, home, days=days)
            finally:
                ledger.close()

        view = get_or_build(
            home, "analytics", parameters, build_analytics,
            force=force, source_scope="analytics",
        )
        results.append({"name": "analytics", "parameters": parameters, "cache_hit": view.cache_hit})

    for days in (30, 90, 180, 365):
        parameters = {"days": days, "target_date": target.isoformat()}
        development = get_or_build(
            home, "development-core", parameters,
            lambda days=days: development_dashboard(home, days=days, target=target),
            force=force,
        )
        results.append({"name": "development-core", "parameters": parameters,
                        "cache_hit": development.cache_hit})
        for name, projector in (
            ("development-summary", development_summary_view),
            ("development-global", development_global_view),
        ):
            projection = get_or_build(
                home, name, {**parameters, "privacy": "safe"},
                lambda projector=projector: safe_value(
                    projector(development.data), cockpit_home=home,
                ),
                force=force,
            )
            results.append({"name": name, "parameters": {**parameters, "privacy": "safe"},
                            "cache_hit": projection.cache_hit})
        intelligence = get_or_build(
            home, "intelligence", {**parameters, "baseline": None},
            lambda days=days, dashboard=development.data: project_intelligence(
                home, days=days, baseline=None, target=target, dashboard=dashboard,
            ),
            force=force,
        )
        results.append({"name": "intelligence", "parameters": {**parameters, "baseline": None},
                        "cache_hit": intelligence.cache_hit})
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "target_date": target.isoformat(),
        "views": results,
        "generated": sum(not item["cache_hit"] for item in results),
        "cached": sum(item["cache_hit"] for item in results),
    }
