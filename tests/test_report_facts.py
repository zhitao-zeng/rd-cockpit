from __future__ import annotations

from pathlib import Path

import rd_cockpit.daily_source as daily_source
from rd_cockpit.report_facts import refresh_report_facts


def test_report_fact_snapshot_reparses_only_changed_day(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "cockpit"
    report_dir = tmp_path / "daily-reports"
    report_dir.mkdir()
    (home / "config").mkdir(parents=True)
    config = home / "config" / "projects.yaml"
    config.write_text("projects: {}\n", encoding="utf-8")
    monkeypatch.setenv("RD_DAILY_REPORT_DIR", str(report_dir))
    monkeypatch.setenv("RD_PROJECTS_CONFIG", str(config))
    for day in ("2026-08-01", "2026-08-02"):
        (report_dir / f"{day}.md").write_text(
            f"# 日报 {day}\n\n## 核心进展\n### 研发工具\n#### 缓存测试\n- **做了什么**：解析 {day}\n",
            encoding="utf-8",
        )

    calls: list[str] = []
    original = daily_source.parse_report

    def parse(path: Path, **kwargs):
        calls.append(path.name)
        return original(path, **kwargs)

    monkeypatch.setattr(daily_source, "parse_report", parse)
    first = refresh_report_facts(home)
    assert first["refresh"]["parsed"] == 2
    assert len(calls) == 2
    calls.clear()
    second = refresh_report_facts(home)
    assert second["refresh"] == {
        "parsed": 0, "reused": 2, "removed": 0, "config_changed": False,
    }
    assert calls == []

    changed = report_dir / "2026-08-02.md"
    changed.write_text(changed.read_text(encoding="utf-8") + "- **结果**：缓存命中\n", encoding="utf-8")
    third = refresh_report_facts(home)
    assert third["refresh"]["parsed"] == 1
    assert third["refresh"]["reused"] == 1
    assert calls == ["2026-08-02.md"]
    assert (home / ".rd-cockpit" / "report-facts.json").stat().st_mode & 0o777 == 0o600
