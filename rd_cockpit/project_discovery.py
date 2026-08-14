"""Discover unregistered projects from Agent session repository evidence.

The deterministic scanner identifies independent Git repositories actually
visited by recent Codex/Claude Code sessions.  Codex reviews only compact
metadata and proposes a classification; it never edits the project registry.
Registration remains an explicit CLI action.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .agent_usage import _claude_session, _codex_session, _git_toplevel, _recent_files
from .artifact_cache import atomic_write_json
from .config import PROJECT_ID, add_project, load_config
from .model_runner import run_codex_json
from .security import redact_text, redact_value
from .runtime import workspace_roots


SCHEMA_VERSION = 1
PROMPT_VERSION = 3
DEFAULT_MODEL = "codex:gpt-5.6-sol@medium"
MAX_CANDIDATES_PER_REVIEW = 30
EXCLUDED_PARTS = {
    ".cache", ".claude", ".codex", ".git", ".venv", "node_modules",
    "site-packages", "models", "checkpoints", "datasets", "data",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _cache_path(home: Path) -> Path:
    return home / ".rd-cockpit" / "project-discovery.json"


def _load_cache(home: Path) -> dict[str, Any]:
    try:
        value = json.loads(_cache_path(home).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        value = {}
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        return {"schema_version": SCHEMA_VERSION, "candidates": {}, "ignored_repos": [], "runs": []}
    value.setdefault("candidates", {})
    value.setdefault("ignored_repos", [])
    value.setdefault("runs", [])
    return value


def _save_cache(home: Path, value: dict[str, Any]) -> None:
    atomic_write_json(_cache_path(home), value)


def _candidate_id(repo: str) -> str:
    return "project:" + hashlib.sha256(repo.encode()).hexdigest()[:12]


def _configured_repo_maps(home: Path) -> tuple[dict[str, str], dict[str, str]]:
    config = load_config(home / "config" / "projects.yaml")
    exact: dict[str, str] = {}
    common: dict[str, str] = {}
    for project_id, item in (config.get("projects") or {}).items():
        if not isinstance(item, dict):
            continue
        for raw in [item.get("repo_path"), *(item.get("match_paths") or [])]:
            if not raw:
                continue
            try:
                path = Path(str(raw)).expanduser().resolve()
            except OSError:
                continue
            exact[str(path)] = str(project_id)
            top = _git_toplevel(str(path))
            if top:
                exact[str(Path(top).resolve())] = str(project_id)
    return exact, common


def _excluded_repo(home: Path, repo: Path) -> bool:
    try:
        resolved = repo.resolve()
    except OSError:
        return True
    if resolved in {home.resolve(), home.parent.resolve(), Path.home().resolve()}:
        return True
    if any(part in EXCLUDED_PARTS for part in resolved.parts):
        return True
    return not any(resolved == root or root in resolved.parents for root in workspace_roots(home))


def _repo_for_path(raw: str | None) -> str | None:
    if not raw:
        return None
    top = _git_toplevel(str(raw))
    if not top:
        return None
    try:
        return str(Path(top).resolve())
    except OSError:
        return None


def _relative(repo: str, raw: str) -> str | None:
    try:
        path = Path(raw).resolve()
        return str(path.relative_to(Path(repo)))
    except (OSError, ValueError):
        return None


def _git_summary(repo: str) -> dict[str, Any]:
    def run(*args: str) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", repo, *args], check=True, capture_output=True,
                text=True, timeout=4,
            )
            return result.stdout.strip()
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return ""

    return {
        "branch": run("branch", "--show-current"),
        "last_commit": run("log", "-1", "--format=%h %cs %s")[:240],
        "tracked_files": int(run("ls-files", "-z",).count("\0")),
    }


def scan_candidates(home: Path, *, days: int = 30) -> list[dict[str, Any]]:
    """Return deterministic evidence for unregistered independent repositories."""
    exact, _ = _configured_repo_maps(home)
    ignored = set(_load_cache(home).get("ignored_repos") or [])
    user_home = Path.home()
    sources = (
        ("codex", _recent_files([user_home / ".codex" / "sessions", user_home / ".codex" / "archived_sessions"], days), _codex_session),
        ("claude_code", _recent_files([user_home / ".claude" / "projects"], days), _claude_session),
    )
    grouped: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "session_ids": set(), "agents": set(), "topics": [], "observed_paths": set(),
        "write_paths": set(), "timestamps": [],
    })
    for agent, files, parser in sources:
        for source_file in files:
            try:
                session = parser(source_file)
            except (OSError, TypeError, ValueError):
                continue
            if not session:
                continue
            repo_paths: set[str] = set()
            cwd_repo = _repo_for_path(session.get("cwd"))
            if cwd_repo:
                repo_paths.add(cwd_repo)
            for raw in [*(session.get("observed_workspace_paths") or []), *(session.get("write_workspace_paths") or [])]:
                repo = _repo_for_path(str(raw))
                if repo:
                    repo_paths.add(repo)
            for repo in repo_paths:
                path = Path(repo)
                if repo in exact or repo in ignored or _excluded_repo(home, path):
                    continue
                evidence = grouped[repo]
                evidence["session_ids"].add(str(session.get("session_id") or source_file.stem))
                evidence["agents"].add(agent)
                if session.get("occurred_at"):
                    evidence["timestamps"].append(str(session["occurred_at"]))
                for topic in session.get("topics") or []:
                    if topic not in evidence["topics"]:
                        evidence["topics"].append(redact_text(str(topic)))
                for raw in session.get("observed_workspace_paths") or []:
                    relative = _relative(repo, str(raw))
                    if relative is not None:
                        evidence["observed_paths"].add(relative)
                for raw in session.get("write_workspace_paths") or []:
                    relative = _relative(repo, str(raw))
                    if relative is not None:
                        evidence["write_paths"].add(relative)

    output: list[dict[str, Any]] = []
    for repo, evidence in grouped.items():
        session_ids = sorted(evidence["session_ids"])
        observed = sorted(evidence["observed_paths"])
        written = sorted(evidence["write_paths"])
        topics = evidence["topics"][-8:]
        timestamps = sorted(evidence["timestamps"])
        strength = "strong" if written or len(session_ids) >= 2 else "weak"
        candidate = {
            "candidate_id": _candidate_id(repo),
            "repo_path": repo,
            "repo_name": Path(repo).name,
            "agents": sorted(evidence["agents"]),
            "session_ids": session_ids,
            "session_count": len(session_ids),
            "topics": topics,
            "observed_paths": observed[:20],
            "write_paths": written[:20],
            "write_evidence_count": len(written),
            "first_seen": timestamps[0] if timestamps else None,
            "last_seen": timestamps[-1] if timestamps else None,
            "evidence_strength": strength,
            "git": _git_summary(repo),
        }
        digest_value = {key: candidate[key] for key in (
            "repo_path", "agents", "session_ids", "topics", "observed_paths", "write_paths", "git",
        )}
        candidate["evidence_digest"] = hashlib.sha256(
            json.dumps(digest_value, ensure_ascii=False, sort_keys=True).encode(),
        ).hexdigest()
        output.append(candidate)
    return sorted(output, key=lambda item: (item.get("last_seen") or "", item["repo_path"]), reverse=True)


def _known_candidate_groups(cache: dict[str, Any]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for item in (cache.get("candidates") or {}).values():
        review = item.get("review") or {}
        if review.get("decision") != "new_project" or not review.get("project_group"):
            continue
        key = str(review["project_group"])
        group = groups.setdefault(key, {
            "project_group": key, "suggested_project_id": review.get("suggested_project_id"),
            "suggested_name": review.get("suggested_name"), "summary": review.get("summary"), "repos": [],
        })
        group["repos"].append(item.get("repo_path"))
    return list(groups.values())


def _instruction(candidates: list[dict[str, Any]], existing: dict[str, Any],
                 known_groups: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    compact = [{key: item.get(key) for key in (
        "candidate_id", "repo_path", "repo_name", "agents", "session_count", "topics",
        "observed_paths", "write_paths", "write_evidence_count", "evidence_strength", "git",
    )} for item in candidates]
    catalog = [{"project_id": key, "name": value.get("name") or key,
                "repo_path": value.get("repo_path")}
               for key, value in existing.items() if isinstance(value, dict)]
    return redact_value({
        "task": "审查 Agent Session 发现的未登记 Git 仓库，判断是否代表一个新的持续研发项目",
        "existing_projects": catalog,
        "known_unregistered_project_groups": known_groups or [],
        "candidates": compact,
        "output_schema": {"reviews": [{
            "candidate_id": "原样返回",
            "decision": "new_project|existing_project|temporary_or_reference|insufficient_evidence",
            "project_group": "仅 new_project；同一产品的多个仓库必须填写相同 snake_case 分组",
            "suggested_project_id": "仅 new_project，snake_case",
            "suggested_name": "仅 new_project，简短中文名",
            "summary": "一句中文说明项目在做什么",
            "existing_project_id": "仅 existing_project，从 existing_projects 选择",
            "confidence": "0 到 1",
            "reason": "一句中文审查理由，必须引用输入中的行为证据",
        }]},
        "rules": [
            "实际写入路径、多次 Session 和明确研发主题是新项目强证据",
            "只读取一次的开源仓库、依赖源码、临时复现目录应归为 temporary_or_reference",
            "已有项目的子模块、部署副本或相关 worktree 应归为 existing_project",
            "多个候选仓库如果是同一产品的前端、后端、MCP 或部署组件，应都选 new_project，但使用同一 project_group、suggested_project_id 和 suggested_name；项目不能按仓库机械拆分",
            "增量审查时若候选属于 known_unregistered_project_groups，必须复用该组的 project_group、suggested_project_id 和 suggested_name",
            "证据不足时必须选择 insufficient_evidence，不得为了填满页面猜测",
            "不得补充输入中没有出现的模型、任务目标、组织关系或项目结论",
            "只能返回 JSON，不能建议自动写配置",
        ],
    })


def _json_object(text: str) -> dict[str, Any]:
    value = text.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value, flags=re.I | re.S).strip()
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("Codex output must be an object")
    return parsed


def _request_codex(
    model_spec: str, instruction: dict[str, Any], *, home: Path | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    parsed, metadata = run_codex_json(
        model_spec, instruction,
        prompt="审查标准输入中的项目候选。严格按 output_schema 返回纯 JSON。",
        executable_env="RD_PROJECT_DISCOVERY_CODEX_BIN",
        timeout_env="RD_PROJECT_DISCOVERY_CODEX_TIMEOUT", default_timeout=240,
        workdir=Path(__file__).resolve().parents[1], temp_prefix="rd-project-discovery-",
        run_context={
            "home": home or os.environ.get("RD_COCKPIT_HOME"), "stage": "discovery",
            "source_hash": hashlib.sha256(
                json.dumps(instruction, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest(),
            "reason": "发现了新的或证据发生变化的项目候选。",
        },
    )
    reviews = parsed.get("reviews")
    if not isinstance(reviews, list):
        raise ValueError("Codex output is missing reviews")
    return reviews, {**metadata.get("usage", {}), "model": model_spec,
                     "provider": metadata["provider"],
                     "reasoning_effort": metadata["reasoning_effort"]}


def _validate_reviews(raw: list[dict[str, Any]], candidates: list[dict[str, Any]],
                      existing: dict[str, Any]) -> dict[str, dict[str, Any]]:
    by_id = {item["candidate_id"]: item for item in candidates}
    decisions = {"new_project", "existing_project", "temporary_or_reference", "insufficient_evidence"}
    output: dict[str, dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, dict) or item.get("candidate_id") not in by_id:
            continue
        candidate_id = str(item["candidate_id"])
        decision = str(item.get("decision") or "")
        if decision not in decisions:
            continue
        existing_id = str(item.get("existing_project_id") or "")
        suggested_id = str(item.get("suggested_project_id") or "")
        project_group = str(item.get("project_group") or suggested_id)
        if decision == "existing_project" and existing_id not in existing:
            decision = "insufficient_evidence"; existing_id = ""
        if decision == "new_project" and not PROJECT_ID.fullmatch(suggested_id):
            decision = "insufficient_evidence"; suggested_id = ""
        if decision == "new_project" and not PROJECT_ID.fullmatch(project_group):
            project_group = suggested_id
        try:
            confidence = min(1.0, max(0.0, float(item.get("confidence", 0))))
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence < 0.65 and decision != "insufficient_evidence":
            decision = "insufficient_evidence"
        output[candidate_id] = {
            "decision": decision,
            "project_group": project_group if decision == "new_project" else "",
            "suggested_project_id": suggested_id if decision == "new_project" else "",
            "suggested_name": str(item.get("suggested_name") or "").strip()[:80],
            "summary": str(item.get("summary") or "").strip()[:300],
            "existing_project_id": existing_id if decision == "existing_project" else "",
            "confidence": confidence,
            "reason": str(item.get("reason") or "").strip()[:400],
        }
    return output


def _material_group_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop empty aggregator roots that would over-match unrelated child repositories."""
    material: list[dict[str, Any]] = []
    for item in items:
        path = Path(str(item.get("repo_path") or ""))
        is_parent = any(path != Path(str(other.get("repo_path") or "")) and
                        path in Path(str(other.get("repo_path") or "")).parents
                        for other in items)
        tracked = int((item.get("git") or {}).get("tracked_files") or 0)
        if tracked > 0 or not is_parent:
            material.append(item)
    return material or items[:1]


