import json
from pathlib import Path

from rd_cockpit.daily_source import parse_report
from rd_cockpit.historical_reports import load_normalized, normalize_report


LEGACY_REPORT = """# 2026-06-01 工作记录

今天主要处理方言模型。训练 ExampleConformer 的 16 类 LID decoder，3500 steps 后准确率达到 97.6%。
相关文件是 dialect-asr/scripts/train_lid.py。

另外排查了潮汕数据，发现训练集与测试集口音不一致，下一步需要补充真实数据。
"""


def _candidate() -> dict:
    return {
        "day_summary": "当天主线是方言 ASR：完成 16 类 LID decoder 训练，并定位潮汕口音分布不一致。",
        "groups": [{
            "title": "方言 ASR",
            "project_ids": ["asr_dialect"],
            "tasks": [{
                "title": "训练 16 类 LID decoder",
                "project_ids": ["asr_dialect"],
                "did": ["基于 ExampleConformer 训练 16 类 LID decoder，共 3500 steps。"],
                "why": [],
                "results": ["整体准确率达到 97.6%。"],
                "files": ["dialect-asr/scripts/train_lid.py"],
                "conclusions": ["训练集与测试集的潮汕口音不一致，后续需补充真实数据。"],
                "evidence_lines": [[3, 6]],
            }],
        }],
        "plan_closure": [],
        "knowledge": ["潮汕口音分布差异会影响当前评测的外推性。"],
        "decisions": ["下一轮优先补充真实潮汕数据。"],
        "blockers": ["缺少匹配测试分布的真实潮汕数据。"],
        "next": ["补充真实潮汕数据并重新评测。"],
        "data_quality": ["旧记录没有给出 commit 和完整评测配置。"],
    }


def test_normalization_keeps_original_and_overlays_readable_sidecar(tmp_path: Path) -> None:
    path = tmp_path / "2026-06-01.md"
    path.write_text(LEGACY_REPORT, encoding="utf-8")
    original = path.read_text(encoding="utf-8")
    calls: list[str] = []

    def requester(model: str, report_date: str, lines: list[str]):
        calls.append(model)
        assert report_date == "2026-06-01"
        assert lines[2].startswith("今天主要处理方言模型")
        return _candidate(), {"usage": {"input_tokens": 100, "output_tokens": 50}}

    result = normalize_report(path, requester=requester)

    assert result["status"] == "generated"
    assert result["model"] == "codex:gpt-5.6-sol@medium"
    assert calls == ["codex:gpt-5.6-sol@medium"]
    assert path.read_text(encoding="utf-8") == original

    sidecar = load_normalized(path)
    assert sidecar is not None
    assert sidecar["source_sha256"]
    assert sidecar["task_count"] == 1
    assert sidecar["groups"][0]["tasks"][0]["evidence"] == ["2026-06-01.md:L3-L6"]
    assert sidecar["groups"][0]["tasks"][0]["confidence"] == "reported"

    report = parse_report(path)
    assert report["day_summary"].startswith("当天主线是方言 ASR")
    assert report["groups"][0]["project_ids"] == ["asr_dialect"]
    assert report["groups"][0]["tasks"][0]["conclusions"]
    assert report["decisions"] == ["下一轮优先补充真实潮汕数据。"]
    assert report["normalization"]["model"] == "codex:gpt-5.6-sol@medium"


def test_normalization_cache_and_source_hash_invalidation(tmp_path: Path) -> None:
    path = tmp_path / "2026-06-01.md"
    path.write_text(LEGACY_REPORT, encoding="utf-8")

    normalize_report(path, requester=lambda *_: (_candidate(), {"usage": {}}))

    def should_not_run(*_):
        raise AssertionError("cached report unexpectedly called the model")

    cached = normalize_report(path, requester=should_not_run)
    assert cached["status"] == "cached"

    path.write_text(LEGACY_REPORT + "\n补充了一条原始记录。\n", encoding="utf-8")
    assert load_normalized(path) is None
    report = parse_report(path)
    assert "normalization" not in report


