from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any


PROJECT_ID = re.compile(r"^[a-z][a-z0-9_]*$")
PROJECT_LIFECYCLES = {"active", "dormant", "historical"}


def _effective_config_path(path: Path) -> Path:
    """Prefer a private local overlay without ever requiring it in Git.

    Public clones ship anonymous ``*.yaml`` templates.  A user can keep their
    real repository paths and research metadata in the matching
    ``*.local.yaml`` file, which is ignored by Git.
    """
    local = path.with_name(f"{path.stem}.local{path.suffix}")
    return local if local.is_file() else path


def config_path(home: Path | None = None) -> Path:
    root = home or Path(os.environ.get("RD_COCKPIT_HOME", Path(__file__).resolve().parents[1]))
    override = os.environ.get("RD_PROJECTS_CONFIG")
    if override:
        return Path(override).expanduser().resolve()
    return _effective_config_path(root.expanduser().resolve() / "config" / "projects.yaml")


def ensure_local_project_config(home: Path) -> Path:
    """Create the ignored, user-owned registry before the first mutation."""
    override = os.environ.get("RD_PROJECTS_CONFIG")
    if override:
        return Path(override).expanduser().resolve()
    root = home.expanduser().resolve()
    public = root / "config" / "projects.yaml"
    local = root / "config" / "projects.local.yaml"
    if local.is_file():
        return local
    local.parent.mkdir(parents=True, exist_ok=True)
    template = public.read_text(encoding="utf-8") if public.is_file() else (
        "projects: {}\n\nmachine: local\nmachines: {}\n"
    )
    temporary = local.with_suffix(local.suffix + ".tmp")
    temporary.write_text(template, encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, local)
    return local


@lru_cache(maxsize=32)
def _load_config_cached(path_text: str, mtime_ns: int, size: int) -> dict[str, Any]:
    path = Path(path_text)
    text = path.read_text(encoding="utf-8")
    try:
        import yaml

        value = yaml.safe_load(text)
    except ImportError:
        value = json.loads(text)
    return value or {"projects": {}, "machines": {}}


def load_config(path: Path) -> dict[str, Any]:
    path = _effective_config_path(path)
    try:
        stat = path.stat()
    except FileNotFoundError:
        return {"projects": {}, "machines": {}}
    return _load_config_cached(str(path.resolve()), stat.st_mtime_ns, stat.st_size)


def project_catalog(home: Path | None = None) -> dict[str, str]:
    config = load_config(config_path(home))
    return {
        str(project_id): str(value.get("name") or project_id)
        for project_id, value in (config.get("projects") or {}).items()
        if isinstance(value, dict)
    }


def project_match_rules(home: Path | None = None) -> list[tuple[str, tuple[str, ...], tuple[str, ...]]]:
    """Return configured heading aliases and concrete path markers."""
    config = load_config(config_path(home))
    output = []
    for project_id, value in (config.get("projects") or {}).items():
        if not isinstance(value, dict):
            continue
        aliases = [str(project_id), str(value.get("name") or ""),
                   *(str(item) for item in value.get("match_keywords") or [])]
        paths = [str(value.get("repo_path") or ""),
                 *(str(item) for item in value.get("match_paths") or [])]
        output.append((str(project_id), tuple(item for item in aliases if item),
                       tuple(item for item in paths if item)))
    return output


def add_project(
    home: Path,
    *,
    project_id: str,
    name: str,
    repo_path: Path,
    priority: str = "P2",
    lifecycle_status: str = "active",
    match_keywords: list[str] | None = None,
    match_paths: list[str] | None = None,
    verification_stages: list[str] | None = None,
    allow_missing: bool = False,
) -> dict[str, Any]:
    """Append one project without reformatting the existing YAML document."""
    if not PROJECT_ID.fullmatch(project_id):
        raise ValueError("project id must match ^[a-z][a-z0-9_]*$")
    if not name.strip():
        raise ValueError("project name cannot be empty")
    if lifecycle_status not in PROJECT_LIFECYCLES:
        raise ValueError(f"lifecycle status must be one of {sorted(PROJECT_LIFECYCLES)}")
    repo = repo_path.expanduser().resolve()
    if not allow_missing and not repo.is_dir():
        raise ValueError(f"repository directory does not exist: {repo}")
    path = ensure_local_project_config(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    config = load_config(path)
    if project_id in (config.get("projects") or {}):
        raise ValueError(f"project {project_id!r} already exists")
    entry = {
        "name": name.strip(),
        "repo_path": str(repo),
        "priority": priority,
        "lifecycle_status": lifecycle_status,
        "match_keywords": list(dict.fromkeys([
            repo.name, name.strip(), *(match_keywords or []),
        ])),
        "match_paths": list(dict.fromkeys([str(repo), *(match_paths or [])])),
        "verification_stages": list(dict.fromkeys(
            verification_stages or ["implementation", "local_validation", "delivery"]
        )),
    }
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - PyYAML is a core dependency
        raise RuntimeError("adding a project requires PyYAML") from exc
    rendered = yaml.safe_dump({project_id: entry}, allow_unicode=True, sort_keys=False).rstrip()
    block = "\n".join(f"  {line}" if line else line for line in rendered.splitlines()) + "\n"
    text = path.read_text(encoding="utf-8") if path.exists() else "projects:\n\nmachines: {}\n"
    # Public starter configs deliberately use the compact ``projects: {}``
    # form. Expand that empty mapping before inserting the first local entry.
    text = re.sub(r"(?m)^projects:\s*\{\s*\}\s*$", "projects:", text, count=1)
    if not re.search(r"(?m)^projects:\s*$", text):
        raise ValueError(f"configuration is missing a top-level projects mapping: {path}")
    projects_header = re.search(r"(?m)^projects:\s*$", text)
    assert projects_header is not None  # guarded above
    next_top_level = re.search(
        r"(?m)^(?![ \t#\r\n])[^:\n]+:\s*",
        text[projects_header.end():],
    )
    position = (
        projects_header.end() + next_top_level.start()
        if next_top_level
        else len(text)
    )
    prefix = text[:position].rstrip() + "\n"
    suffix = text[position:]
    updated = prefix + block + "\n" + suffix
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(updated, encoding="utf-8")
    os.chmod(temporary, 0o600)
    # Parse the exact candidate before replacing the authoritative config.
    yaml.safe_load(temporary.read_text(encoding="utf-8"))
    os.replace(temporary, path)
    os.chmod(path, 0o600)
    return {"project_id": project_id, **entry, "config_path": str(path)}


def project_config(home: Path, project_id: str) -> dict[str, Any]:
    config = load_config(config_path(home))
    try:
        return config["projects"][project_id]
    except KeyError as exc:
        known = ", ".join(sorted(config.get("projects", {})))
        raise ValueError(f"Unknown project {project_id!r}; configured projects: {known}") from exc


def machine_name(home: Path) -> str:
    config = load_config(config_path(home))
    import os
    return os.environ.get("RD_MACHINE", config.get("machine", "local"))
