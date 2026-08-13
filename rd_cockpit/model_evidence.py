"""Reviewed public model-family evidence for algorithm architecture analysis.

The registry is deliberately local and curated.  Nightly analysis never
searches the web: a human or an explicit maintenance task reviews primary
sources first, then stores short paraphrased facts in ``config/model-evidence.yaml``.
Public family facts can explain a model that the project already identifies,
but they never prove which checkpoint is deployed or validate project metrics.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

import yaml


REGISTRY_VERSION = 1
ALLOWED_SCOPES = {"family_reference", "official_undisclosed"}
ALLOWED_SOURCE_TYPES = {
    "official_docs", "official_repository", "official_model_card", "official_paper",
}
SOURCE_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def registry_path(home: Path) -> Path:
    public = home / "config" / "model-evidence.yaml"
    local = home / "config" / "model-evidence.local.yaml"
    return local if local.is_file() else public


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _official_url(value: Any, source_id: str) -> str:
    url = str(value or "").strip()
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError(f"model evidence {source_id}: url must be a public https URL")
    return url


def load_registry(home: Path) -> dict[str, dict[str, Any]]:
    path = registry_path(home)
    if not path.is_file():
        return {}
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, Mapping) or int(value.get("version") or 0) != REGISTRY_VERSION:
        raise ValueError("model evidence registry has an unsupported version")
    raw_sources = value.get("sources") or {}
    if not isinstance(raw_sources, Mapping):
        raise ValueError("model evidence registry sources must be a mapping")
    output: dict[str, dict[str, Any]] = {}
    for raw_id, raw in raw_sources.items():
        source_id = str(raw_id)
        if not SOURCE_ID_RE.fullmatch(source_id) or not isinstance(raw, Mapping):
            raise ValueError(f"invalid model evidence source id: {source_id}")
        scope = str(raw.get("scope") or "")
        source_type = str(raw.get("source_type") or "")
        if scope not in ALLOWED_SCOPES:
            raise ValueError(f"model evidence {source_id}: unsupported scope {scope}")
        if source_type not in ALLOWED_SOURCE_TYPES:
            raise ValueError(f"model evidence {source_id}: unsupported source_type {source_type}")
        retrieved_at = str(raw.get("retrieved_at") or "")
        try:
            date.fromisoformat(retrieved_at)
        except ValueError as exc:
            raise ValueError(f"model evidence {source_id}: retrieved_at must be YYYY-MM-DD") from exc
        projects = _strings(raw.get("projects"))
        facts = _strings(raw.get("facts"))
        if not projects or not facts:
            raise ValueError(f"model evidence {source_id}: projects and facts are required")
        output[source_id] = {
            "id": source_id,
            "label": str(raw.get("label") or source_id).strip(),
            "url": _official_url(raw.get("url"), source_id),
            "scope": scope,
            "source_type": source_type,
            "retrieved_at": retrieved_at,
            "projects": projects,
            "model_aliases": _strings(raw.get("model_aliases")),
            "facts": facts[:12],
        }
    return output


def evidence_for_project(home: Path, project_id: str, *, limit: int = 28) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return bounded evidence facts and browser-safe source metadata."""
    evidence: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    for source_id, source in load_registry(home).items():
        if project_id not in source["projects"]:
            continue
        sources.append({key: source[key] for key in (
            "id", "label", "url", "scope", "source_type", "retrieved_at", "model_aliases",
        )})
        for index, fact in enumerate(source["facts"], 1):
            payload = {
                "source_id": source_id, "url": source["url"], "scope": source["scope"],
                "source_type": source["source_type"], "retrieved_at": source["retrieved_at"],
                "fact": fact,
            }
            digest = hashlib.sha256(
                json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            evidence.append({
                "ref": f"external:{source_id}:F{index}",
                "source_id": source_id,
                "path": source["label"],
                "line_start": None,
                "line_end": None,
                "sha256": digest,
                "kind": "external",
                "scope": source["scope"],
                "source_type": source["source_type"],
                "url": source["url"],
                "retrieved_at": source["retrieved_at"],
                "text": fact,
            })
            if len(evidence) >= limit:
                return evidence, sources
    return evidence, sources
