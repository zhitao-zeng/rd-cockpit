import os
from pathlib import Path

from rd_cockpit.daily_source import available_report_dates, load_report, parse_report


REPORT = """# 日报 2026-08-01

## 昨日计划闭环
- ASR 热词验证：completed，证据 commit:a1
  - 原因：完整评测通过
  - 证据：commit:a1

## 核心进展
### ASR（语音识别）
#### Hotwords 端到端验证
- **做了什么**：修复 `hotwordsScore` 配置。
- **为什么**：原配置被硬编码覆盖。
- **结果**：
  - 12 条测试达到 **1-CER = 0.8880**
  - 完整测试达到 `0.9204`
- **关键文件**：`plugins/asr_offline.py`
- **证据**：commit `a1`；12 tests passed

## Token 消耗
| 来源 | 请求数 | 总量 |
|------|--------|------|
| Claude | 37 | 11,734,003 |
| Codex | 2,037 | 236,016,602 |

- Codex 推理 Token 已包含在输出 Token 中

## 关键结论与知识
- score=2.5 仅适用于当前测试集，证据 commit:a1

## 阻塞 / 待解决
- **ASR 数据不足**：关键词覆盖不足。

## 明日计划
- 生成增强数据

## 数据完整性
- 1 个 session 没有 usage

## 推送摘要
ASR：完成 Hotwords 修复。
"""


def test_parse_existing_daily_report_structure(tmp_path: Path) -> None:
    path = tmp_path / "2026-08-01.md"
    path.write_text(REPORT, encoding="utf-8")

    report = parse_report(path)

    assert report["available"] is True
    assert report["date"] == "2026-08-01"
    assert report["task_count"] == 1
    assert report["groups"][0]["project_ids"] == ["asr"]
    task = report["groups"][0]["tasks"][0]
    assert task["display_title"].endswith("｜Hotwords 端到端验证")
    assert task["did"] == ["修复 hotwordsScore 配置。"]
    assert task["results"] == ["12 条测试达到 1-CER = 0.8880", "完整测试达到 0.9204"]
    assert task["evidence"] == ["commit a1；12 tests passed"]
    assert report["token"]["total_tokens"] == 247_750_605
    assert report["blockers"] == ["ASR 数据不足：关键词覆盖不足。"]
    assert report["next"] == ["生成增强数据"]
    assert report["plan_closure"] == ["ASR 热词验证：completed，证据 commit:a1"]
    assert report["knowledge"] == ["score=2.5 仅适用于当前测试集，证据 commit:a1"]
    assert report["data_quality"] == ["1 个 session 没有 usage"]


def test_load_latest_report_and_missing_date(tmp_path: Path) -> None:
    (tmp_path / "2026-07-31.md").write_text(REPORT.replace("2026-08-01", "2026-07-31"), encoding="utf-8")
    (tmp_path / "2026-08-01.md").write_text(REPORT, encoding="utf-8")

    assert available_report_dates(tmp_path) == ["2026-07-31", "2026-08-01"]
    assert load_report(directory=tmp_path)["date"] == "2026-08-01"
    missing = load_report("2026-08-02", tmp_path)
    assert missing["available"] is False
    assert "尚未生成正式日报" in missing["message"]


def test_default_sources_merge_legacy_dates_with_authority_order(tmp_path: Path, monkeypatch) -> None:
    current = tmp_path / "current"
    curated = tmp_path / "curated"
    legacy = tmp_path / "legacy"
    for root in (current, curated, legacy):
        root.mkdir()
    (legacy / "2026-05-18.md").write_text(REPORT.replace("2026-08-01", "2026-05-18"), encoding="utf-8")
    (legacy / "2026-05-21.md").write_text(REPORT.replace("2026-08-01", "2026-05-21").replace("Hotwords", "旧副本"), encoding="utf-8")
    (curated / "2026-05-21.md").write_text(REPORT.replace("2026-08-01", "2026-05-21").replace("Hotwords", "工作区版本"), encoding="utf-8")
    (current / "2026-08-01.md").write_text(REPORT, encoding="utf-8")
    monkeypatch.setenv("RD_DAILY_REPORT_DIR", str(current))
    monkeypatch.setenv("RD_DAILY_REPORT_LEGACY_DIRS", os.pathsep.join([str(curated), str(legacy)]))

    assert available_report_dates() == ["2026-05-18", "2026-05-21", "2026-08-01"]
    selected = load_report("2026-05-21")
    assert selected["source_path"] == str(curated / "2026-05-21.md")
    assert selected["groups"][0]["tasks"][0]["title"] == "工作区版本 端到端验证"


def test_asr_tasks_are_split_by_research_line_after_full_task_is_read(tmp_path: Path) -> None:
    path = tmp_path / "2026-08-02.md"
    path.write_text("""# 日报 2026-08-02

## 核心进展
### ASR
#### 方言模型训练
- **做了什么**：训练 ExampleConformer。
- **关键文件**：`dialect-asr/train.py`
#### 通用模型比较
- **做了什么**：批量评测模型。
- **关键文件**：`speech-model-eval/evaluate.py`
#### 歌词时间戳
- **做了什么**：生成 LRC。
- **关键文件**：`speech-aligner/aligner.py`
#### 机器人热词
- **做了什么**：验证 Hotwords。
- **关键文件**：`robot-speech/plugins/asr_offline.py`
#### 待整理的语音识别工作
- **做了什么**：整理 ASR 资料。
""", encoding="utf-8")

    report = parse_report(path)

    assert [task["project_ids"] for task in report["groups"][0]["tasks"]] == [
        ["asr_dialect"], ["asr_model_eval"], ["asr_alignment"], ["asr"], ["asr_other"],
    ]
    assert report["groups"][0]["project_ids"] == [
        "asr_dialect", "asr_model_eval", "asr_alignment", "asr", "asr_other",
    ]


def test_generic_asr_heading_is_corrected_by_concrete_embodied_eval_path(tmp_path: Path) -> None:
    path = tmp_path / "2026-08-08.md"
    path.write_text("""# 日报 2026-08-08

## 核心进展
### ASR
#### 构造机器人通道训练 A/B 对照
- **做了什么**：启动控制臂短训练。
- **关键文件**：`robot-speech/eval/run_robot_channel_training_ab.sh`
""", encoding="utf-8")

    task = parse_report(path)["groups"][0]["tasks"][0]

    assert task["project_ids"] == ["asr"]
    assert task["display_title"].endswith("｜构造机器人通道训练 A/B 对照")


def test_extended_projects_use_heading_or_concrete_path_without_incidental_keyword_leaks(tmp_path: Path) -> None:
    path = tmp_path / "2026-08-03.md"
    path.write_text("""# 日报 2026-08-03

## 核心进展
### 其他
#### 视频批量生成
- **关键文件**：`video-generator/generate.py`
#### GPU 资源管理
- **做了什么**：为后续 ASR 训练释放 GPU。
#### 上市公司研究报告生成
- **做了什么**：启动深度研究。
#### 研发驾驶舱页面整理
- **关键文件**：`rd-cockpit/frontend/src/App.tsx`
""", encoding="utf-8")

    report = parse_report(path)

    assert [task["project_ids"] for task in report["groups"][0]["tasks"]] == [
        ["avatar_video"], ["infrastructure"], ["investment_research"], ["research_tools"],
    ]
    assert report["groups"][0]["tasks"][1]["display_title"].endswith(
        "｜GPU 资源管理 — 为后续 ASR 训练释放 GPU"
    )
