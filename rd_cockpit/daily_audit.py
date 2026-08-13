"""Evidence-audited daily-report helpers.

The production path makes one semantic model call to extract readable claims
from the raw collectors.  This module then validates evidence, numbers and
source coverage before rendering the final Markdown deterministically.  It
also retains the older review/finalize helpers for compatibility and tests;
no model is called by this module itself.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PLAN_STATUSES = {
    "completed", "partially_completed", "blocked", "deferred",
    "no_evidence", "cancelled",
}
CONFIDENCE = {"observed", "reported", "inferred", "confirmed"}
INTELLIGENCE_FIELDS = {
    "unknown_updates", "blocker_updates", "breakthroughs", "project_updates",
}
PLAN_STATUS_LABELS = {
    "completed": "已完成",
    "partially_completed": "部分完成",
    "blocked": "阻塞",
    "deferred": "延期",
    "no_evidence": "无完成证据",
    "cancelled": "已取消",
}
CONFIDENCE_LABELS = {
    "observed": "机器观测",
    "reported": "Session 报告",
    "inferred": "模型推断",
    "confirmed": "用户确认",
}
SOURCE_COVERAGE_STATUSES = {
    "core_task", "merged", "supporting", "non_substantive", "insufficient_summary",
}
REQUIRED_SECTIONS = (
    "## 昨日计划闭环",
    "## 核心进展",
    "## Token 消耗",
    "## 关键结论与知识",
    "## 阻塞 / 待解决",
    "## 明日计划",
    "## 数据完整性",
    "## 推送摘要",
)


def _read_json(path: Path | None, default: Any) -> Any:
    if path is None or not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value is None:
        return []
    text = str(value).strip()
    return [text] if text else []


def _sessions(source: dict[str, Any]) -> list[dict[str, Any]]:
    value = source.get("sessions") or []
    return [item for item in value if isinstance(item, dict)]


def _truncate_verbose_sessions(source: dict[str, Any]) -> dict[str, Any]:
    copied = json.loads(json.dumps(source, ensure_ascii=False))
    for session in _sessions(copied):
        session.pop("tool_samples", None)
        session.pop("recent_conclusions", None)
        session.pop("other_intents", None)
        for field, limit in (("first_intent", 1_200), ("last_conclusion", 4_000)):
            text = str(session.get(field) or "")
            if len(text) > limit:
                session[field] = text[:limit] + "\n…（审计输入已截断，原始 Session 保留完整内容）"
        files = session.get("edited_files")
        if isinstance(files, list) and len(files) > 24:
            session["edited_files"] = files[:24]
            session["edited_files_omitted"] = len(files) - 24
        session["narrative_truncated"] = True
    return copied


def _session_fingerprint(item: dict[str, Any]) -> str:
    stable = {
        "session_id": str(item.get("session_id") or ""),
        "project": str(item.get("_project") or ""),
        "source": str(item.get("source") or item.get("_source") or ""),
        "first_intent": str(item.get("first_intent") or ""),
        "last_conclusion": str(item.get("last_conclusion") or ""),
        "edited_files": _string_list(item.get("edited_files")),
        "tool_count": int(item.get("tool_count", 0) or 0),
    }
    value = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _session_entries(sources: dict[str, Any]) -> list[tuple[str, dict[str, Any], str]]:
    """Return every distinct session fragment with an unambiguous evidence ref."""
    raw: list[tuple[str, dict[str, Any], str, str]] = []
    fingerprints: dict[str, set[str]] = {}
    for key in ("claude_sessions", "codex_sessions"):
        for item in _sessions(sources[key]):
            session_id = str(item.get("session_id") or "").strip()
            if not session_id:
                continue
            fingerprint = _session_fingerprint(item)
            raw.append((key, item, session_id, fingerprint))
            fingerprints.setdefault(session_id, set()).add(fingerprint)
    output: list[tuple[str, dict[str, Any], str]] = []
    seen: set[str] = set()
    for key, item, session_id, fingerprint in raw:
        ref = f"session:{session_id}"
        if len(fingerprints[session_id]) > 1:
            ref = f"{ref}#{fingerprint}"
        # Exact duplicate collector records are one evidence fragment.
        if ref in seen:
            continue
        seen.add(ref)
        output.append((key, item, ref))
    return output


def _meaningful(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text and text not in {"(无结论)", "(无文本意图)", "无", "none", "null"})


def _coverage_required_refs(sources: dict[str, Any]) -> list[dict[str, Any]]:
    required = []
    for _, item, ref in _session_entries(sources):
        tool_count = int(item.get("tool_count", 0) or 0)
        edited_files = _string_list(item.get("edited_files"))
        if not (_meaningful(item.get("first_intent")) or _meaningful(item.get("last_conclusion"))
                or tool_count or edited_files):
            continue
        required.append({
            "ref": ref,
            "project": str(item.get("_project") or ""),
            "tool_count": tool_count,
            "edited_file_count": len(edited_files) + int(item.get("edited_files_omitted", 0) or 0),
            "has_conclusion": _meaningful(item.get("last_conclusion")),
        })
    return required


def _project_catalog() -> list[dict[str, Any]]:
    from .config import project_match_rules
    from .daily_source import project_display_names

    names = project_display_names()
    rules = {project_id: (aliases, paths) for project_id, aliases, paths in project_match_rules()}
    return [
        {
            "id": project_id,
            "name": name,
            "aliases": list(rules.get(project_id, ((), ()))[0]),
            "paths": list(rules.get(project_id, ((), ()))[1]),
        }
        for project_id, name in sorted(names.items())
    ]


def _evidence_projects(text: str) -> list[str]:
    from .daily_source import _detail_project_ids, _project_ids

    detailed = _detail_project_ids(text)
    return detailed or _project_ids(text)


def _stable_intelligence_id(kind: str, project_id: str, text: str) -> str:
    normalized = re.sub(r"[^\w\u4e00-\u9fff]+", "", text.casefold())
    digest = hashlib.sha1(f"{project_id}|{normalized}".encode()).hexdigest()[:12]
    return f"{kind}:{project_id}:{digest}"


def _catalog(sources: dict[str, Any]) -> list[dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    known = {str(item["id"]) for item in _project_catalog()}
    for _, item, ref in _session_entries(sources):
        raw = json.dumps(item, ensure_ascii=False)
        declared = str(item.get("_project") or "")
        records[ref] = {
            "ref": ref, "kind": "session", "project": str(item.get("_project") or ""),
            "project_ids": [declared] if declared in known else _evidence_projects(raw),
        }
    for project, commits in (sources["git"].get("repos") or {}).items():
        for value in commits or []:
            sha = str(value).strip().split(maxsplit=1)[0]
            if not sha:
                continue
            ref = f"commit:{sha}"
            project_ids = [str(project)] if str(project) in known else _evidence_projects(str(project))
            records[ref] = {"ref": ref, "kind": "commit", "project": str(project),
                            "project_ids": project_ids}
    for project, files in (sources["files"].get("by_project") or {}).items():
        for value in files or []:
            path = str(value).strip()
            if not path:
                continue
            ref = f"file:{project}/{path}"
            project_ids = [str(project)] if str(project) in known else _evidence_projects(f"{project}/{path}")
            records[ref] = {"ref": ref, "kind": "file", "project": str(project),
                            "project_ids": project_ids}
    return sorted(records.values(), key=lambda item: item["ref"])


def _objective(sources: dict[str, Any]) -> dict[str, Any]:
    claude = sources["claude_sessions"]
    codex = sources["codex_sessions"]
    return {
        "sessions": {
            "claude_or_phanthy": int(claude.get("total_sessions", 0) or 0),
            "codex": int(codex.get("total_sessions", 0) or 0),
        },
        "tool_calls": {
            "claude_or_phanthy": int(claude.get("total_tool_calls", 0) or 0),
            "codex": int(codex.get("total_tool_calls", 0) or 0),
        },
        "token_usage": {
            "claude_or_phanthy": claude.get("token_usage_summary") or {},
            "codex": codex.get("token_usage_summary") or {},
            "claude_projects": claude.get("projects") or {},
            "codex_projects": codex.get("projects") or {},
        },
        "git_commits": int(sources["git"].get("total_commits", 0) or 0),
        "changed_files": int(sources["files"].get("total_files", 0) or 0),
    }


def prepare_bundle(*, report_date: str, sessions: Path, codex: Path, git: Path, files: Path,
                   previous_plan: Path | None) -> dict[str, Any]:
    sources = {
        "claude_sessions": _read_json(sessions, {}),
        "codex_sessions": _read_json(codex, {}),
        "git": _read_json(git, {}),
        "files": _read_json(files, {}),
    }
    bundle = {
        "schema_version": 1,
        "report_date": report_date,
        "project_catalog": _project_catalog(),
        "previous_plan": previous_plan.read_text(encoding="utf-8", errors="replace") if previous_plan and previous_plan.exists() else "",
        "objective": _objective(sources),
        "allowed_evidence_refs": _catalog(sources),
        "coverage_required_refs": _coverage_required_refs(sources),
        "sources": sources,
    }
    # Keep the long-context audit bounded without changing the source files.
    if len(json.dumps(bundle, ensure_ascii=False).encode()) > 180_000:
        sources["claude_sessions"] = _truncate_verbose_sessions(sources["claude_sessions"])
        sources["codex_sessions"] = _truncate_verbose_sessions(sources["codex_sessions"])
        bundle["allowed_evidence_refs"] = _catalog(sources)
        bundle["coverage_required_refs"] = _coverage_required_refs(sources)
        bundle["sources"] = sources
        bundle["input_truncated"] = True
    else:
        bundle["input_truncated"] = False
    return bundle


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


def unpack_model_output(value: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    """Accept Claude/Codex wrapper output as well as a direct model payload."""
    usage = value.get("usage") if isinstance(value.get("usage"), dict) else {}
    model_usage = value.get("modelUsage") if isinstance(value.get("modelUsage"), dict) else {}
    metadata = {
        "duration_ms": value.get("duration_ms"),
        "duration_api_ms": value.get("duration_api_ms"),
        "num_turns": value.get("num_turns"),
        "total_cost_usd": value.get("total_cost_usd"),
        "usage": usage,
        "model_usage": model_usage,
        "provider": value.get("provider"),
        "reasoning_effort": value.get("reasoning_effort"),
    }
    if isinstance(value.get("structured_output"), dict):
        return value["structured_output"], metadata
    if "result" in value:
        return value["result"], metadata
    return value, metadata


def _model_json(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    outer = _read_json(path, None)
    if not isinstance(outer, dict):
        raise ValueError("model output is not JSON; use Claude CLI --output-format json")
    result, metadata = unpack_model_output(outer)
    if isinstance(result, dict):
        return result, metadata
    return _json_object(str(result)), metadata


def _source_text_by_ref(bundle: dict[str, Any]) -> dict[str, str]:
    sources = bundle["sources"]
    output: dict[str, str] = {}
    for _, item, ref in _session_entries(sources):
        output[ref] = json.dumps(item, ensure_ascii=False)
    for project, commits in (sources["git"].get("repos") or {}).items():
        for value in commits or []:
            sha = str(value).strip().split(maxsplit=1)[0]
            output[f"commit:{sha}"] = str(value)
    for project, paths in (sources["files"].get("by_project") or {}).items():
        for value in paths or []:
            output[f"file:{project}/{value}"] = str(value)
    return output


def _numbers(texts: Iterable[str]) -> set[str]:
    output: set[str] = set()
    for text in texts:
        output.update(re.findall(r"(?<![\w.])\d+(?:\.\d+)?%?", str(text)))
    return output


def _evidence(value: Any, allowed: set[str], warnings: list[str], label: str) -> list[str]:
    refs = _string_list(value)
    invalid = [ref for ref in refs if ref not in allowed]
    if invalid:
        warnings.append(f"{label}: removed invalid evidence refs: {', '.join(invalid)}")
    return list(dict.fromkeys(ref for ref in refs if ref in allowed))


def validate_audit(audit: dict[str, Any], bundle: dict[str, Any], *, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    allowed = {str(item["ref"]) for item in bundle.get("allowed_evidence_refs", [])}
    source_text = _source_text_by_ref(bundle)
    warnings: list[str] = []
    valid_projects = {
        str(item.get("id")) for item in bundle.get("project_catalog", []) if item.get("id")
    } | {"unassigned"}
    evidence_projects = {
        str(item.get("ref")): set(_string_list(item.get("project_ids")))
        for item in bundle.get("allowed_evidence_refs", []) if item.get("ref")
    }

    def checked_projects(value: Any, label: str, *, exactly_one: bool = False) -> list[str]:
        values = list(dict.fromkeys(_string_list(value)))
        invalid = [project_id for project_id in values if project_id not in valid_projects]
        if invalid:
            warnings.append(f"{label}: invalid project ids: {', '.join(invalid)}")
        values = [project_id for project_id in values if project_id in valid_projects]
        if exactly_one and len(values) != 1:
            warnings.append(f"{label}: exactly one valid project_id is required")
        return values
    groups = []
    unsupported = 0
    unverified_numbers: list[dict[str, Any]] = []
    for group_index, group in enumerate(audit.get("project_groups") or []):
        if not isinstance(group, dict):
            continue
        tasks = []
        for task_index, task in enumerate(group.get("tasks") or []):
            if not isinstance(task, dict):
                continue
            label = f"group[{group_index}].task[{task_index}]"
            evidence = _evidence(task.get("evidence"), allowed, warnings, label)
            confidence = str(task.get("confidence") or "reported")
            if confidence not in CONFIDENCE:
                confidence = "reported"
            if not evidence:
                confidence = "inferred"
                unsupported += 1
            elif confidence == "observed" and not any(ref.startswith("commit:") for ref in evidence):
                # A Session is a report about what happened.  It
                # supports publishing the claim, but it is not direct machine
                # observation of the implementation or test result.
                confidence = "reported"
            did = _string_list(task.get("did"))
            why = _string_list(task.get("why"))
            results = _string_list(task.get("results"))
            cited_text = " ".join(source_text.get(ref, "") for ref in evidence)
            missing_numbers = sorted(number for number in _numbers([*did, *results]) if number not in cited_text)
            if missing_numbers:
                unverified_numbers.append({"task": str(task.get("title") or "未命名事项"), "numbers": missing_numbers})
            tasks.append({
                "title": str(task.get("title") or "未命名事项"),
                "did": did,
                "why": why,
                "results": results,
                "files": _string_list(task.get("files")),
                "evidence": evidence,
                "confidence": confidence,
                "evidence_status": "supported" if evidence else "unsupported",
                "unverified_numbers": missing_numbers,
            })
        if tasks:
            groups.append({
                "name": str(group.get("name") or "其他"),
                "project_ids": checked_projects(group.get("project_ids"), f"group[{group_index}]"),
                "tasks": tasks,
            })

    task_titles = {
        str(task.get("title") or "")
        for group in groups
        for task in group.get("tasks", [])
    }
    required_coverage = {
        str(item.get("ref") or "")
        for item in bundle.get("coverage_required_refs", [])
        if item.get("ref")
    }
    coverage_by_ref: dict[str, dict[str, str]] = {}
    for index, item in enumerate(audit.get("source_coverage") or []):
        if not isinstance(item, dict):
            continue
        ref = str(item.get("ref") or "").strip()
        status = str(item.get("status") or "").strip()
        task_title = str(item.get("task_title") or "").strip()
        reason = str(item.get("reason") or "").strip()
        if ref not in required_coverage:
            warnings.append(f"source_coverage[{index}]: unknown or unnecessary ref: {ref or '(empty)'}")
            continue
        if ref in coverage_by_ref:
            warnings.append(f"source_coverage[{index}]: duplicate ref: {ref}")
            continue
        if status not in SOURCE_COVERAGE_STATUSES:
            warnings.append(f"source_coverage[{index}]: invalid status for {ref}: {status or '(empty)'}")
            continue
        if status in {"core_task", "merged"} and task_title not in task_titles:
            warnings.append(f"source_coverage[{index}]: {ref} maps to unknown task: {task_title or '(empty)'}")
            continue
        if status in {"supporting", "non_substantive", "insufficient_summary"} and not reason:
            warnings.append(f"source_coverage[{index}]: {ref} requires a reason for status {status}")
            continue
        coverage_by_ref[ref] = {
            "ref": ref, "status": status, "task_title": task_title, "reason": reason,
        }
    missing_coverage = sorted(required_coverage - set(coverage_by_ref))
    if missing_coverage:
        warnings.append("source coverage missing refs: " + ", ".join(missing_coverage))

    closures = []
    for index, item in enumerate(audit.get("plan_closure") or []):
        if not isinstance(item, dict):
            continue
        evidence = _evidence(item.get("evidence"), allowed, warnings, f"plan_closure[{index}]")
        status = str(item.get("status") or "no_evidence")
        if status not in PLAN_STATUSES:
            status = "no_evidence"
        if status in {"completed", "partially_completed"} and not evidence:
            warnings.append(f"plan_closure[{index}]: downgraded {status} to no_evidence")
            status = "no_evidence"
        closures.append({"plan": str(item.get("plan") or "未命名计划"), "status": status,
                         "reason": str(item.get("reason") or ""), "evidence": evidence})

    def normalize_items(key: str) -> list[dict[str, Any]]:
        values = []
        for index, item in enumerate(audit.get(key) or []):
            if isinstance(item, str):
                item = {"text": item}
            if not isinstance(item, dict):
                continue
            evidence = _evidence(item.get("evidence") or item.get("basis"), allowed, warnings, f"{key}[{index}]")
            confidence = str(item.get("confidence") or ("reported" if evidence else "inferred"))
            if confidence not in CONFIDENCE:
                confidence = "reported" if evidence else "inferred"
            values.append({
                "text": str(item.get("text") or item.get("action") or ""),
                "project": str(item.get("project") or item.get("project_id") or ""),
                "scope": str(item.get("scope") or ""),
                "next": str(item.get("next") or item.get("acceptance") or ""),
                "evidence": evidence,
                "confidence": confidence,
            })
        return [item for item in values if item["text"]]

    def normalize_intelligence(key: str) -> list[dict[str, Any]]:
        values = []
        for index, item in enumerate(audit.get(key) or []):
            if not isinstance(item, dict):
                continue
            label = f"{key}[{index}]"
            evidence = _evidence(item.get("evidence"), allowed, warnings, label)
            if not evidence:
                warnings.append(f"{label}: intelligence claim requires evidence")
            confidence = str(item.get("confidence") or ("reported" if evidence else "inferred"))
            if confidence not in CONFIDENCE:
                confidence = "reported" if evidence else "inferred"
            projects = checked_projects(
                item.get("project_ids") or item.get("project_id") or item.get("project"),
                label, exactly_one=True,
            )
            project_id = projects[0] if len(projects) == 1 else ""
            incompatible_refs = [
                ref for ref in evidence
                if evidence_projects.get(ref) and project_id not in evidence_projects[ref]
            ]
            if incompatible_refs:
                warnings.append(
                    f"{label}: evidence belongs to another project: {', '.join(incompatible_refs)}"
                )
            if key in {"unknown_updates", "blocker_updates"}:
                text = str(item.get("question") or "").strip()
                if key == "blocker_updates":
                    text = str(item.get("blocker") or item.get("text") or "").strip()
                action = str(item.get("action") or "open").casefold()
                priority = str(item.get("priority") or "medium").casefold()
                if action not in {"open", "update", "resolve"}:
                    warnings.append(f"{label}: invalid action {action}")
                    action = "open"
                if priority not in {"high", "medium", "low"}:
                    warnings.append(f"{label}: invalid priority {priority}")
                    priority = "medium"
                identifier_key = "unknown_id" if key == "unknown_updates" else "blocker_id"
                identifier = str(item.get(identifier_key) or "").strip()
                if not identifier and project_id and text:
                    identifier = _stable_intelligence_id(
                        "unknown" if key == "unknown_updates" else "blocker", project_id, text,
                    )
                value = {"question" if key == "unknown_updates" else "blocker": text,
                         "action": action, "priority": priority,
                         "missing_evidence": str(item.get("missing_evidence") or "").strip(),
                         identifier_key: identifier}
                numeric_text = [text, value["missing_evidence"]]
            elif key == "breakthroughs":
                value = {"title": str(item.get("title") or "").strip(),
                         "change": str(item.get("change") or "").strip(),
                         "significance": str(item.get("significance") or "").strip()}
                text = value["change"]
                numeric_text = list(value.values())
            else:
                value = {"summary": str(item.get("summary") or "").strip()}
                text = value["summary"]
                numeric_text = [text]
            cited_text = " ".join(source_text.get(ref, "") for ref in evidence)
            missing_numbers = sorted(number for number in _numbers(numeric_text) if number not in cited_text)
            if missing_numbers:
                unverified_numbers.append({"task": label, "numbers": missing_numbers})
            if not text:
                warnings.append(f"{label}: missing readable text")
                continue
            values.append({**value, "project_id": project_id,
                           "project_ids": [project_id] if project_id else [],
                           "project": project_id,
                           "evidence": evidence, "confidence": confidence})
        return values

    return {
        "schema_version": 3,
        "report_date": bundle["report_date"],
        "project_groups": groups,
        "plan_closure": closures,
        "knowledge": normalize_items("knowledge"),
        "blockers": normalize_items("blockers"),
        "next_actions": normalize_items("next_actions"),
        "unknown_updates": normalize_intelligence("unknown_updates"),
        "breakthroughs": normalize_intelligence("breakthroughs"),
        "project_updates": normalize_intelligence("project_updates"),
        "source_coverage": [coverage_by_ref[ref] for ref in sorted(coverage_by_ref)],
        "data_quality": _string_list(audit.get("data_quality")),
        "objective": bundle["objective"],
        "validation": {
            "validated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "allowed_evidence_count": len(allowed),
            "warnings": warnings,
            "unsupported_task_count": unsupported,
            "unverified_numbers": unverified_numbers,
            "input_truncated": bool(bundle.get("input_truncated")),
            "required_source_count": len(required_coverage),
            "covered_source_count": len(coverage_by_ref),
            "missing_source_refs": missing_coverage,
        },
        "audit_model_run": metadata or {},
    }


def validate_audit_candidate(
    audit: dict[str, Any],
    bundle: dict[str, Any],
    *,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply the strict quality gate used before selecting a model attempt."""
    required = {
        "project_groups", "plan_closure", "knowledge", "blockers", "next_actions",
        "source_coverage", "data_quality", *INTELLIGENCE_FIELDS,
    }
    missing = sorted(required - set(audit))
    if missing:
        raise ValueError(f"audit output is missing required fields: {', '.join(missing)}")
    invalid_types = sorted(key for key in required if not isinstance(audit.get(key), list))
    if invalid_types:
        raise ValueError(f"audit output fields must be arrays: {', '.join(invalid_types)}")
    validated = validate_audit(audit, bundle, metadata=metadata)
    validation = validated["validation"]
    issues = list(validation["warnings"])
    if validation["unsupported_task_count"]:
        issues.append(f"{validation['unsupported_task_count']} task(s) have no valid evidence")
    if validation["unverified_numbers"]:
        issues.append(f"{len(validation['unverified_numbers'])} task(s) contain unverified numbers")
    if issues:
        raise ValueError("audit evidence validation failed: " + "; ".join(issues))
    return validated


