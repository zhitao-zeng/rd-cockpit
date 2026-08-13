"""Import aggregate token usage from local Codex and Claude Code transcripts.

Only counters, session identifiers, timestamps, model names, and working
directories are persisted. Prompt and response text is never copied.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections import Counter
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from .config import load_config
from .ledger import Ledger, sha256_file


_RECENT_WORKSPACE_PATH_LIMIT = 30


@lru_cache(maxsize=256)
def _git_common_dir(path_text: str) -> str | None:
    path = Path(path_text)
    if not path.exists(): return None
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            check=True, capture_output=True, text=True, timeout=3,
        )
        return str(Path(result.stdout.strip()).resolve())
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None


@lru_cache(maxsize=1024)
def _git_toplevel(path_text: str) -> str | None:
    path = Path(path_text)
    try: probe = path if path.is_dir() else path.parent
    except OSError: return None
    try:
        while not probe.exists() and probe != probe.parent: probe = probe.parent
    except OSError:
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(probe), "rev-parse", "--show-toplevel"],
            check=True, capture_output=True, text=True, timeout=3,
        )
        return str(Path(result.stdout.strip()).resolve())
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None


def _project_roots(cfg: dict[str, Any]) -> list[Path]:
    values = [cfg.get("repo_path"), *(cfg.get("match_paths") or [])]
    output = []
    for value in values:
        if not value: continue
        try: output.append(Path(value).resolve())
        except OSError: continue
    return output


def _project_for_cwd(home: Path, cwd: str | None, topics: list[str] | None = None,
                     observed_paths: list[str] | None = None) -> str | None:
    if not cwd: return None
    try: target = Path(cwd).resolve()
    except OSError: return None
    config = load_config(home / "config" / "projects.yaml")
    candidates: list[tuple[int, str]] = []
    for project_id, cfg in config.get("projects", {}).items():
        for root in _project_roots(cfg):
            if target == root or root in target.parents:
                candidates.append((len(str(root)), project_id))
    if candidates: return max(candidates)[1]

    # A session may start inside a Git worktree outside the configured repo.
    # Only compare repositories whose configured path is itself a Git root.
    # A legacy project can be an ordinary directory tracked by the parent
    # workspace repository; sharing that parent's git-common-dir does *not*
    # make the whole workspace a worktree of that child project.
    target_common = _git_common_dir(str(target))
    if target_common:
        for project_id, cfg in config.get("projects", {}).items():
            repo = Path(cfg.get("repo_path", "")).resolve()
            repo_top = _git_toplevel(str(repo))
            if not repo_top or Path(repo_top).resolve() != repo:
                continue
            repo_common = _git_common_dir(str(repo))
            if repo_common and target_common == repo_common: return project_id

    # Tool calls provide stronger evidence than conversation text.  A session
    # started at the workspace root is assigned only when the files/workdirs it
    # actually touched point to one configured repository.
    path_scores: dict[str, int] = {}
    other_repo_scores: Counter[str] = Counter()
    configured_repos = {str(Path(cfg["repo_path"]).resolve())
                        for cfg in config.get("projects", {}).values() if cfg.get("repo_path")}
    configured_parents = {str(Path(path).parent) for path in configured_repos}
    configured_common: dict[str, str] = {}
    for project_id, cfg in config.get("projects", {}).items():
        if not cfg.get("repo_path"):
            continue
        repo = Path(cfg["repo_path"]).resolve()
        repo_top = _git_toplevel(str(repo))
        common = _git_common_dir(str(repo))
        if repo_top and Path(repo_top).resolve() == repo and common:
            configured_common[common] = project_id
    # A Codex/Claude session can stay open for days and visit several projects.
    # Usage snapshots describe the session's *current* state, so stale paths
    # from the beginning of the transcript must not outvote the latest work.
    recent_observed_paths = list(observed_paths or [])[-_RECENT_WORKSPACE_PATH_LIMIT:]
    path_counts = Counter(recent_observed_paths)
    for raw_path, count in path_counts.items():
        try: used_path = Path(raw_path).resolve()
        except OSError: continue
        matched_projects: set[str] = set()
        for project_id, cfg in config.get("projects", {}).items():
            if any(used_path == root or root in used_path.parents for root in _project_roots(cfg)):
                matched_projects.add(project_id)
        git_root = _git_toplevel(raw_path)
        if git_root:
            common = _git_common_dir(git_root)
            if common and common in configured_common:
                matched_projects.add(configured_common[common])
        for project_id in matched_projects:
            path_scores[project_id] = path_scores.get(project_id, 0) + count
        dependency_path = any(marker in raw_path for marker in ("/.venv/", "/models/", "/site-packages/"))
        if (git_root and not matched_projects and not dependency_path and git_root not in configured_repos
                and git_root not in configured_parents and git_root != str(home.parent.resolve())):
            other_repo_scores[git_root] += count
    if path_scores:
        ranked_paths = sorted(((score, project_id) for project_id, score in path_scores.items()), reverse=True)
        if other_repo_scores and other_repo_scores.most_common(1)[0][1] >= ranked_paths[0][0]: return None
        if len(ranked_paths) == 1 or ranked_paths[0][0] > ranked_paths[1][0]: return ranked_paths[0][1]
        return None
    if recent_observed_paths:
        # The session demonstrably worked elsewhere; a project name mentioned
        # in chat is not enough to reassign it.
        return None

    # Parent-workspace sessions cannot be assigned from cwd alone.  Use only
    # explicit, project-configured keywords found in the short task titles.
    # A tie remains unassigned instead of being guessed.
    eligible: set[str] = set()
    for project_id, cfg in config.get("projects", {}).items():
        try:
            if target == Path(cfg["repo_path"]).resolve().parent: eligible.add(project_id)
        except (KeyError, OSError): continue
    haystack = " ".join(topics or []).lower()
    scores: list[tuple[int, str]] = []
    for project_id, cfg in config.get("projects", {}).items():
        if project_id not in eligible: continue
        keywords = cfg.get("match_keywords") or [project_id.replace("_", " ")]
        score = sum(len(str(word)) for word in keywords if str(word).lower() in haystack)
        if score: scores.append((score, project_id))
    if not scores: return None
    scores.sort(reverse=True)
    return scores[0][1] if len(scores) == 1 or scores[0][0] > scores[1][0] else None


_STRIP_BLOCKS = (
    r"<recommended_plugins>.*?</recommended_plugins>",
    r"# AGENTS\.md instructions.*?</INSTRUCTIONS>",
    r"<environment_context>.*?</environment_context>",
    r"<permissions instructions>.*?</permissions instructions>",
    r"<collaboration_mode>.*?</collaboration_mode>",
)


def _clean_topic(value: Any) -> str | None:
    if not isinstance(value, str): return None
    text = value
    for pattern in _STRIP_BLOCKS:
        text = re.sub(pattern, " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]{1,80}>", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" \n\t-#")
    if not text or text.lower() in {"继续", "continue", "继续。", "继续实现"}: return None
    if text.startswith(("You have ", "You are ", "[Tool]", "<tool_result")): return None
    return text if len(text) <= 220 else f"{text[:217].rstrip()}…"


def _recent_unique_topics(values: Iterable[Any], limit: int = 4) -> list[str]:
    topics: list[str] = []
    for value in values:
        topic = _clean_topic(value)
        if topic and topic not in topics: topics.append(topic)
    return topics[-limit:]


# Capture portable absolute POSIX paths from tool payloads. Attribution later
# keeps only paths that resolve to configured repositories.
_WORKSPACE_PATH = re.compile(r"(?:/[A-Za-z0-9_.@%+=:,~-]+){2,}")

_WRITE_TOOL_NAMES = {
    "apply_patch", "edit", "write", "multiedit", "notebookedit",
    "create_file", "write_file", "str_replace_editor",
}


def _workspace_paths(value: Any) -> list[str]:
    output: list[str] = []
    if isinstance(value, str):
        output.extend(_WORKSPACE_PATH.findall(value))
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return output
        output.extend(_workspace_paths(decoded))
    elif isinstance(value, dict):
        for child in value.values(): output.extend(_workspace_paths(child))
    elif isinstance(value, list):
        for child in value: output.extend(_workspace_paths(child))
    return output


def _recent_files(paths: Iterable[Path], days: int) -> list[Path]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    output = []
    for root in paths:
        if not root.exists(): continue
        for path in root.rglob("*.jsonl"):
            try: changed = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            except OSError: continue
            if changed >= cutoff: output.append(path)
    return sorted(output)


def _codex_session(path: Path) -> dict[str, Any] | None:
    meta: dict[str, Any] = {}; total: dict[str, Any] = {}; last_at = None
    user_messages: list[str] = []; observed_paths: list[str] = []; write_paths: list[str] = []
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try: item = json.loads(line)
                except json.JSONDecodeError: continue
                if item.get("type") == "session_meta": meta = item.get("payload") or {}
                p = item.get("payload") or {}
                if item.get("type") == "event_msg" and p.get("type") == "user_message":
                    if isinstance(p.get("message"), str): user_messages.append(p["message"])
                if item.get("type") == "response_item" and p.get("type") in {"function_call", "custom_tool_call"}:
                    tool_name = str(p.get("name") or p.get("tool_name") or "").casefold()
                    tool_value = p.get("arguments") or p.get("input")
                    tool_paths = _workspace_paths(tool_value)
                    observed_paths.extend(tool_paths)
                    if tool_name in _WRITE_TOOL_NAMES:
                        write_paths.extend(tool_paths)
                if item.get("type") == "event_msg" and p.get("type") == "token_count":
                    info = p.get("info") or {}; total = info.get("total_token_usage") or total
                    last_at = item.get("timestamp") or last_at
    except OSError:
        return None
    if not total: return None
    return {"agent": "codex", "session_id": meta.get("session_id") or meta.get("id") or path.stem,
            "cwd": meta.get("cwd"), "model": (meta.get("git") or {}).get("model") or meta.get("model_provider"),
            "occurred_at": last_at or meta.get("timestamp"),
            "input_tokens": int(total.get("input_tokens", 0) or 0),
            "output_tokens": int(total.get("output_tokens", 0) or 0),
            "cached_tokens": int(total.get("cached_input_tokens", 0) or 0),
            "cache_write_tokens": int(total.get("cache_write_input_tokens", 0) or 0),
            "reasoning_tokens": int(total.get("reasoning_output_tokens", 0) or 0),
            "total_tokens": int(total.get("total_tokens", 0) or 0),
            "topics": _recent_unique_topics(user_messages),
            "observed_workspace_paths": observed_paths[-_RECENT_WORKSPACE_PATH_LIMIT:],
            "write_workspace_paths": write_paths[-_RECENT_WORKSPACE_PATH_LIMIT:],
            "source_file": str(path)}


def _claude_session(path: Path) -> dict[str, Any] | None:
    totals = {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0, "cache_write_tokens": 0}
    session_id = path.stem; cwd = None; model = None; last_at = None
    titles: list[str] = []; user_messages: list[str] = []; observed_paths: list[str] = []; write_paths: list[str] = []
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try: item = json.loads(line)
                except json.JSONDecodeError: continue
                if item.get("type") == "ai-title" and isinstance(item.get("aiTitle"), str):
                    titles.append(item["aiTitle"])
                if item.get("type") == "user":
                    content = (item.get("message") or {}).get("content")
                    if isinstance(content, str): user_messages.append(content)
                    elif isinstance(content, list):
                        user_messages.extend(block.get("text", "") for block in content
                                             if isinstance(block, dict) and block.get("type") == "text")
                if item.get("type") == "assistant":
                    content = (item.get("message") or {}).get("content")
                    if isinstance(content, list):
                        for block in content:
                            if not isinstance(block, dict) or block.get("type") != "tool_use":
                                continue
                            tool_paths = _workspace_paths(block.get("input"))
                            observed_paths.extend(tool_paths)
                            if str(block.get("name") or "").casefold() in _WRITE_TOOL_NAMES:
                                write_paths.extend(tool_paths)
                usage = ((item.get("message") or {}).get("usage") or {})
                if not usage: continue
                session_id = item.get("sessionId") or session_id; cwd = item.get("cwd") or cwd
                model = (item.get("message") or {}).get("model") or model
                last_at = item.get("timestamp") or last_at
                totals["input_tokens"] += int(usage.get("input_tokens", 0) or 0)
                totals["output_tokens"] += int(usage.get("output_tokens", 0) or 0)
                totals["cached_tokens"] += int(usage.get("cache_read_input_tokens", 0) or 0)
                totals["cache_write_tokens"] += int(usage.get("cache_creation_input_tokens", 0) or 0)
    except OSError:
        return None
    if not any(totals.values()): return None
    return {"agent": "claude_code", "session_id": session_id, "cwd": cwd, "model": model,
            "occurred_at": last_at, **totals, "reasoning_tokens": 0,
            "total_tokens": sum(totals.values()),
            "topics": _recent_unique_topics(titles or user_messages),
            "observed_workspace_paths": observed_paths[-_RECENT_WORKSPACE_PATH_LIMIT:],
            "write_workspace_paths": write_paths[-_RECENT_WORKSPACE_PATH_LIMIT:],
            "source_file": str(path)}


def sync_usage(ledger: Ledger, home: Path, *, days: int = 30) -> dict[str, Any]:
    user_home = Path.home()
    codex_files = _recent_files([user_home / ".codex" / "sessions", user_home / ".codex" / "archived_sessions"], days)
    claude_files = _recent_files([user_home / ".claude" / "projects"], days)
    accepted = 0; unassigned = 0; errors = 0
    for agent, files, parser in (("codex", codex_files, _codex_session), ("claude_code", claude_files, _claude_session)):
        for path in files:
            try: item = parser(path)
            except (OSError, TypeError, ValueError): errors += 1; continue
            if not item: continue
            project_id = _project_for_cwd(home, item.get("cwd"), item.get("topics"),
                                          item.get("observed_workspace_paths"))
            if not project_id: unassigned += 1
            digest = sha256_file(path)
            existing = ledger.db.execute(
                "SELECT current.event_id,current.project_id FROM events current "
                "JOIN evidence usage_evidence ON usage_evidence.event_id=current.event_id "
                "WHERE current.event_type='agent_usage_observed' AND current.source=? "
                "AND current.session_id=? AND usage_evidence.sha256=? "
                "AND NOT EXISTS (SELECT 1 FROM events correction WHERE correction.supersedes=current.event_id) "
                "ORDER BY current.ingested_at DESC LIMIT 1",
                (f"{agent}_usage", item["session_id"], digest),
            ).fetchone()
            if existing:
                if existing["project_id"] != project_id:
                    ledger.correct_project(
                        str(existing["event_id"]), project_id,
                        "usage project evidence changed after project mapping refresh",
                        repo_path=item.get("cwd"),
                    )
            else:
                ledger.append(event_type="agent_usage_observed", source=f"{agent}_usage", project_id=project_id,
                              session_id=item["session_id"], repo_path=item.get("cwd"), status="observed",
                              occurred_at=item.get("occurred_at"), payload=item,
                              evidence=[{"type": "agent_usage_log", "path": str(path), "sha256": digest}],
                              dedup_key=f"agent_usage:v9:{agent}:{item['session_id']}:{digest}")
            accepted += 1
    return {"accepted": accepted, "unassigned": unassigned, "errors": errors,
            "codex_files": len(codex_files), "claude_files": len(claude_files), "days": days}