Reviewer = Callable[[str, dict[str, Any]], tuple[list[dict[str, Any]], dict[str, Any]]]


def refresh_discovery(home: Path, *, days: int = 30, force: bool = False,
                      model: str = DEFAULT_MODEL, reviewer: Reviewer | None = None) -> dict[str, Any]:
    home = home.expanduser().resolve()
    cache = _load_cache(home)
    candidates = scan_candidates(home, days=days)
    existing = load_config(home / "config" / "projects.yaml").get("projects") or {}
    pending = [item for item in candidates if force or
               (cache["candidates"].get(item["candidate_id"]) or {}).get("evidence_digest") != item["evidence_digest"] or
               (cache["candidates"].get(item["candidate_id"]) or {}).get("prompt_version") != PROMPT_VERSION]
    reviewed: dict[str, dict[str, Any]] = {}
    usage: dict[str, Any] = {}
    error = ""
    if pending:
        try:
            request = reviewer or (
                lambda selected_model, payload: _request_codex(selected_model, payload, home=home)
            )
            known_groups = [] if force else _known_candidate_groups(cache)
            for offset in range(0, len(pending), MAX_CANDIDATES_PER_REVIEW):
                batch = pending[offset:offset + MAX_CANDIDATES_PER_REVIEW]
                raw, batch_usage = request(model, _instruction(batch, existing, known_groups))
                reviewed.update(_validate_reviews(raw, batch, existing))
                for key, value in batch_usage.items():
                    if isinstance(value, (int, float)):
                        usage[key] = usage.get(key, 0) + value
                    elif key not in usage:
                        usage[key] = value
        except Exception as exc:  # Keep deterministic evidence available when Codex is unavailable.
            error = f"{type(exc).__name__}: {exc}"
    active_ids = {item["candidate_id"] for item in candidates}
    for item in candidates:
        previous = cache["candidates"].get(item["candidate_id"]) or {}
        review = reviewed.get(item["candidate_id"])
        if review is None and previous.get("evidence_digest") == item["evidence_digest"]:
            review = previous.get("review")
        cache["candidates"][item["candidate_id"]] = {
            **item, "active": True, "review": review,
            "prompt_version": PROMPT_VERSION,
            "reviewed_at": _now() if item["candidate_id"] in reviewed else previous.get("reviewed_at"),
            "review_model": model if item["candidate_id"] in reviewed else previous.get("review_model"),
            "review_error": error if item["candidate_id"] in {row["candidate_id"] for row in pending} and not review else "",
            "status": previous.get("status") if previous.get("status") in {"accepted", "ignored"} else "candidate",
        }
    for candidate_id, item in cache["candidates"].items():
        if candidate_id not in active_ids:
            item["active"] = False
    run = {"at": _now(), "days": days, "found": len(candidates), "pending_reviews": len(pending),
           "reviewed": len(reviewed), "model": model, "usage": usage, "error": error}
    cache["updated_at"] = run["at"]
    cache["scan_days"] = days
    cache["runs"] = [*(cache.get("runs") or [])[-49:], run]
    _save_cache(home, cache)
    return {**read_discovery(home), "run": run}


