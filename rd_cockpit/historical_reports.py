"""One-time, cached normalization for legacy Markdown daily reports.

The original Markdown remains authoritative and is never overwritten.  A
model turns each report into the current readable task schema, while the
program resolves project IDs and evidence line anchors before saving a sidecar
JSON file.  Sidecars are invalidated by the source SHA256.
"""

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .runtime import daily_report_directory, executable as resolve_executable


SCHEMA_VERSION = 1
DEFAULT_REPORT_DIR = daily_report_directory()
DEFAULT_TIMEOUT = 240.0
MODEL_PRIMARY = "codex:gpt-5.6-sol@medium"
MODEL_FALLBACK = "deepseek-local"

ModelRequester = Callable[[str, str, list[str]], tuple[dict[str, Any], dict[str, Any]]]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sidecar_path(report_path: Path) -> Path:
    return report_path.parent / "data" / "normalized" / f"{report_path.stem}.json"


def _validated_audit_path(report_path: Path) -> Path:
    return report_path.parent / "data" / f"{report_path.stem}_audit_validated.json"


def _has_validated_audit(report_path: Path) -> bool:
    """Recognize reports already produced by the current evidence pipeline."""
    path = _validated_audit_path(report_path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    validation = value.get("validation") if isinstance(value, dict) else None
    return bool(
        isinstance(validation, dict)
        and value.get("report_date") == report_path.stem
        and isinstance(value.get("project_groups"), list)
        and not validation.get("warnings")
        and not validation.get("unsupported_task_count")
        and not validation.get("unverified_numbers")
        and not validation.get("missing_source_refs")
    )


def _json_object(text: str) -> dict[str, Any]:
    value = text.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value, flags=re.IGNORECASE | re.DOTALL).strip()
    try:
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for index, character in enumerate(value):
        if character != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(value[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("model output does not contain a JSON object")


def _normalization_instruction(report_date: str, lines: list[str]) -> dict[str, Any]:
    from .daily_source import project_display_names

    catalog = [{"id": key, "name": value} for key, value in project_display_names().items()]
    numbered = "\n".join(f"L{index}: {line}" for index, line in enumerate(lines, 1))
    return {
        "report_date": report_date,
        "project_catalog": catalog,
        "output_schema": {
            "no_activity": "仅当原文明说当天无记录、无实质活动时为 true，否则为 false",
            "day_summary": "2到4句中文，概括当天真正推进的主线和最重要结果",
            "groups": [{
                "title": "可读项目方向",
                "project_ids": ["只能使用 project_catalog 中的 id；无法判断时用 unassigned"],
                "tasks": [{
                    "title": "具体任务标题",
                    "project_ids": ["该任务实际所属项目；只能使用 project_catalog 中的 id"],
                    "did": ["实际动作"],
                    "why": ["明确写出的原因；原文没有则空数组"],
                    "results": ["结果、指标、测试或产物"],
                    "files": ["关键文件或产物路径"],
                    "conclusions": ["明确结论及适用范围"],
                    "evidence_lines": [[1, 3]],
                }],
            }],
            "plan_closure": ["昨日计划及完成/部分完成/阻塞/延后/无证据状态"],
            "knowledge": ["可跨会话复用的结论或经验"],
            "decisions": ["明确采用、拒绝或条件采用的决策"],
            "blockers": ["尚未解决的阻塞"],
            "next": ["下一步可执行动作"],
            "data_quality": ["原文缺失、冲突或无法确认的信息"],
        },
        "rules": [
            "只提取原文明确写出的内容，禁止补充历史事实",
            "休息日或无活动日报保留 groups=[]，设置 no_activity=true，并在 day_summary 直说无研发记录",
            "将过程合并为少量可读任务，但不同项目不得混在同一任务",
            "结果、数字、结论必须用 evidence_lines 指向原文行号",
            "用户计划、设想或待办不能写成已完成结果",
            "旧日报没有原因、结果或证据时保留空数组，并写入 data_quality",
            "ASR 必须尽量区分具身智能、方言竞赛、模型评测、歌词与对齐",
            "即使旧日报把多个项目放在同一大节，每个 task 也必须单独填写 project_ids",
            "输出纯 JSON，不要 Markdown",
        ],
        "numbered_markdown": numbered,
    }


def _request_model(model: str, report_date: str, lines: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    endpoint = os.environ.get("RD_REPORT_NORMALIZE_LLM_URL", "http://127.0.0.1:4000/v1/messages")
    instruction = _normalization_instruction(report_date, lines)
    payload = {
        "model": model,
        "max_tokens": min(24000, max(6000, len(lines) * 45)),
        "temperature": 0,
        "stream": False,
        "system": (
            "你是研发历史迁移审计器。你的任务是把旧日报规范化，而不是重写或美化历史。"
            "原始 Markdown 是唯一事实来源；任何不确定项必须保持未知。"
        ),
        "messages": [{"role": "user", "content": json.dumps(instruction, ensure_ascii=False)}],
    }
    request = Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-api-key": "local-router",
                 "anthropic-version": "2023-06-01"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=DEFAULT_TIMEOUT) as response:  # noqa: S310 - defaults to localhost router
            outer = json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc
    content = outer.get("content") if isinstance(outer, dict) else None
    text = "".join(
        str(block.get("text") or "") for block in content or []
        if isinstance(block, dict) and block.get("type") == "text"
    )
    parsed = _json_object(text)
    usage = outer.get("usage") if isinstance(outer, dict) and isinstance(outer.get("usage"), dict) else {}
    return parsed, {"model": model, "usage": usage}


def _request_codex_model(
    model_spec: str, report_date: str, lines: list[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    model_and_effort = model_spec.removeprefix("codex:")
    model, separator, reasoning = model_and_effort.partition("@")
    if not separator:
        reasoning = os.environ.get("RD_REPORT_NORMALIZE_CODEX_REASONING", "medium")
    executable = resolve_executable("RD_REPORT_NORMALIZE_CODEX_BIN", "codex")
    timeout = float(os.environ.get("RD_REPORT_NORMALIZE_CODEX_TIMEOUT", str(DEFAULT_TIMEOUT)))
    instruction = _normalization_instruction(report_date, lines)
    prompt = (
        "你是研发历史迁移审计器。原始 Markdown 是唯一事实来源。"
        "请审计标准输入的 JSON，并严格按 output_schema 返回纯 JSON；"
        "不得把计划、提问或尝试冒充结果，不确定项保持未知。"
    )
    with tempfile.TemporaryDirectory(prefix="rd-history-audit-") as temporary:
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
    return parsed, {"model": model_spec, "usage": {
        **usage, "provider": "codex-cli", "reasoning_effort": reasoning,
    }}


def _request_any_model(
    model: str, report_date: str, lines: list[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if model.startswith("codex:"):
        return _request_codex_model(model, report_date, lines)
    return _request_model(model, report_date, lines)


def _archive_deepseek_sidecar(sidecar: Path) -> Path | None:
    """Preserve the previous DeepSeek projection before a Codex replacement."""
    try:
        value = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    selected = str((value.get("model_run") or {}).get("selected_model") or "")
    if not selected.startswith("deepseek"):
        return None
    history = sidecar.parent.parent / "normalized-history" / sidecar.stem
    history.mkdir(parents=True, exist_ok=True)
    generated = str(value.get("generated_at") or "unknown").replace(":", "").replace("+", "_")
    destination = history / f"{generated}-{selected}.json"
    if not destination.exists():
        shutil.copy2(sidecar, destination)
    return destination


def _readable_string(value: Any) -> str:
    """Turn a model-produced scalar or small semantic object into prose.

    A few legacy normalizer runs returned decision objects inside an array
    whose schema expected strings.  The old implementation stringified those
    objects, leaking Python dictionaries and evidence line metadata into the
    UI.  Preserve the decision and reason while keeping structural metadata in
    the sidecar.
    """
    item = value
    if isinstance(item, str):
        text = item.strip()
        if text.startswith("{") and text.endswith("}"):
            parsed: Any = None
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                try:
                    parsed = ast.literal_eval(text)
                except (SyntaxError, ValueError):
                    pass
            if isinstance(parsed, dict):
                item = parsed
            else:
                return text
        else:
            return text
    if isinstance(item, dict):
        primary = next((str(item[key]).strip() for key in (
            "decision", "conclusion", "statement", "text", "result", "action",
            "title", "blocker", "plan", "knowledge", "summary",
        ) if item.get(key) is not None and str(item[key]).strip()), "")
        reason = str(item.get("reason") or item.get("explanation") or "").strip()
        if primary and reason and reason not in primary:
            return f"{primary}：{reason}"
        return primary
    if item is None or isinstance(item, (list, tuple, set)):
        return ""
    return str(item).strip()


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(text for item in value if (text := _readable_string(item))))


def _line_ranges(value: Any, line_count: int) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, list) or len(item) != 2:
            continue
        try:
            start, end = int(item[0]), int(item[1])
        except (TypeError, ValueError):
            continue
        start, end = max(1, min(start, end)), min(line_count, max(start, end))
        if start <= end:
            ranges.append((start, end))
    return list(dict.fromkeys(ranges))[:6]


def _matchable(text: str) -> str:
    """Normalize Markdown prose for conservative exact-substring matching."""
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text.casefold())


def _infer_line_ranges(raw_task: dict[str, Any], lines: list[str]) -> list[tuple[int, int]]:
    """Recover line anchors when a model omitted only the evidence field.

    This deliberately uses exact normalized substrings from the generated
    title/content.  It does not use semantic similarity, so a paraphrase that
    cannot be found in the source remains unsupported instead of gaining a
    fabricated citation.
    """
    needles = [
        str(raw_task.get("title") or ""),
        *_strings(raw_task.get("files")),
        *_strings(raw_task.get("results")),
        *_strings(raw_task.get("conclusions")),
        *_strings(raw_task.get("did")),
    ]
    normalized_needles = [value for item in needles if len(value := _matchable(item)) >= 5]
    matched: list[int] = []
    for index, line in enumerate(lines, 1):
        normalized_line = _matchable(line)
        if any(needle in normalized_line for needle in normalized_needles):
            matched.append(index)
    if not matched:
        return []
    # A single legacy bullet can contain an entire task; adjacent matching
    # lines usually represent its result and conclusion and belong together.
    ranges: list[tuple[int, int]] = []
    start = end = matched[0]
    for value in matched[1:]:
        if value <= end + 1:
            end = value
        else:
            ranges.append((start, end))
            start = end = value
    ranges.append((start, end))
    return ranges[:6]


def _validate_candidate(candidate: dict[str, Any], report_path: Path, lines: list[str]) -> dict[str, Any]:
    from .daily_source import _detail_project_ids, _enhanced_title, _project_ids, project_display_names

    if not isinstance(candidate.get("groups"), list):
        raise ValueError("normalization output is missing groups")
    allowed = set(project_display_names()) | {"unassigned"}
    groups: list[dict[str, Any]] = []
    all_project_ids: list[str] = []
    unsupported_tasks = 0
    for raw_group in candidate["groups"]:
        if not isinstance(raw_group, dict):
            continue
        group_title = str(raw_group.get("title") or "其他").strip()
        group_ids = [value for value in _strings(raw_group.get("project_ids"))
                     if value in allowed and value != "unassigned"]
        if not group_ids:
            group_ids = _project_ids(group_title) or ["unassigned"]
        tasks: list[dict[str, Any]] = []
        for raw_task in raw_group.get("tasks") or []:
            if not isinstance(raw_task, dict):
                continue
            task_title = str(raw_task.get("title") or "未命名事项").strip()
            ranges = _line_ranges(raw_task.get("evidence_lines"), len(lines))
            if not ranges:
                ranges = _infer_line_ranges(raw_task, lines)
            evidence = [
                f"{report_path.name}:L{start}" if start == end else f"{report_path.name}:L{start}-L{end}"
                for start, end in ranges
            ]
            task_ids = [value for value in _strings(raw_task.get("project_ids"))
                        if value in allowed and value != "unassigned"]
            task_text = " ".join([
                task_title,
                *_strings(raw_task.get("did")),
                *_strings(raw_task.get("why")),
                *_strings(raw_task.get("results")),
                *_strings(raw_task.get("files")),
                *_strings(raw_task.get("conclusions")),
            ])
            # A legacy section often contains several unrelated projects.
            # Prefer a per-task model assignment, then deterministic concrete
            # repository/path markers, before inheriting the broad group.
            task_ids = task_ids or _project_ids(task_title) or _detail_project_ids(task_text) or group_ids
            task = {
                "title": task_title,
                "project_ids": list(dict.fromkeys(task_ids)),
                "did": _strings(raw_task.get("did")),
                "why": _strings(raw_task.get("why")),
                "results": _strings(raw_task.get("results")),
                "files": _strings(raw_task.get("files")),
                "evidence": evidence,
                "conclusions": _strings(raw_task.get("conclusions")),
                "confidence": "reported" if evidence else "inferred",
            }
            if not evidence:
                unsupported_tasks += 1
            task["display_title"] = _enhanced_title(task)
            tasks.append(task)
            all_project_ids.extend(task["project_ids"])
        if tasks:
            ids = list(dict.fromkeys(project_id for task in tasks for project_id in task["project_ids"]))
            groups.append({"title": group_title, "project_ids": ids or group_ids, "tasks": tasks})

    source_text = "\n".join(lines)
    explicit_no_activity = bool(candidate.get("no_activity")) and bool(re.search(
        r"今日无记录|无实质(?:性)?(?:开发)?活动|全天无活动|没有实质活动|无工作任务", source_text,
    ))
    if lines and not groups and not explicit_no_activity:
        raise ValueError("normalization output contains no readable tasks")
    data_quality = _strings(candidate.get("data_quality"))
    if unsupported_tasks:
        data_quality.append(f"{unsupported_tasks} 个历史任务没有可用原文行号证据，按 inferred 展示。")
    return {
        "day_summary": str(candidate.get("day_summary") or "").strip(),
        "no_activity": explicit_no_activity,
        "groups": groups,
        "plan_closure": _strings(candidate.get("plan_closure")),
        "knowledge": _strings(candidate.get("knowledge")),
        "decisions": _strings(candidate.get("decisions")),
        "blockers": _strings(candidate.get("blockers")),
        "next": _strings(candidate.get("next")),
        "data_quality": list(dict.fromkeys(data_quality)),
        "task_count": sum(len(group["tasks"]) for group in groups),
        "project_ids": list(dict.fromkeys(all_project_ids)),
    }


def load_normalized(report_path: Path) -> dict[str, Any] | None:
    # Current reports already have a richer, evidence-validated structured
    # source.  Never let an older model sidecar override their native parser.
    if _has_validated_audit(report_path):
        return None
    sidecar = _sidecar_path(report_path)
    if not sidecar.exists():
        return None
    try:
        value = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        return None
    if value.get("source_sha256") != _sha256(report_path):
        return None
    return value


def normalize_report(
    report_path: Path,
    *,
    force: bool = False,
    requester: ModelRequester | None = None,
) -> dict[str, Any]:
    report_path = report_path.resolve()
    if _has_validated_audit(report_path):
        return {
            "date": report_path.stem,
            "status": "current_format",
            "path": str(_validated_audit_path(report_path)),
            "model": None,
        }
    if not force and (cached := load_normalized(report_path)):
        return {"date": report_path.stem, "status": "cached", "path": str(_sidecar_path(report_path)),
                "model": (cached.get("model_run") or {}).get("selected_model")}
    lines = report_path.read_text(encoding="utf-8", errors="replace").splitlines()
    source_hash = _sha256(report_path)
    primary = os.environ.get("RD_REPORT_NORMALIZE_MODEL", MODEL_PRIMARY).strip()
    fallback = os.environ.get("RD_REPORT_NORMALIZE_FALLBACK_MODEL", MODEL_FALLBACK).strip()
    models = list(dict.fromkeys(value for value in (primary, fallback) if value))
    request_model = requester or _request_any_model
    attempts: list[dict[str, Any]] = []
    selected: str | None = None
    normalized: dict[str, Any] | None = None
    for model in models:
        try:
            candidate, metadata = request_model(model, report_path.stem, lines)
            normalized = _validate_candidate(candidate, report_path, lines)
        except Exception as exc:
            attempts.append({"model": model, "status": "failed", "error": f"{type(exc).__name__}: {exc}"})
            continue
        selected = model
        attempts.append({"model": model, "status": "ok", "usage": metadata.get("usage", {})})
        break
    if normalized is None or selected is None:
        raise RuntimeError(f"all normalization models failed for {report_path.name}: {attempts}")
    value = {
        "schema_version": SCHEMA_VERSION,
        "report_date": report_path.stem,
        "source_path": str(report_path),
        "source_sha256": source_hash,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model_run": {"primary_model": primary, "fallback_model": fallback or None,
                      "selected_model": selected, "fallback_used": selected != primary,
                      "attempts": attempts},
        **normalized,
    }
    sidecar = _sidecar_path(report_path)
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    archived = _archive_deepseek_sidecar(sidecar) if force and sidecar.exists() else None
    temporary = sidecar.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, sidecar)
    return {"date": report_path.stem, "status": "generated", "path": str(sidecar),
            "model": selected, "tasks": normalized["task_count"],
            "archived": str(archived) if archived else None}


def apply_normalized(report: dict[str, Any], report_path: Path) -> dict[str, Any]:
    value = load_normalized(report_path)
    if not value:
        return report
    output = dict(report)
    groups = copy.deepcopy(value.get("groups", output.get("groups")) or [])
    for group in groups:
        if not isinstance(group, dict):
            continue
        for task in group.get("tasks") or []:
            if not isinstance(task, dict):
                continue
            for key in ("did", "why", "results", "files", "evidence", "conclusions"):
                task[key] = _strings(task.get(key))
    output["groups"] = groups
    for key in ("blockers", "next", "plan_closure", "knowledge", "data_quality"):
        output[key] = _strings(value.get(key, output.get(key)))
    for key in ("task_count", "project_ids"):
        output[key] = value.get(key, output.get(key))
    output["day_summary"] = value.get("day_summary") or ""
    output["no_activity"] = bool(value.get("no_activity"))
    output["decisions"] = _strings(value.get("decisions"))
    output["normalization"] = {
        "available": True,
        "generated_at": value.get("generated_at"),
        "model": (value.get("model_run") or {}).get("selected_model"),
        "fallback_used": bool((value.get("model_run") or {}).get("fallback_used")),
        "source_sha256": value.get("source_sha256"),
    }
    return output


def normalize_directory(
    report_dir: Path,
    *,
    dates: list[str] | None = None,
    force: bool = False,
    workers: int = 1,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    selected = [path for path in sorted(report_dir.glob("????-??-??.md"))
                if not dates or path.stem in dates]
    if limit is not None:
        selected = selected[:max(0, limit)]
    if workers <= 1:
        return [normalize_report(path, force=force) for path in selected]
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(normalize_report, path, force=force): path for path in selected}
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as exc:
                results.append({"date": futures[future].stem, "status": "failed",
                                "error": f"{type(exc).__name__}: {exc}"})
    return sorted(results, key=lambda item: item["date"])


def _main() -> int:
    parser = argparse.ArgumentParser(description="Normalize legacy daily reports into cached sidecar JSON")
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--date", action="append", dest="dates")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    results = normalize_directory(args.report_dir.expanduser(), dates=args.dates, force=args.force,
                                  workers=max(1, args.workers), limit=args.limit)
    summary = {
        "total": len(results),
        "generated": sum(item.get("status") == "generated" for item in results),
        "cached": sum(item.get("status") == "cached" for item in results),
        "current_format": sum(item.get("status") == "current_format" for item in results),
        "failed": sum(item.get("status") == "failed" for item in results),
        "results": results,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(_main())
