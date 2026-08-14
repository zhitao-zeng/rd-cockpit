from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rd_cockpit.algorithm_architecture import (
    _instruction,
    analyze_project,
    architecture_index,
    build_evidence_bundle,
    validate_snapshot,
)
from rd_cockpit.api import create_app
from rd_cockpit.model_evidence import load_registry


@pytest.fixture(autouse=True)
def _isolated_daily_reports(tmp_path: Path, monkeypatch) -> None:
    reports = tmp_path / "daily-reports"
    reports.mkdir()
    monkeypatch.setenv("RD_DAILY_REPORT_DIR", str(reports))
    monkeypatch.setenv("RD_DAILY_REPORT_LEGACY_DIRS", "")


def _home(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "cockpit"
    repo = tmp_path / "algorithm-project"
    (home / "config").mkdir(parents=True)
    repo.mkdir()
    (home / "config" / "projects.yaml").write_text(
        "projects:\n  demo:\n    name: Demo Algorithm\n    repo_path: " + str(repo) + "\n"
        "    priority: P0\n",
        encoding="utf-8",
    )
    (repo / "architecture.py").write_text(
        "class TinyEncoder:\n"
        "    \"\"\"Two Conv blocks produce 32-channel features.\"\"\"\n"
        "\nclass DepthHead:\n"
        "    \"\"\"Predicts one inverse-depth map.\"\"\"\n",
        encoding="utf-8",
    )
    (repo / "results.json").write_text(
        json.dumps({"model": "TinyDepth", "f1": 0.91, "input": "256x256"}),
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com",
         "commit", "-qm", "initial"], check=True,
    )
    return home, repo


def _raw(bundle: dict) -> dict:
    architecture_ref = next(item["ref"] for item in bundle["evidence"] if item["path"] == "architecture.py")
    result_ref = next(item["ref"] for item in bundle["evidence"] if item["path"] == "results.json")
    return {
        "project_id": "demo", "status": "ready", "algorithm_type": "model_pipeline",
        "objective": "预测逆深度。", "summary": "TinyDepth 使用轻量编码器生成逆深度。",
        "pipeline": {
            "nodes": [
                {"id": "image", "label": "图像", "category": "input", "summary": "输入图像",
                 "status": "current", "evidence": [result_ref]},
                {"id": "tiny_depth", "label": "TinyDepth", "category": "model", "summary": "预测逆深度",
                 "status": "current", "evidence": [architecture_ref]},
                {"id": "depth", "label": "逆深度", "category": "output", "summary": "单通道输出",
                 "status": "current", "evidence": [architecture_ref]},
            ],
            "edges": [
                {"source": "image", "target": "tiny_depth", "label": "输入", "data": "RGB",
                 "evidence": [result_ref]},
                {"source": "tiny_depth", "target": "depth", "label": "预测", "data": "inverse depth",
                 "evidence": [architecture_ref]},
            ],
        },
        "models": [{
            "id": "tiny_depth", "node_id": "tiny_depth", "name": "TinyDepth", "variant": "tiny",
            "role": "预测逆深度", "status": "current", "architecture_status": "verified",
            "architecture_summary": "编码器连接深度头。", "input": "256x256 RGB", "output": "逆深度图",
            "blocks": [
                {"id": "encoder", "name": "TinyEncoder", "type": "CNN", "role": "提取特征",
                 "details": "2 个卷积块，32 通道", "evidence": [architecture_ref]},
                {"id": "head", "name": "DepthHead", "type": "Head", "role": "生成逆深度",
                 "details": "单通道", "evidence": [architecture_ref]},
            ],
            "quantization": "", "parameters": "", "artifact_size": "",
            "design_rationale": ["轻量"], "limitations": [],
            "metrics": [{"name": "F1", "value": "0.91", "unit": "", "scope": "demo",
                         "verification": "observed", "evidence": [result_ref]}],
            "evidence": [architecture_ref, result_ref],
        }],
        "design_decisions": [], "alternatives": [], "algorithm_diff": [],
        "open_questions": [], "warnings": [],
    }


