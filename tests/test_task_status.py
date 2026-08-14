from pathlib import Path

from rd_cockpit.task_status import read_status, update_status


def test_task_status_preserves_stage_history_and_reports_model_preflight(
    tmp_path: Path, monkeypatch,
) -> None:
    codex = tmp_path / "codex"
    codex.write_text("#!/bin/sh\n", encoding="utf-8")
    codex.chmod(0o755)
    monkeypatch.setenv("RD_CODEX_BIN", str(codex))
    monkeypatch.delenv("RD_CLAUDE_BIN", raising=False)

    update_status(tmp_path, "pipeline", "running")
    update_status(tmp_path, "reports", "running")
    update_status(tmp_path, "reports", "ok", "cache hit")
    value = read_status(tmp_path)

    assert value["stages"]["pipeline"]["state"] == "running"
    assert value["stages"]["reports"]["state"] == "ok"
    assert value["stages"]["reports"]["message"] == "cache hit"
    assert value["model_tools"]["codex"]["available"] is True


def test_nightly_pipeline_only_uses_registered_status_stages() -> None:
    """Keep shell stages and the Python status contract in lockstep."""
    import re

    from rd_cockpit.task_status import STAGES

    script = (
        Path(__file__).resolve().parents[1] / "hooks" / "normalize-daily-reports.sh"
    ).read_text(encoding="utf-8")
    used = set(re.findall(r"run_stage\s+([a-z_]+)", script))
    assert used <= set(STAGES)
    assert "radar" in used
    assert "intelligence" in used
