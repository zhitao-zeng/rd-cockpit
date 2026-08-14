from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from rd_cockpit.intelligence import _intelligence_for, project_intelligence
from rd_cockpit.intelligence_backfill import (
    DEFAULT_FALLBACK, DEFAULT_MODEL, PROMPT_VERSION, SCHEMA_VERSION,
)
from rd_cockpit.semantic_policy import catalog_fingerprint, policy_fingerprint


def _report(day: str, result: str, blocker: str = "", next_action: str = "") -> str:
    return f"""# 日报 {day}

## 昨日计划闭环
- Robot Speech 验证：completed

## 核心进展
### Robot Speech
#### Robot Speech 远端验证
- **做了什么**：运行统一验证。
- **为什么**：确认方案是否可用。
- **结果**：{result}
- **关键文件**：`robot-speech/asr.py`

## 关键结论与知识
- Robot Speech 结果只适用于统一验证集。

## 阻塞 / 待解决
{f'- {blocker}' if blocker else '无已验证记录'}

## 明日计划
{f'- {next_action}' if next_action else '无已验证记录'}
"""


def _audit(day: str, unknowns: list[dict], breakthroughs: list[dict], updates: list[dict],
           blockers: list[dict] | None = None) -> dict:
    return {
        "report_date": day, "project_groups": [], "unknown_updates": unknowns,
        "blocker_updates": blockers or [], "breakthroughs": breakthroughs, "project_updates": updates,
        "validation": {"warnings": [], "unsupported_task_count": 0,
                       "unverified_numbers": [], "missing_source_refs": []},
    }


def test_project_intelligence_builds_delta_unknown_lifecycle_and_story(tmp_path: Path, monkeypatch) -> None:
    reports = tmp_path / "daily-reports"
    data = reports / "data"
    data.mkdir(parents=True)
    (reports / "2026-08-01.md").write_text(
        _report("2026-08-01", "CER = 12.5%，本地验证通过。", "ASR 是否能在 Jetson 保持精度？"),
        encoding="utf-8",
    )
    (reports / "2026-08-02.md").write_text(
        _report("2026-08-02", "CER = 9.8%，Jetson 验证通过。", next_action="ASR 还需 Judge 验证"),
        encoding="utf-8",
    )
    evidence = ["session:s1"]
    (data / "2026-08-01_audit_validated.json").write_text(json.dumps(_audit(
        "2026-08-01",
        [{"project_ids": ["asr"], "question": "是否能在 Jetson 保持精度？", "action": "open",
          "priority": "high", "missing_evidence": "Jetson A/B", "evidence": evidence}],
        [],
        [{"project_ids": ["asr"], "summary": "项目完成本地基线，但 Jetson 表现仍未知。", "evidence": evidence}],
        [{"project_ids": ["asr"], "blocker_id": "b1", "blocker": "Jetson 环境不可用",
          "action": "open", "priority": "high", "evidence": evidence}],
    )), encoding="utf-8")
    (data / "2026-08-02_audit_validated.json").write_text(json.dumps(_audit(
        "2026-08-02",
        [{"project_ids": ["asr"], "question": "是否能在 Jetson 保持精度？", "action": "resolve",
          "priority": "high", "missing_evidence": "", "evidence": evidence},
         {"project_ids": ["asr"], "question": "Judge 是否保持同等收益？", "action": "open",
          "priority": "medium", "missing_evidence": "Judge submission", "evidence": evidence}],
        [{"project_ids": ["asr"], "title": "Jetson 验证推进", "change": "CER 从 12.5% 降至 9.8%",
          "significance": "远端结果改变采用判断", "evidence": evidence}],
        [{"project_ids": ["asr"], "summary": "Jetson 验证支持当前方案，下一步转向 Judge。", "evidence": evidence}],
        [{"project_ids": ["asr"], "blocker_id": "b1", "blocker": "Jetson 环境不可用",
          "action": "resolve", "priority": "high", "evidence": evidence}],
    )), encoding="utf-8")
    (data / "2026-08-02_codex_sessions.json").write_text(json.dumps({
        "total_sessions": 1, "sessions": [{"cwd": "/tmp/robot-speech", "duration_min": 20,
          "token_usage": {"available": True, "total_tokens": 1234, "requests": 2}}],
    }), encoding="utf-8")
    monkeypatch.setenv("RD_DAILY_REPORT_DIR", str(reports))
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "projects.yaml").write_text(
        "projects:\n  asr:\n    name: Robot Speech\n    repo_path: /tmp/robot-speech\n    verification_stages: []\n", encoding="utf-8",
        )
    monkeypatch.setenv("RD_PROJECTS_CONFIG", str(tmp_path / "config" / "projects.yaml"))

    result = project_intelligence(tmp_path, days=30, baseline=date(2026, 8, 1), target=date(2026, 8, 3))

    pulse = result["pulses"][0]
    detail = result["project_details"]["asr"]
    assert pulse["tokens"] == 1234
    assert pulse["open_unknowns"] == 1
    assert detail["unknowns"][0]["question"] == "Judge 是否保持同等收益？"
    assert detail["delta"]["results"][0]["date"] == "2026-08-02"
    assert detail["breakthroughs"][0]["source_mode"] == "audited"
    assert "Jetson 验证支持当前方案" in detail["storyline"]["summary"]
    assert result["effort_progress"][0]["resolved_unknowns"] == 1
    assert result["effort_progress"][0]["resolved_blockers"] == 1
    assert detail["delta"]["blockers_resolved"][0]["text"] == "Jetson 环境不可用"


