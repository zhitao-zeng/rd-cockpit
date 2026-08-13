from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from .agent_hooks import handle_agent_hook
from .ledger import Ledger, utc_now
from .security import redact_text


_SCALAR_FIELDS = (
    "session_id", "hook_event_name", "cwd", "source", "model", "reason",
    "session_title", "turn_id", "prompt_id", "tool_name", "tool_use_id",
    "transcript_path", "occurred_at", "timestamp", "duration_ms",
)
_SUMMARY_FIELDS = ("last_assistant_message", "compact_summary", "error")
_TOOL_TEXT_FIELDS = ("command", "cmd", "cwd", "workdir", "path", "file_path")
_RESPONSE_TEXT_FIELDS = ("stdout", "stderr", "output", "content", "error")
_RESPONSE_SCALAR_FIELDS = ("exit_code", "exitCode", "is_error", "success", "status")


def _text(value: Any, limit: int, *, tail: bool = False) -> str:
    clean = redact_text(str(value or ""))
    return clean[-limit:] if tail else clean[:limit]


def compact_hook_input(incoming: dict[str, Any]) -> dict[str, Any]:
    """Keep only the fields required to replay a hook without storing a transcript."""
    compact: dict[str, Any] = {}
    for key in _SCALAR_FIELDS:
        value = incoming.get(key)
        if isinstance(value, (str, int, float, bool)):
            compact[key] = _text(value, 2000) if isinstance(value, str) else value
    for key in _SUMMARY_FIELDS:
        value = incoming.get(key)
        if isinstance(value, str) and value:
            compact[key] = _text(value, 12000, tail=key == "error")

    tool_input = incoming.get("tool_input")
    if isinstance(tool_input, dict):
        kept_input: dict[str, Any] = {}
        for key in _TOOL_TEXT_FIELDS:
            value = tool_input.get(key)
            if isinstance(value, str) and value:
                kept_input[key] = _text(value, 6000)
            elif key in {"command", "cmd"} and isinstance(value, list):
                kept_input[key] = [_text(item, 1000) for item in value[:100]]
        if kept_input:
            compact["tool_input"] = kept_input

    response = incoming.get("tool_response", incoming.get("tool_result"))
    if isinstance(response, str):
        compact["tool_response"] = _text(response, 8000, tail=True)
    elif isinstance(response, dict):
        kept_response: dict[str, Any] = {}
        for key in _RESPONSE_TEXT_FIELDS:
            value = response.get(key)
            if isinstance(value, str) and value:
                kept_response[key] = _text(value, 8000, tail=True)
        for key in _RESPONSE_SCALAR_FIELDS:
            value = response.get(key)
            if isinstance(value, (str, int, float, bool)):
                kept_response[key] = value
        if kept_response:
            compact["tool_response"] = kept_response
    return compact


def enqueue_hook(home: Path, source: str, incoming: dict[str, Any], error: str) -> Path:
    root = home / ".rd-cockpit" / "hook-queue"
    root.mkdir(parents=True, exist_ok=True)
    name = f"{time.time_ns()}-{uuid.uuid4().hex}.json"
    path = root / name
    temporary = root / f".{name}.tmp"
    payload = {
        "schema_version": 1,
        "queued_at": utc_now(),
        "source": source,
        "reason": _text(error, 1000),
        "incoming": compact_hook_input(incoming),
    }
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
        handle.write("\n")
    temporary.replace(path)
    return path


def drain_hook_queue(home: Path, ledger: Ledger, *, limit: int = 500) -> dict[str, int]:
    root = home / ".rd-cockpit" / "hook-queue"
    if not root.exists():
        return {"queued": 0, "processed": 0, "failed": 0}
    paths = sorted(root.glob("*.json"))
    processed = 0
    failed = 0
    for path in paths[:max(1, limit)]:
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
            source = str(item["source"])
            incoming = item["incoming"]
            if not isinstance(incoming, dict):
                raise ValueError("queued hook input must be an object")
            handle_agent_hook(home, ledger, source, incoming)
        except sqlite3.OperationalError:
            # Another writer owns the database. Leave this and later entries in
            # place so the next collector cycle can replay them in order.
            failed += 1
            break
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            failed += 1
            invalid = path.with_suffix(".invalid")
            try:
                path.replace(invalid)
            except OSError:
                pass
        else:
            try:
                path.unlink()
            except OSError:
                failed += 1
            else:
                processed += 1
    remaining = len(list(root.glob("*.json")))
    return {"queued": remaining, "processed": processed, "failed": failed}
