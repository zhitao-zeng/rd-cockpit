from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from rd_cockpit.api import create_app
from rd_cockpit.config import load_config
from rd_cockpit.project_discovery import (
    _material_group_items,
    accept_candidate,
    read_discovery,
    refresh_discovery,
    scan_candidates,
)


def _git_init(path: Path) -> None:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / "README.md").write_text("# demo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=path, check=True)


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "cockpit"
    (home / "config").mkdir(parents=True)
    (home / "config" / "projects.yaml").write_text("projects:\n\nmachines: {}\n", encoding="utf-8")
    return home


def _fake_sessions(monkeypatch, repo: Path) -> None:
    import rd_cockpit.project_discovery as module

    source = repo.parent / "session.jsonl"
    source.write_text("{}\n", encoding="utf-8")
    calls = 0

    def recent_files(_paths, _days):
        nonlocal calls
        calls += 1
        return [source] if calls % 2 == 1 else []

    monkeypatch.setattr(module, "_recent_files", recent_files)
    monkeypatch.setattr(module, "_codex_session", lambda _path: {
        "session_id": "codex-new-1", "cwd": str(repo), "occurred_at": "2026-08-11T10:00:00+00:00",
        "topics": ["实现新的语音翻译评测项目"],
        "observed_workspace_paths": [str(repo / "src" / "evaluate.py")],
        "write_workspace_paths": [str(repo / "src" / "evaluate.py")],
    })


def test_session_repo_is_deterministically_discovered_then_codex_reviewed_and_cached(
    tmp_path: Path, monkeypatch,
) -> None:
    home = _home(tmp_path)
    repo = tmp_path / "translation-asr"
    _git_init(repo)
    _fake_sessions(monkeypatch, repo)
    candidates = scan_candidates(home, days=30)
    assert len(candidates) == 1
    assert candidates[0]["repo_path"] == str(repo)
    assert candidates[0]["evidence_strength"] == "strong"

    calls = 0

    def reviewer(model, payload):
        nonlocal calls
        calls += 1
        assert model == "codex:gpt-5.6-sol@medium"
        item = payload["candidates"][0]
        return [{
            "candidate_id": item["candidate_id"], "decision": "new_project",
            "project_group": "asr_translation",
            "suggested_project_id": "asr_translation", "suggested_name": "ASR 语音翻译",
            "summary": "构建并评测语音翻译链路。", "existing_project_id": "",
            "confidence": 0.92, "reason": "Session 在独立仓库内写入 evaluate.py。",
        }], {"input_tokens": 100, "output_tokens": 20}

    generated = refresh_discovery(home, reviewer=reviewer)
    assert generated["counts"]["new_projects"] == 1
    assert generated["candidates"][0]["review"]["suggested_project_id"] == "asr_translation"
    assert calls == 1

    cached = refresh_discovery(home, reviewer=reviewer)
    assert cached["run"]["pending_reviews"] == 0
    assert calls == 1

    accepted = accept_candidate(home, generated["candidates"][0]["candidate_id"])
    assert accepted["project_id"] == "asr_translation"
    assert load_config(home / "config" / "projects.yaml")["projects"]["asr_translation"]["repo_path"] == str(repo)
    assert read_discovery(home)["counts"]["candidates"] == 0


def test_failed_codex_review_never_registers_or_invents_a_project(tmp_path: Path, monkeypatch) -> None:
    home = _home(tmp_path)
    repo = tmp_path / "reference-repo"
    _git_init(repo)
    _fake_sessions(monkeypatch, repo)

    def failed(_model, _payload):
        raise RuntimeError("review unavailable")

    value = refresh_discovery(home, reviewer=failed)
    assert value["run"]["error"].endswith("review unavailable")
    assert value["counts"]["pending_review"] == 1
    assert not load_config(home / "config" / "projects.yaml")["projects"]

    client = TestClient(create_app(home))
    response = client.get("/simple/project-discovery")
    assert response.status_code == 200
    assert response.json()["counts"]["pending_review"] == 1


def test_incremental_review_receives_known_unregistered_project_groups(tmp_path: Path, monkeypatch) -> None:
    home = _home(tmp_path)
    repo = tmp_path / "product-backend"
    _git_init(repo)
    _fake_sessions(monkeypatch, repo)

    seen_groups = []

    def reviewer(_model, payload):
        seen_groups.append(payload["known_unregistered_project_groups"])
        item = payload["candidates"][0]
        return [{
            "candidate_id": item["candidate_id"], "decision": "new_project",
            "project_group": "product", "suggested_project_id": "product",
            "suggested_name": "产品", "summary": "产品后端。", "existing_project_id": "",
            "confidence": 0.9, "reason": "存在独立仓库写入。",
        }], {}

    refresh_discovery(home, reviewer=reviewer)
    assert seen_groups == [[]]
    cache_path = home / ".rd-cockpit" / "project-discovery.json"
    import json
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    item = next(iter(cache["candidates"].values()))
    item["evidence_digest"] = "outdated"
    item["prompt_version"] = 3
    cache_path.write_text(json.dumps(cache), encoding="utf-8")

    refresh_discovery(home, reviewer=reviewer)
    assert seen_groups[-1][0]["project_group"] == "product"


def test_empty_parent_repository_is_not_used_as_a_group_match_path() -> None:
    items = [
        {"repo_path": "/work/product", "git": {"tracked_files": 0}},
        {"repo_path": "/work/product/backend", "git": {"tracked_files": 12}},
        {"repo_path": "/work/product/mcp", "git": {"tracked_files": 8}},
    ]
    assert [item["repo_path"] for item in _material_group_items(items)] == [
        "/work/product/backend", "/work/product/mcp",
    ]
