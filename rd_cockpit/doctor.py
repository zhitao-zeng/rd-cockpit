"""Read-only installation health checks for a local R&D Cockpit."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifact_cache import read_json
from .config import load_config
from .daily_source import available_report_dates, report_directories
from .migrations import LATEST_SCHEMA_VERSION
from .report_facts import FACT_STORE_VERSION


SERVICES = (
    "rd-cockpit-web.service",
    "rd-cockpit-resources.service",
    "rd-cockpit-usage-sync.service",
)
TIMERS = ("rd-cockpit-refresh.timer", "rd-cockpit-maintenance.timer")


def _item(name: str, status: str, summary: str, **details: Any) -> dict[str, Any]:
    return {"name": name, "status": status, "summary": summary, "details": details}


def _integrity(path: Path) -> tuple[str, int | None]:
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=10) as connection:
        check = connection.execute("PRAGMA integrity_check").fetchone()
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    return str(check[0]) if check else "unknown", version


def _database_checks(home: Path) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    hot = home / ".rd-cockpit" / "events.sqlite"
    if not hot.is_file():
        return [_item("database", "error", "事实账本不存在，请先运行 rd init")]
    try:
        check, version = _integrity(hot)
    except sqlite3.Error as exc:
        output.append(_item("database", "error", f"事实账本无法读取：{exc}"))
    else:
        status = "ok" if check == "ok" and version == LATEST_SCHEMA_VERSION else "error"
        output.append(_item(
            "database", status,
            f"事实账本完整性 {check} · schema v{version}/{LATEST_SCHEMA_VERSION}",
            integrity=check, schema_version=version, expected_schema=LATEST_SCHEMA_VERSION,
        ))
    cold = home / ".rd-cockpit" / "events-archive.sqlite"
    if not cold.is_file():
        output.append(_item("cold_store", "ok", "尚无冷数据；数据量较小时属于正常状态"))
    else:
        try:
            check, _ = _integrity(cold)
        except sqlite3.Error as exc:
            output.append(_item("cold_store", "error", f"冷数据无法读取：{exc}"))
        else:
            output.append(_item(
                "cold_store", "ok" if check == "ok" else "error",
                f"冷数据完整性 {check}", integrity=check,
            ))
    return output


def _restore_drill(home: Path) -> dict[str, Any]:
    root = home / ".rd-cockpit" / "backups"
    backups = sorted(root.glob("events-????-??-??.sqlite"), reverse=True)
    if not backups:
        return _item("backup_restore", "warning", "还没有可演练恢复的每日备份")
    source_path = backups[0]
    try:
        with tempfile.TemporaryDirectory(prefix="rd-doctor-restore-") as directory:
            restored = Path(directory) / "restored.sqlite"
            with sqlite3.connect(f"file:{source_path.resolve()}?mode=ro", uri=True) as source:
                with sqlite3.connect(restored) as destination:
                    source.backup(destination)
                    destination.commit()
                    check = destination.execute("PRAGMA integrity_check").fetchone()
            integrity = str(check[0]) if check else "unknown"
    except (OSError, sqlite3.Error) as exc:
        return _item("backup_restore", "error", f"临时恢复演练失败：{exc}")
    return _item(
        "backup_restore", "ok" if integrity == "ok" else "error",
        f"最新备份可恢复，完整性 {integrity}",
        backup=source_path.name, integrity=integrity,
    )


def _json_health(paths: list[Path]) -> tuple[int, int]:
    total = invalid = 0
    for path in paths:
        total += 1
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            invalid += 1
    return total, invalid


def _cache_checks(home: Path) -> list[dict[str, Any]]:
    facts_path = home / ".rd-cockpit" / "report-facts.json"
    facts = read_json(facts_path, {})
    if not facts_path.is_file():
        facts_item = _item("report_facts", "warning", "日报事实快照尚未生成")
    elif not isinstance(facts, dict) or facts.get("schema_version") != FACT_STORE_VERSION:
        facts_item = _item("report_facts", "warning", "日报事实快照版本已过期，后台会重建")
    else:
        facts_item = _item(
            "report_facts", "ok", f"日报事实快照包含 {len(facts.get('records') or {})} 天",
            records=len(facts.get("records") or {}), refresh=facts.get("refresh") or {},
        )
    view_paths = list((home / ".rd-cockpit" / "views").glob("*.json"))
    view_total, view_invalid = _json_health(view_paths)
    view_bytes = sum(path.stat().st_size for path in view_paths)
    configured_limit = int(float(os.environ.get("RD_VIEW_CACHE_MAX_MB", "100")) * 1024 * 1024)
    view_item = _item(
        "view_cache", "error" if view_invalid else ("warning" if view_bytes > configured_limit or not view_total else "ok"),
        f"物化视图 {view_total} 个，占用 {view_bytes / 1024 / 1024:.1f} MB，损坏 {view_invalid} 个",
        total=view_total, invalid=view_invalid, bytes=view_bytes, max_bytes=configured_limit,
    )
    semantic_paths: list[Path] = []
    for root in report_directories():
        data = root / "data"
        if data.is_dir():
            semantic_paths.extend(data.glob("normalized/*.json"))
            semantic_paths.extend(data.glob("*_intelligence_validated.json"))
            semantic_paths.extend(data.glob("experiment-intelligence/*.json"))
    semantic_total, semantic_invalid = _json_health(semantic_paths)
    semantic_item = _item(
        "semantic_cache", "error" if semantic_invalid else "ok",
        f"语义缓存 {semantic_total} 个，损坏 {semantic_invalid} 个",
        total=semantic_total, invalid=semantic_invalid,
    )
    return [facts_item, view_item, semantic_item]


def _service_checks() -> list[dict[str, Any]]:
    if not shutil.which("systemctl"):
        return [_item("services", "warning", "当前环境没有 systemctl，已跳过服务检查")]
    output: list[dict[str, Any]] = []
    for unit in (*SERVICES, *TIMERS):
        try:
            result = subprocess.run(
                ["systemctl", "--user", "show", unit, "--property=ActiveState,SubState", "--value"],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            output.append(_item(unit, "warning", f"无法检查：{exc}"))
            continue
        values = [value for value in result.stdout.splitlines() if value]
        active = result.returncode == 0 and values and values[0] == "active"
        output.append(_item(
            unit, "ok" if active else "warning",
            " · ".join(values) if values else (result.stderr.strip() or "状态未知"),
        ))
    return output


def doctor(home: Path, *, check_services: bool = True, restore_drill: bool = True) -> dict[str, Any]:
    home = home.expanduser().resolve()
    checks: list[dict[str, Any]] = []
    try:
        config = load_config(home / "config" / "projects.yaml")
        projects = config.get("projects") if isinstance(config, dict) else None
        if not isinstance(projects, dict):
            raise ValueError("projects must be a mapping")
        missing = sum(
            not Path(str(value.get("repo_path") or "")).expanduser().is_dir()
            for value in projects.values() if isinstance(value, dict) and value.get("repo_path")
        )
        checks.append(_item(
            "config", "warning" if missing else "ok",
            f"项目注册 {len(projects)} 个，路径缺失 {missing} 个",
            projects=len(projects), missing_repositories=missing,
        ))
    except (OSError, TypeError, ValueError) as exc:
        checks.append(_item("config", "error", f"项目配置无法读取：{exc}"))
    checks.extend(_database_checks(home))
    dates = available_report_dates()
    checks.append(_item(
        "daily_reports", "ok" if dates else "warning",
        f"日报 {len(dates)} 天" + (f"，最新 {dates[-1]}" if dates else ""),
        count=len(dates), latest=dates[-1] if dates else None,
    ))
    checks.extend(_cache_checks(home))
    queue = home / ".rd-cockpit" / "hook-queue"
    queued = len(list(queue.glob("*.json"))) if queue.is_dir() else 0
    invalid = len(list(queue.glob("*.invalid"))) if queue.is_dir() else 0
    checks.append(_item(
        "hook_queue", "warning" if invalid or queued > 100 else "ok",
        f"待处理 Hook {queued} 个，隔离失败 {invalid} 个",
        queued=queued, invalid=invalid,
    ))
    dist = home / "frontend" / "dist" / "index.html"
    checks.append(_item(
        "frontend", "ok" if dist.is_file() else "warning",
        "生产前端已构建" if dist.is_file() else "生产前端尚未构建",
    ))
    if restore_drill:
        checks.append(_restore_drill(home))
    if check_services:
        checks.extend(_service_checks())
    errors = sum(item["status"] == "error" for item in checks)
    warnings = sum(item["status"] == "warning" for item in checks)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "error" if errors else ("warning" if warnings else "ok"),
        "summary": {"checks": len(checks), "errors": errors, "warnings": warnings},
        "checks": checks,
    }
