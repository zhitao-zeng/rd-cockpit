from __future__ import annotations

import json
from pathlib import Path

import pytest

from rd_cockpit.daily_audit import (
    finalize_markdown,
    prepare_bundle,
    prepare_review_bundle,
    repair_audit_candidate,
    render_audit_markdown,
    validate_audit,
    validate_audit_candidate,
    validate_review_candidate,
)


def _write(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def _with_intelligence(value: dict) -> dict:
    value.setdefault("unknown_updates", [])
    value.setdefault("blocker_updates", [])
    value.setdefault("breakthroughs", [])
    value.setdefault("project_updates", [])
    return value


def test_prepare_preserves_session_summary_and_builds_exact_evidence_catalog(tmp_path: Path) -> None:
    session = {
        "session_id": "session-long", "_project": "asr", "first_intent": "修复 ASR",
        "last_conclusion": "latency 83ms", "tool_samples": [{"name": "Bash"}],
        "edited_files": ["runtime.py"], "token_usage": {"total_tokens": 100},
    }
    sessions = _write(tmp_path / "sessions.json", {
        "total_sessions": 1, "total_tool_calls": 4, "token_usage_summary": {"total_tokens": 100},
        "projects": {"asr": {"total_tokens": 100}}, "sessions": [session],
    })
    codex = _write(tmp_path / "codex.json", {"sessions": [], "projects": {}})
    git = _write(tmp_path / "git.json", {"total_commits": 1, "repos": {"asr": ["a410bcd fix streaming"]}})
    files = _write(tmp_path / "files.json", {"total_files": 1, "by_project": {"asr": ["runtime.py"]}})
    bundle = prepare_bundle(report_date="2026-08-03", sessions=sessions, codex=codex, git=git,
                            files=files, previous_plan=None)

    preserved = bundle["sources"]["claude_sessions"]["sessions"][0]
    assert preserved["last_conclusion"] == "latency 83ms"
    assert preserved["tool_samples"] == [{"name": "Bash"}]
    refs = {item["ref"] for item in bundle["allowed_evidence_refs"]}
    assert {"session:session-long", "commit:a410bcd", "file:asr/runtime.py"} <= refs
    assert "checkpoint_count" not in bundle["objective"]


def test_validate_removes_fake_references_and_downgrades_plan(tmp_path: Path) -> None:
    sessions = _write(tmp_path / "sessions.json", {"sessions": []})
    empty = _write(tmp_path / "empty.json", {})
    bundle = prepare_bundle(report_date="2026-08-03", sessions=sessions, codex=empty, git=empty,
                            files=empty, previous_plan=None)
    audit = {
        "project_groups": [{"name": "ASR", "tasks": [{
            "title": "结果", "did": ["执行评测"], "results": ["F1=0.90"],
            "evidence": ["session:not-real"], "confidence": "observed",
        }]}],
        "plan_closure": [{"plan": "完成评测", "status": "completed", "reason": "已完成", "evidence": []}],
        "knowledge": [], "blockers": [], "next_actions": [],
        "source_coverage": [], "data_quality": [],
    }

    result = validate_audit(audit, bundle)

    task = result["project_groups"][0]["tasks"][0]
    assert task["evidence"] == []
    assert task["confidence"] == "inferred"
    assert task["evidence_status"] == "unsupported"
    assert result["plan_closure"][0]["status"] == "no_evidence"
    assert result["validation"]["warnings"]

    with pytest.raises(ValueError, match="evidence validation failed"):
        validate_audit_candidate(_with_intelligence(audit), bundle)


def test_strict_audit_candidate_requires_complete_shape(tmp_path: Path) -> None:
    sessions = _write(tmp_path / "sessions.json", {"sessions": []})
    empty = _write(tmp_path / "empty.json", {})
    bundle = prepare_bundle(report_date="2026-08-03", sessions=sessions, codex=empty, git=empty,
                            files=empty, previous_plan=None)

    with pytest.raises(ValueError, match="missing required fields"):
        validate_audit_candidate({}, bundle)


def test_repair_drops_unverified_numeric_line_but_keeps_supported_task(tmp_path: Path) -> None:
    sessions = _write(tmp_path / "sessions.json", {
        "sessions": [{"session_id": "s1", "_project": "asr", "last_conclusion": "测试通过"}],
    })
    empty = _write(tmp_path / "empty.json", {})
    bundle = prepare_bundle(report_date="2026-08-03", sessions=sessions, codex=empty, git=empty,
                            files=empty, previous_plan=None)
    audit = {
        "project_groups": [{"name": "ASR", "tasks": [{
            "title": "完成测试", "did": ["执行回归", "运行 18 项测试"],
            "results": ["测试通过"], "evidence": ["session:s1"], "confidence": "reported",
        }]}],
        "plan_closure": [], "knowledge": [], "blockers": [], "next_actions": [], "data_quality": [],
        "source_coverage": [{
            "ref": "session:s1", "status": "core_task", "task_title": "完成测试", "reason": "",
        }],
    }

    repaired = repair_audit_candidate(_with_intelligence(audit), bundle)

    task = repaired["project_groups"][0]["tasks"][0]
    assert task["did"] == ["执行回归"]
    assert task["results"] == ["测试通过"]
    assert task["unverified_numbers"] == []
    assert any("18" in item and "已移除" in item for item in repaired["data_quality"])


def test_repair_downgrades_unverified_number_in_unknown_missing_evidence(tmp_path: Path) -> None:
    sessions = _write(tmp_path / "sessions.json", {
        "sessions": [{"session_id": "s1", "_project": "asr",
                      "last_conclusion": "Judge 传输链路仍需验证"}],
    })
    empty = _write(tmp_path / "empty.json", {})
    bundle = prepare_bundle(report_date="2026-08-12", sessions=sessions, codex=empty, git=empty,
                            files=empty, previous_plan=None)
    audit = _with_intelligence({
        "project_groups": [{"name": "ASR", "project_ids": ["asr"], "tasks": [{
            "title": "检查 Judge 链路", "did": ["检查传输链路"],
            "results": ["仍需验证"], "evidence": ["session:s1"], "confidence": "reported",
        }]}],
        "plan_closure": [], "knowledge": [], "blockers": [], "next_actions": [],
        "source_coverage": [{"ref": "session:s1", "status": "core_task",
                             "task_title": "检查 Judge 链路", "reason": ""}],
        "data_quality": [],
    })
    audit["unknown_updates"] = [{
        "project_ids": ["asr"], "question": "Judge 传输链路是否丢包？",
        "action": "open", "priority": "high",
        "missing_evidence": "缺少 RELIABLE/depth=200 对照。",
        "evidence": ["session:s1"], "confidence": "reported",
    }]

    repaired = repair_audit_candidate(audit, bundle)

    assert repaired["unknown_updates"][0]["question"] == "Judge 传输链路是否丢包？"
    assert repaired["unknown_updates"][0]["missing_evidence"] == "缺少可核验的验证证据。"
    assert repaired["validation"]["unverified_numbers"] == []
    assert any("200" in item and "保守表述" in item for item in repaired["data_quality"])


def test_duplicate_session_fragments_get_unique_refs_and_require_full_coverage(tmp_path: Path) -> None:
    sessions = _write(tmp_path / "sessions.json", {
        "sessions": [
            {"session_id": "shared", "_project": "asr", "tool_count": 3,
             "first_intent": "分析声学数据", "last_conclusion": "需要真实录音"},
            {"session_id": "shared", "_project": "ocr", "tool_count": 5,
             "first_intent": "跑 OCR 基线", "last_conclusion": "模型 B 更好"},
        ],
    })
    empty = _write(tmp_path / "empty.json", {})
    bundle = prepare_bundle(report_date="2026-08-03", sessions=sessions, codex=empty, git=empty,
                            files=empty, previous_plan=None)

    refs = [item["ref"] for item in bundle["coverage_required_refs"]]
    assert len(refs) == 2
    assert len(set(refs)) == 2
    assert all(ref.startswith("session:shared#") for ref in refs)

    audit = {
        "project_groups": [{"name": "ASR", "tasks": [{
            "title": "声学数据分析", "did": ["分析数据"], "results": ["需要真实录音"],
            "evidence": [refs[0]], "confidence": "reported",
        }]}],
        "plan_closure": [], "knowledge": [], "blockers": [], "next_actions": [],
        "source_coverage": [{
            "ref": refs[0], "status": "core_task", "task_title": "声学数据分析", "reason": "",
        }],
        "data_quality": [],
    }
    with pytest.raises(ValueError, match="source coverage missing refs"):
        validate_audit_candidate(_with_intelligence(audit), bundle)


def test_duplicate_session_fragments_preserve_each_summary(tmp_path: Path) -> None:
    sessions = _write(tmp_path / "sessions.json", {
        "sessions": [
            {"session_id": "shared", "last_conclusion": "结论一"},
            {"session_id": "shared", "last_conclusion": "结论二"},
        ],
    })
    empty = _write(tmp_path / "empty.json", {})
    bundle = prepare_bundle(report_date="2026-08-03", sessions=sessions, codex=empty, git=empty,
                            files=empty, previous_plan=None)

    fragments = bundle["sources"]["claude_sessions"]["sessions"]
    assert [item["last_conclusion"] for item in fragments] == ["结论一", "结论二"]


def test_session_only_task_is_reported_not_observed(tmp_path: Path) -> None:
    sessions = _write(tmp_path / "sessions.json", {
        "sessions": [{"session_id": "s1", "last_conclusion": "测试通过"}],
    })
    empty = _write(tmp_path / "empty.json", {})
    bundle = prepare_bundle(report_date="2026-08-03", sessions=sessions, codex=empty, git=empty,
                            files=empty, previous_plan=None)
    audit = {
        "project_groups": [{"name": "ASR", "tasks": [{
            "title": "测试", "did": [], "results": ["测试通过"],
            "evidence": ["session:s1"], "confidence": "observed",
        }]}],
        "plan_closure": [], "knowledge": [], "blockers": [], "next_actions": [],
        "source_coverage": [{
            "ref": "session:s1", "status": "core_task", "task_title": "测试", "reason": "",
        }],
        "data_quality": [],
    }

    validated = validate_audit_candidate(_with_intelligence(audit), bundle)
    assert validated["project_groups"][0]["tasks"][0]["confidence"] == "reported"


def test_intelligence_fields_are_evidence_validated(tmp_path: Path) -> None:
    sessions = _write(tmp_path / "sessions.json", {
        "sessions": [{"session_id": "s1", "_project": "asr",
                      "last_conclusion": "Jetson latency 47ms，Judge 尚未验证"}],
    })
    empty = _write(tmp_path / "empty.json", {})
    bundle = prepare_bundle(report_date="2026-08-03", sessions=sessions, codex=empty, git=empty,
                            files=empty, previous_plan=None)
    candidate = _with_intelligence({
        "project_groups": [{"name": "ASR", "project_ids": ["asr"], "tasks": [{
            "title": "Jetson 验证", "did": ["运行验证"], "results": ["latency 47ms"],
            "evidence": ["session:s1"], "confidence": "reported",
        }]}],
        "plan_closure": [], "knowledge": [], "blockers": [], "next_actions": [],
        "source_coverage": [{"ref": "session:s1", "status": "core_task",
                             "task_title": "Jetson 验证", "reason": ""}], "data_quality": [],
    })
    candidate["unknown_updates"] = [{"project_ids": ["asr"], "question": "Judge 是否通过？",
                                      "action": "open", "priority": "high",
                                      "missing_evidence": "Judge result", "evidence": ["session:s1"]}]
    candidate["breakthroughs"] = [{"project_ids": ["asr"], "title": "延迟改善",
                                    "change": "latency 47ms", "significance": "支持远端验证",
                                    "evidence": ["session:s1"]}]
    candidate["project_updates"] = [{"project_ids": ["asr"],
                                      "summary": "Jetson 延迟已确认，Judge 仍未知。",
                                      "evidence": ["session:s1"]}]

    validated = validate_audit_candidate(candidate, bundle)

    assert validated["schema_version"] == 3
    assert validated["unknown_updates"][0]["priority"] == "high"
    assert validated["breakthroughs"][0]["change"] == "latency 47ms"
    assert validated["project_updates"][0]["confidence"] == "reported"


def test_intelligence_rejects_multi_project_and_cross_project_evidence(tmp_path: Path) -> None:
    sessions = _write(tmp_path / "sessions.json", {
        "sessions": [{"session_id": "s1", "_project": "asr", "last_conclusion": "完成 ASR 验证"}],
    })
    empty = _write(tmp_path / "empty.json", {})
    bundle = prepare_bundle(report_date="2026-08-03", sessions=sessions, codex=empty, git=empty,
                            files=empty, previous_plan=None)
    candidate = _with_intelligence({
        "project_groups": [{"name": "ASR", "project_ids": ["asr"], "tasks": [{
            "title": "ASR 验证", "did": ["执行验证"], "results": ["完成验证"],
            "evidence": ["session:s1"], "confidence": "reported",
        }]}],
        "plan_closure": [], "knowledge": [], "blockers": [], "next_actions": [],
        "source_coverage": [{"ref": "session:s1", "status": "core_task",
                             "task_title": "ASR 验证", "reason": ""}], "data_quality": [],
    })
    candidate["project_updates"] = [{"project_ids": ["asr", "ocr"],
                                      "summary": "ASR 和 OCR 均完成验证。", "evidence": ["session:s1"]}]

    with pytest.raises(ValueError, match="exactly one valid project_id"):
        validate_audit_candidate(candidate, bundle)

    candidate["project_updates"] = [{"project_ids": ["ocr"],
                                      "summary": "OCR 完成验证。", "evidence": ["session:s1"]}]
    with pytest.raises(ValueError, match="another project"):
        validate_audit_candidate(candidate, bundle)


def test_semantic_review_reads_raw_bundle_and_retains_both_model_runs(tmp_path: Path) -> None:
    sessions = _write(tmp_path / "sessions.json", {
        "sessions": [{"session_id": "s1", "_project": "asr",
                      "first_intent": "审计解码流程", "last_conclusion": "只读审计，建议后续修复"}],
    })
    empty = _write(tmp_path / "empty.json", {})
    bundle = prepare_bundle(report_date="2026-08-03", sessions=sessions, codex=empty, git=empty,
                            files=empty, previous_plan=None)
    baseline_candidate = {
        "project_groups": [{"name": "ASR", "project_ids": ["asr"], "tasks": [{
            "title": "解码流程审计", "did": ["完成解码流程修复"], "why": [],
            "results": ["只读审计，建议后续修复"], "files": [],
            "evidence": ["session:s1"], "confidence": "reported",
        }]}],
        "plan_closure": [], "knowledge": [], "blockers": [], "next_actions": [],
        "source_coverage": [{
            "ref": "session:s1", "status": "core_task", "task_title": "解码流程审计", "reason": "",
        }],
        "data_quality": [],
    }
    baseline = validate_audit_candidate(
        _with_intelligence(baseline_candidate), bundle, metadata={"requested_model": "deepseek-local"},
    )

    review_input = prepare_review_bundle(bundle, baseline)
    assert review_input["raw_bundle"]["sources"]["claude_sessions"]["sessions"][0]["last_conclusion"] \
        == "只读审计，建议后续修复"
    assert review_input["baseline_audit"] == baseline

    corrected = json.loads(json.dumps(baseline_candidate, ensure_ascii=False))
    corrected["project_groups"][0]["tasks"][0]["did"] = ["完成解码流程只读审计"]
    reviewed = validate_review_candidate(
        corrected,
        bundle,
        baseline,
        metadata={"requested_model": "codex:gpt-5.6-sol@medium"},
    )
    assert reviewed["project_groups"][0]["tasks"][0]["did"] == ["完成解码流程只读审计"]
    assert reviewed["audit_model_run"]["requested_model"] == "deepseek-local"
    assert reviewed["semantic_review_model_run"]["requested_model"] == "codex:gpt-5.6-sol@medium"
    assert reviewed["validation"]["semantic_reviewed"] is True


def test_deterministic_renderer_preserves_validated_numbers_and_sections(tmp_path: Path) -> None:
    sessions = _write(tmp_path / "sessions.json", {
        "total_sessions": 1,
        "total_tool_calls": 7,
        "token_usage_summary": {
            "input_tokens": 1_200, "cached_input_tokens": 1_000,
            "uncached_input_tokens": 200, "output_tokens": 35,
            "reasoning_output_tokens": 5, "total_tokens": 1_235,
            "requests": 3, "cache_read_ratio": 0.8333, "available": True,
        },
        "sessions": [{"session_id": "s1", "last_conclusion": "延迟为 47ms"}],
    })
    empty = _write(tmp_path / "empty.json", {})
    bundle = prepare_bundle(report_date="2026-08-03", sessions=empty, codex=sessions, git=empty,
                            files=empty, previous_plan=None)
    candidate = {
        "project_groups": [{"name": "ASR", "project_ids": ["asr"], "tasks": [{
            "title": "完成延迟核对", "did": ["运行本地核对"], "why": ["确认当前表现"],
            "results": ["延迟为 47ms"], "files": [], "evidence": ["session:s1"],
            "confidence": "reported",
        }]}],
        "plan_closure": [],
        "knowledge": [{"text": "延迟为 47ms", "scope": "本地", "evidence": ["session:s1"],
                       "confidence": "reported"}],
        "blockers": [],
        "next_actions": [],
        "source_coverage": [{
            "ref": "session:s1", "status": "core_task", "task_title": "完成延迟核对", "reason": "",
        }],
        "data_quality": [],
    }
    validated = validate_audit_candidate(_with_intelligence(candidate), bundle)

    report = render_audit_markdown(validated)

    assert report.startswith("# 日报 2026-08-03")
    assert all(section in report for section in (
        "## 昨日计划闭环", "## 核心进展", "## Token 消耗",
        "## 关键结论与知识", "## 阻塞 / 待解决", "## 明日计划",
        "## 数据完整性", "## 推送摘要",
    ))
    assert "延迟为 47ms" in report
    assert "Codex 1" in report
    assert "缓存输入 1,000" in report
    assert "Session 报告" in report


def test_finalize_strips_preamble_and_requires_all_sections() -> None:
    markdown = """I already have the data.\n---\n# 日报 2026-08-03\n## 昨日计划闭环\n无\n## 核心进展\n无\n## Token 消耗\n无\n## 关键结论与知识\n无\n## 阻塞 / 待解决\n无\n## 明日计划\n无\n## 数据完整性\n无\n## 推送摘要\n今日无记录\n{\"schema_version\": 1, \"report_date\": \"2026-08-03\"}\n"""
    report, metadata = finalize_markdown({"result": markdown, "usage": {"input_tokens": 10}}, "2026-08-03")
    assert report.startswith("# 日报 2026-08-03")
    assert "I already" not in report
    assert "schema_version" not in report
    assert metadata["usage"]["input_tokens"] == 10

    with pytest.raises(ValueError, match="missing required sections"):
        finalize_markdown({"result": "# 日报 2026-08-03\n## 核心进展\n无"}, "2026-08-03")
