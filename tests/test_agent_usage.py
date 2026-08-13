from __future__ import annotations

import subprocess
from pathlib import Path

from rd_cockpit.agent_usage import _project_for_cwd


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