def test_current_evidence_report_skips_legacy_model_and_sidecar(tmp_path: Path) -> None:
    path = tmp_path / "2026-08-09.md"
    path.write_text("# 日报 2026-08-09\n\n## 核心进展\n\n无已验证记录\n", encoding="utf-8")
    audit_path = tmp_path / "data" / "2026-08-09_audit_validated.json"
    audit_path.parent.mkdir(parents=True)
    audit_path.write_text(json.dumps({
        "report_date": "2026-08-09",
        "project_groups": [],
        "validation": {
            "warnings": [], "unsupported_task_count": 0,
            "unverified_numbers": [], "missing_source_refs": [],
        },
    }), encoding="utf-8")

    def should_not_run(*_):
        raise AssertionError("current evidence report unexpectedly called the legacy model")

    result = normalize_report(path, requester=should_not_run)

    assert result["status"] == "current_format"
    assert result["path"] == str(audit_path)
    assert load_normalized(path) is None


def test_normalization_falls_back_when_local_model_fails(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "2026-06-01.md"
    path.write_text(LEGACY_REPORT, encoding="utf-8")
    monkeypatch.setenv("RD_REPORT_NORMALIZE_MODEL", "deepseek-local")
    monkeypatch.setenv("RD_REPORT_NORMALIZE_FALLBACK_MODEL", "deepseek")
    calls: list[str] = []

    def requester(model: str, *_):
        calls.append(model)
        if model == "deepseek-local":
            raise RuntimeError("local route unavailable")
        return _candidate(), {"usage": {"input_tokens": 101, "output_tokens": 51}}

    result = normalize_report(path, requester=requester)
    sidecar = json.loads(Path(result["path"]).read_text(encoding="utf-8"))

    assert calls == ["deepseek-local", "deepseek"]
    assert result["model"] == "deepseek"
    assert sidecar["model_run"]["fallback_used"] is True
    assert sidecar["model_run"]["attempts"][0]["status"] == "failed"


def test_task_project_does_not_inherit_every_project_from_mixed_group(tmp_path: Path) -> None:
    path = tmp_path / "2026-07-01.md"
    path.write_text("""# 日报 2026-07-01

## 开发环境与工具链
Codex CLI 使用 router/router_codex.py 跑通 DeepSeek。
另生成上市公司投资研究报告。
""", encoding="utf-8")
    candidate = {
        "day_summary": "完成 Router 配置并生成投资报告。",
        "groups": [{
            "title": "开发环境与工具链",
            "project_ids": ["router", "investment_research"],
            "tasks": [{
                "title": "Codex CLI 安装与配置",
                "did": ["使用 router/router_codex.py 跑通 DeepSeek。"],
                "why": [], "results": ["路由已跑通。"],
                "files": ["router/router_codex.py"], "conclusions": [],
                "evidence_lines": [[3, 4]],
            }],
        }],
    }

    normalize_report(path, requester=lambda *_: (candidate, {"usage": {}}))
    task = parse_report(path)["groups"][0]["tasks"][0]

    assert task["project_ids"] == ["router"]


def test_explicit_no_activity_report_is_a_valid_normalized_day(tmp_path: Path) -> None:
    path = tmp_path / "2026-06-21.md"
    path.write_text("# 日报 2026-06-21\n\n今日无记录。\n", encoding="utf-8")
    candidate = {
        "no_activity": True,
        "day_summary": "当天没有可归档的研发活动记录。",
        "groups": [],
        "plan_closure": [], "knowledge": [], "decisions": [],
        "blockers": [], "next": [], "data_quality": [],
    }

    normalize_report(path, requester=lambda *_: (candidate, {"usage": {}}))
    report = parse_report(path)

    assert report["no_activity"] is True
    assert report["task_count"] == 0
    assert report["day_summary"] == "当天没有可归档的研发活动记录。"


def test_non_substantive_automatic_changes_remain_a_zero_task_day(tmp_path: Path) -> None:
    path = tmp_path / "2026-05-31.md"
    path.write_text(
        "# 日报 2026-05-31\n\n今日无实质性开发活动，只有 VS Code 自动更新日志。\n",
        encoding="utf-8",
    )
    candidate = {"no_activity": True, "day_summary": "当天无实质研发活动。", "groups": []}

    normalize_report(path, requester=lambda *_: (candidate, {"usage": {}}))

    assert parse_report(path)["no_activity"] is True


def test_missing_model_evidence_is_recovered_only_by_exact_source_match(tmp_path: Path) -> None:
    path = tmp_path / "2026-07-08.md"
    path.write_text("""# 日报 2026-07-08

## 核心进展
- **4 并发批量生产跑通**：编写 `scripts/batch_500.py`，4 并发稳定运行，0 次超时。
""", encoding="utf-8")
    candidate = {
        "day_summary": "跑通批量生产。",
        "groups": [{
            "title": "Idol", "project_ids": ["idol"],
            "tasks": [{
                "title": "4 并发批量生产流水线", "project_ids": ["idol"],
                "did": ["编写 scripts/batch_500.py。"], "why": [],
                "results": ["4 并发稳定运行，0 次超时。"],
                "files": ["scripts/batch_500.py"], "conclusions": [],
                "evidence_lines": [],
            }],
        }],
    }

    normalize_report(path, requester=lambda *_: (candidate, {"usage": {}}))
    task = parse_report(path)["groups"][0]["tasks"][0]

    assert task["evidence"] == ["2026-07-08.md:L4"]
    assert task["confidence"] == "reported"


def test_explicit_unassigned_is_refined_by_known_project_path(tmp_path: Path) -> None:
    path = tmp_path / "2026-06-01.md"
    path.write_text("# 日报 2026-06-01\n\nimage-identify/fusion 完成 TruFor 融合优化。\n", encoding="utf-8")
    candidate = {
        "day_summary": "完成图像鉴伪融合优化。",
        "groups": [{
            "title": "图像识别与融合", "project_ids": ["unassigned"],
            "tasks": [{
                "title": "TruFor 融合策略优化", "project_ids": ["unassigned"],
                "did": ["优化 image-identify/fusion。"], "why": [], "results": ["融合优化完成。"],
                "files": ["image-identify/fusion/server.py"], "conclusions": [], "evidence_lines": [[3, 3]],
            }],
        }],
    }

    normalize_report(path, requester=lambda *_: (candidate, {"usage": {}}))

    assert parse_report(path)["groups"][0]["tasks"][0]["project_ids"] == ["image_identify"]


def test_structured_decision_is_rendered_as_readable_text(tmp_path: Path) -> None:
    path = tmp_path / "2026-06-01.md"
    path.write_text(LEGACY_REPORT, encoding="utf-8")
    candidate = _candidate()
    candidate["decisions"] = [{
        "decision": "采用 gpu_memory_utilization=0.95",
        "reason": "20GB HAMI 下仍能容纳模型和 KV cache",
        "evidence_lines": [[3, 4]],
    }]

    normalize_report(path, requester=lambda *_: (candidate, {"usage": {}}))

    assert parse_report(path)["decisions"] == [
        "采用 gpu_memory_utilization=0.95：20GB HAMI 下仍能容纳模型和 KV cache"
    ]


def test_legacy_stringified_decision_is_cleaned_when_read(tmp_path: Path) -> None:
    path = tmp_path / "2026-06-01.md"
    path.write_text(LEGACY_REPORT, encoding="utf-8")
    result = normalize_report(path, requester=lambda *_: (_candidate(), {"usage": {}}))
    sidecar_path = Path(result["path"])
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    sidecar["decisions"] = [
        "{'decision': '改用 8B 模型', 'reason': '20GB 显存不足', 'evidence_lines': [[3, 4]]}"
    ]
    sidecar_path.write_text(json.dumps(sidecar, ensure_ascii=False), encoding="utf-8")

    assert parse_report(path)["decisions"] == ["改用 8B 模型：20GB 显存不足"]
