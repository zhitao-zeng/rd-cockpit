"""Observable state for scheduled, cache-backed maintenance tasks."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifact_cache import atomic_write_json, read_json
from .runtime import executable_status


STAGES = (
    "pipeline", "reports", "classification", "intelligence", "discovery", "experiments", "architecture",
    "radar", "views", "maintenance",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def status_path(home: Path) -> Path:
    return home / ".rd-cockpit" / "task-status.json"


def read_status(home: Path) -> dict[str, Any]:
    value = read_json(status_path(home), {})
    if not isinstance(value, dict): value = {}
    value.setdefault("schema_version", 1)
    value.setdefault("updated_at", None)
    value.setdefault("stages", {})
    value["model_tools"] = {
        "codex": executable_status("RD_CODEX_BIN", "codex"),
        "claude": executable_status("RD_CLAUDE_BIN", "claude"),
    }
    try:
        from .model_runs import model_run_summary
        summary = model_run_summary(home, days=1, limit=20)
        value["model_activity"] = {
            "counts": summary["counts"], "tokens": summary["tokens"],
            "duration_ms": summary["duration_ms"],
        }
    except Exception:
        value["model_activity"] = {"counts": {}, "tokens": {}, "duration_ms": 0}
    return value


def update_status(home: Path, stage: str, state: str, message: str | None = None) -> dict[str, Any]:
    if stage not in STAGES:
        raise ValueError(f"unknown task stage: {stage}")
    if state not in {"running", "ok", "failed", "skipped"}:
        raise ValueError(f"unknown task state: {state}")
    value = read_status(home)
    now = _now()
    current = dict((value.get("stages") or {}).get(stage) or {})
    if state == "running":
        current["started_at"] = now
        current.pop("finished_at", None)
    else:
        current["finished_at"] = now
    current.update({"state": state, "message": message or ""})
    value["stages"][stage] = current
    value["updated_at"] = now
    atomic_write_json(status_path(home), value)
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Record R&D Cockpit scheduled task state")
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--stage", choices=STAGES, required=True)
    parser.add_argument("--state", choices=["running", "ok", "failed", "skipped"], required=True)
    parser.add_argument("--message")
    args = parser.parse_args()
    update_status(args.home.expanduser().resolve(), args.stage, args.state, args.message)


if __name__ == "__main__":
    main()
