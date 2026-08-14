"""Evidence-bound, readable experiment records derived from Daily Reports.

The Daily Report is authoritative.  Agent/usage collectors are deliberately
limited to an effort annotation and session references already present in the
report; neither a Git event nor a token counter becomes an experiment by
itself.  Model extraction is an explicit, cached offline operation.  API reads
never invoke a model.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .daily_audit import _numbers
from .daily_source import available_report_dates, parse_report, project_display_names, report_directory
from .daily_supplement import _read as _read_supplement_source
from .daily_supplement import _session_project
from .intelligence_backfill import (
    DEFAULT_FALLBACK,
    DEFAULT_MODEL,
    _record,
    _refs,
    _request_any_model,
    _strings,
    _write_json,
)


SCHEMA_VERSION = 1
PROMPT_VERSION = 1
DEFAULT_PROJECTS = ("obstacle", "ocr", "asr", "asr_dialect", "asr_model_eval")
KINDS = {"experiment", "benchmark", "evaluation", "ablation", "training", "deployment_validation"}
STATUSES = {"improved", "regressed", "mixed", "failed", "inconclusive", "validated", "observed"}
DIRECTIONS = {"higher", "lower", "target", "unknown"}
EXPERIMENT_MARKERS = re.compile(
    r"实验|评测|benchmark|基准|消融|ablation|训练|微调|对比|比较|验证|回归|"
    r"CER|WER|F1|RTF|准确率|召回率|精度|得分|score|延迟|耗时|吞吐|显存|"
    r"checkpoint|模型选型|参数扫描|压力测试|Judge",
    re.IGNORECASE,
)
ORDINARY_ONLY = re.compile(r"语法检查|编译检查|lint|格式化|单元测试", re.IGNORECASE)
SESSION_REF = re.compile(r"session:([A-Za-z0-9_.:-]+)")


def _source_digest(records: list[dict[str, Any]], projects: list[str]) -> str:
    value = {
        "schema": SCHEMA_VERSION,
        "prompt": PROMPT_VERSION,
        "projects": projects,
        "sources": [(item["date"], item["source_sha256"]) for item in records],
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _task_text(task: dict[str, Any]) -> str:
    return " ".join(
        str(value)
        for key in ("title", "display_title", "did", "why", "results", "conclusions")
        for value in ([task.get(key)] if isinstance(task.get(key), str) else task.get(key) or [])
    )


def _candidate_projects(path: Path, allowed: set[str]) -> list[str]:
    """Return only projects with a result-bearing experimental task that day."""
    parsed = parse_report(path)
    found: set[str] = set()
    for group in parsed.get("groups") or []:
        for task in group.get("tasks") or []:
            project_ids = set(task.get("project_ids") or []) & allowed
            if not project_ids:
                continue
            text = _task_text(task)
            has_result = bool(task.get("results") or task.get("conclusions"))
            # A current audited report can have a long result paragraph in a
            # correctly parsed ``results`` field.  Legacy normalized reports
            # expose the same semantics through their sidecar.
            if has_result and EXPERIMENT_MARKERS.search(text):
                if ORDINARY_ONLY.search(text) and not re.search(
                    r"指标|模型|数据集|端到端|压力|性能|延迟|CER|WER|F1|score|Judge", text, re.I
                ):
                    continue
                found.update(project_ids)
    return sorted(found)


def _instruction(records: list[dict[str, Any]], candidates: dict[str, list[str]]) -> dict[str, Any]:
    names = project_display_names()
    selected = sorted({project_id for values in candidates.values() for project_id in values})
    return {
        "project_catalog": [{"id": key, "name": names.get(key, key)} for key in selected],
        "days": [
            {
                "date": item["date"],
                "candidate_projects": candidates[item["date"]],
                "numbered_markdown": item["numbered_markdown"],
            }
            for item in records
        ],
        "output_schema": {
            "days": [{
                "date": "YYYY-MM-DD",
                "experiments": [{
                    "project_id": "exactly one candidate project id",
                    "title": "what was evaluated, in readable Chinese",
                    "kind": "experiment|benchmark|evaluation|ablation|training|deployment_validation",
                    "question": "question or hypothesis; empty when absent",
                    "method": "what changed or how the comparison was run",
                    "models": [{"name": "model/checkpoint", "role": "candidate|baseline|teacher|runtime|unknown"}],
                    "datasets": [{"name": "dataset/case set", "scope": "version, split or scenario"}],
                    "parameters": [{"name": "parameter", "value": "exact reported value"}],
                    "metrics": [{"name": "metric", "value": "exact reported value", "unit": "unit or empty", "scope": "comparable evaluation scope", "direction": "higher|lower|target|unknown"}],
                    "result_status": "improved|regressed|mixed|failed|inconclusive|validated|observed",
                    "result_summary": "what actually happened",
                    "conclusion": "the supported judgment and its scope; empty when absent",
                    "decision_impact": "adopt/reject/keep/next evidence; empty when absent",
                    "verification_scope": "local|docker|jetson|judge|offline|cross_machine|unknown",
                    "machine": "reported machine or empty",
                    "commit_sha": "reported commit or empty",
                    "artifacts": ["reported result file or artifact"],
                    "evidence": ["report:DATE:Lx-Ly"],
                }],
            }],
        },
        "rules": [
            "Daily Report Markdown is the only fact source. Do not use model memory or repository assumptions.",
            "Return only actual evaluation, benchmark, training, ablation, experiment, or deployment validation with a reported result.",
            "Ordinary coding, Git changes, builds, lint and unit tests alone are not experiments.",
            "One record is one coherent research comparison or validation. Group a sweep; do not turn every run into a record.",
            "Do not merge different projects. Every record has exactly one candidate project_id.",
            "Missing model, dataset, parameter, metric, conclusion or commit stays empty; never infer it.",
            "Every number and every factual result must be supported by exact same-day evidence lines.",
            "Use a narrow metric scope so incompatible datasets, hardware and protocols are never compared.",
            "At most six records per project per day. Return empty experiments when there is no qualifying result.",
            "Return JSON only.",
        ],
    }


def _objects(value: Any, *, limit: int = 20) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value[:limit] if isinstance(item, dict)]


def _clean_objects(value: Any, fields: tuple[str, ...], *, limit: int = 20) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    for item in _objects(value, limit=limit):
        cleaned = {field: str(item.get(field) or "").strip() for field in fields}
        if cleaned[fields[0]]:
            output.append(cleaned)
    return output


def _stable_id(day: str, project_id: str, title: str, result: str) -> str:
    normalized = re.sub(r"[^\w\u4e00-\u9fff]+", "", f"{title}|{result}".casefold())
    digest = hashlib.sha1(f"{day}|{project_id}|{normalized}".encode()).hexdigest()[:14]
    return f"exp:{project_id}:{day}:{digest}"


def _validate_day(
    raw: dict[str, Any], record: dict[str, Any], candidates: set[str], metadata: dict[str, Any],
) -> dict[str, Any]:
    if str(raw.get("date") or "") != record["date"]:
        raise ValueError(f"{record['date']}: model output omitted or changed the date")
    output: list[dict[str, Any]] = []
    errors: list[str] = []
    per_project: Counter[str] = Counter()
    for index, item in enumerate(raw.get("experiments") or []):
        label = f"{record['date']}:experiments[{index}]"
        try:
            if not isinstance(item, dict):
                raise ValueError("record must be an object")
            project_id = str(item.get("project_id") or "").strip()
            if project_id not in candidates:
                raise ValueError(f"project {project_id!r} is not a candidate for this day")
            if per_project[project_id] >= 6:
                raise ValueError("more than six records for one project/day")
            refs, cited, rejected = _refs(item.get("evidence"), record, project_id, label)
            title = str(item.get("title") or "").strip()
            result = str(item.get("result_summary") or "").strip()
            method = str(item.get("method") or "").strip()
            if not title or not result or not method:
                raise ValueError("title, method and result_summary are required")
            kind = str(item.get("kind") or "evaluation").strip().casefold()
            status = str(item.get("result_status") or "observed").strip().casefold()
            if kind not in KINDS:
                raise ValueError(f"invalid kind {kind}")
            if status not in STATUSES:
                raise ValueError(f"invalid result_status {status}")
            models = _clean_objects(item.get("models"), ("name", "role"), limit=12)
            datasets = _clean_objects(item.get("datasets"), ("name", "scope"), limit=12)
            parameters = _clean_objects(item.get("parameters"), ("name", "value"), limit=30)
            metrics = _clean_objects(item.get("metrics"), ("name", "value", "unit", "scope", "direction"), limit=30)
            for metric in metrics:
                if metric["direction"] not in DIRECTIONS:
                    metric["direction"] = "unknown"
            artifacts = _strings(item.get("artifacts"))[:30]
            scalar_fields = {
                key: str(item.get(key) or "").strip()
                for key in ("question", "conclusion", "decision_impact", "verification_scope", "machine", "commit_sha")
            }
            numeric_text = [title, method, result, *scalar_fields.values(), *artifacts]
            numeric_text.extend(value for row in [*models, *datasets, *parameters, *metrics] for value in row.values())
            missing = sorted(number for number in _numbers(numeric_text) if number not in cited)
            if missing:
                raise ValueError(f"unsupported numbers: {', '.join(missing)}")
            session_ids = list(dict.fromkeys(SESSION_REF.findall(cited)))
            normalized = {
                "record_id": _stable_id(record["date"], project_id, title, result),
                "project_id": project_id,
                "date": record["date"],
                "title": title,
                "kind": kind,
                "question": scalar_fields["question"],
                "method": method,
                "models": models,
                "datasets": datasets,
                "parameters": parameters,
                "metrics": metrics,
                "result_status": status,
                "result_summary": result,
                "conclusion": scalar_fields["conclusion"],
                "decision_impact": scalar_fields["decision_impact"],
                "verification_scope": scalar_fields["verification_scope"] or "unknown",
                "machine": scalar_fields["machine"],
                "commit_sha": scalar_fields["commit_sha"],
                "artifacts": artifacts,
                "session_ids": session_ids,
                "evidence": refs,
                "confidence": "reported",
                "source_mode": "daily_report_audited",
            }
            output.append(normalized)
            per_project[project_id] += 1
            errors.extend(f"{label}: removed {reason}" for reason in rejected)
        except ValueError as exc:
            errors.append(f"{label}: removed item: {exc}")
    return {
        "schema_version": SCHEMA_VERSION,
        "prompt_version": PROMPT_VERSION,
        "date": record["date"],
        "source_path": str(record["path"]),
        "source_sha256": record["source_sha256"],
        "experiments": output,
        "validation_errors": errors,
        "model_run": metadata,
    }


def _sidecar(root: Path, day: str) -> Path:
    return root / "data" / "experiments" / f"{day}.json"


def _cached_day(
    root: Path, existing: dict[str, Any], record: dict[str, Any],
    selected_projects: list[str], candidate_projects: list[str],
    policy: str, models: tuple[str, ...],
) -> dict[str, Any] | None:
    if (
        existing.get("source_sha256") != record["source_sha256"]
        or existing.get("selected_projects") != selected_projects
    ):
        return None
    if (
        existing.get("schema_version") == SCHEMA_VERSION
        and existing.get("prompt_version") == PROMPT_VERSION
        and existing.get("policy_fingerprint") == policy
    ):
        return existing
    metadata = existing.get("model_run") if isinstance(existing.get("model_run"), dict) else {}
    deterministic = metadata.get("provider") == "deterministic" and not candidate_projects
    if not deterministic and str(metadata.get("model") or "") not in models:
        return None
    try:
        upgraded = _validate_day(
            existing, record, set(candidate_projects),
            {**metadata, "policy_fingerprint": policy, "prompt_version": PROMPT_VERSION},
        )
    except ValueError:
        return None
    removed = max(
        0, len(existing.get("experiments") or []) - len(upgraded.get("experiments") or []),
    )
    upgraded.update({
        "policy_fingerprint": policy,
        "selected_projects": selected_projects,
        "candidate_projects": candidate_projects,
        "cache_migration": {
            "from_schema": existing.get("schema_version"), "model_call": False,
            "removed_unsupported_records": removed,
        },
    })
    _write_json(_sidecar(root, record["date"]), upgraded)
    return upgraded


def backfill(
    *, directory: Path | None = None, days: int = 90, batch_days: int = 7,
    projects: list[str] | None = None, model: str = DEFAULT_MODEL,
    fallback_model: str = DEFAULT_FALLBACK, force: bool = False,
    target: date | None = None,
) -> dict[str, Any]:
    root = directory or report_directory()
    target = target or date.today()
    selected_projects = list(dict.fromkeys(projects or DEFAULT_PROJECTS))
    from .semantic_policy import policy_fingerprint
    policy = policy_fingerprint(
        "experiment-intelligence-backfill",
        schema_version=SCHEMA_VERSION,
        prompt_version=PROMPT_VERSION,
        models=(model, fallback_model),
        extra={"projects": selected_projects},
    )
    policy_models = tuple(value for value in (model, fallback_model) if value)
    valid_projects = set(project_display_names())
    unknown = sorted(set(selected_projects) - valid_projects)
    if unknown:
        raise ValueError(f"unknown projects: {', '.join(unknown)}")
    since = (target - timedelta(days=max(1, days) - 1)).isoformat()
    report_dates = [value for value in available_report_dates(root) if since <= value <= target.isoformat()]
    candidates: dict[str, list[str]] = {}
    records: list[dict[str, Any]] = []
    cached: list[str] = []
    skipped: list[str] = []
    for day in report_dates:
        path = root / f"{day}.md"
        project_candidates = _candidate_projects(path, set(selected_projects))
        candidates[day] = project_candidates
        record = _record(path)
        sidecar = _sidecar(root, day)
        existing: dict[str, Any] = {}
        try:
            existing = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.exists() else {}
        except (OSError, json.JSONDecodeError):
            pass
        accepted_cache = None if force else _cached_day(
            root, existing, record, selected_projects, project_candidates,
            policy, policy_models,
        )
        cache_ok = accepted_cache is not None and (
            # A fallback result is usable immediately, but a later healthy
            # primary model should automatically replace it. Deterministic
            # empty days never need a model retry.
            (accepted_cache.get("model_run") or {}).get("model") == model
            or (accepted_cache.get("model_run") or {}).get("provider") == "deterministic"
        )
        if cache_ok:
            cached.append(day)
        elif not project_candidates:
            value = {
                "schema_version": SCHEMA_VERSION, "prompt_version": PROMPT_VERSION,
                "policy_fingerprint": policy,
                "date": day, "source_path": str(path), "source_sha256": record["source_sha256"],
                "selected_projects": selected_projects, "candidate_projects": [], "experiments": [],
                "validation_errors": [], "model_run": {"provider": "deterministic", "model": None},
            }
            _write_json(sidecar, value)
            skipped.append(day)
        else:
            records.append(record)

    processed: list[str] = []
    failed: list[dict[str, str]] = []
    calls = 0
    for offset in range(0, len(records), max(1, batch_days)):
        batch = records[offset:offset + max(1, batch_days)]
        instruction = _instruction(batch, candidates)
        accepted = False
        errors: list[dict[str, str]] = []
        for selected_model in list(dict.fromkeys(value for value in (model, fallback_model) if value)):
            calls += 1
            try:
                raw, metadata = _request_any_model(selected_model, instruction, stage="experiments")
                metadata = {**(metadata or {}), "policy_fingerprint": policy,
                            "prompt_version": PROMPT_VERSION}
                by_date = {str(item.get("date")): item for item in raw.get("days", []) if isinstance(item, dict)}
                values = [
                    _validate_day(by_date.get(item["date"], {}), item, set(candidates[item["date"]]), metadata)
                    for item in batch
                ]
                for value in values:
                    value["policy_fingerprint"] = policy
                    value["selected_projects"] = selected_projects
                    value["candidate_projects"] = candidates[value["date"]]
                    _write_json(_sidecar(root, value["date"]), value)
                    processed.append(value["date"])
                accepted = True
                print(
                    f"[{batch[0]['date']}..{batch[-1]['date']}] accepted {len(batch)} report(s) via {metadata.get('model')}",
                    file=sys.stderr, flush=True,
                )
                break
            except (RuntimeError, ValueError, OSError, json.JSONDecodeError) as exc:
                errors.append({"model": selected_model, "error": str(exc)})
                print(f"[{batch[0]['date']}..{batch[-1]['date']}] {selected_model} rejected: {exc}",
                      file=sys.stderr, flush=True)
        if not accepted:
            message = "; ".join(item["error"] for item in errors)
            failed.extend({"date": item["date"], "error": message} for item in batch)
    result = {
        "directory": str(root), "days": days, "report_count": len(report_dates),
        "candidate_report_count": len(records), "processed": processed, "cached": cached,
        "deterministic_empty": skipped, "failed": failed, "model_calls": calls,
        "models": {"primary": model, "fallback": fallback_model or None},
        "projects": selected_projects, "target": target.isoformat(), "since": since,
        "source_digest": _source_digest([_record(root / f"{day}.md") for day in report_dates], selected_projects),
    }
    _write_json(root / "data" / "experiment_backfill_status.json", result)
    return result


def _usage_pools(root: Path) -> dict[tuple[str, str], dict[str, Any]]:
    """Turn cumulative Session counters into project/day increments.

    Codex can keep one Session open for days and the daily collector can emit
    several fragments carrying progressively larger cumulative counters.  A
    raw sum double-counts the full context repeatedly.  Walk all report dates
    chronologically and attribute only the positive counter delta.  Project
    attribution remains an estimate when one Session spans several projects,
    which is stated explicitly in the returned note.
    """
    previous: dict[tuple[str, str], int] = {}
    session_days: dict[tuple[str, str], set[str]] = defaultdict(set)
    projects_per_session_day: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    values: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"total_tokens": 0, "codex_tokens": 0, "claude_tokens": 0, "sessions": set(),
                 "counter_regressions": 0, "multi_project_sessions": set()}
    )
    for day in available_report_dates(root):
        for agent, suffix, field in (
            ("codex", "_codex_sessions.json", "codex_tokens"),
            ("claude", "_sessions.json", "claude_tokens"),
        ):
            source = _read_supplement_source(root / "data" / f"{day}{suffix}")
            for index, session in enumerate(source.get("sessions") or []):
                if not isinstance(session, dict):
                    continue
                usage = session.get("token_usage") or {}
                total = int(usage.get("total_tokens", 0) or 0)
                if not usage.get("available") or total <= 0:
                    continue
                session_id = str(session.get("session_id") or f"anonymous:{day}:{index}")
                session_key = (agent, session_id)
                project_id = _session_project(session)
                projects_per_session_day[(agent, session_id, day)].add(project_id)
                before = previous.get(session_key, 0)
                delta = max(0, total - before)
                previous[session_key] = max(before, total)
                session_days[session_key].add(day)
                pool = values[(day, project_id)]
                if total < before:
                    pool["counter_regressions"] += 1
                if delta:
                    pool["total_tokens"] += delta
                    pool[field] += delta
                    pool["sessions"].add(session_key)
    for (agent, session_id, day), projects in projects_per_session_day.items():
        if len(projects) > 1:
            for project_id in projects:
                values[(day, project_id)]["multi_project_sessions"].add((agent, session_id))
    output: dict[tuple[str, str], dict[str, Any]] = {}
    for key, value in values.items():
        long_sessions = sum(len(session_days[session]) > 1 for session in value["sessions"])
        estimated = bool(value["counter_regressions"] or value["multi_project_sessions"] or long_sessions)
        output[key] = {
            "total_tokens": value["total_tokens"],
            "codex_tokens": value["codex_tokens"],
            "claude_tokens": value["claude_tokens"],
            "sessions": len(value["sessions"]),
            "attribution": "project_day_delta" if value["total_tokens"] else "unavailable",
            "quality": "estimated" if estimated else "counter_delta",
            "long_sessions": long_sessions,
            "counter_regressions": value["counter_regressions"],
            "note": (
                "按每个 Agent Session 的累计计数器计算当天正增量，含缓存输入；这是项目日共享池，不是单实验独占成本。"
                "跨日或跨项目长 Session 只能近似归属。"
                if value["total_tokens"] else "当天没有可归属到该项目的 Agent Token 增量。"
            ),
        }
    return output


def _empty_token_pool() -> dict[str, Any]:
    return {
        "total_tokens": 0, "codex_tokens": 0, "claude_tokens": 0, "sessions": 0,
        "attribution": "unavailable", "quality": "unavailable", "long_sessions": 0,
        "counter_regressions": 0, "note": "当天没有可归属到该项目的 Agent Token 增量。",
    }


def _metric_groups(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        for metric in record.get("metrics") or []:
            key = (
                record["project_id"], str(metric.get("name") or ""), str(metric.get("unit") or ""),
                str(metric.get("scope") or "未注明口径"),
            )
            raw = str(metric.get("value") or "")
            match = re.search(r"-?\d+(?:\.\d+)?", raw)
            if not key[1] or not match:
                continue
            groups[key].append({
                "date": record["date"], "value": float(match.group()), "display_value": raw,
                "record_id": record["record_id"], "title": record["title"],
            })
    output = []
    for (project_id, name, unit, scope), points in groups.items():
        points.sort(key=lambda item: (item["date"], item["record_id"]))
        output.append({"project_id": project_id, "name": name, "unit": unit, "scope": scope, "points": points})
    return sorted(output, key=lambda item: (-len(item["points"]), item["project_id"], item["name"]))


def experiment_intelligence(
    home: Path, *, days: int = 90, project: str | None = None,
    target: date | None = None, directory: Path | None = None,
) -> dict[str, Any]:
    """Read accepted sidecars.  This function is intentionally model-free."""
    del home  # registry paths are resolved by the report parser/config helpers
    root = directory or report_directory()
    target = target or date.today()
    since = (target - timedelta(days=max(1, days) - 1)).isoformat()
    records: list[dict[str, Any]] = []
    validation_errors: list[dict[str, Any]] = []
    analyzed_dates: list[str] = []
    for day in available_report_dates(root):
        if not since <= day <= target.isoformat():
            continue
        sidecar = _sidecar(root, day)
        try:
            value = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        source = root / f"{day}.md"
        if (
            value.get("schema_version") != SCHEMA_VERSION
            or value.get("prompt_version") != PROMPT_VERSION
            or not value.get("policy_fingerprint")
            or value.get("source_sha256") != hashlib.sha256(source.read_bytes()).hexdigest()
        ):
            continue
        analyzed_dates.append(day)
        for error in value.get("validation_errors") or []:
            validation_errors.append({"date": day, "error": str(error)})
        for item in value.get("experiments") or []:
            if isinstance(item, dict) and (not project or item.get("project_id") == project):
                records.append(dict(item))
    records.sort(key=lambda item: (item.get("date", ""), item.get("record_id", "")), reverse=True)
    all_usage_pools = _usage_pools(root)
    pools: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        key = (record["date"], record["project_id"])
        pools.setdefault(key, all_usage_pools.get(key, _empty_token_pool()))
        record["token_context"] = {
            **pools[key],
            "shared_by_records": sum(
                1 for item in records if item["date"] == key[0] and item["project_id"] == key[1]
            ),
        }
    names = project_display_names()
    by_project: dict[str, dict[str, Any]] = {}
    for record in records:
        value = by_project.setdefault(record["project_id"], {
            "project_id": record["project_id"], "name": names.get(record["project_id"], record["project_id"]),
            "record_count": 0, "metric_count": 0, "latest_date": record["date"], "result_status": Counter(),
        })
        value["record_count"] += 1
        value["metric_count"] += len(record.get("metrics") or [])
        value["result_status"][record.get("result_status") or "observed"] += 1
    projects = []
    for value in by_project.values():
        value["result_status"] = dict(value["result_status"])
        value["token_pool_total"] = sum(
            pool["total_tokens"] for (day, pid), pool in pools.items() if pid == value["project_id"]
        )
        value["token_pool_days"] = sum(1 for (_, pid) in pools if pid == value["project_id"])
        projects.append(value)
    projects.sort(key=lambda item: (-item["record_count"], item["project_id"]))
    status = {}
    try:
        status = json.loads((root / "data" / "experiment_backfill_status.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_from": "Daily Report 审计缓存；Agent 事件只作证据与项目日 Token 补充",
        "target": target.isoformat(), "since": since, "project_filter": project,
        "counts": {
            "records": len(records), "projects": len(projects),
            "metrics": sum(len(item.get("metrics") or []) for item in records),
            "conclusions": sum(bool(item.get("conclusion")) for item in records),
            "analyzed_days": len(analyzed_dates), "validation_errors": len(validation_errors),
        },
        "projects": projects,
        "records": records,
        "metric_series": _metric_groups(records),
        "token_pools": [
            {"date": day, "project_id": pid, **pool} for (day, pid), pool in sorted(pools.items())
        ],
        "validation_errors": validation_errors[-30:],
        "backfill_status": status,
        "notes": [
            "实验记录来自日报中的实际实验、评测、训练、消融或部署验证，不由 Git/Token 事件直接生成。",
            "指标只有在项目、名称、单位和评测口径完全一致时才进入同一趋势序列。",
            "Token 只展示项目日共享池，不是某条实验独占成本，也不会伪装成单实验精确拆账。",
        ],
    }
