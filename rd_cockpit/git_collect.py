from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any

from .ledger import Ledger


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], check=True, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.stdout.strip()


def snapshot(repo: Path) -> dict[str, Any]:
    root = Path(git(repo, "rev-parse", "--show-toplevel"))
    head = git(root, "rev-parse", "HEAD")
    branch = git(root, "branch", "--show-current") or "(detached)"
    status = git(root, "status", "--porcelain=v1")
    name_status = git(root, "diff", "--name-status")
    untracked = git(root, "ls-files", "--others", "--exclude-standard")
    diff = subprocess.run(["git", "-C", str(root), "diff", "--binary", "--no-ext-diff"],
                          check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout
    diff_hash = hashlib.sha256(diff).hexdigest()
    tree_hash = hashlib.sha256((status + "\n" + diff_hash + "\n" + untracked).encode()).hexdigest()
    return {
        "repo_path": str(root), "commit_sha": head, "branch": branch,
        "dirty": bool(status), "status": status, "name_status": name_status,
        "untracked": untracked, "diff_hash": diff_hash, "tree_hash": tree_hash,
    }


def record_snapshot(ledger: Ledger, *, project_id: str, repo: Path, machine: str) -> str:
    snap = snapshot(repo)
    dedup = f"git_snapshot:{project_id}:{snap['commit_sha']}:{snap['tree_hash']}:{snap['branch']}"
    return ledger.append(
        event_type="git_snapshot", source="git", project_id=project_id, machine=machine,
        repo_path=snap["repo_path"], commit_sha=snap["commit_sha"], dirty=snap["dirty"],
        status="dirty" if snap["dirty"] else "clean", payload={k: v for k, v in snap.items() if k != "repo_path"},
        dedup_key=dedup,
    )
