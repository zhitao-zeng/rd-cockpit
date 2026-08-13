from __future__ import annotations

from datetime import date
from pathlib import Path

from rd_cockpit.development import _failure_state, _phase, _primary_project_ids, _work_types, development_dashboard


def _report(day: str, title: str, result: str, closure: str, blocker: str = "") -> str:
    blocker_section = f"\n## 阻塞 / 待解决\n- {blocker}\n" if blocker else ""
    return f"""# 日报 {day}

## 昨日计划闭环
- {closure}

## 核心进展
### ASR（语音识别）
#### {title}
- **做了什么**：完成 ASR 评测与实现。
- **为什么**：验证低错误率方案。
- **结果**：{result}
- **关键文件**：`robot-speech/asr.py`

## 关键结论与知识
- ASR 当前最佳方案来自统一验证集。
{blocker_section}
## 明日计划
- ASR 继续完成远端验证
"""


def test_development_dashboard_builds_eight_readable_views(tmp_path: Path, monkeypatch) -> None:
    report_dir = tmp_path / "daily-reports"
    report_dir.mkdir()
    (report_dir / "2026-08-01.md").write_text(
        _report("2026-08-01", "Speech Research 流式模型验证", "CER = 12.5%，本地测试通过。", "Speech Research baseline：completed"),
        encoding="utf-8",
    )
    (report_dir / "2026-08-02.md").write_text(
        _report("2026-08-02", "Speech Research 流式模型远端验证", "CER = 9.8%，远端验证完成。", "Speech Research 远端验证：partially_completed",
                "Speech Research 远端环境阻塞"),
        encoding="utf-8",
    )
    monkeypatch.setenv("RD_DAILY_REPORT_DIR", str(report_dir))
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "projects.yaml").write_text(
            "projects:\n  speech_research:\n    name: Speech Research\n    repo_path: /tmp/speech-research\n    verification_stages: []\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("RD_PROJECTS_CONFIG", str(tmp_path / "config" / "projects.yaml"))

    result = development_dashboard(tmp_path, days=30, target=date(2026, 8, 3))

    assert result["report_count"] == 2
    assert len(result["storylines"]["speech_research"]) == 2
    assert result["threads"]["speech_research"][0]["nodes"]
    assert [point["value"] for point in result["metrics"]] == [12.5, 9.8]
    assert result["lifecycles"][0]["status"] == "blocked"
    assert result["effort_output"][0]["results"] == 2
    assert result["activity"]["dates"] == ["2026-08-01", "2026-08-02"]
    assert result["plans"]["counts"] == {"完成": 1, "部分完成": 1}
    assert result["knowledge"]["nodes"] and result["knowledge"]["edges"]
    conclusion_nodes = [node for node in result["knowledge"]["nodes"] if node["category"] == "结论"]
    assert len(conclusion_nodes) == 2
    assert not any("CER" in node.get("full_text", "") for node in conclusion_nodes)
    assert result["time_travel"][0]["projects"][0]["not_known_until"] == "2026-08-02"


def test_development_dashboard_preserves_configured_dormant_status(tmp_path: Path, monkeypatch) -> None:
    report_dir = tmp_path / "daily-reports"
    report_dir.mkdir()
    (report_dir / "2026-08-01.md").write_text(
            _report("2026-08-01", "Speech Research 历史评测", "本地评测完成。", "Speech Research baseline：completed"),
        encoding="utf-8",
    )
    monkeypatch.setenv("RD_DAILY_REPORT_DIR", str(report_dir))
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "projects.yaml").write_text(
            "projects:\n  speech_research:\n    name: Speech Research\n    repo_path: /tmp/speech-research\n"
        "    lifecycle_status: dormant\n    verification_stages: []\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("RD_PROJECTS_CONFIG", str(tmp_path / "config" / "projects.yaml"))

    result = development_dashboard(tmp_path, days=30, target=date(2026, 8, 2))

    assert result["lifecycles"][0]["status"] == "dormant"


def test_work_type_keeps_title_intent_and_multiple_evidenced_types() -> None:
    text = "分析原因后实现 HTTP 服务，并完成远端验证"
    assert _phase(text, "实现 HTTP 服务") == "实现"
    assert _work_types(text) == ["探索", "实现", "验证"]
    assert _phase("调研后实现并发布上线", "正式发布上线") == "交付"
    assert _phase("重启服务并确认端口", "推理服务恢复") == "运维"
    assert _phase("运行批处理生成十条结果", "多歌曲批量生成与调度") == "执行"


def test_failure_state_respects_negation_recovery_and_open_work() -> None:
    assert _failure_state("4 卡方案可运行，不 OOM") == "none"
    assert _failure_state("使用 ast 检查，确保无语法错误后构建成功") == "none"
    assert _failure_state("人工校验完成，但问题数量未记录") == "none"
    assert _failure_state("生成视频与预期不匹配，正在排查同步逻辑") == "historical"
    assert _failure_state("修复旧版失败分类问题，4/4 全部成功") == "resolved"
    assert _failure_state("CP8 触发驱动崩溃，建议规避") == "resolved"
    assert _failure_state("三首歌曲仍无法跑，等待 ASR 补齐") == "open"


def test_leading_project_owns_dependency_blocker() -> None:
    assert _primary_project_ids("video-generator：真实 ASR 后端尚未接通") == ["avatar_video"]
    assert _primary_project_ids("document-assistant OCR 优化仍未发布：等待构建") == ["resume_copilot"]
    assert set(_primary_project_ids("Judge 队列阻塞（ASR/OCR/Obstacle）：等待平台处理")) == {
        "asr_other", "ocr", "obstacle",
    }
    assert _primary_project_ids("embodied-ai：Jetson 产品 VAD 下的 ASR A/B 尚未完成") == ["asr"]
