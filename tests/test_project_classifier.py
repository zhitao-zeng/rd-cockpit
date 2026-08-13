from __future__ import annotations

import json
from pathlib import Path

from rd_cockpit.daily_source import parse_report
from rd_cockpit.project_classifier import classify_directory, task_fingerprint


REPORT = """# 日报 2026-07-23

## 核心进展
### ASR
#### ASR 项目总结与决策复盘
- **做了什么**：系统回顾所有 ASR 项目并生成复盘文档。
- **关键文件**：`docs/superpowers/plans/asr-projects-overview.md`
"""


def test_cached_llm_classification_is_applied_without_page_load_request(tmp_path: Path) -> None:
    report_path = tmp_path / "2026-07-23.md"
    report_path.write_text(REPORT, encoding="utf-8")
    original = parse_report(report_path)
    task = original["groups"][0]["tasks"][0]
    assert task["project_ids"] == ["asr_other"]
    key = task_fingerprint("2026-07-23", task)
    cache = {
        "schema_version": 1,
        "entries": {key: {"project_id": "research_tools", "confidence": 0.91,
                          "reason": "内容是项目复盘文档", "model": "deepseek-local"}},
        "runs": [],
    }
    data = tmp_path / "data"
    data.mkdir()
    (data / "project-classifications.json").write_text(json.dumps(cache), encoding="utf-8")

    classified = parse_report(report_path)["groups"][0]["tasks"][0]

    assert classified["project_ids"] == ["research_tools"]
    assert classified["classification"]["source"] == "llm_cache"


def test_classifier_batches_only_unresolved_asr_other(tmp_path: Path, monkeypatch) -> None:
    report_path = tmp_path / "2026-07-23.md"
    report_path.write_text(REPORT, encoding="utf-8")

    def fake_request(model, records, allowed):
        assert model == "codex:gpt-5.6-sol@medium"
        assert len(records) == 1
        return ([{"key": records[0]["key"], "project_id": "asr_other",
                  "confidence": 0.8, "reason": "泛 ASR 复盘，无法细分"}],
                {"input_tokens": 100, "output_tokens": 20})

    monkeypatch.setattr("rd_cockpit.project_classifier._request_model", fake_request)
    result = classify_directory(tmp_path)

    assert result["classified"] == 1
    cache = json.loads((tmp_path / "data" / "project-classifications.json").read_text())
    assert next(iter(cache["entries"].values()))["project_id"] == "asr_other"


def test_classifier_falls_back_to_deepseek_local(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "2026-07-23.md").write_text(REPORT, encoding="utf-8")
    calls = []

    def fake_request(model, records, allowed):
        calls.append(model)
        if model.startswith("codex:"):
            raise RuntimeError("Codex unavailable")
        return ([{"key": records[0]["key"], "project_id": "asr_other",
                  "confidence": 0.9, "reason": "泛 ASR"}], {})

    monkeypatch.setattr("rd_cockpit.project_classifier._request_model", fake_request)
    result = classify_directory(tmp_path)

    assert calls == ["codex:gpt-5.6-sol@medium", "deepseek-local"]
    assert result["model"] == "deepseek-local"
