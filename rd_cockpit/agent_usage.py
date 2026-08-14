"""Import aggregate token usage from local Codex and Claude Code transcripts.

Only counters, session identifiers, timestamps, model names, and working
directories are persisted. Prompt and response text is never copied.
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import subprocess
from collections import Counter
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from .artifact_cache import atomic_write_json, read_json
from .config import load_config
from .ledger import Ledger, sha256_file


_RECENT_WORKSPACE_PATH_LIMIT = 30
_USAGE_COUNTERS = (
    "input_tokens", "output_tokens", "cached_tokens", "cache_write_tokens",
    "reasoning_tokens", "total_tokens",
)
_LOCAL_TZ = ZoneInfo("Asia/Shanghai")


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


def _usage_state_path(home: Path) -> Path:
    return home / ".rd-cockpit" / "usage-sync-state.json"


def _config_signature(home: Path) -> str:
    """Invalidate file classifications when the effective project map changes."""
    config = load_config(home / "config" / "projects.yaml")
    encoded = json.dumps(config.get("projects", {}), ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _file_signature(path: Path) -> dict[str, int]:
    stat = path.stat()
    return {"mtime_ns": stat.st_mtime_ns, "size": stat.st_size}


def _incremental_session(
    agent: str, path: Path, previous: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, int]:
    """Parse only newly appended JSONL records for a long-lived session."""
    size = path.stat().st_size
    prior_item = previous.get("item") if isinstance(previous, dict) and isinstance(previous.get("item"), dict) else None
    prior_offset = int(previous.get("offset", 0)) if isinstance(previous, dict) else 0
    if prior_offset < 0 or prior_offset > size:
        prior_item = None
        prior_offset = 0
    item = dict(prior_item or {})
    topics = list(item.get("topics") or [])
    observed_paths = list(item.get("observed_workspace_paths") or [])
    write_paths = list(item.get("write_workspace_paths") or [])
    totals = {
        key: int(item.get(key, 0) or 0)
        for key in ("input_tokens", "output_tokens", "cached_tokens", "cache_write_tokens", "reasoning_tokens")
    }
    codex_total = int(item.get("total_tokens", 0) or 0)
    offset = prior_offset
    with path.open("rb") as handle:
        handle.seek(prior_offset)
        while True:
            line_start = handle.tell()
            raw = handle.readline()
            if not raw:
                offset = handle.tell()
                break
            if not raw.endswith(b"\n"):
                offset = line_start
                break
            offset = handle.tell()
            try:
                record = json.loads(raw.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            payload = record.get("payload") or {}
            if agent == "codex":
                if record.get("type") == "session_meta":
                    item["session_id"] = payload.get("session_id") or payload.get("id") or item.get("session_id")
                    item["cwd"] = payload.get("cwd") or item.get("cwd")
                    item["model"] = (payload.get("git") or {}).get("model") or payload.get("model_provider") or item.get("model")
                    item["occurred_at"] = item.get("occurred_at") or payload.get("timestamp")
                if record.get("type") == "event_msg" and payload.get("type") == "user_message":
                    topic = _clean_topic(payload.get("message"))
                    if topic: topics.append(topic)
                if record.get("type") == "response_item" and payload.get("type") in {"function_call", "custom_tool_call"}:
                    tool_name = str(payload.get("name") or payload.get("tool_name") or "").casefold()
                    paths = _workspace_paths(payload.get("arguments") or payload.get("input"))
                    observed_paths.extend(paths)
                    if tool_name in _WRITE_TOOL_NAMES: write_paths.extend(paths)
                if record.get("type") == "event_msg" and payload.get("type") == "token_count":
                    usage = (payload.get("info") or {}).get("total_token_usage") or {}
                    codex_total = int(usage.get("total_tokens", 0) or 0)
                    totals.update({
                        "input_tokens": int(usage.get("input_tokens", 0) or 0),
                        "output_tokens": int(usage.get("output_tokens", 0) or 0),
                        "cached_tokens": int(usage.get("cached_input_tokens", 0) or 0),
                        "cache_write_tokens": int(usage.get("cache_write_input_tokens", 0) or 0),
                        "reasoning_tokens": int(usage.get("reasoning_output_tokens", 0) or 0),
                    })
                    item["occurred_at"] = record.get("timestamp") or item.get("occurred_at")
            else:
                if record.get("type") == "ai-title":
                    topic = _clean_topic(record.get("aiTitle"))
                    if topic: topics.append(topic)
                if record.get("type") == "user":
                    content = (record.get("message") or {}).get("content")
                    values = [content] if isinstance(content, str) else [
                        block.get("text") for block in content or []
                        if isinstance(block, dict) and block.get("type") == "text"
                    ]
                    topics.extend(topic for value in values if (topic := _clean_topic(value)))
                if record.get("type") == "assistant":
                    for block in (record.get("message") or {}).get("content") or []:
                        if not isinstance(block, dict) or block.get("type") != "tool_use": continue
                        paths = _workspace_paths(block.get("input"))
                        observed_paths.extend(paths)
                        if str(block.get("name") or "").casefold() in _WRITE_TOOL_NAMES: write_paths.extend(paths)
                usage = (record.get("message") or {}).get("usage") or {}
                if usage:
                    item["session_id"] = record.get("sessionId") or item.get("session_id")
                    item["cwd"] = record.get("cwd") or item.get("cwd")
                    item["model"] = (record.get("message") or {}).get("model") or item.get("model")
                    item["occurred_at"] = record.get("timestamp") or item.get("occurred_at")
                    totals["input_tokens"] += int(usage.get("input_tokens", 0) or 0)
                    totals["output_tokens"] += int(usage.get("output_tokens", 0) or 0)
                    totals["cached_tokens"] += int(usage.get("cache_read_input_tokens", 0) or 0)
                    totals["cache_write_tokens"] += int(usage.get("cache_creation_input_tokens", 0) or 0)
    item.update(totals)
    item["agent"] = agent
    item["session_id"] = item.get("session_id") or path.stem
    item["total_tokens"] = codex_total if agent == "codex" else sum(totals.values())
    item["topics"] = _recent_unique_topics(topics)
    item["observed_workspace_paths"] = observed_paths[-_RECENT_WORKSPACE_PATH_LIMIT:]
    item["write_workspace_paths"] = write_paths[-_RECENT_WORKSPACE_PATH_LIMIT:]
    item["source_file"] = str(path)
    return item, offset


def _activity_day(value: str | None) -> str:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(_LOCAL_TZ).date().isoformat()
    except ValueError:
        return datetime.now(_LOCAL_TZ).date().isoformat()


def _counters(payload: dict[str, Any] | None) -> dict[str, int]:
    value = payload or {}
    return {key: max(0, int(value.get(key, 0) or 0)) for key in _USAGE_COUNTERS}


def _latest_legacy_totals(ledger: Ledger, agent: str, session_id: str) -> dict[str, int]:
    row = ledger.db.execute(
        "SELECT payload_json FROM events WHERE event_type='agent_usage_observed' "
        "AND source=? AND session_id=? ORDER BY ingested_at DESC LIMIT 1",
        (f"{agent}_usage", session_id),
    ).fetchone()
    if not row:
        return _counters(None)
    try:
        return _counters(json.loads(row["payload_json"] or "{}"))
    except (TypeError, json.JSONDecodeError):
        return _counters(None)


def _settle_usage_row(ledger: Ledger, row: Any, *, reason: str) -> bool:
    try:
        payload = json.loads(row["payload_json"] or "{}")
        settled = json.loads(row["settled_totals_json"] or "{}")
    except (TypeError, json.JSONDecodeError):
        return False
    current, before = _counters(payload), _counters(settled)
    delta = {key: max(0, current[key] - before[key]) for key in _USAGE_COUNTERS}
    if not delta["total_tokens"] and not any(delta[key] for key in _USAGE_COUNTERS[:-1]):
        return False
    day = str(row["activity_day"] or _activity_day(row["occurred_at"]))
    event_payload = {
        "agent": row["agent"], "session_id": row["session_id"],
        "activity_day": day, "settlement_reason": reason,
        "token_delta": delta, "total_tokens": delta["total_tokens"],
        "cumulative_total_tokens": current["total_tokens"],
        "source_file": row["source_path"],
    }
    ledger.append(
        event_type="agent_usage_settled", source=str(row["source"]),
        project_id=row["project_id"], session_id=row["session_id"],
        repo_path=row["repo_path"], status="observed", occurred_at=row["occurred_at"],
        payload=event_payload,
        evidence=[{"type": "agent_usage_log", "path": row["source_path"],
                   "sha256": row["evidence_sha256"]}],
        dedup_key=(f"agent_usage_settled:v1:{row['agent']}:{row['session_id']}:"
                   f"{day}:{row['project_id'] or 'unassigned'}:{current['total_tokens']}"),
    )
    ledger.db.execute(
        "UPDATE current_session_usage SET settled_totals_json=?,updated_at=? "
        "WHERE agent=? AND session_id=?",
        (json.dumps(current, sort_keys=True), datetime.now(timezone.utc).isoformat(),
         row["agent"], row["session_id"]),
    )
    ledger.db.commit()
    return True


def _upsert_current_usage(
    ledger: Ledger, *, agent: str, item: dict[str, Any], project_id: str | None,
    path: Path, digest: str,
) -> tuple[bool, bool]:
    session_id = str(item["session_id"])
    previous = ledger.db.execute(
        "SELECT * FROM current_session_usage WHERE agent=? AND session_id=?",
        (agent, session_id),
    ).fetchone()
    day = _activity_day(item.get("occurred_at"))
    settled = (_latest_legacy_totals(ledger, agent, session_id) if previous is None
               else _counters(json.loads(previous["settled_totals_json"] or "{}")))
    boundary_settled = False
    if previous is not None and (
        previous["project_id"] != project_id or str(previous["activity_day"] or "") != day
    ):
        boundary_settled = _settle_usage_row(ledger, previous, reason="project_or_day_boundary")
        refreshed = ledger.db.execute(
            "SELECT settled_totals_json FROM current_session_usage WHERE agent=? AND session_id=?",
            (agent, session_id),
        ).fetchone()
        settled = _counters(json.loads(refreshed["settled_totals_json"] or "{}")) if refreshed else settled
    now = datetime.now(timezone.utc).isoformat()
    ledger.db.execute(
        """INSERT INTO current_session_usage
        (agent,session_id,source,project_id,repo_path,source_path,activity_day,occurred_at,
         updated_at,payload_json,evidence_sha256,settled_totals_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(agent,session_id) DO UPDATE SET
          source=excluded.source,project_id=excluded.project_id,repo_path=excluded.repo_path,
          source_path=excluded.source_path,activity_day=excluded.activity_day,
          occurred_at=excluded.occurred_at,updated_at=excluded.updated_at,
          payload_json=excluded.payload_json,evidence_sha256=excluded.evidence_sha256,
          settled_totals_json=excluded.settled_totals_json""",
        (agent, session_id, f"{agent}_usage", project_id, item.get("cwd"), str(path), day,
         item.get("occurred_at"), now, json.dumps(item, ensure_ascii=False, sort_keys=True),
         digest, json.dumps(settled, sort_keys=True)),
    )
    ledger.db.commit()
    return previous is None, boundary_settled


def _settle_quiet_sessions(ledger: Ledger, quiet_minutes: int) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=max(1, quiet_minutes))
    settled = 0
    for row in ledger.db.execute("SELECT * FROM current_session_usage").fetchall():
        try:
            occurred = datetime.fromisoformat(str(row["occurred_at"] or "").replace("Z", "+00:00"))
            if occurred.tzinfo is None:
                occurred = occurred.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if occurred <= cutoff and _settle_usage_row(ledger, row, reason="session_quiet"):
            settled += 1
    return settled


def sync_usage(ledger: Ledger, home: Path, *, days: int = 30, force: bool = False) -> dict[str, Any]:
    user_home = Path.home()
    codex_files = _recent_files([user_home / ".codex" / "sessions", user_home / ".codex" / "archived_sessions"], days)
    claude_files = _recent_files([user_home / ".claude" / "projects"], days)
    state_path = _usage_state_path(home)
    state = read_json(state_path, {})
    if not isinstance(state, dict): state = {}
    previous_files = state.get("files") if isinstance(state.get("files"), dict) else {}
    config_signature = _config_signature(home)
    force = force or state.get("schema_version") != 2 or state.get("config_signature") != config_signature
    next_files: dict[str, dict[str, Any]] = {}
    accepted = 0; inserted = 0; corrected = 0; unchanged = 0; unassigned = 0; errors = 0
    projection_created = 0; boundary_settled = 0
    for agent, files, parser in (("codex", codex_files, _codex_session), ("claude_code", claude_files, _claude_session)):
        for path in files:
            key = str(path)
            try:
                signature = _file_signature(path)
            except OSError:
                errors += 1
                continue
            previous = previous_files.get(key) if isinstance(previous_files.get(key), dict) else None
            if (not force and previous and previous.get("mtime_ns") == signature["mtime_ns"]
                    and previous.get("size") == signature["size"]):
                next_files[key] = previous
                unchanged += 1
                continue
            try:
                item, offset = _incremental_session(agent, path, None if force else previous)
            except (OSError, TypeError, ValueError): errors += 1; continue
            next_files[key] = {**signature, "offset": offset, "item": item}
            if not item or not any(int(item.get(field, 0) or 0) for field in (
                "input_tokens", "output_tokens", "cached_tokens", "cache_write_tokens",
            )):
                continue
            project_id = _project_for_cwd(home, item.get("cwd"), item.get("topics"),
                                          item.get("observed_workspace_paths"))
            if not project_id: unassigned += 1
            digest = sha256_file(path)
            created, did_settle = _upsert_current_usage(
                ledger, agent=agent, item=item, project_id=project_id, path=path, digest=digest,
            )
            projection_created += int(created)
            boundary_settled += int(did_settle)
            inserted += 1
            accepted += 1
    quiet_minutes = max(1, int(os.environ.get("RD_USAGE_QUIET_MINUTES", "30")))
    quiet_settled = _settle_quiet_sessions(ledger, quiet_minutes)
    atomic_write_json(state_path, {
        "schema_version": 2,
        "config_signature": config_signature,
        "days": days,
        "files": next_files,
    })
    total = len(codex_files) + len(claude_files)
    return {"accepted": accepted, "parsed": accepted, "inserted": inserted,
            "corrected": corrected, "unchanged": unchanged, "discovered_files": total,
            "unassigned": unassigned, "errors": errors,
            "projection_created": projection_created,
            "settled": boundary_settled + quiet_settled,
            "quiet_minutes": quiet_minutes,
            "codex_files": len(codex_files), "claude_files": len(claude_files),
            "days": days, "forced": force}
