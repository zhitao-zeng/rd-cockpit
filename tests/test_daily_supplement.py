import json
from pathlib import Path

from rd_cockpit.daily_supplement import load_supplement


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def test_daily_supplement_attributes_sessions_tokens_git_and_files(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    _write(data / "2026-08-01_codex_sessions.json", {
        "total_sessions": 1,
        "total_tool_calls": 12,
        "token_usage_summary": {"requests": 3, "total_tokens": 1000},
        "sessions": [{
            "cwd": "/workspace/embodied-ai",
            "edited_files": ["/workspace/robot-speech/perception/config.yaml"],
            "tool_count": 12,
            "duration_min": 20,
            "token_usage": {"available": True, "total_tokens": 1000, "requests": 3},
        }],
    })
    _write(data / "2026-08-01_git.json", {
        "total_commits": 2,
        "repos": {"robot-speech": ["a one", "b two"]},
    })
    _write(data / "2026-08-01_files.json", {
        "total_files": 1,
        "by_project": {"robot-speech": ["perception/config.yaml"]},
    })

    result = load_supplement("2026-08-01", tmp_path)

    assert result["available"] is True
    assert result["totals"]["tokens"] == 1000
    assert result["coverage"]["token_attribution_ratio"] == 1.0
    assert result["projects"] == [{
        "project_id": "asr", "name": "Embodied AI / ASR", "sessions": 1, "claude_sessions": 0,
        "codex_sessions": 1, "requests": 3, "tool_calls": 12, "duration_minutes": 20.0,
        "tokens": 1000, "claude_tokens": 0, "codex_tokens": 1000,
        "commits": 2, "changed_files": 1,
    }]


def test_daily_supplement_splits_asr_repositories(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    sessions = []
    for index, cwd in enumerate((
        "/workspace/dialect-asr",
        "/workspace/speech-model-eval",
        "/workspace/speech-aligner",
        "/workspace/robot-speech",
    ), start=1):
        sessions.append({
            "cwd": cwd, "edited_files": [], "tool_count": 1, "duration_min": 1,
            "token_usage": {"available": True, "total_tokens": index * 100, "requests": 1},
        })
    _write(data / "2026-08-02_codex_sessions.json", {
        "total_sessions": 4, "total_tool_calls": 4,
        "token_usage_summary": {"requests": 4, "total_tokens": 1000}, "sessions": sessions,
    })

    result = load_supplement("2026-08-02", tmp_path)
    by_id = {item["project_id"]: item for item in result["projects"]}

    assert by_id["asr_dialect"]["tokens"] == 100
    assert by_id["asr_model_eval"]["tokens"] == 200
    assert by_id["asr_alignment"]["tokens"] == 300
    assert by_id["asr"]["tokens"] == 400


def test_firered_model_inside_embodied_asr_is_not_mistaken_for_dialect(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    _write(data / "2026-08-05_codex_sessions.json", {
        "total_sessions": 1,
        "token_usage_summary": {"requests": 1, "total_tokens": 201022},
        "sessions": [{
            "cwd": "/workspace/embodied-ai",
            "_project": "embodied-ai",
            "first_intent": "总结机器人 ASR 的 VAD、热词和 Conformer 对比结果",
            "last_conclusion": "当前机器人 ASR 的域差异仍需真实数据验证",
            "edited_files": [],
            "token_usage": {"available": True, "total_tokens": 201022, "requests": 1},
        }],
    })
    result = load_supplement("2026-08-05", tmp_path)
    by_id = {item["project_id"]: item for item in result["projects"]}
    assert by_id["asr"]["tokens"] == 201022
    assert "asr_dialect" not in by_id
