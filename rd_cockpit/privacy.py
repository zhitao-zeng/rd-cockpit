"""Presentation-only redaction for the LAN-facing read-only API."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .security import redact_text


_PRIVATE_IP = re.compile(
    r"(?<!\d)(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|"
    r"172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})(?::\d{1,5})?(?!\d)",
)
_ABSOLUTE_PATH = re.compile(
    r"(?<![/\w])/(?:home|mnt|data|srv|opt|tmp|var|usr|etc|root|workspace)/"
    r"[^\s`\"'<>),;]+",
)
_SESSION_KEYS = {"session_id", "session_ids", "source_path"}
_MACHINE_KEYS = {"machine", "hostname", "host", "container_id", "pid"}


def _path_label(raw: str) -> str:
    value = raw.rstrip("/.:")
    name = Path(value).name
    suffix = raw[len(value):]
    return f"<local-path>/{name or '…'}{suffix}"


def safe_text(text: str, *, cockpit_home: Path) -> str:
    value = redact_text(text)
    roots = [cockpit_home.expanduser().resolve(), cockpit_home.expanduser().resolve().parent, Path.home()]
    for root, label in (
        (roots[0], "<cockpit>"), (roots[1], "<workspace>"), (roots[2], "~"),
    ):
        value = value.replace(str(root), label)
    value = _PRIVATE_IP.sub("<private-host>", value)
    value = _ABSOLUTE_PATH.sub(lambda match: _path_label(match.group(0)), value)
    return value


def safe_value(value: Any, *, cockpit_home: Path, key: str | None = None) -> Any:
    if key in _SESSION_KEYS:
        if isinstance(value, list):
            return []
        return None if value is None else "<private>"
    if key in _MACHINE_KEYS:
        if value in (None, "", "local"):
            return value
        return "<remote>"
    if isinstance(value, str):
        return safe_text(value, cockpit_home=cockpit_home)
    if isinstance(value, list):
        return [safe_value(item, cockpit_home=cockpit_home) for item in value]
    if isinstance(value, dict):
        return {
            item_key: safe_value(item, cockpit_home=cockpit_home, key=str(item_key))
            for item_key, item in value.items()
        }
    return value
