from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from rd_cockpit.api import create_app
from rd_cockpit.daily_source import parse_report
from rd_cockpit.experiment_intelligence import _record, _usage_pools, _validate_day, backfill, experiment_intelligence


def _report(day: str = "2026-08-01") -> str:
    return f"""# 日报 {day}

## 核心进展
### 具身智能 OCR
#### OCR 双后端评测
- **做了什么**：比较 TensorRT 和 MNN 两种后端。
- **为什么**：确认 Jetson 上的部署选择。
- **结果**（reported）：TensorRT 延迟 47ms，MNN 延迟 82ms；测试集为 waic-v2。
- **关键文件**：results/benchmark.json
- **证据**：session:codex-ocr

### 具身智能 ASR
#### 修改 README 并运行单元测试
- **做了什么**：修正文档。
- **结果**：18 项单元测试通过。
"""


def test_daily_parser_accepts_confidence_qualified_result_field(tmp_path: Path) -> None:
    path = tmp_path / "2026-08-01.md"
    path.write_text(_report(), encoding="utf-8")
    parsed = parse_report(path)
    task = parsed["groups"][0]["tasks"][0]
    assert task["why"] == ["确认 Jetson 上的部署选择。"]
    assert task["results"] == ["TensorRT 延迟 47ms，MNN 延迟 82ms；测试集为 waic-v2。"]


def test_experiment_validator_keeps_readable_evidence_bound_record(tmp_path: Path) -> None:
    path = tmp_path / "2026-08-01.md"
    path.write_text(_report(), encoding="utf-8")
    record = _record(path)
    raw = {
        "date": "2026-08-01",
        "experiments": [{
            "project_id": "ocr", "title": "OCR 双后端延迟评测", "kind": "benchmark",
            "question": "TensorRT 是否比 MNN 更快", "method": "在 Jetson 上比较两个后端",
            "models": [{"name": "TensorRT", "role": "candidate"}, {"name": "MNN", "role": "baseline"}],
            "datasets": [{"name": "waic-v2", "scope": "同一测试集"}], "parameters": [],
            "metrics": [{"name": "延迟", "value": "47", "unit": "ms", "scope": "Jetson / waic-v2 / TensorRT", "direction": "lower"}],
            "result_status": "improved", "result_summary": "TensorRT 延迟 47ms，MNN 延迟 82ms。",
            "conclusion": "TensorRT 在该 Jetson 口径下更快。", "decision_impact": "保留 TensorRT 作为候选后端",
            "verification_scope": "jetson", "machine": "Jetson", "commit_sha": "", "artifacts": ["results/benchmark.json"],
            "evidence": ["report:2026-08-01:L4-L10"],
        }],
    }
    result = _validate_day(raw, record, {"ocr"}, {"model": "codex:gpt-5.6-sol@medium"})
    assert not result["validation_errors"]
    assert result["experiments"][0]["session_ids"] == ["codex-ocr"]
    assert result["experiments"][0]["metrics"][0]["value"] == "47"


def test_experiment_validator_rejects_unsupported_metric_and_cross_project(tmp_path: Path) -> None:
    path = tmp_path / "2026-08-01.md"
    path.write_text(_report(), encoding="utf-8")
    record = _record(path)
    base = {
        "title": "ASR 指标", "kind": "evaluation", "question": "", "method": "运行评测",
        "models": [], "datasets": [], "parameters": [],
        "metrics": [{"name": "WER", "value": "9.8", "unit": "%", "scope": "test", "direction": "lower"}],
        "result_status": "improved", "result_summary": "WER 为 9.8%。", "conclusion": "效果提升。",
        "decision_impact": "", "verification_scope": "offline", "machine": "", "commit_sha": "", "artifacts": [],
    }
    raw = {"date": "2026-08-01", "experiments": [
        {**base, "project_id": "asr", "evidence": ["report:2026-08-01:L4-L10"]},
        {**base, "project_id": "ocr", "evidence": ["report:2026-08-01:L4-L10"]},
    ]}
    result = _validate_day(raw, record, {"ocr", "asr"}, {})
    assert result["experiments"] == []
    assert any("belongs to" in error for error in result["validation_errors"])
    assert any("unsupported numbers" in error for error in result["validation_errors"])


