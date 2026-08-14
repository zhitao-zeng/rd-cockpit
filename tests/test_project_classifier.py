from __future__ import annotations

import json
from pathlib import Path

from rd_cockpit.daily_source import parse_report
from rd_cockpit.project_classifier import (
    PROMPT_VERSION, SCHEMA_VERSION, _legacy_task_fingerprint,
    classify_directory, task_fingerprint,
)
from rd_cockpit.semantic_policy import catalog_fingerprint, policy_fingerprint


REPORT = """# 日报 2026-07-23

## 核心进展
### ASR
#### ASR 项目总结与决策复盘
- **做了什么**：系统回顾所有 ASR 项目并生成复盘文档。
- **关键文件**：`docs/superpowers/plans/asr-projects-overview.md`
"""


def _configure(tmp_path: Path, monkeypatch) -> dict[str, str]:
    config = tmp_path / "projects.yaml"
    config.write_text(
        "projects:\n  asr:\n    name: ASR\n  research_tools:\n    name: Research Tools\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("RD_PROJECTS_CONFIG", str(config))
    return {"asr": "ASR", "research_tools": "Research Tools"}


def test_cached_llm_classification_is_applied_without_page_load_request(tmp_path: Path, monkeypatch) -> None:
    allowed = _configure(tmp_path, monkeypatch)
    report_path = tmp_path / "2026-07-23.md"
    report_path.write_text(REPORT, encoding="utf-8")
    original = parse_report(report_path)
    task = original["groups"][0]["tasks"][0]
    assert task["project_ids"] == ["asr_other"]
    key = task_fingerprint("2026-07-23", task)
    policy = policy_fingerprint(
        "daily-report-project-classification",
        schema_version=SCHEMA_VERSION,
        prompt_version=PROMPT_VERSION,
        models=("codex:gpt-5.6-sol@medium", "deepseek-local"),
        extra={"catalog": catalog_fingerprint(allowed)},
    )
    cache = {
        "schema_version": SCHEMA_VERSION,
        "entries": {key: {"project_id": "research_tools", "confidence": 0.91,
                          "reason": "内容是项目复盘文档", "model": "deepseek-local",
                          "policy_fingerprint": policy}},
        "runs": [],
    }
    data = tmp_path / "data"
    data.mkdir()
    (data / "project-classifications.json").write_text(json.dumps(cache), encoding="utf-8")

    classified = parse_report(report_path)["groups"][0]["tasks"][0]

    assert classified["project_ids"] == ["research_tools"]
    assert classified["classification"]["source"] == "llm_cache"


def test_classifier_batches_only_unresolved_asr_other(tmp_path: Path, monkeypatch) -> None:
    _configure(tmp_path, monkeypatch)
    report_path = tmp_path / "2026-07-23.md"
    report_path.write_text(REPORT, encoding="utf-8")

    def fake_request(model, records, allowed):
        assert model == "codex:gpt-5.6-sol@medium"
        assert len(records) == 1
        assert set(allowed) == {"asr", "research_tools"}
        return ([{"key": records[0]["key"], "project_id": "research_tools",
                  "confidence": 0.8, "reason": "这是跨项目复盘工具"}],
                {"input_tokens": 100, "output_tokens": 20})

    monkeypatch.setattr("rd_cockpit.project_classifier._request_model", fake_request)
    result = classify_directory(tmp_path)

    assert result["classified"] == 1
    cache = json.loads((tmp_path / "data" / "project-classifications.json").read_text())
    assert next(iter(cache["entries"].values()))["project_id"] == "research_tools"


def test_classifier_falls_back_to_deepseek_local(tmp_path: Path, monkeypatch) -> None:
    _configure(tmp_path, monkeypatch)
    (tmp_path / "2026-07-23.md").write_text(REPORT, encoding="utf-8")
    calls = []

    def fake_request(model, records, allowed):
        calls.append(model)
        if model.startswith("codex:"):
            raise RuntimeError("Codex unavailable")
        return ([{"key": records[0]["key"], "project_id": "research_tools",
                  "confidence": 0.9, "reason": "跨项目研究工具"}], {})

    monkeypatch.setattr("rd_cockpit.project_classifier._request_model", fake_request)
    result = classify_directory(tmp_path)

    assert calls == ["codex:gpt-5.6-sol@medium", "deepseek-local"]
    assert result["model"] == "deepseek-local"


def test_classifier_migrates_legacy_decision_without_model_call(tmp_path: Path, monkeypatch) -> None:
    _configure(tmp_path, monkeypatch)
    report_path = tmp_path / "2026-07-23.md"
    report_path.write_text(REPORT, encoding="utf-8")
    task = parse_report(report_path)["groups"][0]["tasks"][0]
    data = tmp_path / "data"
    data.mkdir()
    (data / "project-classifications.json").write_text(json.dumps({
        "schema_version": 1,
        "entries": {
            _legacy_task_fingerprint("2026-07-23", task): {
                "project_id": "research_tools", "confidence": 0.91,
                "reason": "内容是项目复盘文档", "model": "codex:gpt-5.6-sol@medium",
            },
        },
        "runs": [],
    }), encoding="utf-8")

    monkeypatch.setattr(
        "rd_cockpit.project_classifier._request_model",
        lambda *_: (_ for _ in ()).throw(AssertionError("migration should not call a model")),
    )
    result = classify_directory(tmp_path)
    assert result["status"] == "migrated"
    assert result["migrated"] == 1
    assert parse_report(report_path)["groups"][0]["tasks"][0]["project_ids"] == ["research_tools"]
