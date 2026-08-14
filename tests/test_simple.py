from pathlib import Path
from datetime import date, datetime, timezone

from rd_cockpit.ledger import Ledger
from rd_cockpit.simple import _usage, analytics, daily_records, knowledge


def test_usage_keeps_latest_snapshot_per_agent_session(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "events.sqlite")
    base = {
        "agent": "codex",
        "session_id": "session-1",
        "input_tokens": 80,
        "output_tokens": 20,
        "cached_tokens": 50,
        "reasoning_tokens": 0,
    }
    ledger.append(
        event_type="agent_usage_observed",
        source="codex_usage",
        session_id="session-1",
        payload={**base, "total_tokens": 100},
        dedup_key="usage-old",
    )
    ledger.append(
        event_type="agent_usage_observed",
        source="codex_usage",
        session_id="session-1",
        payload={**base, "total_tokens": 200},
        dedup_key="usage-new",
    )

    result = _usage(ledger.events())

    assert result["total_tokens"] == 200
    assert result["agents"]["codex"]["sessions"] == 1
    ledger.close()


def test_daily_usage_uses_latest_project_assignment(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    config = tmp_path / "config"
    config.mkdir()
    (config / "projects.yaml").write_text(
        f"projects:\n  asr:\n    name: ASR\n    repo_path: {repo}\n    verification_stages: []\n",
        encoding="utf-8",
    )
    ledger = Ledger(tmp_path / ".rd-cockpit" / "events.sqlite")
    payload = {
        "agent": "codex", "session_id": "session-1", "input_tokens": 80,
        "output_tokens": 20, "cached_tokens": 50, "reasoning_tokens": 0,
        "total_tokens": 100, "topics": ["维护研究记录"],
    }
    occurred_at = "2026-08-02T08:00:00+00:00"
    ledger.append(event_type="agent_usage_observed", source="codex_usage", project_id="asr",
                  session_id="session-1", payload=payload, occurred_at=occurred_at,
                  dedup_key="old-assignment")
    ledger.append(event_type="agent_usage_observed", source="codex_usage", project_id=None,
                  session_id="session-1", payload={**payload, "total_tokens": 200}, occurred_at=occurred_at,
                  dedup_key="corrected-assignment")

    result = daily_records(ledger, tmp_path, date(2026, 8, 2))

    assert result["records"][0]["usage"]["total_tokens"] == 0
    assert result["unassigned_usage"]["total_tokens"] == 200
    # Raw user prompts are usage metadata, not a readable research summary.
    assert result["unassigned_work"] == []
    ledger.close()


def test_knowledge_hides_progress_results_and_keeps_explicit_claims(
    tmp_path: Path, monkeypatch,
) -> None:
    report = {
        "date": "2026-08-02",
        "token": {"total_tokens": 0, "rows": []},
        "knowledge": ["OCR 核心瓶颈：识别模型能力不足，不是检测框数量"],
        "decisions": ["OCR 暂不继续降低检测阈值"],
        "groups": [{
            "title": "OCR",
            "tasks": [{
                "title": "构建并测试 OCR 镜像",
                "display_title": "具身智能 OCR｜构建并测试 OCR 镜像",
                "project_ids": ["ocr"],
                "results": ["镜像构建成功", "125 项测试通过"],
                "conclusions": [
                    "识别模型能力不足，不是检测框数量",
                    "降低检测阈值只会增加误检",
                ],
            }],
        }],
    }
    monkeypatch.setattr("rd_cockpit.daily_source.iter_reports", lambda **_: iter([report]))
    monkeypatch.setattr("rd_cockpit.daily_source._project_ids", lambda text: ["ocr"])
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "projects.yaml").write_text("projects: {}\n", encoding="utf-8")
    ledger = Ledger(tmp_path / ".rd-cockpit" / "events.sqlite")

    result = knowledge(ledger, tmp_path)

    titles = [item["title"] for item in result["items"]]
    assert "镜像构建成功" not in titles
    assert "125 项测试通过" not in titles
    assert "OCR 核心瓶颈" in titles
    assert "OCR 暂不继续降低检测阈值" in titles
    assert "降低检测阈值只会增加误检" in titles
    assert all(item["title"] != item.get("detail") for item in result["items"])
    assert result["summary"]["hidden_task_results"] == 2
    assert result["summary"]["deduplicated"] == 1
    ledger.close()


