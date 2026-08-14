from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import rd_cockpit.life as life
from rd_cockpit.ledger import Ledger


def test_life_dashboard_combines_personal_calendar_reports_and_fun_stats(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "projects.yaml").write_text(
        "projects:\n  asr:\n    name: ASR\n    repo_path: /tmp/asr\n    verification_stages: []\n",
        encoding="utf-8",
    )
    (tmp_path / "config" / "personal.yaml").write_text(
        """profile:
  employment_start: 2025-08-01
  payday_day: 15
  annual_leave_total: 10
  annual_leave_used: 2.5
  anniversaries: []
project_start_dates:
  asr: 2026-01-01
holidays:
  region: CN
  custom: []
fun:
  token_per_book: 100000
""",
        encoding="utf-8",
    )
    latest = {
        "available": True, "date": "2026-08-02", "task_count": 3,
        "blockers": ["one blocker"], "push_summary": "ASR progressed",
        "token": {"total_tokens": 500_000}, "knowledge": ["WER improved"],
        "groups": [],
    }
    monkeypatch.setattr(life, "available_report_dates", lambda: ["2026-08-01", "2026-08-02"])
    monkeypatch.setattr(life, "available_supplement_dates", lambda: [])
    monkeypatch.setattr(life, "load_report", lambda report_date=None: latest if report_date is None else {"available": False})
    monkeypatch.setattr(life, "iter_reports", lambda **_: [latest])

    ledger = Ledger(tmp_path / ".rd-cockpit" / "events.sqlite")
    now = datetime(2026, 8, 3, 8, tzinfo=ZoneInfo("Asia/Shanghai"))
    result = life.life_dashboard(ledger, tmp_path, date(2026, 8, 3), now)

    assert result["employment"]["day_number"] == 368
    assert result["next_rest"] == {"date": "2026-08-08", "days": 5, "reason": "周末"}
    assert result["next_holiday"]["name"] == "中秋节"
    assert result["next_holiday"]["days"] == 53
    assert result["payday"]["days"] == 12
    assert result["annual_leave"]["remaining"] == 7.5
    assert result["report_streak"]["current"] == 2
    assert result["token_books"]["books"] == 5.0
    assert result["research_weather"]["name"] == "多云转晴"
    assert result["random_knowledge"]["text"] == "WER improved"
    assert result["gpu_pet"]["state"] == "还没孵化"
    assert result["projects"][0]["days"] == 215
    ledger.close()


def test_life_dashboard_marks_unknown_personal_values_instead_of_guessing(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "projects.yaml").write_text("projects: {}\n", encoding="utf-8")
    (tmp_path / "config" / "personal.yaml").write_text("profile: {}\n", encoding="utf-8")
    monkeypatch.setattr(life, "available_report_dates", lambda: [])
    monkeypatch.setattr(life, "available_supplement_dates", lambda: [])
    monkeypatch.setattr(life, "load_report", lambda report_date=None: {"available": False, "token": {}})
    monkeypatch.setattr(life, "iter_reports", lambda **_: [])
    ledger = Ledger(tmp_path / ".rd-cockpit" / "events.sqlite")

    result = life.life_dashboard(ledger, tmp_path, date(2026, 8, 3))

    assert result["employment"]["configured"] is False
    assert result["payday"]["configured"] is False
    assert result["annual_leave"]["configured"] is False
    ledger.close()