def _model_registry(home: Path, *, url: str = "https://models.example.org/tiny") -> None:
    (home / "config" / "model-evidence.yaml").write_text(
        "version: 1\n"
        "sources:\n"
        "  tiny_official:\n"
        "    label: TinyDepth official\n"
        f"    url: {url}\n"
        "    source_type: official_repository\n"
        "    scope: family_reference\n"
        "    retrieved_at: 2026-08-11\n"
        "    projects: [demo]\n"
        "    model_aliases: [TinyDepth]\n"
        "    facts:\n"
        "      - TinyDepth family uses an encoder and a depth head.\n",
        encoding="utf-8",
    )


def test_bundle_is_bounded_and_source_referenced(tmp_path: Path) -> None:
    home, repo = _home(tmp_path)
    (repo / "config.yaml").write_text("api_key: super-secret\nmodel: TinyDepth\n", encoding="utf-8")
    outside = tmp_path / "outside-model.py"
    outside.write_text("class SecretArchitecture: pass\n", encoding="utf-8")
    (repo / "external-architecture.py").symlink_to(outside)

    bundle = build_evidence_bundle(home, "demo")

    assert bundle["project"]["id"] == "demo"
    assert bundle["source_state"]["head"]
    assert bundle["evidence"]
    assert all(item["ref"].startswith("source:repo:") for item in bundle["evidence"])
    rendered = json.dumps(bundle, ensure_ascii=False)
    assert "super-secret" not in rendered
    assert "SecretArchitecture" not in rendered
    instruction = json.dumps(_instruction(bundle), ensure_ascii=False)
    assert str(repo) not in instruction


def test_validation_drops_unsupported_metric_and_unknown_reference(tmp_path: Path) -> None:
    home, _ = _home(tmp_path)
    bundle = build_evidence_bundle(home, "demo")
    raw = _raw(bundle)
    raw["models"][0]["metrics"].append({
        "name": "Latency", "value": "47ms", "unit": "ms", "scope": "demo",
        "verification": "observed", "evidence": ["source:repo:missing.json:L1-L2"],
    })

    value = validate_snapshot(raw, bundle, {"model": "fake"})

    assert value["status"] == "ready"
    assert [item["name"] for item in value["models"][0]["metrics"]] == ["F1"]
    assert any("unknown evidence ref" in item or "catalog evidence" in item for item in value["validation_errors"])


def test_official_family_evidence_is_separate_and_cannot_verify_deployment(tmp_path: Path) -> None:
    home, _ = _home(tmp_path)
    _model_registry(home)
    bundle = build_evidence_bundle(home, "demo")
    external_ref = next(item["ref"] for item in bundle["evidence"] if item["kind"] == "external")
    raw = _raw(bundle)
    raw["models"][0]["evidence"].append(external_ref)
    raw["models"][0]["blocks"][0]["evidence"] = [external_ref]
    raw["models"][0]["architecture_status"] = "verified"
    raw["models"][0]["metrics"] = [{
        "name": "Family score", "value": "1", "unit": "", "scope": "official",
        "verification": "observed", "evidence": [external_ref],
    }]

    value = validate_snapshot(raw, bundle, {"model": "fake"})

    model = value["models"][0]
    assert model["architecture_basis"] == "mixed"
    assert model["architecture_status"] == "partial"
    assert model["metrics"] == []
    assert value["evidence_catalog"][external_ref]["url"] == "https://models.example.org/tiny"
    assert any("external family references" in item for item in value["validation_errors"])


def test_official_undisclosed_evidence_keeps_model_opaque(tmp_path: Path) -> None:
    home, _ = _home(tmp_path)
    _model_registry(home)
    registry = home / "config" / "model-evidence.yaml"
    registry.write_text(
        registry.read_text(encoding="utf-8").replace(
            "scope: family_reference", "scope: official_undisclosed"
        ),
        encoding="utf-8",
    )
    bundle = build_evidence_bundle(home, "demo")
    external_ref = next(item["ref"] for item in bundle["evidence"] if item["kind"] == "external")
    raw = _raw(bundle)
    raw["models"][0]["evidence"].append(external_ref)
    raw["models"][0]["blocks"][0]["evidence"] = [external_ref]

    value = validate_snapshot(raw, bundle, {"model": "fake"})

    model = value["models"][0]
    assert model["architecture_basis"] == "undisclosed"
    assert model["architecture_status"] == "opaque"
    assert model["blocks"] == []


