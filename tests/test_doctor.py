from __future__ import annotations

from pathlib import Path

from rd_cockpit.doctor import doctor
from rd_cockpit.ledger import Ledger
from rd_cockpit.maintenance import online_backup


def test_doctor_checks_database_and_restores_backup_to_temporary_copy(
    tmp_path: Path, monkeypatch,
) -> None:
    home = tmp_path / "cockpit"
    repo = tmp_path / "repo"
    repo.mkdir()
    (home / "config").mkdir(parents=True)
    config = home / "config" / "projects.yaml"
    config.write_text(
        f"projects:\n  demo:\n    name: Demo\n    repo_path: {repo}\n",
        encoding="utf-8",
    )
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "2026-08-01.md").write_text("# 日报 2026-08-01\n", encoding="utf-8")
    monkeypatch.setenv("RD_DAILY_REPORT_DIR", str(reports))
    monkeypatch.setenv("RD_PROJECTS_CONFIG", str(config))
    ledger = Ledger(home / ".rd-cockpit" / "events.sqlite")
    ledger.append(event_type="test_completed", source="fixture")
    ledger.close()
    online_backup(home)
    (home / "frontend" / "dist").mkdir(parents=True)
    (home / "frontend" / "dist" / "index.html").write_text("ok", encoding="utf-8")

    result = doctor(home, check_services=False)

    assert result["summary"]["errors"] == 0
    checks = {item["name"]: item for item in result["checks"]}
    assert checks["database"]["status"] == "ok"
    assert checks["backup_restore"]["status"] == "ok"
    assert checks["daily_reports"]["details"]["latest"] == "2026-08-01"
