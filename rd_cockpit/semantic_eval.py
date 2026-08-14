"""Offline golden regression suite for model-derived Daily Report semantics."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from .historical_reports import _validate_candidate


DEFAULT_CASES = Path(__file__).resolve().parents[1] / "evals" / "semantic-regression.json"


def _candidate(case: dict[str, Any]) -> Any:
    candidate = case.get("candidate")
    if candidate is not None:
        return candidate
    task = case.get("task")
    if not isinstance(task, dict):
        return None
    project_ids = task.get("project_ids") or case.get("project_ids") or ["unassigned"]
    normalized = {
        "title": task.get("title") or "未命名事项",
        "project_ids": project_ids,
        "did": task.get("did") or [],
        "why": task.get("why") or [],
        "results": task.get("results") or [],
        "files": task.get("files") or [],
        "conclusions": task.get("conclusions") or [],
        "evidence_lines": task.get("evidence_lines") or [],
    }
    return {
        "day_summary": case.get("day_summary") or "",
        "no_activity": bool(case.get("no_activity")),
        "groups": [{
            "title": case.get("group_title") or "研发进展",
            "project_ids": project_ids,
            "tasks": [normalized],
        }],
        "plan_closure": case.get("plan_closure") or [],
        "knowledge": case.get("knowledge") or [],
        "decisions": case.get("decisions") or [],
        "blockers": case.get("blockers") or [],
        "next": case.get("next") or [],
        "data_quality": case.get("data_quality") or [],
    }


def _assertions(case: dict[str, Any], result: dict[str, Any] | None) -> list[str]:
    expected = case.get("assert")
    if not isinstance(expected, dict) or result is None:
        return []
    failures: list[str] = []
    if "task_count" in expected and result.get("task_count") != expected["task_count"]:
        failures.append(f"task_count={result.get('task_count')} expected {expected['task_count']}")
    if "no_activity" in expected and bool(result.get("no_activity")) != bool(expected["no_activity"]):
        failures.append(f"no_activity={result.get('no_activity')} expected {expected['no_activity']}")
    if "project_ids" in expected and set(result.get("project_ids") or []) != set(expected["project_ids"]):
        failures.append(f"project_ids={result.get('project_ids')} expected {expected['project_ids']}")
    tasks = [task for group in result.get("groups") or [] for task in group.get("tasks") or []]
    if "confidence" in expected:
        observed = [task.get("confidence") for task in tasks]
        if observed != expected["confidence"]:
            failures.append(f"confidence={observed} expected {expected['confidence']}")
    quality = " ".join(str(value) for value in result.get("data_quality") or [])
    for text in expected.get("quality_contains") or []:
        if str(text) not in quality:
            failures.append(f"data_quality missing {text!r}")
    return failures


def evaluate_cases(path: Path = DEFAULT_CASES) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases") if isinstance(payload, dict) else None
    if not isinstance(cases, list):
        raise ValueError("semantic eval file requires a cases list")
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="rd-semantic-eval-") as directory:
        root = Path(directory)
        for index, case in enumerate(cases):
            name = str(case.get("name") or f"case-{index + 1}")
            expected = str(case.get("expected") or "accept")
            markdown = str(case.get("markdown") or "")
            candidate = _candidate(case)
            error = ""
            accepted = False
            result: dict[str, Any] | None = None
            try:
                if not isinstance(candidate, dict):
                    raise ValueError("candidate must be an object")
                result = _validate_candidate(
                    candidate, root / f"{case.get('date') or '2026-01-01'}.md",
                    markdown.splitlines(),
                )
                accepted = bool(result.get("task_count")) or bool(result.get("no_activity"))
            except (TypeError, ValueError) as exc:
                error = str(exc)
            assertion_failures = _assertions(case, result)
            passed = (accepted if expected == "accept" else not accepted) and not assertion_failures
            results.append({
                "name": name, "expected": expected,
                "observed": "accept" if accepted else "reject",
                "passed": passed, "error": error,
                "assertion_failures": assertion_failures,
            })
    return {
        "suite": str(path), "total": len(results),
        "passed": sum(item["passed"] for item in results),
        "failed": sum(not item["passed"] for item in results),
        "results": results,
    }