def test_model_evidence_registry_rejects_non_https_url(tmp_path: Path) -> None:
    home, _ = _home(tmp_path)
    _model_registry(home, url="http://internal.example/model")
    with pytest.raises(ValueError, match="public https"):
        load_registry(home)


def test_analyze_project_caches_same_source_hash(tmp_path: Path, monkeypatch) -> None:
    home, _ = _home(tmp_path)
    calls: list[str] = []

    def fake_request(model, instruction, repo, **_kwargs):
        calls.append(model)
        bundle = build_evidence_bundle(home, "demo")
        return _raw(bundle), {"model": model, "provider": "fake", "usage": {}}

    monkeypatch.setattr("rd_cockpit.algorithm_architecture._request_model", fake_request)
    first = analyze_project(home, "demo")
    second = analyze_project(home, "demo")

    assert first["status"] == "ready"
    assert first["evidence_catalog"]
    assert second["cache_hit"] is True
    assert calls == ["codex:gpt-5.6-sol@medium"]
    assert architecture_index(home)["counts"]["ready"] == 1


def test_unrelated_git_commit_does_not_invalidate_algorithm_snapshot(
    tmp_path: Path, monkeypatch,
) -> None:
    home, repo = _home(tmp_path)
    calls: list[str] = []

    def fake_request(model, instruction, selected_repo, **_kwargs):
        calls.append(model)
        return _raw(build_evidence_bundle(home, "demo")), {
            "model": model, "provider": "fake", "usage": {},
        }

    monkeypatch.setattr("rd_cockpit.algorithm_architecture._request_model", fake_request)
    analyze_project(home, "demo")
    (repo / "release-notes.txt").write_text("packaging only\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "release-notes.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com",
         "commit", "-qm", "docs: packaging note"], check=True,
    )

    second = analyze_project(home, "demo")
    assert second["refresh_action"] == "cache_hit"
    assert calls == ["codex:gpt-5.6-sol@medium"]


def test_validation_rejects_cross_project_output(tmp_path: Path) -> None:
    home, _ = _home(tmp_path)
    bundle = build_evidence_bundle(home, "demo")
    raw = _raw(bundle)
    raw["project_id"] = "other"

    try:
        validate_snapshot(raw, bundle, {})
    except ValueError as exc:
        assert "project_id" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("cross-project output must be rejected")


def test_read_only_api_serves_snapshot_without_absolute_source_roots(tmp_path: Path, monkeypatch) -> None:
    home, repo = _home(tmp_path)
    (home / "config" / "project-research-briefs.yaml").write_text(
        "schema_version: 1\nprojects:\n  demo:\n    title: Demo review\n    models: []\n",
        encoding="utf-8",
    )

    def fake_request(model, instruction, selected_repo, **_kwargs):
        bundle = build_evidence_bundle(home, "demo")
        return _raw(bundle), {"model": model, "provider": "fake", "usage": {}}

    monkeypatch.setattr("rd_cockpit.algorithm_architecture._request_model", fake_request)
    analyze_project(home, "demo")
    client = TestClient(create_app(home))

    index = client.get("/simple/algorithm-architecture")
    detail = client.get("/simple/algorithm-architecture/demo")

    assert index.status_code == 200
    assert index.json()["counts"]["ready"] == 1
    assert detail.status_code == 200
    body = detail.json()
    assert body["snapshot"]["models"][0]["name"] == "TinyDepth"
    assert body["research_brief"]["title"] == "Demo review"
    assert all("root" not in source for source in body["snapshot"]["sources"])
    assert str(repo) not in json.dumps(body, ensure_ascii=False)
