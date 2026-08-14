"""Canonical project identities for user-facing projections.

Collectors and historical reports may contain old heuristic buckets.  They
remain valid raw evidence, but only projects from the user's registry may
become first-class Dashboard projects.  Explicit ``legacy_project_ids`` (or a
top-level ``project_aliases`` mapping) can migrate an old label without
rewriting the source report.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Iterable

from .config import config_path, load_config


UNASSIGNED = "unassigned"


def _config(home: Path | None = None) -> dict[str, Any]:
    return load_config(config_path(home))


def registered_project_names(home: Path | None = None) -> dict[str, str]:
    config = _config(home)
    return {
        str(project_id): str(value.get("name") or project_id)
        for project_id, value in (config.get("projects") or {}).items()
        if isinstance(value, dict)
    }


def project_aliases(home: Path | None = None) -> dict[str, str]:
    config = _config(home)
    projects = config.get("projects") or {}
    aliases: dict[str, str] = {}
    for raw, target in (config.get("project_aliases") or {}).items():
        if str(target) in projects and str(raw) != str(target):
            aliases[str(raw)] = str(target)
    for project_id, value in projects.items():
        if not isinstance(value, dict):
            continue
        for raw in value.get("legacy_project_ids") or []:
            if str(raw) and str(raw) != str(project_id):
                aliases[str(raw)] = str(project_id)
    # ``asr_other`` was the old broad fallback.  When a registry has an
    # explicit primary ASR project, plain ASR evidence belongs there; specific
    # dialect/alignment/evaluation evidence is classified before this step.
    if "asr" in projects:
        aliases.setdefault("asr_other", "asr")
    return aliases


def canonical_project_id(project_id: object, home: Path | None = None) -> str:
    value = str(project_id or "").strip()
    if not value or value == UNASSIGNED:
        return UNASSIGNED
    names = registered_project_names(home)
    if value in names:
        return value
    return project_aliases(home).get(value, UNASSIGNED)


def canonical_project_ids(
    values: Iterable[object], home: Path | None = None, *, default_unassigned: bool = True,
) -> list[str]:
    output = list(dict.fromkeys(canonical_project_id(value, home) for value in values))
    concrete = [value for value in output if value != UNASSIGNED]
    if concrete:
        return concrete
    return [UNASSIGNED] if default_unassigned and output else ([] if not default_unassigned else [UNASSIGNED])


def visible_project_names(home: Path | None = None) -> dict[str, str]:
    return {**registered_project_names(home), UNASSIGNED: "未登记历史记录"}


def canonicalize_report(report: dict[str, Any], home: Path | None = None) -> dict[str, Any]:
    """Return a copy whose visible project IDs all belong to the registry."""
    output = copy.deepcopy(report)
    raw_unmapped: set[str] = set()
    report_ids: list[str] = []
    for group in output.get("groups") or []:
        group_ids: list[str] = []
        for task in group.get("tasks") or []:
            raw = [str(value) for value in task.get("project_ids") or [] if str(value)]
            mapped = canonical_project_ids(raw, home)
            raw_unmapped.update(
                value for value in raw
                if value != UNASSIGNED and canonical_project_id(value, home) == UNASSIGNED
            )
            if raw != mapped:
                task["raw_project_ids"] = raw
            task["project_ids"] = mapped
            group_ids.extend(mapped)
        group["project_ids"] = canonical_project_ids(group_ids, home)
        report_ids.extend(group["project_ids"])
    output["project_ids"] = canonical_project_ids(report_ids, home)
    output["project_names"] = visible_project_names(home)
    output["unmapped_project_ids"] = sorted(raw_unmapped)
    if "_supplement" in output:
        output["_supplement"] = canonicalize_supplement(output.get("_supplement"), home)
    return output


def canonicalize_supplement(
    supplement: dict[str, Any] | None, home: Path | None = None,
) -> dict[str, Any] | None:
    if not isinstance(supplement, dict):
        return supplement
    output = copy.deepcopy(supplement)
    merged: dict[str, dict[str, Any]] = {}
    additive = (
        "sessions", "claude_sessions", "codex_sessions", "requests", "tool_calls",
        "duration_minutes", "tokens", "claude_tokens", "codex_tokens", "commits",
        "changed_files",
    )
    names = visible_project_names(home)
    for item in output.get("projects") or []:
        project_id = canonical_project_id(item.get("project_id"), home)
        target = merged.setdefault(project_id, {
            "project_id": project_id, "name": names.get(project_id, project_id),
            **{field: 0 for field in additive},
        })
        for field in additive:
            target[field] += item.get(field, 0) or 0
    output["projects"] = sorted(merged.values(), key=lambda item: (-int(item["tokens"]), item["project_id"]))
    return output
