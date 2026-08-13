"""Portable runtime defaults shared by optional Agent integrations."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def executable(env_name: str, command: str) -> str:
    """Resolve an optional tool without embedding an author's home path."""
    configured = os.environ.get(env_name)
    if configured:
        return str(Path(configured).expanduser())
    return shutil.which(command) or command


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