def read_discovery(home: Path) -> dict[str, Any]:
    cache = _load_cache(home.expanduser().resolve())
    discovered = [item for item in cache["candidates"].values()
                  if item.get("active") and item.get("status") not in {"accepted", "ignored"}]
    discovered.sort(key=lambda item: (item.get("last_seen") or "", item.get("repo_path") or ""), reverse=True)
    counts = {"candidates": 0, "total_discovered": len(discovered), "new_projects": 0, "existing_projects": 0,
              "temporary_or_reference": 0, "insufficient_evidence": 0, "pending_review": 0}
    new_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rows: list[dict[str, Any]] = []
    for item in discovered:
        review = item.get("review") or {}
        decision = review.get("decision")
        if decision == "new_project":
            new_groups[str(review.get("project_group") or review.get("suggested_project_id") or item["candidate_id"])].append(item)
        elif decision == "existing_project":
            counts["existing_projects"] += 1
        elif decision == "temporary_or_reference":
            counts["temporary_or_reference"] += 1
        elif decision == "insufficient_evidence":
            counts["insufficient_evidence"] += 1
            rows.append(dict(item))
        else:
            counts["pending_review"] += 1
            rows.append(dict(item))
    for group_items in new_groups.values():
        group_items = _material_group_items(group_items)
        primary = dict(group_items[0])
        primary["related_repos"] = [item["repo_path"] for item in group_items]
        primary["group_size"] = len(group_items)
        primary["session_count"] = len({session for item in group_items for session in item.get("session_ids") or []})
        primary["write_evidence_count"] = sum(int(item.get("write_evidence_count") or 0) for item in group_items)
        rows.append(primary)
    counts["new_projects"] = len(new_groups)
    rows.sort(key=lambda item: (
        0 if (item.get("review") or {}).get("decision") == "new_project" else 1,
        item.get("last_seen") or "", item.get("repo_path") or "",
    ), reverse=False)
    counts["candidates"] = len(rows)
    for item in rows:
        review = item.get("review") or {}
        item["accept_command"] = (
            f"cd {home.expanduser().resolve()} && .venv/bin/python -m rd_cockpit.cli "
            f"project accept {item['candidate_id']}"
            if review.get("decision") == "new_project" else ""
        )
    return {"updated_at": cache.get("updated_at"), "scan_days": cache.get("scan_days", 30),
            "counts": counts, "candidates": rows,
            "model_policy": {"reviewer": DEFAULT_MODEL, "fallback": None,
                             "registry_write": "explicit_confirmation_only"}}


