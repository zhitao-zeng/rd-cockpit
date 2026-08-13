"""Curated, readable research reviews for project detail pages.

The automatic architecture snapshot is intentionally conservative and may lag
behind a manual project review.  This module exposes a small, versioned config
overlay for explanations that have already been checked against project
artifacts.  Reading a brief never invokes a model.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .config import load_config


def load_research_brief(home: Path, project_id: str) -> dict[str, Any] | None:
    public = home / "config" / "project-research-briefs.yaml"
    local = home / "config" / "project-research-briefs.local.yaml"
    config = load_config(local if local.is_file() else public)
    projects = config.get("projects") or {}
    raw = projects.get(project_id) if isinstance(projects, Mapping) else None
    if not isinstance(raw, Mapping):
        return None
    return {
        "schema_version": int(config.get("schema_version") or 1),
        "project_id": project_id,
        **dict(raw),
    }
