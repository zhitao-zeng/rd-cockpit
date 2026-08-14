"""One-time, cached semantic calibration for historical Daily Reports.

The Markdown report remains authoritative.  This module asks a model only for
the four semantic streams needed by the project-intelligence page and stores a
SHA256-bound sidecar.  It never rewrites a report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .artifact_cache import atomic_write_json, sha256_path
from .daily_audit import _json_object, _numbers
from .daily_source import (
    _detail_project_ids,
    _project_ids,
    available_report_dates,
    parse_report,
    report_directory,
)
from .project_identity import (
    canonical_project_ids, canonicalize_report, registered_project_names,
)
from .semantic_feedback import feedback_fingerprint, feedback_for_records
from .model_runner import run_claude_json, run_codex_json


FIELDS = ("unknown_updates", "blocker_updates", "breakthroughs", "project_updates")
SCHEMA_VERSION = 2
PROMPT_VERSION = 2
REF_RE = re.compile(r"^report:(\d{4}-\d{2}-\d{2}):L(\d+)-L(\d+)$")
DEFAULT_MODEL = "codex:gpt-5.6-sol@medium"
DEFAULT_FALLBACK = "deepseek-local"


def _write_json(path: Path, value: Any) -> None:
    atomic_write_json(path, value)


def _source_sha(path: Path) -> str:
    return sha256_path(path)


def attribution_fingerprint(report: dict[str, Any]) -> str:
    """Hash parsed project ownership, including cached classifier decisions."""
    value = [{
        "group": str(group.get("title") or ""),
        "tasks": [{
            "title": str(task.get("title") or ""),
            "project_ids": list(task.get("project_ids") or []),
            "classification": {
                key: (task.get("classification") or {}).get(key)
                for key in ("source", "model", "confidence")
            },
        } for task in group.get("tasks") or []],
    } for group in report.get("groups") or []]
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _stable_id(kind: str, project_id: str, text: str) -> str:
    normalized = re.sub(r"[^\w\u4e00-\u9fff]+", "", text.casefold())
    digest = hashlib.sha1(f"{project_id}|{normalized}".encode()).hexdigest()[:12]
    return f"{kind}:{project_id}:{digest}"


def _line_owners(lines: list[str], report_projects: list[str]) -> dict[int, set[str]]:
    """Conservatively attribute Markdown lines to their current task heading."""
    owners: dict[int, set[str]] = {}
    section = ""
    group_ids: list[str] = []
    task_ids: list[str] = []
    for index, raw in enumerate(lines, 1):
        text = raw.strip()
        if text.startswith("## ") and not text.startswith("### "):
            section = text[3:].strip()
            group_ids, task_ids = [], []
        elif section == "核心进展" and text.startswith("### ") and not text.startswith("#### "):
            group_ids = canonical_project_ids(_project_ids(text[4:]), default_unassigned=False)
            task_ids = []
        elif section == "核心进展" and text.startswith("#### "):
            task_ids = canonical_project_ids(
                _project_ids(text[5:]), default_unassigned=False,
            ) or group_ids
        detailed = canonical_project_ids(_detail_project_ids(text), default_unassigned=False)
        explicit = detailed or canonical_project_ids(_project_ids(text), default_unassigned=False)
        if section == "核心进展":
            # A task heading owns its body.  Concrete paths only refine an
            # unclassified/generic heading; incidental paths in prose must not
            # turn one task into several projects.
            if task_ids and task_ids != ["asr_other"]:
                current = task_ids
            else:
                current = detailed or task_ids or group_ids or explicit
        else:
            current = explicit
        if not current and len(report_projects) == 1:
            current = report_projects
        owners[index] = set(current)
    return owners


def _record(path: Path) -> dict[str, Any]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    parsed = canonicalize_report(parse_report(path))
    project_ids = [value for value in parsed.get("project_ids", []) if value != "unassigned"]
    return {
        "date": str(parsed.get("date") or path.stem),
        "path": path,
        "source_sha256": _source_sha(path),
        "attribution_fingerprint": attribution_fingerprint(parsed),
        "lines": lines,
        "project_ids": project_ids,
        "task_count": int(parsed.get("task_count") or 0),
        "unmapped_project_ids": list(parsed.get("unmapped_project_ids") or []),
        "owners": _line_owners(lines, project_ids),
        "numbered_markdown": "\n".join(f"L{index}: {line}" for index, line in enumerate(lines, 1)),
    }


def _instruction(records: list[dict[str, Any]], state: dict[str, dict[str, dict[str, Any]]]) -> dict[str, Any]:
    feedback = []
    seen_feedback: set[str] = set()
    for record in records:
        for item in record.get("feedback") or []:
            event_id = str(item.get("event_id") or "")
            if event_id and event_id not in seen_feedback:
                seen_feedback.add(event_id)
                feedback.append(item)
    return {
        "project_catalog": [{"id": key, "name": value}
                            for key, value in registered_project_names().items()],
        "previous_open_unknowns": list(state["unknown"].values()),
        "previous_open_blockers": list(state["blocker"].values()),
        "days": [{
            "date": item["date"], "project_ids": item["project_ids"],
            "numbered_markdown": item["numbered_markdown"],
        } for item in records],
        "user_feedback": feedback,
        "output_schema": {"days": [{
            "date": "YYYY-MM-DD",
            "unknown_updates": [{"project_id": "one catalog id", "unknown_id": "stable id when known",
                "question": "research uncertainty", "action": "open|update|resolve",
                "priority": "high|medium|low", "missing_evidence": "needed validation", "evidence": ["report:DATE:Lx-Ly"]}],
            "blocker_updates": [{"project_id": "one catalog id", "blocker_id": "stable id when known",
                "blocker": "actual blocking condition", "action": "open|update|resolve",
                "priority": "high|medium|low", "missing_evidence": "condition to unblock", "evidence": ["report:DATE:Lx-Ly"]}],
            "breakthroughs": [{"project_id": "one catalog id", "title": "short title",
                "change": "metric, verification or belief change", "significance": "why it changes the project judgment",
                "evidence": ["report:DATE:Lx-Ly"]}],
            "project_updates": [{"project_id": "one catalog id",
                "summary": "one complete Chinese sentence covering problem, action, result and belief change",
                "evidence": ["report:DATE:Lx-Ly"]}],
        }]},
        "rules": [
            "Markdown is the only source of truth; do not add facts from memory.",
            "Every item belongs to exactly one project_id from project_catalog.",
            "Evidence must cite exact same-day line ranges and must support the complete claim and every number.",
            "Do not combine ASR, OCR, workspace, Resume Copilot or any other projects in one update.",
            "Unknowns are unresolved research questions, not ordinary TODO items. Resolve only with explicit evidence.",
            "Blockers actually prevent progress. Risks, suggestions and ordinary next actions are not blockers.",
            "Breakthroughs must change a metric, verification stage, conclusion or research direction; commits and builds alone do not qualify.",
            "At most one project_update per substantive project per day. Return empty arrays for no activity.",
            "Reuse the supplied stable ID when updating or resolving a previous open item.",
            "User feedback is an evaluation signal, not a factual source. Correct the cited failure only when the Markdown evidence supports the correction.",
            "For noise or incorrect feedback, omit the unsupported item. For missing feedback, recover only evidence-backed content. For wrong_project feedback, use corrected_project_id only when the cited lines support it.",
            "Return JSON only.",
        ],
    }


def _run_context(model_spec: str, instruction: dict[str, Any], stage: str) -> dict[str, Any]:
    return {
        "home": os.environ.get("RD_COCKPIT_HOME"), "stage": stage,
        "source_hash": hashlib.sha256(
            json.dumps(instruction, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest(),
        "fallback_used": model_spec != DEFAULT_MODEL,
        "reason": "新增或变更的日报语义证据需要离线提炼。",
    }


def _request_codex(
    model_spec: str, instruction: dict[str, Any], *, stage: str = "intelligence",
) -> tuple[dict[str, Any], dict[str, Any]]:
    prompt = (
        "你是研发项目情报审计器。审计标准输入中的多日日报，严格遵守 output_schema 和 rules，"
        "只返回一个 JSON 对象。不要重写日报，不要把待办、提交或一般工作包装成研究突破。"
    )
    return run_codex_json(
        model_spec, instruction, prompt=prompt, executable_env="RD_INTELLIGENCE_CODEX_BIN",
        timeout_env="RD_INTELLIGENCE_MODEL_TIMEOUT", default_timeout=1200,
        workdir=Path(__file__).resolve().parents[1], temp_prefix="rd-intelligence-",
        run_context=_run_context(model_spec, instruction, stage),
    )


def _request_claude(
    model: str, instruction: dict[str, Any], *, stage: str = "intelligence",
) -> tuple[dict[str, Any], dict[str, Any]]:
    prompt = "你是研发项目情报审计器。严格按输入 schema 返回纯 JSON，所有结论必须引用日报行号。"
    return run_claude_json(
        model, instruction, prompt=prompt, executable_env="RD_INTELLIGENCE_CLAUDE_BIN",
        timeout_env="RD_INTELLIGENCE_MODEL_TIMEOUT", default_timeout=1200,
        run_context=_run_context(model, instruction, stage),
    )


def _request_any_model(
    model: str, instruction: dict[str, Any], *, stage: str = "intelligence",
) -> tuple[dict[str, Any], dict[str, Any]]:
    return (_request_codex(model, instruction, stage=stage) if model.startswith("codex:")
            else _request_claude(model, instruction, stage=stage))


def _refs(value: Any, record: dict[str, Any], project_id: str, label: str) -> tuple[list[str], str, list[str]]:
    refs = list(dict.fromkeys(_strings(value)))
    if not refs:
        raise ValueError(f"{label}: evidence is required")
    accepted = []
    cited = []
    rejected = []
    for ref in refs:
        match = REF_RE.fullmatch(ref)
        if not match or match.group(1) != record["date"]:
            rejected.append(f"invalid evidence ref {ref}")
            continue
        start, end = int(match.group(2)), int(match.group(3))
        if not 1 <= start <= end <= len(record["lines"]):
            rejected.append(f"evidence range outside report: {ref}")
            continue
        owners = set().union(*(record["owners"].get(line, set()) for line in range(start, end + 1)))
        generic_asr_owner = owners == {"asr_other"} and (
            project_id == "asr" or project_id.startswith("asr_")
        )
        if owners and project_id not in owners and not generic_asr_owner:
            rejected.append(f"evidence {ref} belongs to {sorted(owners)}, not {project_id}")
            continue
        accepted.append(ref)
        cited.extend(record["lines"][start - 1:end])
    if not accepted:
        raise ValueError(f"{label}: " + "; ".join(rejected))
    return accepted, " ".join(cited), rejected


def _validate_day(raw: dict[str, Any], record: dict[str, Any], valid: set[str], metadata: dict[str, Any]) -> dict[str, Any]:
    if str(raw.get("date") or "") != record["date"]:
        raise ValueError(f"{record['date']}: model output omitted or changed the date")
    output: dict[str, Any] = {field: [] for field in FIELDS}
    validation_errors: list[str] = []
    for field in FIELDS:
        for index, item in enumerate(raw.get(field) or []):
            if not isinstance(item, dict):
                continue
            label = f"{record['date']}:{field}[{index}]"
            try:
                projects = _strings(item.get("project_ids")) or _strings(item.get("project_id"))
                if len(projects) != 1 or projects[0] not in valid or projects[0] == "unassigned":
                    raise ValueError("exactly one catalog project_id is required")
                project_id = projects[0]
                refs, cited, rejected_refs = _refs(item.get("evidence"), record, project_id, label)
                if field == "unknown_updates":
                    text = str(item.get("question") or "").strip()
                    kind, identifier = "unknown", "unknown_id"
                elif field == "blocker_updates":
                    text = str(item.get("blocker") or item.get("text") or "").strip()
                    kind, identifier = "blocker", "blocker_id"
                elif field == "breakthroughs":
                    text = str(item.get("change") or "").strip()
                    kind = identifier = ""
                else:
                    text = str(item.get("summary") or "").strip()
                    kind = identifier = ""
                if not text:
                    raise ValueError("readable text is required")
                numeric = [text]
                if field == "breakthroughs":
                    numeric.extend([str(item.get("title") or ""), str(item.get("significance") or "")])
                missing = sorted(number for number in _numbers(numeric) if number not in cited)
                if missing:
                    raise ValueError(f"unsupported numbers: {', '.join(missing)}")
                normalized = {**item, "project_id": project_id, "project_ids": [project_id],
                              "evidence": refs, "confidence": "reported", "source_mode": "historical_audited"}
                if field in {"unknown_updates", "blocker_updates"}:
                    action = str(item.get("action") or "open").casefold()
                    if action not in {"open", "update", "resolve"}:
                        raise ValueError(f"invalid action {action}")
                    normalized["action"] = action
                    normalized[identifier] = str(item.get(identifier) or "").strip() or _stable_id(kind, project_id, text)
                output[field].append(normalized)
                validation_errors.extend(f"{label}: removed {reason}" for reason in rejected_refs)
            except ValueError as exc:
                validation_errors.append(f"{label}: removed item: {exc}")
    return {"schema_version": SCHEMA_VERSION, "prompt_version": PROMPT_VERSION,
            "policy_fingerprint": metadata.get("policy_fingerprint"),
            "feedback_fingerprint": record.get("feedback_fingerprint"),
            "date": record["date"], "source_path": str(record["path"]),
            "source_sha256": record["source_sha256"],
            "attribution_fingerprint": record["attribution_fingerprint"], **output,
            "validation_errors": validation_errors, "model_run": metadata}


def _quality_gate(day: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    """Reject a semantic snapshot that is structurally valid but not useful."""
    accepted = sum(len(day.get(field) or []) for field in FIELDS)
    errors = len(day.get("validation_errors") or [])
    project_updates = len(day.get("project_updates") or [])
    texts: list[str] = []
    for field, key in (
        ("unknown_updates", "question"), ("blocker_updates", "blocker"),
        ("breakthroughs", "change"), ("project_updates", "summary"),
    ):
        texts.extend(str(item.get(key) or "").strip().casefold()
                     for item in day.get(field) or [] if str(item.get(key) or "").strip())
    duplicate_ratio = 0.0 if not texts else 1.0 - len(set(texts)) / len(texts)
    reasons = []
    if record.get("task_count", 0) and record.get("project_ids") and project_updates == 0:
        reasons.append("日报有可读项目任务，但没有任何项目段落总结")
    if errors > max(3, accepted * 2):
        reasons.append(f"被校验移除的候选过多（{errors} 个错误 / {accepted} 个保留）")
    if len(texts) >= 3 and duplicate_ratio > 0.5:
        reasons.append(f"摘要重复率过高（{duplicate_ratio:.0%}）")
    result = {
        "passed": not reasons, "accepted_items": accepted,
        "validation_error_count": errors, "duplicate_ratio": round(duplicate_ratio, 3),
        "unmapped_project_labels": len(record.get("unmapped_project_ids") or []),
        "reasons": reasons,
    }
    if reasons:
        raise ValueError("quality gate rejected: " + "; ".join(reasons))
    return result


def _apply_state(state: dict[str, dict[str, dict[str, Any]]], day: dict[str, Any]) -> None:
    for field, kind, id_key, text_key in (
        ("unknown_updates", "unknown", "unknown_id", "question"),
        ("blocker_updates", "blocker", "blocker_id", "blocker"),
    ):
        for item in day.get(field) or []:
            identifier = str(item.get(id_key) or _stable_id(kind, str(item.get("project_id") or ""), str(item.get(text_key) or "")))
            if item.get("action") == "resolve":
                state[kind].pop(identifier, None)
            else:
                state[kind][identifier] = {id_key: identifier, "project_id": item.get("project_id"),
                                           text_key: item.get(text_key), "priority": item.get("priority"),
                                           "missing_evidence": item.get("missing_evidence")}


def _sidecar(root: Path, day: str) -> Path:
    return root / "data" / f"{day}_intelligence_validated.json"


def _cached_day(
    root: Path, existing: dict[str, Any] | None, record: dict[str, Any],
    valid: set[str], policy: str, models: tuple[str, ...],
) -> dict[str, Any] | None:
    if not existing or existing.get("source_sha256") != record["source_sha256"]:
        return None
    stored_feedback = existing.get("feedback_fingerprint")
    expected_feedback = record.get("feedback_fingerprint")
    if stored_feedback != expected_feedback and not (
        stored_feedback is None and not record.get("feedback")
    ):
        return None
    attribution_matches = (
        existing.get("attribution_fingerprint") == record.get("attribution_fingerprint")
    )
    if (
        existing.get("schema_version") == SCHEMA_VERSION
        and existing.get("prompt_version") == PROMPT_VERSION
        and existing.get("policy_fingerprint") == policy
        and attribution_matches
    ):
        return existing
    metadata = existing.get("model_run") if isinstance(existing.get("model_run"), dict) else {}
    if str(metadata.get("model") or "") not in models:
        return None
    try:
        upgraded = _validate_day(
            existing, record, valid,
            {**metadata, "policy_fingerprint": policy, "prompt_version": PROMPT_VERSION},
        )
        upgraded["quality_gate"] = _quality_gate(upgraded, record)
    except ValueError:
        return None
    upgraded["cache_migration"] = {
        "from_schema": existing.get("schema_version"), "model_call": False,
    }
    _write_json(_sidecar(root, record["date"]), upgraded)
    return upgraded


def backfill(
    *, directory: Path | None = None, days: int = 90, batch_days: int = 7,
    model: str = DEFAULT_MODEL, fallback_model: str = DEFAULT_FALLBACK,
    force: bool = False, target: date | None = None,
) -> dict[str, Any]:
    root = directory or report_directory()
    target = target or date.today()
    since = (target - timedelta(days=max(1, days) - 1)).isoformat()
    dates = [value for value in available_report_dates(root) if since <= value <= target.isoformat()]
    records = [_record(root / f"{day}.md") for day in dates]
    cockpit_home = Path(os.environ.get("RD_COCKPIT_HOME") or Path(__file__).resolve().parents[1])
    feedback_database = cockpit_home / ".rd-cockpit" / "events.sqlite"
    if feedback_database.is_file():
        from .ledger import Ledger
        ledger = Ledger(feedback_database, readonly=True)
        try:
            for record in records:
                record["feedback"] = feedback_for_records(ledger, [record])
                record["feedback_fingerprint"] = feedback_fingerprint(record["feedback"])
        finally:
            ledger.close()
    else:
        for record in records:
            record["feedback"] = []
            record["feedback_fingerprint"] = feedback_fingerprint(())
    valid = set(registered_project_names())
    from .semantic_policy import catalog_fingerprint, policy_fingerprint
    policy = policy_fingerprint(
        "project-intelligence-backfill",
        schema_version=SCHEMA_VERSION,
        prompt_version=PROMPT_VERSION,
        models=(model, fallback_model),
        extra={"catalog": catalog_fingerprint(registered_project_names())},
    )
    policy_models = tuple(value for value in (model, fallback_model) if value)
    state: dict[str, dict[str, dict[str, Any]]] = {"unknown": {}, "blocker": {}}
    processed: list[str] = []
    cached: list[str] = []
    failed: list[dict[str, str]] = []
    calls = 0
    index = 0
    while index < len(records):
        record = records[index]
        sidecar = _sidecar(root, record["date"])
        existing: dict[str, Any] | None = None
        try:
            existing = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.exists() else None
        except json.JSONDecodeError:
            existing = None
        accepted_cache = None if force else _cached_day(
            root, existing, record, valid, policy, policy_models,
        )
        if accepted_cache is not None:
            cached.append(record["date"])
            _apply_state(state, accepted_cache)
            index += 1
            continue
        batch = []
        size = 0
        while index < len(records) and len(batch) < max(1, batch_days):
            candidate = records[index]
            candidate_sidecar = _sidecar(root, candidate["date"])
            if batch and not force and candidate_sidecar.exists():
                try:
                    value = json.loads(candidate_sidecar.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    value = {}
                if _cached_day(root, value, candidate, valid, policy, policy_models) is not None:
                    break
            candidate_size = len(candidate["numbered_markdown"].encode())
            if batch and size + candidate_size > 150_000:
                break
            batch.append(candidate)
            size += candidate_size
            index += 1
        instruction = _instruction(batch, state)
        raw = metadata = None
        errors = []
        for selected_model in (model, fallback_model):
            if not selected_model or selected_model in [item.get("model") for item in errors]:
                continue
            calls += 1
            try:
                raw, metadata = _request_any_model(selected_model, instruction)
                metadata = {**(metadata or {}), "policy_fingerprint": policy,
                            "prompt_version": PROMPT_VERSION}
                by_date = {str(item.get("date")): item for item in raw.get("days", []) if isinstance(item, dict)}
                validated = [_validate_day(by_date.get(item["date"], {}), item, valid, metadata) for item in batch]
                for value, item in zip(validated, batch, strict=True):
                    value["quality_gate"] = _quality_gate(value, item)
                for value in validated:
                    _write_json(_sidecar(root, value["date"]), value)
                    processed.append(value["date"])
                    _apply_state(state, value)
                break
            except (RuntimeError, ValueError, OSError, json.JSONDecodeError) as exc:
                errors.append({"model": selected_model, "error": str(exc)})
                print(f"[{batch[0]['date']}..{batch[-1]['date']}] {selected_model} rejected: {exc}",
                      file=sys.stderr, flush=True)
                raw = None
        if raw is None:
            failed.extend({"date": item["date"], "error": "; ".join(error["error"] for error in errors)} for item in batch)
        else:
            print(f"[{batch[0]['date']}..{batch[-1]['date']}] accepted {len(batch)} report(s) via {metadata.get('model')}",
                  file=sys.stderr, flush=True)
    result = {"directory": str(root), "days": days, "report_count": len(records),
              "processed": processed, "cached": cached, "failed": failed, "model_calls": calls,
              "models": {"primary": model, "fallback": fallback_model or None},
              "target": target.isoformat(), "since": since}
    _write_json(root / "data" / "intelligence_backfill_status.json", result)
    return result


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Backfill evidence-bound project intelligence")
    value.add_argument("--directory", type=Path)
    value.add_argument("--days", type=int, default=90)
    value.add_argument("--batch-days", type=int, default=7)
    value.add_argument("--model", default=os.environ.get("RD_INTELLIGENCE_MODEL", DEFAULT_MODEL))
    value.add_argument("--fallback-model", default=os.environ.get("RD_INTELLIGENCE_FALLBACK_MODEL", DEFAULT_FALLBACK))
    value.add_argument("--force", action="store_true")
    value.add_argument("--target")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    target = date.fromisoformat(args.target) if args.target else None
    print(json.dumps(backfill(directory=args.directory, days=args.days, batch_days=args.batch_days,
                              model=args.model, fallback_model=args.fallback_model,
                              force=args.force, target=target), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
