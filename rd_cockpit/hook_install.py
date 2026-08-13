"""Safely install user-level Codex and Claude Code lifecycle hooks."""

from __future__ import annotations

import json
import shlex
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any


EVENTS = ("SessionStart", "PostToolUse", "SessionEnd")
REMOVED_EVENTS = ("Stop", "PostCompact")


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _backup(path: Path) -> str | None:
    if not path.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.name}.rd-cockpit-backup-{stamp}")
    counter = 1
    while target.exists():
        target = path.with_name(f"{path.name}.rd-cockpit-backup-{stamp}-{counter}")
        counter += 1
    shutil.copy2(path, target)
    return str(target)


def _group(command: Any, *, matcher: str | None = None, timeout: int = 5) -> dict[str, Any]:
    value: dict[str, Any] = {"hooks": [{"type": "command", **command, "timeout": timeout}]}
    if matcher:
        value["matcher"] = matcher
    return value


def _merge_group(settings: dict[str, Any], event: str, group: dict[str, Any], marker: str) -> bool:
    hooks = settings.setdefault("hooks", {})
    groups = hooks.setdefault(event, [])
    for existing in groups:
        for handler in existing.get("hooks", []) if isinstance(existing, dict) else []:
            command = str(handler.get("command", ""))
            args = " ".join(str(value) for value in handler.get("args", []))
            if marker in command or marker in args:
                if existing == group:
                    return False
                existing.clear()
                existing.update(group)
                return True
    groups.append(group)
    return True


def _remove_marked_groups(settings: dict[str, Any], events: tuple[str, ...], marker: str) -> bool:
    changed = False
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return False
    for event in events:
        groups = hooks.get(event)
        if not isinstance(groups, list):
            continue
        kept = []
        for group in groups:
            handlers = group.get("hooks", []) if isinstance(group, dict) else []
            owned = any(marker in str(handler.get("command", ""))
                        or marker in " ".join(str(value) for value in handler.get("args", []))
                        for handler in handlers if isinstance(handler, dict))
            if owned:
                changed = True
            else:
                kept.append(group)
        if kept:
            hooks[event] = kept
        elif event in hooks:
            del hooks[event]
    return changed


def install_user_hooks(cockpit_home: Path, user_home: Path) -> dict[str, Any]:
    cockpit_home = cockpit_home.resolve()
    python = (cockpit_home / ".venv" / "bin" / "python").resolve()
    script = (cockpit_home / "hooks" / "agent-hook.py").resolve()
    if not python.exists():
        raise ValueError(f"R&D Cockpit Python not found: {python}")
    if not script.exists():
        raise ValueError(f"agent hook adapter not found: {script}")

    result: dict[str, Any] = {"codex": {}, "claude_code": {}}
    marker = str(script)

    codex_path = user_home / ".codex" / "hooks.json"
    codex = json.loads(codex_path.read_text(encoding="utf-8")) if codex_path.exists() else {
        "description": "User lifecycle hooks for R&D Cockpit session and experiment facts.", "hooks": {}}
    codex_command = " ".join((shlex.quote(str(python)), shlex.quote(str(script)), "--source", "codex"))
    codex_changed = False
    codex_changed |= _remove_marked_groups(codex, REMOVED_EVENTS, marker)
    for event in EVENTS:
        matcher = "^Bash$" if event == "PostToolUse" else None
        timeout = 3 if event == "SessionEnd" else 5
        codex_changed |= _merge_group(codex, event, _group({"command": codex_command}, matcher=matcher,
                                                            timeout=timeout), marker)
    codex_backup = _backup(codex_path) if codex_changed else None
    if codex_changed:
        _atomic_json(codex_path, codex)
    result["codex"] = {"path": str(codex_path), "changed": codex_changed, "backup": codex_backup,
                       "requires_review": True,
                       "review_command": "在 Codex 中打开 /hooks，审核并信任 R&D Cockpit hooks"}

    claude_path = user_home / ".claude" / "settings.json"
    claude = json.loads(claude_path.read_text(encoding="utf-8")) if claude_path.exists() else {}
    handler = {"command": str(python), "args": [str(script), "--source", "claude-code"]}
    claude_changed = False
    claude_changed |= _remove_marked_groups(claude, REMOVED_EVENTS, marker)
    for event in EVENTS:
        matcher = "Bash" if event == "PostToolUse" else None
        timeout = 5
        claude_changed |= _merge_group(claude, event, _group(handler, matcher=matcher, timeout=timeout), marker)
    # Claude has a separate failure event; Codex reports failed Bash calls to PostToolUse.
    claude_changed |= _merge_group(claude, "PostToolUseFailure",
                                   _group(handler, matcher="Bash", timeout=5), marker)
    claude_backup = _backup(claude_path) if claude_changed else None
    if claude_changed:
        _atomic_json(claude_path, claude)
    result["claude_code"] = {"path": str(claude_path), "changed": claude_changed,
                             "backup": claude_backup, "requires_review": False,
                             "verify_command": "在 Claude Code 中运行 /hooks 查看已加载配置"}
    return result