def test_last_day_payday_and_explicit_remaining_leave(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "projects.yaml").write_text("projects: {}\n", encoding="utf-8")
    (tmp_path / "config" / "personal.yaml").write_text(
        "profile:\n  payday_day: last\n  annual_leave_remaining: 10.5\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(life, "available_report_dates", lambda: [])
    monkeypatch.setattr(life, "available_supplement_dates", lambda: [])
    monkeypatch.setattr(life, "load_report", lambda report_date=None: {"available": False, "token": {}})
    monkeypatch.setattr(life, "iter_reports", lambda **_: [])
    ledger = Ledger(tmp_path / ".rd-cockpit" / "events.sqlite")

    result = life.life_dashboard(ledger, tmp_path, date(2026, 8, 3))

    assert result["payday"] == {"configured": True, "date": "2026-08-31", "days": 28,
                                 "day": 31, "rule": "last_day"}
    assert result["annual_leave"]["remaining"] == 10.5
    assert result["annual_leave"]["configured"] is True
    ledger.close()


def test_random_knowledge_does_not_promote_ordinary_task_results(monkeypatch) -> None:
    monkeypatch.setattr(life, "iter_reports", lambda **_: [{
        "date": "2026-08-03",
        "knowledge": [],
        "groups": [{"tasks": [{
            "results": ["前端构建成功并上传文件"],
            "conclusions": [],
        }]}],
    }])

    result = life._random_knowledge(date(2026, 8, 3))

    assert result == {"available": False, "text": None, "date": None}


def test_random_knowledge_accepts_explicit_task_conclusions(monkeypatch) -> None:
    monkeypatch.setattr(life, "iter_reports", lambda **_: [{
        "date": "2026-08-03",
        "knowledge": [],
        "groups": [{"tasks": [{
            "results": ["测试通过"],
            "conclusions": ["DDS 发现延迟是首包丢失的根因"],
        }]}],
    }])

    result = life._random_knowledge(date(2026, 8, 3))

    assert result == {
        "available": True,
        "text": "DDS 发现延迟是首包丢失的根因",
        "date": "2026-08-03",
    }


def test_gpu_pet_zoo_keeps_each_observed_gpu_and_marks_stale_samples(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / ".rd-cockpit" / "events.sqlite")
    ledger.append(
        event_type="resource_snapshot",
        source="test",
        occurred_at="2026-08-03T00:00:00+00:00",
        payload={
            "sampled_at": "2026-08-03T00:00:00+00:00",
            "gpus": [
                {"index": 0, "utilization_pct": 82, "memory_used_mb": 16384, "temperature_c": 72},
                {"index": 1, "utilization_pct": 0, "memory_used_mb": 8192, "temperature_c": 45},
            ],
        },
    )

    fresh = life._gpu_pet(ledger, datetime(2026, 8, 3, 8, 30, tzinfo=ZoneInfo("Asia/Shanghai")))
    assert fresh["pets"][0]["state"] == "此刻忙碌"
    assert fresh["pets"][1]["state"] == "显存已分配"
    assert fresh["pets"][1]["memory_used_mb"] == 8192

    stale = life._gpu_pet(ledger, datetime(2026, 8, 3, 10, 30, tzinfo=ZoneInfo("Asia/Shanghai")))
    assert stale["state"] == "睡着了"
    assert all(item["stale"] for item in stale["pets"])
    assert stale["pets"][0]["state"] == "快照过期"
    ledger.close()


def test_gpu_pet_requires_three_spread_samples_before_claiming_a_trend(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / ".rd-cockpit" / "events.sqlite")
    for minute in (0, 5, 10):
        ledger.append(
            event_type="resource_snapshot", source="test",
            occurred_at=f"2026-08-03T00:{minute:02d}:00+00:00",
            payload={"sampled_at": f"2026-08-03T00:{minute:02d}:00+00:00", "gpus": [
                {"index": 0, "utilization_pct": 0, "memory_used_mb": 8192, "temperature_c": 45},
                {"index": 1, "utilization_pct": 70, "memory_used_mb": 12000, "temperature_c": 65},
            ]},
        )

    result = life._gpu_pet(ledger, datetime(2026, 8, 3, 8, 12, tzinfo=ZoneInfo("Asia/Shanghai")))

    assert result["pets"][0]["state"] == "显存驻留 · 持续低利用率"
    assert result["pets"][1]["state"] == "持续奔跑"
    ledger.close()