def test_backfill_is_cached_and_api_reads_without_model_calls(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "2026-08-01.md"
    path.write_text(_report(), encoding="utf-8")
    calls: list[str] = []

    def fake_model(model: str, instruction: dict):
        calls.append(model)
        return {"days": [{"date": "2026-08-01", "experiments": [{
            "project_id": "ocr", "title": "OCR 双后端延迟评测", "kind": "benchmark",
            "question": "", "method": "比较 TensorRT 和 MNN 两种后端",
            "models": [{"name": "TensorRT", "role": "candidate"}, {"name": "MNN", "role": "baseline"}],
            "datasets": [{"name": "waic-v2", "scope": "同一测试集"}], "parameters": [],
            "metrics": [{"name": "延迟", "value": "47", "unit": "ms", "scope": "Jetson / waic-v2 / TensorRT", "direction": "lower"}],
            "result_status": "improved", "result_summary": "TensorRT 延迟 47ms，MNN 延迟 82ms。",
            "conclusion": "TensorRT 在该口径下更快。", "decision_impact": "",
            "verification_scope": "jetson", "machine": "Jetson", "commit_sha": "", "artifacts": ["results/benchmark.json"],
            "evidence": ["report:2026-08-01:L4-L10"],
        }]}]}, {"model": model, "provider": "test"}

    monkeypatch.setattr("rd_cockpit.experiment_intelligence._request_any_model", fake_model)
    first = backfill(directory=tmp_path, days=2, target=date(2026, 8, 2), projects=["ocr"])
    second = backfill(directory=tmp_path, days=2, target=date(2026, 8, 2), projects=["ocr"])
    assert first["processed"] == ["2026-08-01"]
    assert second["cached"] == ["2026-08-01"]
    assert calls == ["codex:gpt-5.6-sol@medium"]

    # Browser/API projection is a pure sidecar read and marks Token as absent,
    # never inventing a per-experiment cost.
    response = experiment_intelligence(tmp_path, days=2, target=date(2026, 8, 2), directory=tmp_path)
    assert response["counts"]["records"] == 1
    assert response["records"][0]["token_context"]["attribution"] == "unavailable"
    assert "不是某条实验独占成本" in response["notes"][2]


def test_usage_pool_differences_cumulative_long_session_counters(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    for day, totals in (("2026-08-01", [100, 150]), ("2026-08-02", [220])):
        (tmp_path / f"{day}.md").write_text(f"# 日报 {day}\n", encoding="utf-8")
        (data / f"{day}_codex_sessions.json").write_text(json.dumps({"sessions": [{
            "session_id": "long-codex", "cwd": "/workspace/text-recognition",
            "token_usage": {"available": True, "total_tokens": total},
        } for total in totals]}), encoding="utf-8")
    pools = _usage_pools(tmp_path)
    assert pools[("2026-08-01", "ocr")]["total_tokens"] == 150
    assert pools[("2026-08-02", "ocr")]["total_tokens"] == 70
    assert pools[("2026-08-02", "ocr")]["quality"] == "estimated"
    assert pools[("2026-08-02", "ocr")]["long_sessions"] == 1


def test_fallback_sidecar_is_retried_by_primary_model(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "2026-08-01.md"
    path.write_text(_report(), encoding="utf-8")
    attempts: list[str] = []

    def empty_response(model: str, instruction: dict):
        attempts.append(model)
        if model.startswith("codex:") and attempts.count(model) == 1:
            raise RuntimeError("temporary transport error")
        return {"days": [{"date": "2026-08-01", "experiments": []}]}, {
            "model": model, "provider": "test",
        }

    monkeypatch.setattr("rd_cockpit.experiment_intelligence._request_any_model", empty_response)
    first = backfill(directory=tmp_path, days=2, target=date(2026, 8, 2), projects=["ocr"])
    second = backfill(directory=tmp_path, days=2, target=date(2026, 8, 2), projects=["ocr"])
    assert first["model_calls"] == 2
    assert second["processed"] == ["2026-08-01"]
    assert attempts == ["codex:gpt-5.6-sol@medium", "deepseek-local", "codex:gpt-5.6-sol@medium"]
    sidecar = json.loads((tmp_path / "data" / "experiments" / "2026-08-01.json").read_text())
    assert sidecar["model_run"]["model"] == "codex:gpt-5.6-sol@medium"


def test_read_only_experiment_api_returns_sidecar_and_rejects_unknown_project(
    tmp_path: Path, monkeypatch,
) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "2026-08-01.md").write_text(_report(), encoding="utf-8")
    monkeypatch.setenv("RD_DAILY_REPORT_DIR", str(reports))
    monkeypatch.setattr("rd_cockpit.experiment_intelligence._request_any_model", lambda model, instruction: (
        {"days": [{"date": "2026-08-01", "experiments": []}]}, {"model": model, "provider": "test"},
    ))
    backfill(directory=reports, days=2, target=date(2026, 8, 2), projects=["ocr"])
    home = tmp_path / "cockpit"
    (home / "config").mkdir(parents=True)
    (home / "config" / "projects.yaml").write_text(
        "projects:\n  ocr:\n    name: OCR\n    repo_path: /tmp/ocr\n", encoding="utf-8",
    )
    client = TestClient(create_app(home))
    response = client.get("/simple/experiment-intelligence", params={"target_date": "2026-08-02"})
    assert response.status_code == 200
    assert response.json()["generated_from"].startswith("Daily Report")
    assert client.get("/simple/experiment-intelligence", params={"project": "missing"}).status_code == 404
