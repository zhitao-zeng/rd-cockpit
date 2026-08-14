from pathlib import Path

import pytest

from rd_cockpit.ledger import Ledger
from rd_cockpit.semantic_feedback import (
    feedback_fingerprint, feedback_for_records, latest_feedback, record_feedback,
)


def _home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "cockpit"
    (home / "config").mkdir(parents=True)
    config = home / "config" / "projects.yaml"
    config.write_text("projects:\n  demo:\n    name: Demo\n", encoding="utf-8")
    monkeypatch.setenv("RD_PROJECTS_CONFIG", str(config))
    return home


def test_feedback_is_append_only_and_latest_choice_wins(tmp_path: Path, monkeypatch) -> None:
    home = _home(tmp_path, monkeypatch)
    ledger = Ledger(home / ".rd-cockpit" / "events.sqlite")
    base = {"view": "storyline", "item_id": "storyline:demo", "project_id": "demo",
            "text": "旧摘要", "source_dates": ["2026-08-01"]}
    record_feedback(home, ledger, {**base, "rating": "noise"})
    record_feedback(home, ledger, {**base, "rating": "accurate"})
    latest = latest_feedback(ledger, view="storyline")
    assert len(latest) == 1 and latest[0]["rating"] == "accurate"
    assert ledger.scalar("SELECT COUNT(*) FROM events WHERE event_type='semantic_feedback_recorded'") == 2
    relevant = feedback_for_records(ledger, [{"date": "2026-08-01", "project_ids": ["demo"]}])
    assert relevant[0]["rating"] == "accurate"
    assert feedback_fingerprint(relevant) == feedback_fingerprint(relevant)
    ledger.close()


def test_feedback_rejects_unknown_corrected_project(tmp_path: Path, monkeypatch) -> None:
    home = _home(tmp_path, monkeypatch)
    ledger = Ledger(home / ".rd-cockpit" / "events.sqlite")
    with pytest.raises(ValueError, match="registered project"):
        record_feedback(home, ledger, {
            "view": "storyline", "item_id": "storyline:demo", "project_id": "demo",
            "rating": "wrong_project", "corrected_project_id": "invented",
        })
    ledger.close()
