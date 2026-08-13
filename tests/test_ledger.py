from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from rd_cockpit.ledger import Ledger


def test_append_is_idempotent_and_retract_is_append_only(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "events.sqlite")
    first = ledger.append(event_type="test_completed", source="test", status="passed",
                          payload={"count": 1}, dedup_key="test:one")
    second = ledger.append(event_type="test_completed", source="test", status="passed",
                           payload={"count": 1}, dedup_key="test:one")
    assert first == second
    correction = ledger.retract(first, "fixture was invalid")
    assert correction != first
    assert ledger.events() == []
    rows = ledger.events(include_history=True)
    assert [row["event_type"] for row in rows] == ["test_completed", "event_retracted"]
    assert rows[1]["supersedes"] == first
    ledger.close()


def test_evidence_is_attached(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "events.sqlite")
    event = ledger.append(event_type="benchmark_completed", source="test", status="passed",
                          evidence=[{"type": "result_json", "path": "result.json", "sha256": "abc"}])
    evidence = ledger.event_evidence(event)
    assert len(evidence) == 1
    assert evidence[0]["sha256"] == "abc"
    ledger.close()


def test_project_correction_preserves_history_but_updates_effective_view(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "events.sqlite")
    original = ledger.append(
        event_type="agent_session_completed", source="codex", project_id="asr_dialect",
        status="result", payload={"summary": "完成小说视频生成"},
    )
    corrected = ledger.correct_project(
        original, "avatar_video", "parent repository was mistaken for child project",
        repo_path="/workspace/video-generator",
    )
    assert corrected != original
    effective = ledger.events()
    assert len(effective) == 1
    assert effective[0]["event_id"] == corrected
    assert effective[0]["project_id"] == "avatar_video"
    assert effective[0]["supersedes"] == original
    assert len(ledger.events(include_history=True)) == 2
    ledger.close()
