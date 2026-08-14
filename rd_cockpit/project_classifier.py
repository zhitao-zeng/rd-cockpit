"""Cached LLM classification for ambiguous daily-report project records.

Opening the UI never invokes a model.  This module batches only records left
in the ``asr_other`` fallback after deterministic path matching, asks the
configured audit model once, validates the result against registered projects, and stores reusable decisions
beside the daily reports.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifact_cache import atomic_write_json
from .model_runner import run_claude_json, run_codex_json
from .runtime import daily_report_directory


SCHEMA_VERSION = 2
PROMPT_VERSION = 2
CONFIDENCE_THRESHOLD = 0.75
DEFAULT_ENDPOINT = "http://127.0.0.1:4000/v1/messages"

def _strings(value: Any) -> list[str]:
    return [str(item).strip() for item in value or [] if str(item).strip()]


def task_fingerprint(report_date: str | None, task: dict[str, Any]) -> str:
    stable = {
        "prompt_version": PROMPT_VERSION,
        "date": report_date or "",
        "title": str(task.get("title") or ""),
        "did": _strings(task.get("did")),
        "why": _strings(task.get("why")),
        "results": _strings(task.get("results")),
        "files": _strings(task.get("files")),
        "conclusions": _strings(task.get("conclusions")),
    }
    encoded = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:20]


def _legacy_task_fingerprint(report_date: str | None, task: dict[str, Any]) -> str:
    stable = {
        "date": report_date or "", "title": str(task.get("title") or ""),
        "did": _strings(task.get("did")), "why": _strings(task.get("why")),
        "results": _strings(task.get("results")), "files": _strings(task.get("files")),
        "conclusions": _strings(task.get("conclusions")),
    }
    encoded = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:20]


def _cache_path(report_path: Path) -> Path:
    return report_path.parent / "data" / "project-classifications.json"


def _load_cache(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        value = {}
    if not isinstance(value, dict):
        return {"schema_version": SCHEMA_VERSION, "entries": {}, "runs": []}
    if value.get("schema_version") != SCHEMA_VERSION:
        legacy_entries = value.get("entries") if value.get("schema_version") == 1 else {}
        return {
            "schema_version": SCHEMA_VERSION, "entries": {},
            "legacy_entries": legacy_entries if isinstance(legacy_entries, dict) else {},
            "runs": value.get("runs") if isinstance(value.get("runs"), list) else [],
        }
    value.setdefault("entries", {})
    value.setdefault("runs", [])
    value.setdefault("legacy_entries", {})
    return value


def cached_classification(
    report_path: Path, report_date: str | None, task: dict[str, Any],
) -> dict[str, Any] | None:
    value = _load_cache(_cache_path(report_path))
    item = value["entries"].get(task_fingerprint(report_date, task))
    if not isinstance(item, dict):
        return None
    from .project_identity import registered_project_names
    from .semantic_policy import catalog_fingerprint, policy_fingerprint

    allowed = registered_project_names()
    expected = policy_fingerprint(
        "daily-report-project-classification",
        schema_version=SCHEMA_VERSION,
        prompt_version=PROMPT_VERSION,
        models=(
            os.environ.get("RD_PROJECT_CLASSIFY_MODEL", "codex:gpt-5.6-sol@medium"),
            os.environ.get("RD_PROJECT_CLASSIFY_FALLBACK_MODEL", "deepseek-local"),
        ),
        extra={"catalog": catalog_fingerprint(allowed)},
    )
    return item if item.get("policy_fingerprint") == expected else None


def _json_object(text: str) -> dict[str, Any]:
    value = text.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value,
                       flags=re.IGNORECASE | re.DOTALL).strip()
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("model output must be a JSON object")
    return parsed


def _instruction(records: list[dict[str, Any]], allowed: dict[str, str]) -> dict[str, Any]:
    catalog = [
        {"id": project_id, "name": name,
         "scope": f"Only choose this project when the report evidence clearly belongs to {name}."}
        for project_id, name in allowed.items()
    ]
    return {
        "task": "Assign each unclassified daily-report record to the most accurate single project.",
        "project_catalog": catalog,
        "records": records,
        "output_schema": {"classifications": [{
            "key": "return the input key unchanged", "project_id": "one id from project_catalog",
            "confidence": "number from 0 to 1", "reason": "one concise reason",
        }]},
        "rules": [
            "Use the complete paragraph and concrete file paths, not a single keyword.",
            "Do not invent a project or relationship absent from the input.",
            "Keep ambiguous records unassigned when the evidence is insufficient.",
            "Return JSON only and do not omit or add keys.",
        ],
    }


def _request(model: str, records: list[dict[str, Any]], allowed: dict[str, str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    instruction = _instruction(records, allowed)
    parsed, metadata = run_claude_json(
        model, instruction, prompt="你是研发日报项目分类审计器。只分类，不总结，不补充事实。只返回 JSON。",
        executable_env="RD_PROJECT_CLASSIFY_CLAUDE_BIN",
        timeout_env="RD_PROJECT_CLASSIFY_TIMEOUT", default_timeout=180,
        run_context={
            "home": os.environ.get("RD_COCKPIT_HOME"), "stage": "classification",
            "source_hash": hashlib.sha256(
                json.dumps(instruction, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest(), "fallback_used": True,
            "reason": "出现无法通过路径规则归类的日报段落。",
        },
    )
    items = parsed.get("classifications")
    if not isinstance(items, list):
        raise ValueError("model output is missing classifications")
    return items, {**(metadata.get("usage") or {}), "provider": metadata.get("provider")}


def _request_codex(
    model_spec: str, records: list[dict[str, Any]], allowed: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    prompt = "对标准输入中的 JSON 执行项目分类。严格按输入的 output_schema 返回纯 JSON。"
    instruction = _instruction(records, allowed)
    parsed, metadata = run_codex_json(
        model_spec, instruction, prompt=prompt, executable_env="RD_PROJECT_CLASSIFY_CODEX_BIN",
        timeout_env="RD_PROJECT_CLASSIFY_CODEX_TIMEOUT", default_timeout=240,
        reasoning_env="RD_PROJECT_CLASSIFY_CODEX_REASONING",
        workdir=Path(__file__).resolve().parents[1], temp_prefix="rd-project-classifier-",
        run_context={
            "home": os.environ.get("RD_COCKPIT_HOME"), "stage": "classification",
            "source_hash": hashlib.sha256(
                json.dumps(instruction, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest(),
            "reason": "出现无法通过路径规则归类的日报段落。",
        },
    )
    items = parsed.get("classifications")
    if not isinstance(items, list):
        raise ValueError("Codex output is missing classifications")
    return items, {**metadata.get("usage", {}),
                   "reasoning_effort": metadata["reasoning_effort"],
                   "provider": metadata["provider"]}


def _request_model(
    model: str, records: list[dict[str, Any]], allowed: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if model.startswith("codex:"):
        return _request_codex(model, records, allowed)
    return _request(model, records, allowed)


def classify_directory(report_dir: Path, *, force: bool = False) -> dict[str, Any]:
    from .daily_source import parse_report
    from .project_identity import registered_project_names
    from .semantic_policy import catalog_fingerprint, policy_fingerprint

    cache_path = report_dir / "data" / "project-classifications.json"
    cache = _load_cache(cache_path)
    allowed = registered_project_names()
    primary = os.environ.get(
        "RD_PROJECT_CLASSIFY_MODEL", "codex:gpt-5.6-sol@medium",
    ).strip()
    fallback = os.environ.get("RD_PROJECT_CLASSIFY_FALLBACK_MODEL", "deepseek-local").strip()
    policy = policy_fingerprint(
        "daily-report-project-classification",
        schema_version=SCHEMA_VERSION,
        prompt_version=PROMPT_VERSION,
        models=(primary, fallback),
        extra={"catalog": catalog_fingerprint(allowed)},
    )
    pending: list[dict[str, Any]] = []
    migrated = 0
    for report_path in sorted(report_dir.glob("????-??-??.md")):
        report = parse_report(report_path, apply_project_cache=not force)
        for group in report.get("groups") or []:
            for task in group.get("tasks") or []:
                if task.get("project_ids") != ["asr_other"]:
                    continue
                key = task_fingerprint(report.get("date"), task)
                if (
                    not force and key in cache["entries"]
                    and (cache["entries"].get(key) or {}).get("policy_fingerprint") == policy
                ):
                    continue
                legacy = None if force else (cache.get("legacy_entries") or {}).get(
                    _legacy_task_fingerprint(report.get("date"), task),
                )
                if isinstance(legacy, dict) and str(legacy.get("project_id") or "") in allowed:
                    cache["entries"][key] = {
                        **legacy, "policy_fingerprint": policy, "prompt_version": PROMPT_VERSION,
                        "cache_migration": {"from_schema": 1, "model_call": False},
                    }
                    migrated += 1
                    continue
                pending.append({
                    "key": key, "date": report.get("date"), "title": task.get("title"),
                    "did": _strings(task.get("did")), "why": _strings(task.get("why")),
                    "results": _strings(task.get("results")), "files": _strings(task.get("files")),
                    "conclusions": _strings(task.get("conclusions")),
                })
    if not pending:
        if migrated:
            cache["legacy_entries"] = {}
            atomic_write_json(cache_path, cache)
        return {"status": "migrated" if migrated else "cached", "pending": 0,
                "classified": 0, "migrated": migrated, "path": str(cache_path)}

    models = list(dict.fromkeys(model for model in (primary, fallback) if model))
    attempts: list[dict[str, Any]] = []
    selected: str | None = None
    classifications: list[dict[str, Any]] = []
    for model in models:
        try:
            classifications, usage = _request_model(model, pending, allowed)
        except Exception as exc:
            attempts.append({"model": model, "status": "failed", "error": f"{type(exc).__name__}: {exc}"})
            continue
        selected = model
        attempts.append({"model": model, "status": "ok", "usage": usage})
        break
    if selected is None:
        raise RuntimeError(f"all project classification models failed: {attempts}")

    expected = {item["key"] for item in pending}
    stored = 0
    for item in classifications:
        if not isinstance(item, dict) or item.get("key") not in expected:
            continue
        project_id = str(item.get("project_id") or "")
        try:
            confidence = min(1.0, max(0.0, float(item.get("confidence", 0))))
        except (TypeError, ValueError):
            confidence = 0.0
        if project_id not in allowed:
            continue
        cache["entries"][item["key"]] = {
            "project_id": project_id, "confidence": confidence,
            "reason": str(item.get("reason") or "").strip(), "model": selected,
            "policy_fingerprint": policy, "prompt_version": PROMPT_VERSION,
            "classified_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        stored += 1
    cache["runs"].append({
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "primary_model": primary, "selected_model": selected,
        "fallback_used": selected != primary, "records": len(pending), "stored": stored,
        "attempts": attempts,
    })
    cache["legacy_entries"] = {}
    atomic_write_json(cache_path, cache)
    return {"status": "generated", "pending": len(pending), "classified": stored,
            "migrated": migrated, "model": selected, "path": str(cache_path),
            "attempts": attempts}


def main() -> None:
    parser = argparse.ArgumentParser(description="Classify cached ambiguous daily-report projects")
    parser.add_argument("--report-dir", type=Path, default=daily_report_directory())
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    print(json.dumps(classify_directory(args.report_dir.expanduser(), force=args.force),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
