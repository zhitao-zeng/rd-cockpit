from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from rd_cockpit.ledger import Ledger
from rd_cockpit.migrations import LATEST_SCHEMA_VERSION


def test_legacy_database_is_backed_up_and_migrated_in_order(tmp_path: Path) -> None:
    database = tmp_path / ".rd-cockpit" / "events.sqlite"
    database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE legacy_marker(value TEXT NOT NULL)")
        connection.execute("INSERT INTO legacy_marker VALUES ('preserve-me')")
        connection.commit()

    ledger = Ledger(database)
    assert ledger.scalar("PRAGMA user_version") == LATEST_SCHEMA_VERSION
    assert [
        row[0] for row in ledger.db.execute(
            "SELECT version FROM schema_migrations ORDER BY version",
        )
    ] == list(range(1, LATEST_SCHEMA_VERSION + 1))
    assert ledger.scalar("SELECT value FROM legacy_marker") == "preserve-me"
    ledger.close()

    backups = list((database.parent / "migration-backups").glob("events-v0-to-v*.sqlite"))
    assert len(backups) == 1
    assert backups[0].stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(backups[0]) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT value FROM legacy_marker").fetchone()[0] == "preserve-me"
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0


def test_readonly_ledger_neither_migrates_nor_accepts_writes(tmp_path: Path) -> None:
    database = tmp_path / "events.sqlite"
    writer = Ledger(database)
    writer.append(event_type="test_completed", source="test")
    writer.close()

    reader = Ledger(database, readonly=True)
    assert len(reader.events()) == 1
    with pytest.raises(sqlite3.OperationalError):
        reader.db.execute("INSERT INTO report_runs(report_id,report_date,generated_at) VALUES ('x','x','x')")
    reader.close()
