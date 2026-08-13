"""Cached LLM classification for ambiguous daily-report project records.

Opening the UI never invokes a model.  This module batches only records left
in the ``asr_other`` fallback after deterministic path matching, asks the
local DeepSeek route once, validates the result, and stores reusable decisions
beside the daily reports.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .runtime import daily_report_directory, executable as resolve_executable


SCHEMA_VERSION = 1
CONFIDENCE_THRESHOLD = 0.75
DEFAULT_ENDPOINT = "http://127.0.0.1:4000/v1/messages"

def _strings(value: Any) -> list[str]:
    return [str(item).strip() for item in value or [] if str(item).strip()]


def task_fingerprint(report_date: str | None, task: dict[str, Any]) -> str:
    stable = {
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


def _cache_path(report_path: Path) -> Path:
    return report_path.parent / "data" / "project-classifications.json"


def _load_cache(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        value = {}
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        return {"schema_version": SCHEMA_VERSION, "entries": {}, "runs": []}
    value.setdefault("entries", {})
    value.setdefault("runs", [])
    return value


def cached_classification(
    report_path: Path, report_date: str | None, task: dict[str, Any],
) -> dict[str, Any] | None:
    value = _load_cache(_cache_path(report_path))
    item = value["entries"].get(task_fingerprint(report_date, task))
    return item if isinstance(item, dict) else None


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
    payload = {
        "model": model, "max_tokens": max(2000, len(records) * 180),
        "temperature": 0, "stream": False,
        "system": "你是研发日报项目分类审计器。只分类，不总结，不补充事实。",
        "messages": [{"role": "user", "content": json.dumps(instruction, ensure_ascii=False)}],
    }
    endpoint = os.environ.get("RD_PROJECT_CLASSIFY_LLM_URL", DEFAULT_ENDPOINT)
    request = Request(endpoint, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                      headers={"Content-Type": "application/json", "x-api-key": "local-router",
                               "anthropic-version": "2023-06-01"}, method="POST")
    try:
        with urlopen(request, timeout=float(os.environ.get("RD_PROJECT_CLASSIFY_TIMEOUT", "180"))) as response:  # noqa: S310
            outer = json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc
    text = "".join(str(block.get("text") or "") for block in outer.get("content") or []
                   if isinstance(block, dict) and block.get("type") == "text")
    parsed = _json_object(text)
    items = parsed.get("classifications")
    if not isinstance(items, list):
        raise ValueError("model output is missing classifications")
    return items, outer.get("usage") or {}


def _request_codex(
    model_spec: str, records: list[dict[str, Any]], allowed: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    model_and_effort = model_spec.removeprefix("codex:")
    model, separator, reasoning = model_and_effort.partition("@")
    if not separator:
        reasoning = os.environ.get("RD_PROJECT_CLASSIFY_CODEX_REASONING", "medium")
    executable = resolve_executable("RD_PROJECT_CLASSIFY_CODEX_BIN", "codex")
    prompt = "对标准输入中的 JSON 执行项目分类。严格按输入的 output_schema 返回纯 JSON。"
    instruction = _instruction(records, allowed)
    timeout = float(os.environ.get("RD_PROJECT_CLASSIFY_CODEX_TIMEOUT", "240"))
    with tempfile.TemporaryDirectory(prefix="rd-project-classifier-") as temporary:
        message_path = Path(temporary) / "last-message.json"
        command = [
            executable, "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules",
            "--skip-git-repo-check", "--sandbox", "read-only", "--model", model,
            "-c", 'model_provider="openai"',
            "-c", f'model_reasoning_effort="{reasoning}"',
            "-C", str(Path(__file__).resolve().parents[1]), "--json",
            "--output-last-message", str(message_path), prompt,
        ]
        try:
            completed = subprocess.run(
                command, input=json.dumps(instruction, ensure_ascii=False), capture_output=True,
                text=True, timeout=timeout, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"Codex timed out after {timeout:g}s") from exc
        except OSError as exc:
            raise RuntimeError(f"could not start Codex: {exc}") from exc
        if completed.returncode:
            detail = completed.stderr.strip().splitlines()
            suffix = f": {detail[-1][:300]}" if detail else ""
            raise RuntimeError(f"Codex exited with {completed.returncode}{suffix}")
        try:
            parsed = _json_object(message_path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError(f"invalid Codex structured output: {exc}") from exc
        usage: dict[str, Any] = {}
        for line in completed.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
                usage = event["usage"]
    items = parsed.get("classifications")
    if not isinstance(items, list):
        raise ValueError("Codex output is missing classifications")
    return items, {**usage, "reasoning_effort": reasoning, "provider": "codex-cli"}


def _request_model(
    model: str, records: list[dict[str, Any]], allowed: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if model.startswith("codex:"):
        return _request_codex(model, records, allowed)
    return _request(model, records, allowed)


def classify_directory(report_dir: Path, *, force: bool = False) -> dict[str, Any]:
    from .daily_source import parse_report, project_display_names

    cache_path = report_dir / "data" / "project-classifications.json"
    cache = _load_cache(cache_path)
    pending: list[dict[str, Any]] = []
    for report_path in sorted(report_dir.glob("????-??-??.md")):
        report = parse_report(report_path, apply_project_cache=not force)
        for group in report.get("groups") or []:
            for task in group.get("tasks") or []:
                if task.get("project_ids") != ["asr_other"]:
                    continue
                key = task_fingerprint(report.get("date"), task)
                if not force and key in cache["entries"]:
                    continue
                pending.append({
                    "key": key, "date": report.get("date"), "title": task.get("title"),
                    "did": _strings(task.get("did")), "why": _strings(task.get("why")),
                    "results": _strings(task.get("results")), "files": _strings(task.get("files")),
                    "conclusions": _strings(task.get("conclusions")),
                })
    if not pending:
        return {"status": "cached", "pending": 0, "classified": 0, "path": str(cache_path)}

    allowed = project_display_names()
    primary = os.environ.get(
        "RD_PROJECT_CLASSIFY_MODEL", "codex:gpt-5.6-sol@medium",
    ).strip()
    fallback = os.environ.get("RD_PROJECT_CLASSIFY_FALLBACK_MODEL", "deepseek-local").strip()
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
            "classified_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        stored += 1
    cache["runs"].append({
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "primary_model": primary, "selected_model": selected,
        "fallback_used": selected != primary, "records": len(pending), "stored": stored,
        "attempts": attempts,
    })
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, cache_path)
    return {"status": "generated", "pending": len(pending), "classified": stored,
            "model": selected, "path": str(cache_path), "attempts": attempts}


def main() -> None:
    parser = argparse.ArgumentParser(description="Classify cached ambiguous daily-report projects")
    parser.add_argument("--report-dir", type=Path, default=daily_report_directory())
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    print(json.dumps(classify_directory(args.report_dir.expanduser(), force=args.force),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