def test_historical_unknown_fallback_ignores_data_quality_disclaimer(tmp_path: Path, monkeypatch) -> None:
    reports = tmp_path / "daily-reports"
    reports.mkdir()
    (reports / "2026-08-01.md").write_text(
        _report("2026-08-01", "上述具体数值来自 session 叙述，未逐条核对，标注待验证。",
                "ASR 缺少真实录音验证"), encoding="utf-8",
    )
    monkeypatch.setenv("RD_DAILY_REPORT_DIR", str(reports))
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "projects.yaml").write_text(
        "projects:\n  asr:\n    name: Robot Speech\n    repo_path: /tmp/robot-speech\n    verification_stages: []\n", encoding="utf-8",
        )
    monkeypatch.setenv("RD_PROJECTS_CONFIG", str(tmp_path / "config" / "projects.yaml"))

    result = project_intelligence(tmp_path, days=30, target=date(2026, 8, 2))

    questions = [item["question"] for item in result["project_details"]["asr"]["unknowns"]]
    assert questions == ["ASR 缺少真实录音验证"]


def test_same_latest_baseline_produces_empty_delta(tmp_path: Path, monkeypatch) -> None:
    reports = tmp_path / "daily-reports"
    reports.mkdir()
    (reports / "2026-08-01.md").write_text(_report("2026-08-01", "CER = 9.8%。"), encoding="utf-8")
    monkeypatch.setenv("RD_DAILY_REPORT_DIR", str(reports))
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "projects.yaml").write_text(
        "projects:\n  asr:\n    name: Robot Speech\n    repo_path: /tmp/robot-speech\n    verification_stages: []\n", encoding="utf-8",
        )
    monkeypatch.setenv("RD_PROJECTS_CONFIG", str(tmp_path / "config" / "projects.yaml"))

    result = project_intelligence(tmp_path, days=30, baseline=date(2026, 8, 1), target=date(2026, 8, 2))
    assert result["baseline_date"] == "2026-08-01"
    assert result["project_details"]["asr"]["delta"]["change_count"] == 0