def test_knowledge_prefers_named_product_over_generic_asr_dependency(
    tmp_path: Path, monkeypatch,
) -> None:
    (tmp_path / "config").mkdir()
    config = tmp_path / "config" / "projects.yaml"
    config.write_text(
        "projects:\n  asr:\n    name: ASR\n  avatar_video:\n    name: Avatar Video\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("RD_PROJECTS_CONFIG", str(config))
    report = {
        "date": "2026-08-09",
        "knowledge": ["video-generator 提交需接通真实媒体、TTS 和 ASR 后端。"],
        "decisions": [],
        "groups": [],
    }
    monkeypatch.setattr("rd_cockpit.daily_source.iter_reports", lambda **_: iter([report]))
    monkeypatch.setattr(
        "rd_cockpit.daily_source._project_ids",
        lambda text: ["asr_other", "avatar_video"],
    )
    ledger = Ledger(tmp_path / "events.sqlite")

    result = knowledge(ledger, tmp_path)

    assert result["items"][0]["project_id"] == "avatar_video"
    ledger.close()


def test_knowledge_infers_project_from_matching_task_conclusion(
    tmp_path: Path, monkeypatch,
) -> None:
    (tmp_path / "config").mkdir()
    config = tmp_path / "config" / "projects.yaml"
    config.write_text("projects:\n  ocr:\n    name: OCR\n", encoding="utf-8")
    monkeypatch.setenv("RD_PROJECTS_CONFIG", str(config))
    claim = "降低检测阈值只会增加误检"
    report = {
        "date": "2026-08-09",
        "knowledge": [claim],
        "decisions": [],
        "groups": [{
            "title": "OCR",
            "tasks": [{
                "title": "阈值实验",
                "display_title": "具身智能 OCR｜阈值实验",
                "project_ids": ["ocr"],
                "results": [],
                "conclusions": [claim],
            }],
        }],
    }
    monkeypatch.setattr("rd_cockpit.daily_source.iter_reports", lambda **_: iter([report]))
    monkeypatch.setattr("rd_cockpit.daily_source._project_ids", lambda text: [])
    ledger = Ledger(tmp_path / "events.sqlite")

    result = knowledge(ledger, tmp_path)

    assert len(result["items"]) == 1
    assert result["items"][0]["project_id"] == "ocr"
    ledger.close()


def test_analytics_counts_explicit_claims_not_ordinary_results(
    tmp_path: Path, monkeypatch,
) -> None:
    report = {
        "date": "2026-08-02",
        "token": {"total_tokens": 0, "rows": []},
        "knowledge": ["OCR 核心瓶颈：识别模型能力不足，不是检测框数量"],
        "decisions": ["OCR 暂不继续降低检测阈值"],
        "groups": [{
            "title": "OCR",
            "tasks": [{
                "title": "构建并测试 OCR 镜像",
                "project_ids": ["ocr"],
                "did": ["构建镜像"],
                "results": ["镜像构建成功", "125 项测试通过"],
                "conclusions": [
                    "识别模型能力不足，不是检测框数量",
                    "降低检测阈值只会增加误检",
                ],
            }],
        }],
    }
    monkeypatch.setattr("rd_cockpit.daily_source.iter_reports", lambda **_: iter([report]))
    monkeypatch.setattr("rd_cockpit.daily_source._project_ids", lambda text: ["ocr"])
    monkeypatch.setattr("rd_cockpit.daily_supplement.available_supplement_dates", lambda: [])
    monkeypatch.setattr(
        "rd_cockpit.daily_supplement.load_supplement",
        lambda day: {"available": False, "projects": []},
    )
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "projects.yaml").write_text(
        "projects:\n  ocr:\n    name: OCR\n", encoding="utf-8",
    )
    monkeypatch.setenv("RD_PROJECTS_CONFIG", str(tmp_path / "config" / "projects.yaml"))
    ledger = Ledger(tmp_path / ".rd-cockpit" / "events.sqlite")

    result = analytics(ledger, tmp_path, days=30)
    ocr = next(item for item in result["daily"] if item["project_id"] == "ocr")

    assert ocr["activities"] == 1
    assert ocr["conclusions"] == 3
    ledger.close()


def test_analytics_exposes_only_aggregated_agent_lifecycle_activity(
    tmp_path: Path, monkeypatch,
) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "projects.yaml").write_text(
        "projects:\n  demo:\n    name: Demo\n", encoding="utf-8",
    )
    monkeypatch.setenv("RD_PROJECTS_CONFIG", str(tmp_path / "config" / "projects.yaml"))
    monkeypatch.setattr("rd_cockpit.daily_source.iter_reports", lambda **_: iter([]))
    monkeypatch.setattr("rd_cockpit.daily_supplement.available_supplement_dates", lambda: [])
    ledger = Ledger(tmp_path / ".rd-cockpit" / "events.sqlite")
    stamp = datetime.now(timezone.utc).isoformat()
    ledger.record_agent_activity(
        source="codex", session_id="session-1", project_id="demo",
        semantic_kind="command", failed=False, duration_ms=90_000,
        occurred_at=stamp, activity_key="one",
    )
    ledger.record_agent_activity(
        source="codex", session_id="session-1", project_id="demo",
        semantic_kind="command", failed=True, duration_ms=30_000,
        occurred_at=stamp, activity_key="two",
    )

    result = analytics(ledger, tmp_path, days=7)

    assert result["agent_activity"]["totals"] == {
        "completed": 1, "failed": 1, "duration_minutes": 2.0, "sessions": 1,
    }
    assert result["agent_activity"]["projects"][0]["project_id"] == "demo"
    assert "session_id" not in result["agent_activity"]["projects"][0]
    ledger.close()
