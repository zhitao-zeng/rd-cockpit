from __future__ import annotations

import subprocess
from pathlib import Path

from rd_cockpit.agent_usage import _project_for_cwd, sync_usage
from rd_cockpit.ledger import Ledger


def _git_init(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)


def test_parent_repository_is_not_mistaken_for_tracked_child_project(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _git_init(workspace)
    dialect = workspace / "dialect-asr"
    dialect.mkdir()
    novel = workspace / "video-generator"
    _git_init(novel)

    home = workspace / "rd-cockpit"
    (home / "config").mkdir(parents=True)
    (home / "config" / "projects.yaml").write_text(
        "projects:\n"
        f"  asr_dialect:\n    repo_path: {dialect}\n"
        f"  avatar_video:\n    repo_path: {novel}\n",
        encoding="utf-8",
    )

    assert _project_for_cwd(home, str(workspace)) is None
    assert _project_for_cwd(home, str(dialect)) == "asr_dialect"
    assert _project_for_cwd(
        home, str(workspace), observed_paths=[str(novel / "src" / "render.py")],
    ) == "avatar_video"


def test_observed_path_inside_linked_worktree_maps_to_configured_project(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo = workspace / "main"
    _git_init(repo)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "tracked.py").write_text("print('ok')\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    worktree = workspace / "worktrees" / "feature"
    worktree.parent.mkdir()
    subprocess.run(["git", "worktree", "add", "-q", "-b", "feature", str(worktree)], cwd=repo, check=True)

    home = workspace / "cockpit"
    (home / "config").mkdir(parents=True)
    (home / "config" / "projects.yaml").write_text(
        f"projects:\n  demo:\n    repo_path: {repo}\n", encoding="utf-8",
    )
    assert _project_for_cwd(
        home, str(workspace), observed_paths=[str(worktree / "tracked.py")],
    ) == "demo"


def test_recent_paths_win_when_a_long_session_switches_projects(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    old_repo = workspace / "old-project"
    new_repo = workspace / "new-project"
    _git_init(old_repo)
    _git_init(new_repo)

    home = workspace / "cockpit"
    (home / "config").mkdir(parents=True)
    (home / "config" / "projects.yaml").write_text(
        "projects:\n"
        f"  old_project:\n    repo_path: {old_repo}\n"
        f"  new_project:\n    repo_path: {new_repo}\n",
        encoding="utf-8",
    )

    observed_paths = [str(old_repo / "legacy.py")] * 70
    observed_paths.extend([str(new_repo / "current.py")] * 30)
    assert _project_for_cwd(
        home, str(workspace), observed_paths=observed_paths,
    ) == "new_project"


def test_usage_sync_skips_unchanged_transcripts(tmp_path: Path, monkeypatch) -> None:
    user = tmp_path / "user"
    sessions = user / ".codex" / "sessions"
    sessions.mkdir(parents=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    home = tmp_path / "cockpit"
    (home / "config").mkdir(parents=True)
    (home / "config" / "projects.yaml").write_text(
        f"projects:\n  demo:\n    repo_path: {repo}\n", encoding="utf-8",
    )
    transcript = sessions / "session.jsonl"
    transcript.write_text(
        '{"type":"session_meta","payload":{"id":"session-1","cwd":"' + str(repo) + '"}}\n'
        '{"type":"event_msg","timestamp":"2026-08-14T01:00:00Z","payload":{"type":"token_count","info":{"total_token_usage":{"input_tokens":10,"output_tokens":2,"total_tokens":12}}}}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: user))
    ledger = Ledger(home / ".rd-cockpit" / "events.sqlite")
    first = sync_usage(ledger, home, days=30)
    second = sync_usage(ledger, home, days=30)
    assert first["parsed"] == 1
    assert first["inserted"] == 1
    assert second["parsed"] == 0
    assert second["unchanged"] == 1
    with transcript.open("a", encoding="utf-8") as handle:
        handle.write(
            '{"type":"event_msg","timestamp":"2026-08-14T01:05:00Z","payload":{"type":"token_count","info":{"total_token_usage":{"input_tokens":20,"output_tokens":4,"total_tokens":24}}}}\n'
        )
    third = sync_usage(ledger, home, days=30)
    assert third["parsed"] == 1
    latest = ledger.db.execute(
        "SELECT payload_json FROM current_session_usage WHERE agent='codex' AND session_id='session-1'"
    ).fetchone()
    assert latest is not None
    assert __import__("json").loads(latest["payload_json"])["total_tokens"] == 24
    assert ledger.events(event_types={"agent_usage_observed"}) == []
    assert ledger.events(event_types={"agent_usage_settled"})
    ledger.close()
