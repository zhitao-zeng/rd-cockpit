from __future__ import annotations

import gzip
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rd_cockpit.ledger import Ledger
from rd_cockpit.maintenance import maintain
from rd_cockpit.resources import rollup_history


def _resource_payload(utilization: float, memory: float) -> dict:
    return {
        "gpus": [{
            "index": 0,
            "utilization_pct": utilization,
            "memory_used_mb": memory,
            "temperature_c": 60,
            "power_w": 120,
        }],
    }


def test_maintenance_backs_up_before_pruning_and_keeps_legacy_rows(tmp_path: Path) -> None:
    home = tmp_path
    database = home / ".rd-cockpit" / "events.sqlite"
    ledger = Ledger(database)
    old = (datetime.now(timezone.utc) - timedelta(days=40)).replace(
        minute=5, second=0, microsecond=0,
    )
    old_resource_ids = [
        ledger.append(
            event_type="resource_snapshot", source="resource_sampler", machine="gpu-box",
            occurred_at=(old + timedelta(minutes=index * 10)).isoformat(),
            payload=_resource_payload(utilization, memory),
            evidence=[{"type": "sample", "path": f"sample-{index}.json"}],
        )
        for index, (utilization, memory) in enumerate(((20, 1000), (60, 3000)))
    ]
    recent_resource = ledger.append(
        event_type="resource_snapshot", source="resource_sampler", machine="gpu-box",
        occurred_at=datetime.now(timezone.utc).isoformat(), payload=_resource_payload(80, 4000),
    )
    legacy = ledger.append(
        event_type="agent_tool_completed", source="codex", occurred_at=old.isoformat(),
        payload={"tool": "pytest"}, evidence=[{"type": "command", "path": "run.log"}],
    )
    ledger.close()

    result = maintain(home, backup_retention_days=14, resource_retention_days=30)

    backup = home / ".rd-cockpit" / "backups" / result["backup"]["path"]
    assert backup.is_file()
    assert backup.stat().st_mode & 0o777 == 0o600
    assert result["backup"]["integrity_check"] == "ok"
    with sqlite3.connect(backup) as copy:
        assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        backed_up = {
            row[0] for row in copy.execute(
                "SELECT event_id FROM events WHERE event_type='resource_snapshot'",
            )
        }
    assert set(old_resource_ids).issubset(backed_up)

    ledger = Ledger(database)
    remaining_resources = {
        row["event_id"] for row in ledger.events(
            event_types={"resource_snapshot"}, include_history=True,
        )
    }
    assert remaining_resources == {recent_resource}
    assert ledger.db.execute("SELECT 1 FROM events WHERE event_id=?", (legacy,)).fetchone() is None
    assert {row["event_id"] for row in ledger.events(include_history=True)} >= {legacy}
    assert ledger.event_evidence(legacy)[0]["path"] == "run.log"

    day = rollup_history(ledger, days=365, kind="day")["points"]
    hour = rollup_history(ledger, days=365, kind="hour")["points"]
    old_day = next(point for point in day if point["sample_count"] == 2)
    assert len(day) == 2
    assert len(hour) == 2
    assert old_day["avg_utilization_pct"] == 40
    assert old_day["avg_memory_used_mb"] == 2000
    assert old_day["max_memory_used_mb"] == 3000
    ledger.close()

    assert result["resources"]["source_rows_deleted"] == 2
    resource_archive = Path(result["resources"]["archives"][0]["path"])
    assert resource_archive.stat().st_mode & 0o777 == 0o600
    with gzip.open(resource_archive, "rt", encoding="utf-8") as handle:
        archived_resources = [json.loads(line) for line in handle]
    assert {item["event_id"] for item in archived_resources} == set(old_resource_ids)
    assert archived_resources[0]["evidence"][0]["path"].startswith("sample-")

    legacy_archive = Path(result["legacy_archive"]["archives"][0]["path"])
    with gzip.open(legacy_archive, "rt", encoding="utf-8") as handle:
        archived_legacy = [json.loads(line) for line in handle]
    assert [item["event_id"] for item in archived_legacy] == [legacy]
    assert result["legacy_archive"]["source_rows_deleted"] == 0
    assert result["cold_store"]["removed_from_hot"] == 1
    assert result["view_cache"]["remaining_bytes"] >= 0
    assert Path(result["cold_store"]["path"]).stat().st_mode & 0o777 == 0o600

    # Re-running maintenance on the same UTC day must preserve the earlier,
    # pre-compaction recovery copy instead of replacing it with a smaller DB.
    second = maintain(home, backup_retention_days=14, resource_retention_days=30)
    assert second["backup"]["reused_daily_backup"] is True
    with sqlite3.connect(backup) as copy:
        assert copy.execute(
            "SELECT COUNT(*) FROM events WHERE event_type='resource_snapshot'",
        ).fetchone()[0] == 3
    assert second["legacy_archive"]["archives"][0]["reused"] is True


def test_cold_event_can_be_retracted_without_losing_history(tmp_path: Path) -> None:
    database = tmp_path / ".rd-cockpit" / "events.sqlite"
    ledger = Ledger(database)
    old = datetime.now(timezone.utc) - timedelta(days=45)
    original = ledger.append(
        event_type="agent_tool_completed", source="codex", project_id="demo",
        occurred_at=old.isoformat(), payload={"tool": "pytest"},
        evidence=[{"type": "log", "path": "pytest.log"}],
    )
    ledger.close()

    maintain(tmp_path, resource_retention_days=30)
    ledger = Ledger(database)
    assert [row["event_id"] for row in ledger.events(project_id="demo")] == [original]
    correction = ledger.retract(original, "tool record belonged to a dry run")
    assert ledger.events(project_id="demo") == []
    history = ledger.events(project_id="demo", include_history=True)
    assert {row["event_id"] for row in history} == {original, correction}
    assert ledger.event_evidence(original)[0]["path"] == "pytest.log"
    ledger.close()


def test_maintenance_can_archive_resource_samples_without_pruning(tmp_path: Path) -> None:
    database = tmp_path / ".rd-cockpit" / "events.sqlite"
    ledger = Ledger(database)
    event_id = ledger.append(
        event_type="resource_snapshot", source="resource_sampler", machine="local",
        occurred_at=(datetime.now(timezone.utc) - timedelta(days=40)).isoformat(),
        payload=_resource_payload(10, 500),
    )
    ledger.close()

    result = maintain(tmp_path, resource_retention_days=30, prune_resources=False)
    assert result["resources"]["source_rows_deleted"] == 0
    ledger = Ledger(database)
    assert ledger.db.execute("SELECT 1 FROM events WHERE event_id=?", (event_id,)).fetchone()
    ledger.close()