def repair_audit_candidate(
    audit: dict[str, Any],
    bundle: dict[str, Any],
    *,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fail closed by removing unsupported task claims from a complete audit.

    Model retries occasionally return an otherwise useful audit with one
    unsupported number or an invalid evidence reference.  The report must not
    publish that claim, but discarding the whole day is unnecessarily brittle.
    This repair keeps only allowed references, drops unsupported tasks, removes
    individual ``did``/``results`` lines containing unverified numbers, and
    records every removal in ``data_quality`` before running the strict gate
    again.
    """
    required = {
        "project_groups", "plan_closure", "knowledge", "blockers", "next_actions",
        "source_coverage", "data_quality", *INTELLIGENCE_FIELDS,
    }
    missing = sorted(required - set(audit))
    if missing:
        raise ValueError(f"audit output is missing required fields: {', '.join(missing)}")
    invalid_types = sorted(key for key in required if not isinstance(audit.get(key), list))
    if invalid_types:
        raise ValueError(f"audit output fields must be arrays: {', '.join(invalid_types)}")

    repaired = json.loads(json.dumps(audit, ensure_ascii=False))
    allowed = {str(item["ref"]) for item in bundle.get("allowed_evidence_refs", [])}
    source_text = _source_text_by_ref(bundle)
    quality = _string_list(repaired.get("data_quality"))

    groups: list[dict[str, Any]] = []
    for group in repaired.get("project_groups") or []:
        if not isinstance(group, dict):
            continue
        tasks: list[dict[str, Any]] = []
        for task in group.get("tasks") or []:
            if not isinstance(task, dict):
                continue
            title = str(task.get("title") or "未命名事项")
            evidence = list(dict.fromkeys(ref for ref in _string_list(task.get("evidence")) if ref in allowed))
            if not evidence:
                quality.append(f"任务「{title}」没有有效证据，已从核心进展中移除。")
                continue
            cited_text = " ".join(source_text.get(ref, "") for ref in evidence)
            removed_numbers: set[str] = set()
            for field in ("did", "results"):
                safe_lines: list[str] = []
                for line in _string_list(task.get(field)):
                    missing_numbers = {number for number in _numbers([line]) if number not in cited_text}
                    if missing_numbers:
                        removed_numbers.update(missing_numbers)
                        continue
                    safe_lines.append(line)
                task[field] = safe_lines
            task["evidence"] = evidence
            if removed_numbers:
                quality.append(
                    f"任务「{title}」含未获证据支持的数字（{', '.join(sorted(removed_numbers))}），相关表述已移除。"
                )
            tasks.append(task)
        if tasks:
            group["tasks"] = tasks
            groups.append(group)
    repaired["project_groups"] = groups

    for key in ("knowledge", "blockers", "next_actions", *INTELLIGENCE_FIELDS):
        retained: list[dict[str, Any]] = []
        for item in repaired.get(key) or []:
            if not isinstance(item, dict):
                continue
            evidence_key = "evidence" if "evidence" in item else "basis"
            evidence = [ref for ref in _string_list(item.get(evidence_key)) if ref in allowed]
            item[evidence_key] = evidence
            if key in INTELLIGENCE_FIELDS and not evidence:
                quality.append(f"情报字段 {key} 中一条内容没有有效证据，已移除。")
                continue
            if key in INTELLIGENCE_FIELDS:
                cited_text = " ".join(source_text.get(ref, "") for ref in evidence)
                if key in {"unknown_updates", "blocker_updates"}:
                    primary_key = "question" if key == "unknown_updates" else (
                        "blocker" if item.get("blocker") is not None else "text"
                    )
                    primary_numbers = {
                        number for number in _numbers([item.get(primary_key) or ""])
                        if number not in cited_text
                    }
                    if primary_numbers:
                        quality.append(
                            f"情报字段 {key} 的核心描述含未获证据支持的数字"
                            f"（{', '.join(sorted(primary_numbers))}），该条已移除。"
                        )
                        continue
                    missing_numbers = {
                        number for number in _numbers([item.get("missing_evidence") or ""])
                        if number not in cited_text
                    }
                    if missing_numbers:
                        item["missing_evidence"] = "缺少可核验的验证证据。"
                        quality.append(
                            f"情报字段 {key} 的缺失证据描述含未获支持的数字"
                            f"（{', '.join(sorted(missing_numbers))}），已改为保守表述。"
                        )
                elif key == "breakthroughs":
                    unsafe_text = [item.get("title") or "", item.get("change") or "",
                                   item.get("significance") or ""]
                    missing_numbers = {
                        number for number in _numbers(unsafe_text) if number not in cited_text
                    }
                    if missing_numbers:
                        quality.append(
                            "一条关键转折含未获证据支持的数字"
                            f"（{', '.join(sorted(missing_numbers))}），已移除。"
                        )
                        continue
                elif key == "project_updates":
                    missing_numbers = {
                        number for number in _numbers([item.get("summary") or ""])
                        if number not in cited_text
                    }
                    if missing_numbers:
                        quality.append(
                            "一条项目摘要含未获证据支持的数字"
                            f"（{', '.join(sorted(missing_numbers))}），已移除。"
                        )
                        continue
                retained.append(item)
        if key in INTELLIGENCE_FIELDS:
            repaired[key] = retained
    for item in repaired.get("plan_closure") or []:
        if not isinstance(item, dict):
            continue
        item["evidence"] = [ref for ref in _string_list(item.get("evidence")) if ref in allowed]
        if item.get("status") in {"completed", "partially_completed"} and not item["evidence"]:
            item["status"] = "no_evidence"
            quality.append(f"计划「{item.get('plan') or '未命名计划'}」缺少有效证据，已降级为无证据。")
    repaired["data_quality"] = list(dict.fromkeys(quality))
    return validate_audit_candidate(repaired, bundle, metadata=metadata)


def prepare_review_bundle(
    bundle: dict[str, Any],
    baseline_audit: dict[str, Any],
) -> dict[str, Any]:
    """Give the independent reviewer both the raw day and the first audit.

    The reviewer needs the original Session narratives to find
    omissions and semantic overclaims.  Passing only the first audit would make
    it a prose editor rather than an independent audit.
    """
    return {
        "schema_version": 1,
        "report_date": bundle.get("report_date"),
        "baseline_audit": baseline_audit,
        "raw_bundle": bundle,
    }


def validate_review_candidate(
    reviewed_audit: dict[str, Any],
    bundle: dict[str, Any],
    baseline_audit: dict[str, Any],
    *,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a semantic review and retain both model-run records.

    A reviewer may add a genuinely omitted task or remove/rewrite an overstated
    one, but every resulting claim still has to pass the same evidence,
    numeric, and complete-source-coverage gates as the first extraction.
    """
    reviewed = repair_audit_candidate(reviewed_audit, bundle, metadata=metadata)
    review_metadata = reviewed.pop("audit_model_run", metadata or {})
    reviewed["audit_model_run"] = baseline_audit.get("audit_model_run") or {}
    reviewed["semantic_review_model_run"] = review_metadata
    reviewed["validation"]["semantic_reviewed"] = True
    return reviewed


def _joined(values: Any, empty: str = "无已验证记录") -> str:
    items = _string_list(values)
    return "；".join(items) if items else empty


def _refs(values: Any) -> str:
    return _joined(values)


def render_audit_markdown(audit: dict[str, Any]) -> str:
    """Render a validated audit without another model call.

    The structured audit already contains the readable Chinese task fields.
    Keeping this final step deterministic prevents a prose model from changing
    counts, rounding metrics, or weakening evidence labels.
    """
    report_date = str(audit.get("report_date") or "").strip()
    if not report_date:
        raise ValueError("validated audit is missing report_date")
    validation = audit.get("validation") or {}
    if validation.get("warnings") or validation.get("unsupported_task_count") \
            or validation.get("unverified_numbers") or validation.get("missing_source_refs"):
        raise ValueError("validated audit still contains unresolved evidence issues")

    lines = [f"# 日报 {report_date}", "", "## 昨日计划闭环", ""]
    closures = audit.get("plan_closure") or []
    if closures:
        for item in closures:
            lines.extend((
                f"- **{item.get('plan') or '未命名计划'}**",
                f"  - 状态：{PLAN_STATUS_LABELS.get(str(item.get('status')), item.get('status') or '未知')}",
                f"  - 原因：{item.get('reason') or '无已验证说明'}",
                f"  - 证据：{_refs(item.get('evidence'))}",
            ))
    else:
        lines.append("无已验证记录")

    lines.extend(("", "## 核心进展", ""))
    groups = audit.get("project_groups") or []
    if not groups:
        lines.append("无已验证记录")
    for group in groups:
        lines.extend((f"### {group.get('name') or '其他'}", ""))
        for task in group.get("tasks") or []:
            confidence = CONFIDENCE_LABELS.get(
                str(task.get("confidence")), str(task.get("confidence") or "未知"),
            )
            files = [f"`{value}`" for value in _string_list(task.get("files"))]
            lines.extend((
                f"#### {task.get('title') or '未命名事项'}", "",
                f"- **做了什么**：{_joined(task.get('did'))}",
                f"- **为什么**：{_joined(task.get('why'), '原始证据未说明')}",
                f"- **结果**：{_joined(task.get('results'))}",
                f"- **关键文件**：{_joined(files)}",
                f"- **证据与可信度**：{_refs(task.get('evidence'))}；{confidence}", "",
            ))

    objective = audit.get("objective") or {}
    sessions = objective.get("sessions") or {}
    tool_calls = objective.get("tool_calls") or {}
    token_usage = objective.get("token_usage") or {}
    claude = token_usage.get("claude_or_phanthy") or {}
    codex = token_usage.get("codex") or {}
    lines.extend((
        "## Token 消耗", "",
        f"- Session：Claude/Phanthy {sessions.get('claude_or_phanthy', 0)}，Codex {sessions.get('codex', 0)}。",
        f"- 工具调用：Claude/Phanthy {tool_calls.get('claude_or_phanthy', 0)}，Codex {tool_calls.get('codex', 0)}。",
        f"- Codex：缓存输入 {int(codex.get('cached_input_tokens', 0) or 0):,}，非缓存输入 {int(codex.get('uncached_input_tokens', 0) or 0):,}，输出 {int(codex.get('output_tokens', 0) or 0):,}，总 Token {int(codex.get('total_tokens', 0) or 0):,}。",
        f"- Codex 请求数：{int(codex.get('requests', 0) or 0)}；推理输出 {int(codex.get('reasoning_output_tokens', 0) or 0):,}；缓存读取比例 {codex.get('cache_read_ratio')}。",
        f"- Claude/Phanthy：{'有可用统计' if claude.get('available') else '无可用 Token 统计'}。",
        f"- Git commit：{int(objective.get('git_commits', 0) or 0)}；变更文件：{int(objective.get('changed_files', 0) or 0)}。",
        "- Token 是模型用量，不等同于人工工作量。", "",
        "## 关键结论与知识", "",
    ))
    knowledge = audit.get("knowledge") or []
    if not knowledge:
        lines.append("无已验证记录")
    for item in knowledge:
        confidence = CONFIDENCE_LABELS.get(
            str(item.get("confidence")), str(item.get("confidence") or "未知"),
        )
        text = str(item.get("text") or "").rstrip("。；")
        scope = f"；适用范围：{item['scope']}" if item.get("scope") else ""
        lines.append(f"- {text}{scope}；证据：{_refs(item.get('evidence'))}；{confidence}")

    lines.extend(("", "## 阻塞 / 待解决", ""))
    blockers = audit.get("blockers") or []
    if not blockers:
        lines.append("无已验证记录")
    for item in blockers:
        project = f"**{item['project']}**：" if item.get("project") else ""
        lines.extend((
            f"- {project}{item.get('text') or '未命名阻塞'}",
            f"  - 下一步：{item.get('next') or '尚未明确'}",
            f"  - 证据：{_refs(item.get('evidence'))}",
        ))

    lines.extend(("", "## 明日计划", ""))
    next_actions = audit.get("next_actions") or []
    if not next_actions:
        lines.append("无已验证记录")
    for item in next_actions:
        project = f"**{item['project']}**：" if item.get("project") else ""
        lines.extend((
            f"- {project}{item.get('text') or '未命名动作'}",
            f"  - 验收：{item.get('next') or '尚未明确'}",
            f"  - 依据：{_refs(item.get('evidence'))}",
        ))

    lines.extend(("", "## 数据完整性", ""))
    quality = _string_list(audit.get("data_quality"))
    lines.extend(f"- {value}" for value in quality)
    lines.extend((
        f"- 来源覆盖：{validation.get('covered_source_count', 0)}/{validation.get('required_source_count', 0)}；无效任务：{validation.get('unsupported_task_count', 0)}；未验证数字：无。",
        f"- 输入是否截断：{'是' if validation.get('input_truncated') else '否'}。", "",
        "## 推送摘要", "",
    ))
    summary: list[str] = []
    for group in groups:
        for task in group.get("tasks") or []:
            result = _string_list(task.get("results"))[:2]
            text = f"{group.get('name') or '其他'}：{task.get('title') or '未命名事项'}。"
            if result:
                text += _joined(result).rstrip("。；") + "。"
            summary.append(text)
    if blockers:
        summary.append("当前阻塞：" + "；".join(
            str(item.get("text") or "").rstrip("。；") for item in blockers
        ) + "。")
    if next_actions:
        summary.append("下一步：" + "；".join(
            str(item.get("text") or "").rstrip("。；") for item in next_actions
        ) + "。")
    push = "\n\n".join(summary) or "今日无已验证记录。"
    if len(push) > 2_000:
        push = push[:1_988].rsplit("。", 1)[0] + "。…"
    lines.append(push)

    report = "\n".join(lines).rstrip() + "\n"
    missing_sections = [section for section in REQUIRED_SECTIONS if section not in report]
    if missing_sections:
        raise ValueError(f"deterministic report is missing sections: {', '.join(missing_sections)}")
    return report


def finalize_markdown(output: dict[str, Any], report_date: str) -> tuple[str, dict[str, Any]]:
    result, metadata = unpack_model_output(output)
    if not isinstance(result, str):
        raise ValueError("writer result must be Markdown text")
    text = result.replace("===REPORT_START===", "").replace("===REPORT_END===", "").strip()
    heading = f"# 日报 {report_date}"
    start = text.find(heading)
    if start < 0:
        raise ValueError(f"writer output is missing exact heading: {heading}")
    text = text[start:].strip()
    lines = text.splitlines()
    while len(lines) > 1 and lines[1].strip() in {"---", "***"}:
        lines.pop(1)
    # Some OpenAI-compatible Claude routers append the stdin JSON after the
    # requested answer.  It is never part of the report; discard only a
    # recognizable pipeline bundle that appears after the final section.
    push_index = next((index for index, line in enumerate(lines) if line.strip() == "## 推送摘要"), -1)
    if push_index >= 0:
        trailing_bundle = next(
            (index for index in range(push_index + 1, len(lines))
             if lines[index].lstrip().startswith('{"schema_version"')
             or lines[index].lstrip().startswith('{"report_date"')),
            None,
        )
        if trailing_bundle is not None:
            lines = lines[:trailing_bundle]
    text = "\n".join(lines).strip() + "\n"
    missing = [section for section in REQUIRED_SECTIONS if section not in text]
    if missing:
        raise ValueError(f"writer output is missing required sections: {', '.join(missing)}")
    return text, metadata


def _main() -> int:
    parser = argparse.ArgumentParser(description="Prepare, validate, and render the evidence-audited daily report")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--date", required=True); prepare.add_argument("--sessions", type=Path, required=True)
    prepare.add_argument("--codex", type=Path, required=True); prepare.add_argument("--git", type=Path, required=True)
    prepare.add_argument("--files", type=Path, required=True)
    prepare.add_argument("--previous-plan", type=Path); prepare.add_argument("--output", type=Path, required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--bundle", type=Path, required=True); validate.add_argument("--model-output", type=Path, required=True)
    validate.add_argument("--output", type=Path, required=True)
    validate.add_argument("--requested-model")
    validate.add_argument("--repair-unsafe-claims", action="store_true")
    review_prepare = sub.add_parser("review-prepare")
    review_prepare.add_argument("--bundle", type=Path, required=True)
    review_prepare.add_argument("--audit", type=Path, required=True)
    review_prepare.add_argument("--output", type=Path, required=True)
    review_validate = sub.add_parser("review-validate")
    review_validate.add_argument("--bundle", type=Path, required=True)
    review_validate.add_argument("--baseline", type=Path, required=True)
    review_validate.add_argument("--model-output", type=Path, required=True)
    review_validate.add_argument("--output", type=Path, required=True)
    review_validate.add_argument("--requested-model")
    render = sub.add_parser("render")
    render.add_argument("--audit", type=Path, required=True)
    render.add_argument("--output", type=Path, required=True)
    finalize = sub.add_parser("finalize")
    finalize.add_argument("--date", required=True); finalize.add_argument("--model-output", type=Path, required=True)
    finalize.add_argument("--output", type=Path, required=True); finalize.add_argument("--metadata-output", type=Path)
    finalize.add_argument("--requested-model")
    args = parser.parse_args()

    if args.command == "prepare":
        value = prepare_bundle(report_date=args.date, sessions=args.sessions, codex=args.codex,
                               git=args.git, files=args.files, previous_plan=args.previous_plan)
        _write_json(args.output, value)
        return 0
    if args.command == "validate":
        bundle = _read_json(args.bundle, None)
        if not isinstance(bundle, dict):
            raise SystemExit("invalid audit bundle")
        audit, metadata = _model_json(args.model_output)
        if args.requested_model:
            metadata["requested_model"] = args.requested_model
        validator = repair_audit_candidate if args.repair_unsafe_claims else validate_audit_candidate
        _write_json(args.output, validator(audit, bundle, metadata=metadata))
        return 0
    if args.command == "review-prepare":
        bundle = _read_json(args.bundle, None)
        baseline = _read_json(args.audit, None)
        if not isinstance(bundle, dict) or not isinstance(baseline, dict):
            raise SystemExit("invalid review inputs")
        _write_json(args.output, prepare_review_bundle(bundle, baseline))
        return 0
    if args.command == "review-validate":
        bundle = _read_json(args.bundle, None)
        baseline = _read_json(args.baseline, None)
        if not isinstance(bundle, dict) or not isinstance(baseline, dict):
            raise SystemExit("invalid review validation inputs")
        audit, metadata = _model_json(args.model_output)
        if args.requested_model:
            metadata["requested_model"] = args.requested_model
        _write_json(
            args.output,
            validate_review_candidate(audit, bundle, baseline, metadata=metadata),
        )
        return 0
    if args.command == "render":
        audit = _read_json(args.audit, None)
        if not isinstance(audit, dict):
            raise SystemExit("invalid validated audit")
        markdown = render_audit_markdown(audit)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(markdown, encoding="utf-8")
        os.replace(temporary, args.output)
        return 0
    outer = _read_json(args.model_output, None)
    if not isinstance(outer, dict):
        raise SystemExit("invalid writer output")
    markdown, metadata = finalize_markdown(outer, args.date)
    if args.requested_model:
        metadata["requested_model"] = args.requested_model
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(markdown, encoding="utf-8")
    os.replace(temporary, args.output)
    if args.metadata_output:
        _write_json(args.metadata_output, metadata)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
