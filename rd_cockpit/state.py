from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import project_config
from .ledger import Ledger


@dataclass
class ProjectState:
    project_id: str
    name: str
    lifecycle_status: str
    goal: str | None
    repo_path: str
    branch: str | None
    head: str | None
    dirty: bool | None
    stages: dict[str, dict[str, Any]]
    blockers: list[str]
    remaining: list[str]
    recent_events: list[dict[str, Any]]


def _payload(row: Any) -> dict[str, Any]:
    try:
        return json.loads(row["payload_json"])
    except (TypeError, json.JSONDecodeError):
        return {}


def build_state(ledger: Ledger, home: Path, project_id: str, *, at: str | None = None) -> ProjectState:
    cfg = project_config(home, project_id)
    # A date-only upper bound means the end of that UTC calendar day.  This
    # keeps `rd state project --at 2026-08-02` useful while preserving exact
    # timestamp travel for callers that need it.
    upper_bound = f"{at}T23:59:59.999999+00:00" if at and len(at) == 10 else at
    events = ledger.events(project_id=project_id, until=upper_bound)
    snap = next((e for e in reversed(events) if e["event_type"] == "git_snapshot"), None)
    payload = _payload(snap) if snap else {}
    stages = {stage: {"status": "pending"} for stage in cfg.get("verification_stages", [])}
    if "implementation" in stages and snap:
        stages["implementation"] = {"status": "passed", "event_id": snap["event_id"], "commit": snap["commit_sha"]}
    for event in events:
        if event["event_type"] == "verification_stage_changed":
            p = _payload(event)
            stage = p.get("stage")
            if stage in stages:
                stages[stage] = {
                    "status": p.get("status", event["status"] or "unknown"),
                    "event_id": event["event_id"], "commit": event["commit_sha"],
                    "machine": event["machine"], "reason": p.get("reason"),
                    "tree_hash": p.get("tree_hash"), "verified_at": event["occurred_at"],
                }
        elif event["event_type"] == "test_completed" and event["status"] == "passed":
            for stage in ("unit_test", "local_eval", "module_test"):
                if stage in stages and stages[stage]["status"] == "pending":
                    stages[stage] = {"status": "passed", "event_id": event["event_id"], "commit": event["commit_sha"],
                                     "tree_hash": _payload(event).get("tree_hash"), "verified_at": event["occurred_at"]}
        elif event["event_type"] in {"benchmark_completed", "experiment_completed"} and event["status"] == "passed":
            for stage in ("local_model", "local_eval", "sample_resume"):
                if stage in stages and stages[stage]["status"] == "pending":
                    stages[stage] = {"status": "passed", "event_id": event["event_id"], "commit": event["commit_sha"],
                                     "tree_hash": _payload(event).get("tree_hash"), "verified_at": event["occurred_at"]}
    current_tree_hash = payload.get("tree_hash")
    current_head = snap["commit_sha"] if snap else None
    for stage, value in stages.items():
        if stage == "implementation" or value.get("status") != "passed":
            continue
        verified_tree = value.get("tree_hash")
        code_changed = (verified_tree and current_tree_hash and verified_tree != current_tree_hash)
        legacy_changed = (not verified_tree and (value.get("commit") != current_head or bool(snap and snap["dirty"])))
        if code_changed or legacy_changed:
            value["status"] = "stale"
            value["stale_reason"] = "working tree or commit changed after verification"
    sessions = [e for e in events if e["event_type"] in {"agent_session_started", "agent_session_completed"}]
    completed_sessions = {e["session_id"] for e in sessions if e["event_type"] == "agent_session_completed"}
    active_starts = [e for e in sessions if e["event_type"] == "agent_session_started" and e["session_id"] not in completed_sessions]
    active_goal = _payload(active_starts[-1]).get("goal") if active_starts else None
    handoff_events = [e for e in sessions if e["event_type"] == "agent_session_completed"]
    latest = _payload(handoff_events[-1]) if handoff_events else {}
    open_plans = [e for e in events if e["event_type"] == "plan_created"]
    closed_plan_texts = {_payload(e).get("text") for e in events if e["event_type"] == "plan_closed"}
    latest_open_plan = next((e for e in reversed(open_plans) if _payload(e).get("text") not in closed_plan_texts), None)
    if active_goal is None and latest_open_plan:
        active_goal = _payload(latest_open_plan).get("text")
    blockers: list[str] = []
    for event in events:
        if event["event_type"] == "blocker_created":
            blockers.append(_payload(event).get("text", "unspecified blocker"))
        elif event["event_type"] == "blocker_resolved":
            text = _payload(event).get("text")
            if text in blockers: blockers.remove(text)
    recent = [{"event_id": e["event_id"], "occurred_at": e["occurred_at"], "type": e["event_type"],
               "status": e["status"], "source": e["source"], "commit": e["commit_sha"]} for e in events[-12:]]
    return ProjectState(
        project_id=project_id, name=cfg.get("name", project_id),
        lifecycle_status=str(cfg.get("lifecycle_status") or "active"), goal=active_goal,
        repo_path=cfg["repo_path"], branch=payload.get("branch"), head=snap["commit_sha"] if snap else None,
        dirty=None if snap is None else bool(snap["dirty"]), stages=stages, blockers=blockers,
        remaining=list(latest.get("remaining", [])),
        recent_events=recent,
    )


def state_dict(state: ProjectState) -> dict[str, Any]:
    return {
        "project_id": state.project_id, "name": state.name,
        "lifecycle_status": state.lifecycle_status, "goal": state.goal,
        "repo_path": state.repo_path, "branch": state.branch, "head": state.head,
        "dirty": state.dirty, "verification": state.stages, "blockers": state.blockers,
        "remaining": state.remaining,
        "recent_events": state.recent_events,
    }
