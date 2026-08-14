"""Incremental, private snapshots of parsed Markdown Daily Reports.

Daily Reports remain the source of truth.  This module only avoids parsing the
same Markdown and sidecar files repeatedly while materializing several views.
Every record is content-addressed by the files that can affect its parsed
meaning. Changing a report, semantic sidecar or usage supplement invalidates
that day; changing shared project configuration deliberately rebuilds all
days because it can change project attribution globally.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifact_cache import atomic_write_json, read_json, sha256_path


FACT_STORE_VERSION = 1
PARSER_VERSION = 1
SUPPLEMENT_SUFFIXES = (
    "_sessions.json", "_codex_sessions.json", "_git.json", "_files.json",
)


def _cache_path(home: Path) -> Path:
    return home / ".rd-cockpit" / "report-facts.json"


def _selected_report_paths(directory: Path | None = None) -> list[Path]:
    from .daily_source import REPORT_NAME, report_directories

    roots = [directory] if directory is not None else report_directories()
    selected: dict[str, Path] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.glob("????-??-??.md")):
            match = REPORT_NAME.match(path.name)
            if match:
                selected.setdefault(match.group(1), path)
    return [selected[key] for key in sorted(selected)]


def _file_digest(path: Path) -> tuple[str, int] | None:
    try:
        return sha256_path(path), path.stat().st_size
    except OSError:
        return None


def _record_dependencies(path: Path) -> list[Path]:
    data = path.parent / "data"
    return [
        path,
        data / "normalized" / f"{path.stem}.json",
        data / "project-classifications.json",
        *(data / f"{path.stem}{suffix}" for suffix in SUPPLEMENT_SUFFIXES),
    ]


def _fingerprint(path: Path) -> str:
    payload = {
        "parser_version": PARSER_VERSION,
        "dependencies": [
            {"name": dependency.name, "parent": dependency.parent.name, "digest": digest}
            for dependency in _record_dependencies(path)
            if (digest := _file_digest(dependency)) is not None
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _config_fingerprint(home: Path) -> str:
    values: list[dict[str, Any]] = []
    for path in (home / "config" / "projects.yaml", home / "config" / "projects.local.yaml"):
        if (digest := _file_digest(path)) is not None:
            values.append({"name": path.name, "digest": digest})
    encoded = json.dumps(values, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def refresh_report_facts(
    home: Path, *, directory: Path | None = None, force: bool = False,
) -> dict[str, Any]:
    """Refresh changed report facts and return the complete private snapshot."""
    from .daily_source import parse_report
    from .daily_supplement import load_supplement

    home = home.expanduser().resolve()
    path = _cache_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    lock_path = path.with_suffix(".lock")
    with lock_path.open("a+", encoding="utf-8") as lock:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        existing = read_json(path, {})
        if not isinstance(existing, dict) or existing.get("schema_version") != FACT_STORE_VERSION:
            existing = {}
        previous = existing.get("records") if isinstance(existing.get("records"), dict) else {}
        config_fingerprint = _config_fingerprint(home)
        config_changed = existing.get("config_fingerprint") != config_fingerprint
        records: dict[str, dict[str, Any]] = {}
        parsed = reused = 0
        for report_path in _selected_report_paths(directory):
            day = report_path.stem
            fingerprint = _fingerprint(report_path)
            old = previous.get(day) if isinstance(previous, dict) else None
            if (
                not force and not config_changed and isinstance(old, dict)
                and old.get("source_fingerprint") == fingerprint
                and isinstance(old.get("report"), dict)
            ):
                records[day] = old
                reused += 1
                continue
            report = parse_report(report_path)
            report["_supplement"] = load_supplement(day, directory=report_path.parent)
            records[day] = {
                "source_fingerprint": fingerprint,
                "source_name": report_path.name,
                "report": report,
            }
            parsed += 1
        value = {
            "schema_version": FACT_STORE_VERSION,
            "parser_version": PARSER_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "config_fingerprint": config_fingerprint,
            "records": records,
            "refresh": {
                "parsed": parsed,
                "reused": reused,
                "removed": max(0, len(previous) - len(records)) if isinstance(previous, dict) else 0,
                "config_changed": config_changed,
            },
        }
        atomic_write_json(path, value)
        os.chmod(path, 0o600)
        return value


def load_report_facts(
    home: Path, *, directory: Path | None = None, since: str | None = None,
    force: bool = False,
) -> list[dict[str, Any]]:
    snapshot = refresh_report_facts(home, directory=directory, force=force)
    records = snapshot.get("records") if isinstance(snapshot, dict) else {}
    output: list[dict[str, Any]] = []
    for day in sorted(records if isinstance(records, dict) else {}):
        if since is not None and day < since:
            continue
        item = records[day]
        if isinstance(item, dict) and isinstance(item.get("report"), dict):
            output.append(item["report"])
    return output