def accept_candidate(home: Path, candidate_id: str, *, project_id: str | None = None,
                     name: str | None = None, priority: str = "P2",
                     lifecycle_status: str = "active") -> dict[str, Any]:
    home = home.expanduser().resolve()
    cache = _load_cache(home)
    candidate = cache["candidates"].get(candidate_id)
    if not isinstance(candidate, dict) or not candidate.get("active"):
        raise ValueError(f"unknown active project candidate: {candidate_id}")
    review = candidate.get("review") or {}
    if review.get("decision") != "new_project":
        raise ValueError("candidate has not been approved by Codex as a new project")
    selected_id = project_id or str(review.get("suggested_project_id") or "")
    selected_name = name or str(review.get("suggested_name") or candidate.get("repo_name") or "")
    project_group = str(review.get("project_group") or selected_id)
    related = [item for item in cache["candidates"].values()
               if item.get("active") and (item.get("review") or {}).get("decision") == "new_project"
               and str((item.get("review") or {}).get("project_group") or
                       (item.get("review") or {}).get("suggested_project_id")) == project_group]
    related = _material_group_items(related)
    related_paths = [str(item["repo_path"]) for item in related if item.get("repo_path") != candidate.get("repo_path")]
    result = add_project(home, project_id=selected_id, name=selected_name,
                         repo_path=Path(candidate["repo_path"]), priority=priority,
                         lifecycle_status=lifecycle_status, match_paths=related_paths)
    for item in related or [candidate]:
        item["status"] = "accepted"
        item["accepted_at"] = _now()
        item["accepted_project_id"] = selected_id
    _save_cache(home, cache)
    return {**result, "candidate_id": candidate_id, "codex_review": review}


def ignore_candidate(home: Path, candidate_id: str) -> dict[str, Any]:
    home = home.expanduser().resolve()
    cache = _load_cache(home)
    candidate = cache["candidates"].get(candidate_id)
    if not isinstance(candidate, dict):
        raise ValueError(f"unknown project candidate: {candidate_id}")
    repo = str(candidate["repo_path"])
    cache["ignored_repos"] = sorted(set([*(cache.get("ignored_repos") or []), repo]))
    candidate["status"] = "ignored"
    candidate["ignored_at"] = _now()
    _save_cache(home, cache)
    return {"candidate_id": candidate_id, "repo_path": repo, "status": "ignored"}
