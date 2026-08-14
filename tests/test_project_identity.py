from pathlib import Path

from rd_cockpit.project_identity import (
    canonical_project_ids, canonicalize_report, registered_project_names,
)


def _config(tmp_path: Path, monkeypatch) -> Path:
    path = tmp_path / "projects.yaml"
    path.write_text("""projects:
  speech:
    name: Speech
    legacy_project_ids: [old_speech]
  vision:
    name: Vision
project_aliases:
  legacy_vision: vision
""", encoding="utf-8")
    monkeypatch.setenv("RD_PROJECTS_CONFIG", str(path))
    return path


def test_only_registered_projects_become_visible_projects(tmp_path: Path, monkeypatch) -> None:
    _config(tmp_path, monkeypatch)
    assert registered_project_names() == {"speech": "Speech", "vision": "Vision"}
    assert canonical_project_ids(["old_speech", "unknown"]) == ["speech"]
    assert canonical_project_ids(["legacy_vision"]) == ["vision"]
    assert canonical_project_ids(["unknown"]) == ["unassigned"]


def test_report_keeps_raw_unmapped_ids_but_groups_them_as_unassigned(
    tmp_path: Path, monkeypatch,
) -> None:
    _config(tmp_path, monkeypatch)
    source = {"groups": [{"tasks": [
        {"project_ids": ["old_speech"]}, {"project_ids": ["unknown"]},
    ]}]}
    result = canonicalize_report(source)
    assert [item["project_ids"] for item in result["groups"][0]["tasks"]] == [
        ["speech"], ["unassigned"],
    ]
    assert result["unmapped_project_ids"] == ["unknown"]
    assert source["groups"][0]["tasks"][0]["project_ids"] == ["old_speech"]
