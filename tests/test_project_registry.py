import json
from pathlib import Path

from rd_cockpit.cli import main
from rd_cockpit.config import add_project, load_config
from rd_cockpit.daily_source import parse_report
from rd_cockpit.daily_supplement import load_supplement


def _base_home(tmp_path: Path) -> Path:
    home = tmp_path / "cockpit"
    (home / "config").mkdir(parents=True)
    (home / "config" / "projects.yaml").write_text(
        "projects:\n  existing:\n    name: Existing\n    repo_path: /tmp/existing\n"
        "machines:\n  local:\n    kind: workstation\n",
        encoding="utf-8",
    )
    return home


def test_add_project_preserves_config_and_lists_it(tmp_path: Path, capsys) -> None:
    home = _base_home(tmp_path)
    repo = tmp_path / "translation-asr"
    repo.mkdir()

    result = add_project(
        home,
        project_id="asr_translation",
        name="ASR / 翻译",
        repo_path=repo,
        lifecycle_status="dormant",
        match_keywords=["语音翻译"],
        verification_stages=["implementation", "evaluation"],
    )
    config = load_config(home / "config" / "projects.yaml")
    assert result["project_id"] == "asr_translation"
    assert config["projects"]["existing"]["name"] == "Existing"
    assert config["projects"]["asr_translation"]["repo_path"] == str(repo)
    assert config["projects"]["asr_translation"]["lifecycle_status"] == "dormant"
    assert config["machines"]["local"]["kind"] == "workstation"

    assert main(["--home", str(home), "project", "list", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["asr_translation"] == "ASR / 翻译"


def test_add_first_project_to_compact_empty_public_registry(tmp_path: Path) -> None:
    home = tmp_path / "cockpit"
    (home / "config").mkdir(parents=True)
    public = home / "config" / "projects.yaml"
    public.write_text(
        "projects: {}\n\nmachine: local\nmachines: {}\n",
        encoding="utf-8",
    )
    repo = tmp_path / "sample-research"
    repo.mkdir()

    result = add_project(
        home,
        project_id="sample_research",
        name="Sample Research",
        repo_path=repo,
    )

    local = home / "config" / "projects.local.yaml"
    assert result["config_path"] == str(local)
    assert local.stat().st_mode & 0o777 == 0o600
    assert load_config(public)["projects"]["sample_research"]["name"] == "Sample Research"
    assert "sample_research" not in public.read_text(encoding="utf-8")


def test_registered_project_drives_report_and_token_attribution(
    tmp_path: Path, monkeypatch,
) -> None:
    home = _base_home(tmp_path)
    repo = tmp_path / "translation-asr"
    repo.mkdir()
    add_project(
        home,
        project_id="asr_translation",
        name="ASR / 翻译",
        repo_path=repo,
        match_keywords=["语音翻译"],
    )
    monkeypatch.setenv("RD_COCKPIT_HOME", str(home))

    report = tmp_path / "2026-08-11.md"
    report.write_text(
        "# 日报 2026-08-11\n\n## 核心进展\n\n"
        "### ASR / 翻译\n#### 语音翻译实验\n"
        f"- **做了什么**：运行端到端评测。\n- **关键文件**：{repo}/evaluate.py\n",
        encoding="utf-8",
    )
    parsed = parse_report(report)
    task = parsed["groups"][0]["tasks"][0]
    assert task["project_ids"] == ["asr_translation"]
    assert parsed["project_names"]["asr_translation"] == "ASR / 翻译"

    data = tmp_path / "reports" / "data"
    data.mkdir(parents=True)
    (data / "2026-08-11_codex_sessions.json").write_text(json.dumps({
        "total_sessions": 1,
        "token_usage_summary": {"requests": 1, "total_tokens": 321},
        "sessions": [{
            "cwd": str(repo), "edited_files": [],
            "token_usage": {"available": True, "total_tokens": 321, "requests": 1},
        }],
    }), encoding="utf-8")
    supplement = load_supplement("2026-08-11", tmp_path / "reports")
    by_id = {item["project_id"]: item for item in supplement["projects"]}
    assert by_id["asr_translation"]["tokens"] == 321
    assert by_id["asr_translation"]["name"] == "ASR / 翻译"
