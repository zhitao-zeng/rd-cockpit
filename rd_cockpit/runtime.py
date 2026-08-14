"""Portable runtime defaults shared by optional Agent integrations."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def executable(env_name: str, command: str) -> str:
    """Resolve an optional tool without embedding an author's home path.

    A task-specific override wins, followed by the shared ``RD_CODEX_BIN`` or
    ``RD_CLAUDE_BIN`` setting.  The shared setting matters for systemd/cron:
    those processes intentionally have a small PATH and otherwise fail even
    though the executable is available in an interactive shell.
    """
    shared_name = {
        "codex": "RD_CODEX_BIN",
        "claude": "RD_CLAUDE_BIN",
    }.get(command)
    configured = os.environ.get(env_name) or (os.environ.get(shared_name) if shared_name else None)
    if configured:
        return str(Path(configured).expanduser())
    return shutil.which(command) or command


def executable_status(env_name: str, command: str) -> dict[str, str | bool]:
    """Return a serializable preflight result for background task status."""
    resolved = executable(env_name, command)
    candidate = Path(resolved).expanduser()
    available = candidate.is_file() and os.access(candidate, os.X_OK)
    if not available and os.path.sep not in resolved:
        located = shutil.which(resolved)
        if located:
            resolved = located
            available = True
    return {"command": command, "path": resolved, "available": available}


def daily_report_directory() -> Path:
    """Return the user-owned report directory; report content stays outside Git."""
    configured = os.environ.get("RD_DAILY_REPORT_DIR")
    return Path(configured).expanduser() if configured else Path.home() / "daily-reports"


def workspace_roots(cockpit_home: Path) -> tuple[Path, ...]:
    """Bound project discovery to configured roots (default: cockpit parent)."""
    configured = os.environ.get("RD_WORKSPACE_ROOTS")
    values = [value for value in configured.split(os.pathsep) if value.strip()] if configured else []
    roots = [Path(value).expanduser().resolve() for value in values]
    return tuple(roots or [cockpit_home.expanduser().resolve().parent])