def test_failed_reaudit_uses_explicitly_labelled_last_good_snapshot(tmp_path: Path, monkeypatch) -> None:
    config = tmp_path / "projects.yaml"
    config.write_text("projects:\n  asr:\n    name: ASR\n", encoding="utf-8")
    monkeypatch.setenv("RD_PROJECTS_CONFIG", str(config))
    report_path = tmp_path / "2026-08-01.md"
    report_path.write_text("# 日报 2026-08-01\n\n旧内容已变化\n", encoding="utf-8")
    data = tmp_path / "data"
    data.mkdir()
    policy = policy_fingerprint(
        "project-intelligence-backfill", schema_version=SCHEMA_VERSION,
        prompt_version=PROMPT_VERSION, models=(DEFAULT_MODEL, DEFAULT_FALLBACK),
        extra={"catalog": catalog_fingerprint({"asr": "ASR"})},
    )
    (data / "2026-08-01_intelligence_validated.json").write_text(json.dumps({
        "schema_version": SCHEMA_VERSION, "prompt_version": PROMPT_VERSION,
        "policy_fingerprint": policy, "source_sha256": "old-source",
        "project_updates": [{"project_id": "asr", "summary": "上次可信摘要"}],
    }), encoding="utf-8")
    (data / "intelligence_backfill_status.json").write_text(json.dumps({
        "failed": [{"date": "2026-08-01", "error": "quality gate rejected"}],
    }), encoding="utf-8")

    value, mode = _intelligence_for({
        "date": "2026-08-01", "source_path": str(report_path),
    })

    assert mode == "stale_last_good"
    assert value and value["project_updates"][0]["summary"] == "上次可信摘要"


def test_stale_unknown_leaves_current_board_without_being_deleted(tmp_path: Path, monkeypatch) -> None:
    reports = tmp_path / "daily-reports"
    data = reports / "data"
    data.mkdir(parents=True)
    for day in ("2026-06-01", "2026-08-01"):
        (reports / f"{day}.md").write_text(_report(day, "CER = 9.8%。"), encoding="utf-8")
    (data / "2026-06-01_audit_validated.json").write_text(json.dumps(_audit(
        "2026-06-01", [{"project_ids": ["asr"], "question": "旧环境是否可用？",
                         "action": "open", "priority": "high", "evidence": ["session:s1"]}], [], [],
    )), encoding="utf-8")
    (data / "2026-08-01_audit_validated.json").write_text(json.dumps(_audit(
        "2026-08-01", [], [], [{"project_ids": ["asr"], "summary": "新环境验证继续推进。",
                                  "evidence": ["session:s2"]}],
    )), encoding="utf-8")
    monkeypatch.setenv("RD_DAILY_REPORT_DIR", str(reports))
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "projects.yaml").write_text(
        "projects:\n  asr:\n    name: Robot Speech\n    repo_path: /tmp/robot-speech\n    verification_stages: []\n", encoding="utf-8",
        )
    monkeypatch.setenv("RD_PROJECTS_CONFIG", str(tmp_path / "config" / "projects.yaml"))

    result = project_intelligence(tmp_path, days=90, target=date(2026, 8, 2))
    detail = result["project_details"]["asr"]
    assert detail["unknowns"] == []
    assert detail["stale_unknown_count"] == 1


def test_multi_project_update_is_not_copied_into_two_storylines(tmp_path: Path, monkeypatch) -> None:
    reports = tmp_path / "daily-reports"
    data = reports / "data"
    data.mkdir(parents=True)
    (reports / "2026-08-01.md").write_text("""# 日报 2026-08-01

## 核心进展
### Robot Speech
#### Robot Speech 验证
- **结果**：Robot Speech 本地验证完成。
### OCR
#### OCR 验证
- **结果**：OCR 本地验证完成。
""", encoding="utf-8")
    (data / "2026-08-01_audit_validated.json").write_text(json.dumps(_audit(
        "2026-08-01", [], [], [{"project_ids": ["asr", "ocr"],
                                  "summary": "错误地把 ASR 与 OCR 合成同一个故事。",
                                  "evidence": ["session:s1"]}],
    )), encoding="utf-8")
    monkeypatch.setenv("RD_DAILY_REPORT_DIR", str(reports))
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "projects.yaml").write_text(
        "projects:\n  asr:\n    name: Robot Speech\n    repo_path: /tmp/robot-speech\n    verification_stages: []\n"
        "  ocr:\n    name: OCR\n    repo_path: /tmp/ocr\n    verification_stages: []\n", encoding="utf-8",
        )
    monkeypatch.setenv("RD_PROJECTS_CONFIG", str(tmp_path / "config" / "projects.yaml"))

    result = project_intelligence(tmp_path, days=30, target=date(2026, 8, 2))
    for project_id in ("asr", "ocr"):
        assert "合成同一个故事" not in result["project_details"][project_id]["storyline"]["summary"]
