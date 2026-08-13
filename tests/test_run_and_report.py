from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def run_cli(home: Path, *args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "rd_cockpit", "--home", str(home), *args],
                          cwd=Path(__file__).parents[1], text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, input=input_text)


def test_run_records_result_and_daily_report(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "hello.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "hello.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    config = tmp_path / "config"
    config.mkdir()
    (config / "projects.yaml").write_text(f"projects:\n  demo:\n    name: Demo\n    repo_path: {repo}\n    verification_stages: [implementation, unit_test, local_model]\n", encoding="utf-8")
    assert run_cli(tmp_path, "init").returncode == 0
    result = run_cli(tmp_path, "run", "--project", "demo", "--type", "test", "--", sys.executable, "-c", "print('ok')")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["exit_code"] == 0
    verify = run_cli(tmp_path, "verify", "--project", "demo", "--stage", "local_model", "--status", "passed", "--confirm")
    assert verify.returncode == 0, verify.stderr
    state = run_cli(tmp_path, "resume", "demo", "--json")
    assert state.returncode == 0, state.stderr
    assert json.loads(state.stdout)["verification"]["local_model"]["status"] == "passed"
    (repo / "hello.txt").write_text("changed after verification\n", encoding="utf-8")
    refreshed = run_cli(tmp_path, "status", "demo", "--json")
    assert refreshed.returncode == 0, refreshed.stderr
    refreshed_state = json.loads(refreshed.stdout)["demo"]
    assert refreshed_state["verification"]["local_model"]["status"] == "stale"
    anomalies = run_cli(tmp_path, "anomalies", "demo", "--json")
    assert anomalies.returncode == 0, anomalies.stderr
    assert any(item["code"] == "stale_verification" for item in json.loads(anomalies.stdout))
    tested = run_cli(tmp_path, "run", "--project", "demo", "--type", "test", "--", sys.executable, "-c", "print('dirty tree tested')")
    assert tested.returncode == 0, tested.stderr
    refreshed_again = run_cli(tmp_path, "status", "demo", "--json")
    assert refreshed_again.returncode == 0, refreshed_again.stderr
    anomalies_again = json.loads(run_cli(tmp_path, "anomalies", "demo", "--json").stdout)
    assert not any(item["code"] == "unverified_code_change" for item in anomalies_again)
    hook_start = run_cli(tmp_path, "hook", "--kind", "start", input_text=json.dumps({
        "project_id": "demo", "goal": "hook handoff", "event_id": "hook-1"}))
    assert hook_start.returncode == 0, hook_start.stderr
    start_payload = json.loads(hook_start.stdout)
    hook_start_repeat = run_cli(tmp_path, "hook", "--kind", "start", input_text=json.dumps({
        "project_id": "demo", "goal": "hook handoff", "event_id": "hook-1"}))
    assert json.loads(hook_start_repeat.stdout)["event_id"] == start_payload["event_id"]
    active_sessions = json.loads(run_cli(tmp_path, "sessions", "--active", "--json").stdout)
    assert any(item["session_id"] == start_payload["session_id"] for item in active_sessions)
    hook_end = run_cli(tmp_path, "hook", "--kind", "end", input_text=json.dumps({
        "project_id": "demo", "session_id": start_payload["session_id"], "status": "completed",
        "summary": "完成剩余验证并结束 Session。", "results": ["最终验证完成"],
        "remaining": ["nothing"], "event_id": "hook-2"}))
    assert hook_end.returncode == 0, hook_end.stderr
    sessions = json.loads(run_cli(tmp_path, "sessions", "--json").stdout)
    assert any(item["session_id"] == start_payload["session_id"] and item["status"] == "completed" for item in sessions)
    missing = run_cli(tmp_path, "run", "--project", "demo", "--type", "test", "--", "definitely-not-a-command")
    assert missing.returncode == 127
    decision = run_cli(tmp_path, "decision", "--project", "demo", "--text", "adopt demo backend", "--status", "adopted", "--metric", "latency=47", "--parameter", "rec_min_score=0.9", "--key", "rec_min_score", "--confirm")
    assert decision.returncode == 0, decision.stderr
    experiment = run_cli(tmp_path, "experiment", "--action", "completed", "--project", "demo", "--name", "backend sweep", "--hypothesis", "native is faster", "--status", "passed")
    assert experiment.returncode == 0, experiment.stderr
    experiment_id = json.loads(experiment.stdout)["experiment_id"]
    capsule = run_cli(tmp_path, "capsule", experiment_id, "--project", "demo")
    assert capsule.returncode == 0 and (tmp_path / "experiments" / experiment_id / "manifest.json").exists()
    reproduce = run_cli(tmp_path, "reproduce", experiment_id)
    assert reproduce.returncode == 0
    why = json.loads(run_cli(tmp_path, "why", "backend", "--project", "demo").stdout)
    assert any(item["type"] == "decision_adopted" for item in why)
    lineage = json.loads(run_cli(tmp_path, "insights", "lineage", "demo").stdout)
    assert any(item["parameter"] == "rec_min_score" for item in lineage)
    graph = json.loads(run_cli(tmp_path, "insights", "graph", "demo").stdout)
    assert "nodes" in graph and "edges" in graph
    efficiency = json.loads(run_cli(tmp_path, "insights", "efficiency", "demo").stdout)
    assert efficiency["total"] >= 1
    coverage = json.loads(run_cli(tmp_path, "insights", "coverage", "demo").stdout)
    assert "coverage" in coverage
    context = json.loads(run_cli(tmp_path, "insights", "context", "demo").stdout)
    assert context["project"]["project_id"] == "demo"
    assert run_cli(tmp_path, "hypothesis", "--project", "demo", "--hypothesis-id", "H-1", "--statement", "backend is faster").returncode == 0
    assert run_cli(tmp_path, "baseline", "--project", "demo", "--record", "--metric", "latency_ms=47").returncode == 0
    assert json.loads(run_cli(tmp_path, "advanced", "health", "demo").stdout)["project_id"] == "demo"
    assert run_cli(tmp_path, "snapshot", "--project", "demo", "--reason", "test").returncode == 0
    weekly = run_cli(tmp_path, "weekly", "--date", "2030-01-01")
    assert weekly.returncode == 0, weekly.stderr
    assert (tmp_path / "reports" / "week" / "2030-W01.json").exists()
    next_actions = run_cli(tmp_path, "next", "demo")
    assert next_actions.returncode == 0, next_actions.stderr
    assert json.loads(next_actions.stdout)
    historical = run_cli(tmp_path, "state", "demo", "--at", "2030-01-01T00:00:00+00:00")
    assert historical.returncode == 0, historical.stderr
    assert json.loads(historical.stdout)["project_id"] == "demo"
    search = run_cli(tmp_path, "search", "backend", "--project", "demo")
    assert search.returncode == 0 and "decision_adopted" in search.stdout
    plan = run_cli(tmp_path, "plan", "--project", "demo", "--text", "demo acceptance", "--acceptance", "exit code 0")
    assert plan.returncode == 0, plan.stderr
    closed = run_cli(tmp_path, "plan", "--action", "close", "--project", "demo", "--text", "demo acceptance", "--status", "completed", "--confirm")
    assert closed.returncode == 0, closed.stderr
    report = run_cli(tmp_path, "daily", "--date", "2030-01-01")
    assert report.returncode == 0
    # The generated report is deterministic in shape even when the chosen date is empty.
    assert (tmp_path / "reports" / "2030-01-01.json").exists()
    report_payload = json.loads((tmp_path / "reports" / "2030-01-01.json").read_text(encoding="utf-8"))
    assert report_payload["semantic"]["generator"] == "deterministic"
    assert "next_actions" in report_payload["semantic"]
    imported = run_cli(tmp_path, "import-report", str(tmp_path / "reports" / "2030-01-01.json"), "--project", "demo")
    assert imported.returncode == 0, imported.stderr
    scan = run_cli(tmp_path, "scan", "demo")
    assert scan.returncode == 0 and "snapshots" in scan.stdout
    activity_path = tmp_path / "activity.json"
    activity_path.write_text(json.dumps({"intervals": [{"start": "2030-01-01T01:00:00+00:00",
                                                          "end": "2030-01-01T02:30:00+00:00",
                                                          "project_id": "demo", "source": "activitywatch"}]}), encoding="utf-8")
    activity = run_cli(tmp_path, "activity-import", str(activity_path))
    assert activity.returncode == 0, activity.stderr
    stats = json.loads(run_cli(tmp_path, "stats", "--period", "week", "--date", "2030-01-01").stdout)
    assert stats["time"]["human_active_hours"] == 1.5
    assert stats["trend"] and stats["trend"][0]["date"] == "2030-01-01"
