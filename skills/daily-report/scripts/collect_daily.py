#!/usr/bin/env python3
"""Collect a bounded, local-only evidence bundle for the Daily Report skill."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from rd_cockpit.config import config_path, load_config
from rd_cockpit.daily_audit import prepare_bundle
from rd_cockpit.runtime import daily_report_directory


TRIVIAL = {"hi", "hello", "ok", "test", "continue", "go on", "你好", "好的", "继续"}
SECRET_PATTERNS = (
    re.compile(
        r"(?i)(password|passwd|api[ _-]?key|access[ _-]?token|secret|authorization)"
        r"\s*(?:=|:|：)\s*([^\s,;，；]+)"
    ),
    re.compile(r"(?i)\b(sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9_]{12,})\b"),
)
WRITE_TOOLS = {"apply_patch", "edit", "write", "multiedit", "write_file", "create_file"}
ABSOLUTE_PATH = re.compile(r"(?:/[A-Za-z0-9_.@%+=:,~-]+){2,}")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def _redact(value: str, limit: int) -> str:
    text = " ".join(str(value).split())
    text = SECRET_PATTERNS[0].sub(lambda match: f"{match.group(1)}=<redacted>", text)
    text = SECRET_PATTERNS[1].sub("<redacted>", text)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


def _day_bounds(day: str) -> tuple[datetime, datetime]:
    local = datetime.strptime(day, "%Y-%m-%d").astimezone()
    return local, local + timedelta(days=1)


def _substantive(value: str) -> bool:
    text = value.strip().casefold()
    return bool(len(text) >= 3 and text not in TRIVIAL and not text.startswith((
        "<environment_context>", "# agents.md instructions", "<recommended_plugins>",
    )))


def _project_roots(home: Path) -> dict[str, tuple[Path, ...]]:
    config = load_config(config_path(home))
    output: dict[str, tuple[Path, ...]] = {}
    for project_id, item in (config.get("projects") or {}).items():
        if not isinstance(item, dict):
            continue
        values = [item.get("repo_path"), *(item.get("match_paths") or [])]
        roots = []
        for value in values:
            if not value:
                continue
            try:
                roots.append(Path(str(value)).expanduser().resolve())
            except OSError:
                continue
        output[str(project_id)] = tuple(roots)
    return output


def _project_for_paths(roots: dict[str, tuple[Path, ...]], values: Iterable[str]) -> str:
    scored: Counter[str] = Counter()
    for raw in values:
        try:
            path = Path(raw).expanduser().resolve()
        except (OSError, TypeError):
            continue
        for project_id, project_roots in roots.items():
            if any(path == root or root in path.parents for root in project_roots):
                scored[project_id] += 1
    if not scored:
        return "unassigned"
    ranked = scored.most_common()
    return ranked[0][0] if len(ranked) == 1 or ranked[0][1] > ranked[1][1] else "unassigned"


def _tool_paths(value: Any) -> list[str]:
    if isinstance(value, str):
        return ABSOLUTE_PATH.findall(value)
    if isinstance(value, dict):
        return [path for child in value.values() for path in _tool_paths(child)]
    if isinstance(value, list):
        return [path for child in value for path in _tool_paths(child)]
    return []


def _claude_session(path: Path, start: datetime, end: datetime,
                    roots: dict[str, tuple[Path, ...]]) -> dict[str, Any] | None:
    session_id = path.stem
    cwd = ""
    first: datetime | None = None
    last: datetime | None = None
    user: list[str] = []
    conclusions: list[str] = []
    tool_counts: Counter[str] = Counter()
    tool_samples: list[dict[str, str]] = []
    observed_paths: list[str] = []
    edited: list[str] = []
    usage: Counter[str] = Counter()
    try:
        lines = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return None
    with lines:
        for line in lines:
            try:
                item = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            at = _timestamp(item.get("timestamp"))
            if at is None or not start <= at < end:
                continue
            first = at if first is None else min(first, at)
            last = at if last is None else max(last, at)
            session_id = str(item.get("sessionId") or session_id)
            cwd = str(item.get("cwd") or cwd)
            message = item.get("message") if isinstance(item.get("message"), dict) else {}
            content = message.get("content")
            if item.get("type") == "user":
                blocks = [content] if isinstance(content, str) else content if isinstance(content, list) else []
                for block in blocks:
                    text = block if isinstance(block, str) else block.get("text") if isinstance(block, dict) else None
                    if isinstance(text, str) and _substantive(text):
                        user.append(_redact(text, 800))
            if item.get("type") == "assistant" and isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text" and isinstance(block.get("text"), str):
                        text = _redact(block["text"], 2400)
                        if len(text) >= 20:
                            conclusions.append(text)
                    if block.get("type") == "tool_use":
                        name = str(block.get("name") or "unknown")
                        payload = block.get("input")
                        tool_counts[name] += 1
                        paths = _tool_paths(payload)
                        observed_paths.extend(paths)
                        if name.casefold() in WRITE_TOOLS:
                            edited.extend(paths)
                        if len(tool_samples) < 20:
                            tool_samples.append({"name": name, "input_summary": _redact(
                                json.dumps(payload, ensure_ascii=False), 220,
                            )})
            raw_usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
            usage.update({
                "input_tokens": int(raw_usage.get("input_tokens", 0) or 0),
                "cached_input_tokens": int(raw_usage.get("cache_read_input_tokens", 0) or 0),
                "cache_write_input_tokens": int(raw_usage.get("cache_creation_input_tokens", 0) or 0),
                "output_tokens": int(raw_usage.get("output_tokens", 0) or 0),
            })
    if first is None or not (user or conclusions or tool_counts):
        return None
    total_tokens = sum(usage.values())
    evidence_paths = [cwd, *observed_paths[-30:], *edited[-30:]]
    return {
        "session_id": session_id,
        "cwd": cwd,
        "duration_min": round((last - first).total_seconds() / 60, 1) if last else 0,
        "first_intent": user[0] if user else "(无文本意图)",
        "other_intents": user[1:6],
        "last_conclusion": conclusions[-1] if conclusions else "(无结论)",
        "recent_conclusions": conclusions[-3:],
        "tool_count": sum(tool_counts.values()),
        "tool_breakdown": dict(tool_counts),
        "tool_samples": tool_samples,
        "edited_files": sorted(set(edited))[:30],
        "token_usage": {**dict(usage), "total_tokens": total_tokens,
                        "requests": 1 if total_tokens else 0, "available": bool(total_tokens)},
        "_project": _project_for_paths(roots, evidence_paths),
        "_source": "claude_code",
    }


def _codex_session(path: Path, start: datetime, end: datetime,
                   roots: dict[str, tuple[Path, ...]]) -> dict[str, Any] | None:
    metadata: dict[str, Any] = {}
    user: list[str] = []
    conclusions: list[str] = []
    tool_counts: Counter[str] = Counter()
    tool_samples: list[dict[str, str]] = []
    observed_paths: list[str] = []
    edited: list[str] = []
    usage: Counter[str] = Counter()
    first: datetime | None = None
    last: datetime | None = None
    try:
        lines = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return None
    with lines:
        for line in lines:
            try:
                item = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if item.get("type") == "session_meta" and isinstance(item.get("payload"), dict):
                metadata.update(item["payload"])
            at = _timestamp(item.get("timestamp"))
            if at is None or not start <= at < end:
                continue
            first = at if first is None else min(first, at)
            last = at if last is None else max(last, at)
            payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            kind = payload.get("type")
            if item.get("type") == "event_msg" and kind == "user_message":
                text = payload.get("message")
                if isinstance(text, str) and _substantive(text):
                    user.append(_redact(text, 800))
            elif item.get("type") == "event_msg" and kind == "task_complete":
                text = payload.get("last_agent_message")
                if isinstance(text, str) and text.strip():
                    conclusions.append(_redact(text, 2400))
            elif item.get("type") == "event_msg" and kind == "patch_apply_end":
                changes = payload.get("changes")
                if isinstance(changes, dict):
                    edited.extend(str(value) for value in changes)
            elif item.get("type") == "event_msg" and kind == "token_count":
                info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
                current = info.get("last_token_usage") if isinstance(info.get("last_token_usage"), dict) else {}
                usage.update({
                    "input_tokens": int(current.get("input_tokens", 0) or 0),
                    "cached_input_tokens": int(current.get("cached_input_tokens", 0) or 0),
                    "output_tokens": int(current.get("output_tokens", 0) or 0),
                    "reasoning_output_tokens": int(current.get("reasoning_output_tokens", 0) or 0),
                    "total_tokens": int(current.get("total_tokens", 0) or 0),
                    "requests": 1,
                })
            elif item.get("type") == "response_item" and kind in {"function_call", "custom_tool_call"}:
                name = str(payload.get("name") or payload.get("tool_name") or "unknown")
                raw = payload.get("arguments") or payload.get("input")
                tool_counts[name] += 1
                paths = _tool_paths(raw)
                observed_paths.extend(paths)
                if name.casefold() in WRITE_TOOLS:
                    edited.extend(paths)
                if len(tool_samples) < 20:
                    tool_samples.append({"name": name, "input_summary": _redact(str(raw), 220)})
    if first is None or not (user or conclusions or tool_counts):
        return None
    cwd = str(metadata.get("cwd") or "")
    total_tokens = int(usage.get("total_tokens", 0) or 0)
    evidence_paths = [cwd, *observed_paths[-30:], *edited[-30:]]
    return {
        "session_id": str(metadata.get("id") or metadata.get("session_id") or path.stem),
        "cwd": cwd,
        "duration_min": round((last - first).total_seconds() / 60, 1) if last else 0,
        "first_intent": user[0] if user else "(无文本意图)",
        "other_intents": user[1:6],
        "last_conclusion": conclusions[-1] if conclusions else "(无结论)",
        "recent_conclusions": conclusions[-3:],
        "tool_count": sum(tool_counts.values()),
        "tool_breakdown": dict(tool_counts),
        "tool_samples": tool_samples,
        "edited_files": sorted(set(edited))[:30],
        "token_usage": {**dict(usage), "available": bool(total_tokens)},
        "_project": _project_for_paths(roots, evidence_paths),
        "_source": "codex",
    }


def _aggregate(day: str, source: str, sessions: list[dict[str, Any]]) -> dict[str, Any]:
    token_fields = (
        "input_tokens", "cached_input_tokens", "cache_write_input_tokens",
        "output_tokens", "reasoning_output_tokens", "total_tokens", "requests",
    )
    tokens = {field: sum(int((item.get("token_usage") or {}).get(field, 0) or 0)
                         for item in sessions) for field in token_fields}
    tokens["available"] = bool(tokens["total_tokens"])
    return {
        "date": day,
        "source": source,
        "total_sessions": len(sessions),
        "total_tool_calls": sum(int(item.get("tool_count", 0) or 0) for item in sessions),
        "token_usage_summary": tokens,
        "sessions": sessions,
    }


def _recent_jsonl(root: Path, start: datetime) -> list[Path]:
    if not root.is_dir():
        return []
    output = []
    for path in root.rglob("*.jsonl"):
        try:
            if path.stat().st_mtime >= start.timestamp() - 86400:
                output.append(path)
        except OSError:
            continue
    return sorted(output)


def _git_facts(day: str, start: datetime, end: datetime,
               roots: dict[str, tuple[Path, ...]]) -> tuple[dict[str, Any], dict[str, Any]]:
    repos: dict[str, list[str]] = {}
    changed: dict[str, list[str]] = {}
    since, until = start.isoformat(), end.isoformat()
    for project_id, values in roots.items():
        repo = next((value for value in values if (value / ".git").exists()), None)
        if repo is None:
            continue
        try:
            log = subprocess.run(
                ["git", "-C", str(repo), "log", f"--since={since}", f"--until={until}",
                 "--all", "--no-merges", "--format=%h %s"],
                capture_output=True, text=True, timeout=15, check=False,
            )
            commits = [line for line in log.stdout.splitlines() if line.strip()]
            names = subprocess.run(
                ["git", "-C", str(repo), "log", f"--since={since}", f"--until={until}",
                 "--all", "--no-merges", "--name-only", "--format="],
                capture_output=True, text=True, timeout=15, check=False,
            )
            files = {line.strip() for line in names.stdout.splitlines() if line.strip()}
            status = subprocess.run(
                ["git", "-C", str(repo), "status", "--porcelain", "-z"],
                capture_output=True, timeout=15, check=False,
            )
            for record in status.stdout.decode("utf-8", errors="replace").split("\0"):
                relative = record[3:].strip() if len(record) > 3 else ""
                candidate = repo / relative
                try:
                    modified = datetime.fromtimestamp(candidate.stat().st_mtime).astimezone()
                except OSError:
                    continue
                if relative and start <= modified < end:
                    files.add(relative)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if commits:
            repos[project_id] = commits
        if files:
            changed[project_id] = sorted(files)[:300]
    return (
        {"date": day, "total_commits": sum(len(value) for value in repos.values()), "repos": repos},
        {"date": day, "total_files": sum(len(value) for value in changed.values()), "by_project": changed},
    )


def _previous_plan(report_dir: Path, day: str) -> tuple[Path, Path | None]:
    current = datetime.strptime(day, "%Y-%m-%d")
    previous = report_dir / f"{(current - timedelta(days=1)).date().isoformat()}.md"
    output = report_dir / "data" / f"{day}_previous_plan.txt"
    selected: list[str] = []
    if previous.is_file():
        inside = False
        for line in previous.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip() == "## 明日计划":
                inside = True
                continue
            if inside and line.startswith("## "):
                break
            if inside:
                selected.append(line)
    output.write_text("\n".join(selected)[:12000] if selected else "", encoding="utf-8")
    os.chmod(output, 0o600)
    return output, previous if previous.is_file() else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=datetime.now().astimezone().date().isoformat())
    parser.add_argument("--cockpit-home", type=Path)
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--claude-root", type=Path)
    parser.add_argument("--codex-root", type=Path)
    args = parser.parse_args()

    datetime.strptime(args.date, "%Y-%m-%d")
    cockpit = (args.cockpit_home or Path(os.environ.get(
        "RD_COCKPIT_HOME", Path(__file__).resolve().parents[3],
    ))).expanduser().resolve()
    # daily_audit resolves the project catalog through this standard setting.
    # Keep explicit --cockpit-home runs and the validator on the same registry.
    os.environ["RD_COCKPIT_HOME"] = str(cockpit)
    report_dir = (args.report_dir or daily_report_directory()).expanduser().resolve()
    data_dir = report_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(data_dir, 0o700)
    roots = _project_roots(cockpit)
    start, end = _day_bounds(args.date)

    claude_root = (args.claude_root or Path.home() / ".claude" / "projects").expanduser()
    codex_root = (args.codex_root or Path.home() / ".codex" / "sessions").expanduser()
    claude_sessions = [
        item for path in _recent_jsonl(claude_root, start)
        if (item := _claude_session(path, start, end, roots))
    ]
    codex_sessions = [
        item for path in _recent_jsonl(codex_root, start)
        if (item := _codex_session(path, start, end, roots))
    ]
    git, files = _git_facts(args.date, start, end, roots)

    session_file = data_dir / f"{args.date}_sessions.json"
    codex_file = data_dir / f"{args.date}_codex_sessions.json"
    git_file = data_dir / f"{args.date}_git.json"
    files_file = data_dir / f"{args.date}_files.json"
    bundle_file = data_dir / f"{args.date}_audit_input.json"
    candidate_file = data_dir / f"{args.date}_audit_candidate.json"
    validated_file = data_dir / f"{args.date}_audit_validated.json"
    report_file = report_dir / f"{args.date}.md"
    for path, value in (
        (session_file, _aggregate(args.date, "claude_code", claude_sessions)),
        (codex_file, _aggregate(args.date, "codex", codex_sessions)),
        (git_file, git),
        (files_file, files),
    ):
        _write_json(path, value)
    previous_plan, previous_report = _previous_plan(report_dir, args.date)
    _write_json(bundle_file, prepare_bundle(
        report_date=args.date,
        sessions=session_file,
        codex=codex_file,
        git=git_file,
        files=files_file,
        previous_plan=previous_plan,
    ))
    print(json.dumps({
        "date": args.date,
        "audit_bundle": str(bundle_file),
        "candidate_file": str(candidate_file),
        "validated_file": str(validated_file),
        "report_file": str(report_file),
        "previous_report": str(previous_report) if previous_report else None,
        "registered_projects": sorted(roots),
        "collected": {
            "claude_sessions": len(claude_sessions),
            "codex_sessions": len(codex_sessions),
            "git_commits": git["total_commits"],
            "changed_files": files["total_files"],
        },
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
