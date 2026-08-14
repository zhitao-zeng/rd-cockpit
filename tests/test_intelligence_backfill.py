from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from rd_cockpit.intelligence_backfill import _record, _validate_day, backfill
from rd_cockpit.ledger import Ledger
from rd_cockpit.semantic_feedback import record_feedback


def _report(day: str, project: str, task: str, result: str) -> str:
    return f"""# 日报 {day}

## 核心进展
### {project}
#### {task}
- **做了什么**：完成验证。
- **结果**：{result}

## 关键结论与知识
- {result}
"""


def _configure(tmp_path: Path, monkeypatch) -> None:
    config = tmp_path / "projects.yaml"
    config.write_text(
        "projects:\n  asr:\n    name: ASR\n  ocr:\n    name: OCR\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("RD_PROJECTS_CONFIG", str(config))
    monkeypatch.setenv("RD_COCKPIT_HOME", str(tmp_path / "cockpit"))


def test_backfill_writes_hash_bound_sidecars_and_reuses_cache(tmp_path: Path, monkeypatch) -> None:
    _configure(tmp_path, monkeypatch)
    (tmp_path / "2026-08-01.md").write_text(
        _report("2026-08-01", "具身智能 ASR", "本地验证", "CER 为 12.5%。"), encoding="utf-8",
    )
    (tmp_path / "2026-08-02.md").write_text(
        _report("2026-08-02", "具身智能 ASR", "远端验证", "CER 为 9.8%。"), encoding="utf-8",
    )
    calls = []

    def fake_request(model, instruction):
        calls.append(model)
        days = []
        for item in instruction["days"]:
            last = len(item["numbered_markdown"].splitlines())
            value = "12.5%" if item["date"].endswith("01") else "9.8%"
            days.append({"date": item["date"], "unknown_updates": [], "blocker_updates": [],
                         "breakthroughs": [{"project_id": "asr", "title": "指标变化",
                           "change": f"CER 为 {value}", "significance": "改变当前效果判断",
                           "evidence": [f"report:{item['date']}:L1-L{last}"]}],
                         "project_updates": [{"project_id": "asr",
                           "summary": f"ASR 完成验证，CER 为 {value}。",
                           "evidence": [f"report:{item['date']}:L1-L{last}"]}]})
        return {"days": days}, {"model": model}

    monkeypatch.setattr("rd_cockpit.intelligence_backfill._request_any_model", fake_request)
    before = (tmp_path / "2026-08-01.md").read_text(encoding="utf-8")
    first = backfill(directory=tmp_path, days=5, batch_days=7, target=date(2026, 8, 3))

    assert first["processed"] == ["2026-08-01", "2026-08-02"]
    assert first["model_calls"] == 1
    assert not first["failed"]
    assert (tmp_path / "2026-08-01.md").read_text(encoding="utf-8") == before
    sidecar = json.loads((tmp_path / "data" / "2026-08-01_intelligence_validated.json").read_text())
    assert sidecar["project_updates"][0]["project_id"] == "asr"
    assert sidecar["source_sha256"]

    # A pre-fingerprint cache can be revalidated locally; adding cache metadata
    # must not spend another model call for identical source evidence.
    sidecar["schema_version"] = 1
    sidecar.pop("prompt_version", None)
    sidecar.pop("policy_fingerprint", None)
    (tmp_path / "data" / "2026-08-01_intelligence_validated.json").write_text(
        json.dumps(sidecar), encoding="utf-8",
    )

    second = backfill(directory=tmp_path, days=5, batch_days=7, target=date(2026, 8, 3))
    assert second["processed"] == []
    assert second["cached"] == ["2026-08-01", "2026-08-02"]
    assert second["model_calls"] == 0
    assert calls == ["codex:gpt-5.6-sol@medium"]


def test_backfill_rejects_evidence_owned_by_another_project(tmp_path: Path, monkeypatch) -> None:
    _configure(tmp_path, monkeypatch)
    path = tmp_path / "2026-08-01.md"
    path.write_text("""# 日报 2026-08-01

## 核心进展
### 具身智能 ASR
#### ASR 远端验证
- **结果**：CER 为 9.8%。
### OCR
#### OCR 本地评测
- **结果**：1-CER 为 0.76。
""", encoding="utf-8")
    record = _record(path)
    raw = {"date": "2026-08-01", "unknown_updates": [], "blocker_updates": [], "breakthroughs": [],
           "project_updates": [{"project_id": "ocr", "summary": "OCR 的 CER 为 9.8%。",
                                "evidence": ["report:2026-08-01:L4-L6"]}]}

    validated = _validate_day(raw, record, {"asr", "ocr", "unassigned"}, {})
    assert validated["project_updates"] == []
    assert "belongs to" in validated["validation_errors"][0]


def test_feedback_invalidates_only_its_cited_report(tmp_path: Path, monkeypatch) -> None:
    _configure(tmp_path, monkeypatch)
    for day in ("2026-08-01", "2026-08-02"):
        (tmp_path / f"{day}.md").write_text(
            _report(day, "具身智能 ASR", "验证", "CER 为 9.8%。"), encoding="utf-8",
        )
    calls: list[list[str]] = []

    def fake_request(model, instruction):
        calls.append([item["date"] for item in instruction["days"]])
        output = []
        for item in instruction["days"]:
            last = len(item["numbered_markdown"].splitlines())
            output.append({"date": item["date"], "unknown_updates": [], "blocker_updates": [],
                           "breakthroughs": [], "project_updates": [{
                               "project_id": "asr", "summary": "ASR 完成验证，CER 为 9.8%。",
                               "evidence": [f"report:{item['date']}:L1-L{last}"],
                           }]})
        return {"days": output}, {"model": model}

    monkeypatch.setattr("rd_cockpit.intelligence_backfill._request_any_model", fake_request)
    backfill(directory=tmp_path, days=5, target=date(2026, 8, 3))
    cockpit = tmp_path / "cockpit"
    ledger = Ledger(cockpit / ".rd-cockpit" / "events.sqlite")
    record_feedback(cockpit, ledger, {
        "view": "storyline", "item_id": "storyline:asr", "project_id": "asr",
        "rating": "incorrect", "text": "摘要有误", "source_dates": ["2026-08-01"],
    })
    ledger.close()

    result = backfill(directory=tmp_path, days=5, target=date(2026, 8, 3))

    assert calls == [["2026-08-01", "2026-08-02"], ["2026-08-01"]]
    assert result["processed"] == ["2026-08-01"]
    assert result["cached"] == ["2026-08-02"]


def test_quality_gate_rejects_empty_semantic_snapshot(tmp_path: Path, monkeypatch) -> None:
    _configure(tmp_path, monkeypatch)
    (tmp_path / "2026-08-01.md").write_text(
        _report("2026-08-01", "具身智能 ASR", "验证", "CER 为 9.8%。"), encoding="utf-8",
    )
    monkeypatch.setattr(
        "rd_cockpit.intelligence_backfill._request_any_model",
        lambda model, instruction: ({"days": [{
            "date": "2026-08-01", "unknown_updates": [], "blocker_updates": [],
            "breakthroughs": [], "project_updates": [],
        }]}, {"model": model}),
    )

    result = backfill(directory=tmp_path, days=5, target=date(2026, 8, 2))

    assert len(result["failed"]) == 1
    assert "quality gate rejected" in result["failed"][0]["error"]
    assert not (tmp_path / "data" / "2026-08-01_intelligence_validated.json").exists()
