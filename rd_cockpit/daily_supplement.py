"""Structured, non-narrative facts that supplement an existing daily report.

Collector JSON is useful for counts and attribution, but its prompt snippets are
not suitable for display.  This module exposes only aggregate sessions, token,
commit and changed-file facts and maps them to research projects when a concrete
path or repository name supports that mapping.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .daily_source import (
    _configured_project_rules,
    _project_ids,
    project_display_names,
    report_directory,
)


PROJECT_NAMES = {
    "asr": "ASR",
    "asr_dialect": "Dialect ASR",
    "asr_model_eval": "ASR Evaluation",
    "asr_alignment": "Speech Alignment",
    "asr_other": "ASR / 其他",
    "ocr": "OCR",
    "obstacle": "Obstacle",
    "research_tools": "Research Tools",
    "infrastructure": "Infrastructure",
    "unassigned": "尚未按项目归属",
}

PATH_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("research_tools", ("rd-cockpit", "研发驾驶舱", "r&d cockpit")),
    ("asr_dialect", ("dialect-asr", "dialect-speech")),
    ("asr_alignment", ("speech-aligner", "forced-alignment")),
    ("asr_model_eval", ("speech-model-eval", "asr-benchmark")),
    ("asr", ("robot-speech", "on-device-asr")),
    ("avatar_video", ("video-generator",)),
    ("investment_research", ("company-research",)),
    ("music_voice", ("music-generation", "speech-generation")),
    ("llm_inference", ("llm-serving",)),
    ("ocr", ("text-recognition", "/ocr/")),
    ("obstacle", ("obstacle-detection", "depth-estimation")),
    ("resume_copilot", ("document-assistant",)),
    ("idol", ("character-generator",)),
    ("router", ("model-router",)),
    ("asr_other", ("/asr_", "/asr/")),
)


def available_supplement_dates(directory: Path | None = None) -> list[str]:
    root = (directory or report_directory()) / "data"
    if not root.exists():
        return []
    dates: set[str] = set()
    for path in root.iterdir():
        name = path.name
        if len(name) >= 10 and name[:10].count("-") == 2 and name.endswith(
            ("_sessions.json", "_codex_sessions.json", "_git.json", "_files.json")
        ):
            dates.add(name[:10])
    return sorted(dates)


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def _path_project(text: str) -> str | None:
    value = text.casefold()
    for project_id, _, paths in _configured_project_rules():
        if any(path.casefold() in value for path in paths):
            return project_id
    for project_id, markers in PATH_MARKERS:
        if any(marker in value for marker in markers):
            return project_id
    return None


def _session_project(session: dict[str, Any]) -> str:
    concrete = [str(session.get("cwd") or "")]
    concrete.extend(str(value) for value in session.get("edited_files") or [])
    counts: dict[str, int] = defaultdict(int)
    for value in concrete:
        if project_id := _path_project(value):
            counts[project_id] += 1
    if counts:
        ranked = sorted(counts.items(), key=lambda item: item[1], reverse=True)
        if len(ranked) == 1 or ranked[0][1] > ranked[1][1]:
            return ranked[0][0]

    declared = str(session.get("_project") or "").casefold()
    configured = _project_ids(declared)
    if len(configured) == 1:
        return configured[0]
    # Intent text is classification-only and is never returned to the UI.  Use
    # it only when it points to exactly one known research direction.
    intent = " ".join(str(session.get(key) or "") for key in ("first_intent", "last_conclusion"))
    intent_matches = _project_ids(intent)
    return intent_matches[0] if len(intent_matches) == 1 else "unassigned"


def _project_for_repo(repo: str) -> str:
    if project_id := _path_project(repo):
        return project_id
    return "unassigned"


def _new_project(project_id: str) -> dict[str, Any]:
    names = {**PROJECT_NAMES, **project_display_names()}
    return {"project_id": project_id, "name": names.get(project_id, project_id),
            "sessions": 0, "claude_sessions": 0, "codex_sessions": 0, "requests": 0,
            "tool_calls": 0, "duration_minutes": 0.0, "tokens": 0,
            "claude_tokens": 0, "codex_tokens": 0, "commits": 0,
            "changed_files": 0}


def load_supplement(report_date: str, directory: Path | None = None) -> dict[str, Any]:
    root = (directory or report_directory()) / "data"
    claude = _read(root / f"{report_date}_sessions.json")
    codex = _read(root / f"{report_date}_codex_sessions.json")
    git = _read(root / f"{report_date}_git.json")
    files = _read(root / f"{report_date}_files.json")
    available = any((claude, codex, git, files))
    projects: dict[str, dict[str, Any]] = {}

    def project_item(project_id: str) -> dict[str, Any]:
        return projects.setdefault(project_id, _new_project(project_id))

    totals = {"sessions": 0, "requests": 0, "tool_calls": 0, "duration_minutes": 0.0,
              "tokens": 0, "commits": int(git.get("total_commits", 0) or 0),
              "changed_files": int(files.get("total_files", 0) or 0)}
    usage_sessions = 0
    attributed_sessions = 0
    attributed_tokens = 0

    for agent, source in (("claude", claude), ("codex", codex)):
        totals["sessions"] += int(source.get("total_sessions", 0) or 0)
        totals["tool_calls"] += int(source.get("total_tool_calls", 0) or 0)
        token_summary = source.get("token_usage_summary") or {}
        totals["requests"] += int(token_summary.get("requests", 0) or 0)
        totals["tokens"] += int(token_summary.get("total_tokens", 0) or 0)
        for session in source.get("sessions") or []:
            project_id = _session_project(session)
            item = project_item(project_id)
            item["sessions"] += 1
            item[f"{agent}_sessions"] += 1
            item["tool_calls"] += int(session.get("tool_count", 0) or 0)
            duration = float(session.get("duration_min", 0) or 0)
            item["duration_minutes"] += duration
            totals["duration_minutes"] += duration
            usage = session.get("token_usage") or {}
            if usage.get("available"):
                usage_sessions += 1
                amount = int(usage.get("total_tokens", 0) or 0)
                item["tokens"] += amount
                item[f"{agent}_tokens"] += amount
                item["requests"] += int(usage.get("requests", 0) or 0)
                if project_id != "unassigned":
                    attributed_tokens += amount
            if project_id != "unassigned":
                attributed_sessions += 1

    for repo, commits in (git.get("repos") or {}).items():
        project_item(_project_for_repo(str(repo)))["commits"] += len(commits or [])

    for owner, changed in (files.get("by_project") or {}).items():
        for changed_path in changed or []:
            project_id = _path_project(f"{owner}/{changed_path}") or _project_for_repo(str(owner))
            project_item(project_id)["changed_files"] += 1

    for item in projects.values():
        item["duration_minutes"] = round(item["duration_minutes"], 1)

    ordered = sorted(projects.values(), key=lambda item: (item["project_id"] == "unassigned", -item["tokens"], -item["commits"]))
    return {
        "available": available,
        "date": report_date,
        "totals": totals,
        "projects": ordered,
        "coverage": {
            "sessions_with_usage": usage_sessions,
            "sessions_total": totals["sessions"],
            "attributed_sessions": attributed_sessions,
            "attributed_tokens": attributed_tokens,
            "token_attribution_ratio": round(attributed_tokens / totals["tokens"], 4) if totals["tokens"] else None,
        },
        "sources": {"claude": bool(claude), "codex": bool(codex), "git": bool(git), "files": bool(files)},
    }
