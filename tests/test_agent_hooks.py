from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from rd_cockpit.agent_hooks import _classify_command, handle_agent_hook
from rd_cockpit.sessions import session_views
from rd_cockpit.hook_install import install_user_hooks
from rd_cockpit.hook_queue import drain_hook_queue
from rd_cockpit.ledger import Ledger


def _home(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "evaluate.py").write_text("print('ok')\n", encoding="utf-8")
    subprocess.run(["git", "add", "evaluate.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    config = tmp_path / "config"
    config.mkdir()
    (config / "projects.yaml").write_text(
        f"projects:\n  demo:\n    name: Demo\n    repo_path: {repo}\n", encoding="utf-8")
    return tmp_path, repo


def test_agent_lifecycle_records_sessions_and_experiment_facts_without_stop_summary(tmp_path: Path) -> None:
    home, repo = _home(tmp_path)
    ledger = Ledger(home / ".rd-cockpit" / "events.sqlite")
    base = {"session_id": "agent-session-1", "cwd": str(repo),
            "transcript_path": str(tmp_path / "transcript.jsonl")}
    started = handle_agent_hook(home, ledger, "codex", {
        **base, "hook_event_name": "SessionStart", "source": "startup",
        "session_title": "评测 Demo 模型", "occurred_at": "2026-08-03T01:00:00+00:00",
    })
    assert started["project_id"] == "demo"

    tool = handle_agent_hook(home, ledger, "codex", {
        **base, "hook_event_name": "PostToolUse", "turn_id": "turn-1", "tool_name": "Bash",
        "tool_use_id": "tool-1", "occurred_at": "2026-08-03T01:20:00+00:00",
        "tool_input": {"cmd": "python evaluate.py --dataset waic-v2 --model ppocr-v6 --threshold 0.9",
                       "workdir": str(repo)},
        "tool_response": {"stdout": "WER: 0.12\nlatency_ms=47 ms\n", "exit_code": 0},
    })
    assert tool["classified"] == "experiment"
    assert tool["semantic_event"]
    metrics = ledger.events(event_types={"metric_observed"})
    assert {json.loads(row["payload_json"])["name"] for row in metrics} == {"wer", "latency_ms"}

    ended = handle_agent_hook(home, ledger, "codex", {
        **base, "hook_event_name": "SessionEnd", "reason": "other",
        "occurred_at": "2026-08-03T02:00:00+00:00",
    })
    assert ended["event_id"]
    assert session_views(ledger)[0]["status"] == "completed"
    ledger.close()


def test_claude_failed_test_is_extracted_without_another_model_call(tmp_path: Path) -> None:
    home, repo = _home(tmp_path)
    ledger = Ledger(home / ".rd-cockpit" / "events.sqlite")
    incoming = {"session_id": "claude-session-1", "cwd": str(repo),
                "transcript_path": str(tmp_path / "claude.jsonl")}
    handle_agent_hook(home, ledger, "claude-code", {
        **incoming, "hook_event_name": "PostToolUseFailure", "tool_name": "Bash",
        "tool_use_id": "tool-failed", "tool_input": {"command": "pytest tests -q"},
        "error": "2 failed, 24 passed", "occurred_at": "2026-08-03T03:00:00+00:00",
    })
    failed = ledger.events(event_types={"test_failed"})
    assert len(failed) == 1
    payload = json.loads(failed[0]["payload_json"])
    assert payload["test_counts"] == {"failed": 2, "passed": 24}
    assert failed[0]["provenance"] == "inferred"
    ledger.close()


def test_only_executed_runner_is_classified_not_incidental_eval_text() -> None:
    assert _classify_command("python evaluate.py --dataset waic-v2") == "experiment"
    assert _classify_command("python -m pytest tests -q") == "test"
    assert _classify_command("trtexec --onnx model.onnx") == "benchmark"
    assert _classify_command("jq '.results[]' outputs/evaluation-result.json") is None
    assert _classify_command(
        "python research_state.py add-task --kind evaluation --title 'Run model evaluation'"
    ) is None
    assert _classify_command("ssh host 'cat evaluation.log'") is None


def test_chained_command_output_after_pytest_does_not_pollute_test_metrics(tmp_path: Path) -> None:
    home, repo = _home(tmp_path)
    ledger = Ledger(home / ".rd-cockpit" / "events.sqlite")

    handle_agent_hook(home, ledger, "codex", {
        "session_id": "chained-test", "hook_event_name": "PostToolUse",
        "cwd": str(repo), "tool_name": "Bash", "tool_use_id": "chained-1",
        "tool_input": {"cmd": "pytest -q && python - <<'PY'\nprint('report')\nPY"},
        "tool_response": {
            "stdout": "...... [100%]\n6 passed in 0.06s\n历史结论 F1=0.909\n152 passed, 43 skipped\n",
            "exit_code": 0,
        },
    })

    row = ledger.events(event_types={"test_completed"})[0]
    payload = json.loads(row["payload_json"])
    assert payload["test_counts"] == {"passed": 6}
    assert payload["metrics"] == {}
    assert ledger.events(event_types={"metric_observed"}) == []
    ledger.close()


def test_executable_hook_queues_quickly_while_ledger_is_locked(tmp_path: Path) -> None:
    home, repo = _home(tmp_path)
    database = home / ".rd-cockpit" / "events.sqlite"
    Ledger(database).close()
    blocker = sqlite3.connect(database, timeout=1)
    blocker.execute("PRAGMA journal_mode=WAL")
    blocker.execute("BEGIN IMMEDIATE")

    script = Path(__file__).resolve().parents[1] / "hooks" / "agent-hook.py"
    incoming = {
        "session_id": "queued-session",
        "hook_event_name": "PostToolUse",
        "cwd": str(repo),
        "session_title": "验证锁等待降级",
        "turn_id": "queued-turn", "tool_name": "Bash", "tool_use_id": "queued-tool",
        "tool_input": {"command": "pytest -q"},
        "tool_response": {"stdout": "1 passed token=secret-value", "exit_code": 0},
    }
    env = dict(os.environ)
    env["RD_COCKPIT_HOME"] = str(home)
    started = time.monotonic()
    completed = subprocess.run(
        [sys.executable, str(script), "--source", "codex"],
        input=json.dumps(incoming),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        timeout=2,
        check=True,
    )
    elapsed = time.monotonic() - started
    response = json.loads(completed.stdout)
    assert elapsed < 1
    assert response == {}
    queued = list((home / ".rd-cockpit" / "hook-queue").glob("*.json"))
    assert len(queued) == 1
    assert "secret-value" not in queued[0].read_text(encoding="utf-8")

    blocker.rollback()
    blocker.close()
    direct = subprocess.run(
        [sys.executable, str(script), "--source", "codex"],
        input=json.dumps(incoming),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        timeout=2,
        check=True,
    )
    assert json.loads(direct.stdout) == {}
    ledger = Ledger(database)
    result = drain_hook_queue(home, ledger)
    assert result == {"queued": 0, "processed": 1, "failed": 0}
    assert ledger.scalar(
        "SELECT COUNT(*) FROM events WHERE session_id='queued-session' "
        "AND event_type='agent_session_started'"
    ) == 1
    assert ledger.scalar(
        "SELECT COUNT(*) FROM events WHERE session_id='queued-session' "
        "AND event_type='test_completed'"
    ) == 1
    ledger.close()


def test_hook_installer_preserves_existing_settings_and_is_idempotent(tmp_path: Path) -> None:
    cockpit = tmp_path / "cockpit"
    (cockpit / ".venv" / "bin").mkdir(parents=True)
    (cockpit / ".venv" / "bin" / "python").write_text("", encoding="utf-8")
    (cockpit / "hooks").mkdir()
    (cockpit / "hooks" / "agent-hook.py").write_text("", encoding="utf-8")
    user = tmp_path / "user"
    (user / ".claude").mkdir(parents=True)
    (user / ".claude" / "settings.json").write_text(
        json.dumps({"env": {"KEEP": "yes"}, "hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": "/existing/hook"}]}]}}), encoding="utf-8")

    first = install_user_hooks(cockpit, user)
    assert first["codex"]["changed"] is True
    assert first["claude_code"]["changed"] is True
    claude = json.loads((user / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert claude["env"]["KEEP"] == "yes"
    assert any(handler.get("command") == "/existing/hook"
               for group in claude["hooks"]["Stop"] for handler in group["hooks"])
    assert "PostToolUseFailure" in claude["hooks"]
    assert all("agent-hook.py" not in " ".join(
        [str(handler.get("command", "")), *[str(value) for value in handler.get("args", [])]])
        for group in claude["hooks"]["Stop"] for handler in group["hooks"])

    second = install_user_hooks(cockpit, user)
    assert second["codex"]["changed"] is False
    assert second["claude_code"]["changed"] is False
