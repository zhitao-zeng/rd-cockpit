from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from .config import (
    add_project, config_path, ensure_local_project_config, load_config,
    machine_name, project_catalog, project_config,
)
from .git_collect import record_snapshot, snapshot
from .ledger import Ledger, sha256_file, utc_now
from .report import build_facts, write_report
from .period import build_period_facts, write_period_report
from .resources import sample
from .state import build_state, state_dict
from .anomalies import find_anomalies
from .security import redact_text, redact_value
from . import insights as insight_views
from . import advanced as advanced_views
from .agent_usage import sync_usage
from .sessions import session_views
from .agent_hooks import handle_agent_hook
from .hook_install import install_user_hooks
from .hook_queue import drain_hook_queue


def _home(value: str | None) -> Path:
    if value: return Path(value).expanduser().resolve()
    return Path(os.environ.get("RD_COCKPIT_HOME", Path(__file__).resolve().parents[1])).resolve()


def _ledger(args: argparse.Namespace) -> tuple[Path, Ledger]:
    home = _home(args.home)
    return home, Ledger(home / ".rd-cockpit" / "events.sqlite")


def _project(args: argparse.Namespace, home: Path) -> tuple[str, dict[str, Any], Path]:
    cfg = project_config(home, args.project)
    return args.project, cfg, Path(cfg["repo_path"])


def _json_payload(text: str | None) -> dict[str, Any]:
    if not text: return {}
    path = Path(text)
    if path.exists(): return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(text)


def cmd_init(args: argparse.Namespace) -> int:
    home = _home(args.home); (home / ".rd-cockpit").mkdir(parents=True, exist_ok=True)
    ledger = Ledger(home / ".rd-cockpit" / "events.sqlite"); ledger.close()
    project_file = ensure_local_project_config(home)
    print(json.dumps({"home": str(home), "database": str(home / ".rd-cockpit" / "events.sqlite"),
                      "config": str(project_file)}, ensure_ascii=False, indent=2))
    return 0


def cmd_project_add(args: argparse.Namespace) -> int:
    home = _home(args.home)
    try:
        result = add_project(
            home,
            project_id=args.project_id,
            name=args.name,
            repo_path=Path(args.repo),
            priority=args.priority,
            lifecycle_status=args.lifecycle,
            match_keywords=args.keyword,
            match_paths=args.path,
            verification_stages=args.stage,
            allow_missing=args.allow_missing,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    result["activation"] = {
        "frontend": "automatic",
        "daily_reports": "automatic for matching headings and configured paths",
        "agent_usage": "automatic when session paths match the configured repository",
        "algorithm_architecture": "automatic on the next cached nightly architecture refresh",
        "experiment_intelligence": "include with --project on the next cached experiment backfill",
        "ambiguous_history": "use a one-time Codex historical audit only when matching is insufficient",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_project_list(args: argparse.Namespace) -> int:
    home = _home(args.home)
    catalog = project_catalog(home)
    if args.json:
        print(json.dumps(catalog, ensure_ascii=False, indent=2))
    else:
        for project_id, name in catalog.items():
            print(f"{project_id}\t{name}")
    return 0


def cmd_project_discover(args: argparse.Namespace) -> int:
    from .project_discovery import refresh_discovery

    home = _home(args.home)
    result = refresh_discovery(
        home, days=max(1, min(args.days, 365)), force=args.force, model=args.model,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get("run", {}).get("error") else 0


def cmd_project_accept(args: argparse.Namespace) -> int:
    from .project_discovery import accept_candidate

    try:
        result = accept_candidate(
            _home(args.home), args.candidate_id, project_id=args.project_id,
            name=args.name, priority=args.priority, lifecycle_status=args.lifecycle,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_project_ignore(args: argparse.Namespace) -> int:
    from .project_discovery import ignore_candidate

    try:
        result = ignore_candidate(_home(args.home), args.candidate_id)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_intelligence_backfill(args: argparse.Namespace) -> int:
    from .intelligence_backfill import backfill

    result = backfill(
        directory=Path(args.directory).expanduser().resolve() if args.directory else None,
        days=args.days,
        batch_days=args.batch_days,
        model=args.model,
        fallback_model=args.fallback_model,
        force=args.force,
        target=date.fromisoformat(args.target) if args.target else None,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["failed"] else 0


def cmd_experiment_backfill(args: argparse.Namespace) -> int:
    """Build cached readable experiment records from Daily Reports."""
    from .experiment_intelligence import backfill

    result = backfill(
        directory=Path(args.directory).expanduser().resolve() if args.directory else None,
        days=args.days,
        batch_days=args.batch_days,
        projects=args.project,
        model=args.model,
        fallback_model=args.fallback_model,
        force=args.force,
        target=date.fromisoformat(args.target) if args.target else None,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["failed"] else 0


def cmd_algorithm_analyze(args: argparse.Namespace) -> int:
    """Refresh evidence-grounded algorithm architecture snapshots.

    Model calls live exclusively behind this explicit/background command.  The
    API and browser only read the versioned snapshot, so browsing never spends
    tokens and can never mutate a project repository.
    """
    from .algorithm_architecture import analyze_all

    home = _home(args.home)
    projects = None if args.all else ([args.project] if args.project else None)
    result = analyze_all(
        home,
        project_ids=projects,
        model=args.model,
        fallback_model=args.fallback_model,
        force=args.force,
        progress=lambda message: print(message, file=sys.stderr, flush=True),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["failures"] or result["counts"]["analysis_failed"] else 0


def cmd_status(args: argparse.Namespace) -> int:
    home, ledger = _ledger(args)
    config = load_config(home / "config" / "projects.yaml")
    ids = [args.project] if args.project else sorted(config.get("projects", {}))
    result = {}
    try:
        for project_id in ids:
            cfg = project_config(home, project_id)
            repo = Path(cfg["repo_path"])
            try: record_snapshot(ledger, project_id=project_id, repo=repo, machine=machine_name(home))
            except (subprocess.CalledProcessError, FileNotFoundError) as exc:
                print(f"warning: cannot snapshot {project_id}: {exc}", file=sys.stderr)
            result[project_id] = state_dict(build_state(ledger, home, project_id))
        if args.json: print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            for project_id, state in result.items():
                stage = next((f"{k}={v['status']}" for k, v in state["verification"].items() if v["status"] in {"pending", "partial", "stale"}), "complete")
                print(f"{project_id}: {stage}; HEAD={state['head'] or 'unknown'}; dirty={state['dirty']}")
        return 0
    finally:
        ledger.close()


def cmd_scan(args: argparse.Namespace) -> int:
    """Record Git snapshots for configured repositories without touching them."""
    home, ledger = _ledger(args)
    config = load_config(home / "config" / "projects.yaml")
    ids = [args.project] if args.project else sorted(config.get("projects", {}))
    try:
        count = 0
        while True:
            accepted = []
            for project_id in ids:
                cfg = project_config(home, project_id)
                try:
                    eid = record_snapshot(ledger, project_id=project_id, repo=Path(cfg["repo_path"]),
                                          machine=machine_name(home))
                    accepted.append({"project": project_id, "event_id": eid})
                except (subprocess.CalledProcessError, FileNotFoundError) as exc:
                    print(f"warning: cannot snapshot {project_id}: {exc}", file=sys.stderr)
            print(json.dumps({"scanned_at": utc_now(), "snapshots": accepted}, ensure_ascii=False), flush=True)
            count += 1
            if not args.watch or (args.count and count >= args.count):
                return 0
            time.sleep(max(1, args.interval))
    except KeyboardInterrupt:
        return 0
    finally:
        ledger.close()


def cmd_resume(args: argparse.Namespace) -> int:
    home, ledger = _ledger(args)
    try:
        result = state_dict(build_state(ledger, home, args.project))
        if args.json: print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
        print(f"Project: {result['name']} ({result['project_id']})")
        print(f"Goal: {result['goal'] or 'not recorded'}")
        print(f"Repository: {result['repo_path']}")
        print(f"Branch: {result['branch'] or 'unknown'}")
        print(f"HEAD: {result['head'] or 'unknown'}")
        print(f"Dirty: {result['dirty']}")
        print("Verification:")
        for stage, value in result["verification"].items(): print(f"  {stage:<22} {value['status']}")
        for label in ("blockers", "remaining"):
            print(f"{label}:")
            for item in result[label]: print(f"  - {item}")
        return 0
    finally: ledger.close()


def cmd_run(args: argparse.Namespace) -> int:
    home, ledger = _ledger(args)
    if args.command and args.command[0] == "--": args.command = args.command[1:]
    if not args.command: raise SystemExit("rd run requires a command after --")
    project_id, cfg, repo = _project(args, home)
    before = snapshot(repo)
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    run_dir = home / ".rd-cockpit" / "runs" / run_id; run_dir.mkdir(parents=True, exist_ok=True)
    started = ledger.append(event_type="command_started", source="rd_run", project_id=project_id,
                            task_id=args.task, session_id=args.session, machine=machine_name(home),
                            repo_path=str(repo), commit_sha=before["commit_sha"], dirty=before["dirty"],
                            payload={"run_id": run_id, "command": redact_value(args.command), "type": args.type, "dataset": redact_text(args.dataset) if args.dataset else None, "model": redact_text(args.model) if args.model else None},
                            dedup_key=f"run_started:{run_id}")
    # The command receives the user's normal environment so existing training and
    # deployment commands keep working. Environment values are deliberately not
    # persisted in the ledger; a later redaction layer can record an allow-list.
    command_error = None
    try:
        proc = subprocess.run(args.command, cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              env=dict(os.environ))
        returncode, output = proc.returncode, proc.stdout
    except OSError as exc:
        returncode, output, command_error = 127, f"command failed to start: {exc}\n", str(exc)
    log_path = run_dir / "stdout.log"; log_path.write_text(redact_text(output), encoding="utf-8", errors="replace")
    status = "passed" if returncode == 0 else "failed"
    event_type = "command_failed" if command_error else {"test": "test_completed", "benchmark": "benchmark_completed", "experiment": "experiment_completed"}.get(args.type, "command_completed")
    payload: dict[str, Any] = {"run_id": run_id, "command": redact_value(args.command), "type": args.type, "exit_code": returncode,
                               "dataset": args.dataset, "model": args.model, "log_path": str(log_path),
                               "tree_hash": before["tree_hash"]}
    if command_error: payload["error"] = command_error
    if args.metrics:
        payload["metrics"] = _json_payload(args.metrics)
    completed = ledger.append(event_type=event_type, source="rd_run", project_id=project_id, task_id=args.task,
                              session_id=args.session, machine=machine_name(home), repo_path=str(repo),
                              commit_sha=before["commit_sha"], dirty=before["dirty"], status=status, payload=payload,
                              evidence=[{"type": "stdout", "path": str(log_path), "sha256": sha256_file(log_path)}],
                              dedup_key=f"run_completed:{run_id}")
    after = snapshot(repo)
    record_snapshot(ledger, project_id=project_id, repo=repo, machine=machine_name(home))
    result = {"event_id": completed, "run_id": run_id, "exit_code": returncode, "log": str(log_path)}
    if args.type in {"experiment", "benchmark"}:
        try: result["capsule"] = advanced_views.experiment_capsule(ledger, home, run_id, project_id)
        except Exception as exc: result["capsule_error"] = str(exc)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    ledger.close()
    return returncode


def cmd_verify(args: argparse.Namespace) -> int:
    home, ledger = _ledger(args); project_id, cfg, repo = _project(args, home)
    snap = snapshot(repo)
    eid = ledger.append(event_type="verification_stage_changed", source="rd_cli", project_id=project_id,
                        task_id=args.task, machine=args.machine or machine_name(home), repo_path=str(repo),
                        commit_sha=snap["commit_sha"], dirty=snap["dirty"], status=args.status,
                        verification="user_confirmed" if args.confirm else "unverified",
                        payload={"stage": args.stage, "status": args.status, "reason": args.reason,
                                 "tree_hash": snap["tree_hash"]},
                        evidence=[{"type": "path", "path": args.evidence}] if args.evidence else (),
                        dedup_key=f"verify:{project_id}:{args.stage}:{snap['commit_sha']}:{args.status}:{args.machine or machine_name(home)}")
    print(eid); ledger.close(); return 0


def cmd_start(args: argparse.Namespace) -> int:
    home, ledger = _ledger(args); project_id, cfg, repo = _project(args, home)
    session = args.session or f"session_{uuid.uuid4().hex[:12]}"
    ledger.append(event_type="agent_session_started", source=args.agent, project_id=project_id, task_id=args.task,
                  session_id=session, machine=machine_name(home), repo_path=str(repo), payload={"goal": args.goal},
                  provenance="reported", dedup_key=f"session_started:{session}")
    (home / ".rd-cockpit" / "current_session").write_text(session, encoding="utf-8")
    print(session); ledger.close(); return 0


def _session_id(home: Path, explicit: str | None) -> str:
    if explicit:
        return explicit
    current = home / ".rd-cockpit" / "current_session"
    if not current.exists() or not current.read_text(encoding="utf-8").strip():
        raise SystemExit("session is required; use --session or run rd start first")
    return current.read_text(encoding="utf-8").strip()


def cmd_plan(args: argparse.Namespace) -> int:
    home, ledger = _ledger(args)
    project_id, cfg, repo = _project(args, home)
    snap = snapshot(repo)
    action = args.action
    payload = {"text": args.text, "acceptance": args.acceptance or [], "status": args.status,
               "reason": args.reason}
    event_type = "plan_created" if action == "open" else "plan_closed"
    status = "open" if action == "open" else args.status
    eid = ledger.append(event_type=event_type, source="rd_cli", project_id=project_id, task_id=args.task,
                        machine=machine_name(home), repo_path=str(repo), commit_sha=snap["commit_sha"],
                        dirty=snap["dirty"], status=status, provenance="reported", payload=payload,
                        verification="user_confirmed" if args.confirm else "unverified",
                        dedup_key=f"plan:{action}:{project_id}:{args.text}:{args.status or 'open'}")
    scene = advanced_views.workspace_snapshot(ledger, home, project_id, reason="session_end")
    print(json.dumps({"event_id": eid, "workspace_snapshot": scene["event_id"]}, ensure_ascii=False)); ledger.close(); return 0


def cmd_decision(args: argparse.Namespace) -> int:
    home, ledger = _ledger(args)
    project_id, _, repo = _project(args, home)
    snap = snapshot(repo)
    decision_id = args.decision_id or f"decision_{uuid.uuid4().hex[:12]}"
    metrics = {}
    for item in args.metric or []:
        if "=" not in item:
            raise SystemExit("--metric must use name=value")
        key, value = item.split("=", 1); metrics[key] = value
    parameters = {}
    for item in args.parameter or []:
        if "=" not in item: raise SystemExit("--parameter must use name=value")
        key, value = item.split("=", 1); parameters[key] = value
    payload = {"decision_id": decision_id, "text": args.text, "status": args.status,
               "reason": args.reason, "metrics": metrics, "parameters": parameters, "scope": args.scope,
               "decision_key": args.key, "model": args.model, "dataset": args.dataset,
               "supports": args.supports or [],
               "tree_hash": snap["tree_hash"]}
    eid = ledger.append(event_type=f"decision_{args.status}", source="rd_cli", project_id=project_id,
                        task_id=args.task, session_id=args.session, machine=machine_name(home),
                        repo_path=str(repo), commit_sha=snap["commit_sha"], dirty=snap["dirty"],
                        status=args.status, verification="user_confirmed" if args.confirm else "unverified",
                        payload=payload, evidence=[{"type": "path", "path": path} for path in args.evidence or []],
                        dedup_key=f"decision:{decision_id}:{args.status}")
    print(json.dumps({"event_id": eid, "decision_id": decision_id}, ensure_ascii=False, indent=2))
    ledger.close(); return 0


def cmd_experiment(args: argparse.Namespace) -> int:
    home, ledger = _ledger(args)
    project_id, _, repo = _project(args, home)
    snap = snapshot(repo)
    experiment_id = args.experiment_id or f"experiment_{uuid.uuid4().hex[:12]}"
    parameters = {}
    for item in args.parameter or []:
        if "=" not in item: raise SystemExit("--parameter must use name=value")
        key, value = item.split("=", 1); parameters[key] = value
    payload = {"experiment_id": experiment_id, "name": args.name, "hypothesis": args.hypothesis,
               "dataset": args.dataset, "model": args.model, "result": args.result,
               "classification": args.classification, "parameters": parameters,
               "supports": args.supports or [], "tree_hash": snap["tree_hash"]}
    eid = ledger.append(event_type=f"experiment_{args.action}", source="rd_cli", project_id=project_id,
                        task_id=args.task, session_id=args.session, machine=machine_name(home),
                        repo_path=str(repo), commit_sha=snap["commit_sha"], dirty=snap["dirty"],
                        status=args.status or ("running" if args.action == "started" else args.action),
                        payload=payload, evidence=[{"type": "path", "path": path} for path in args.evidence or []],
                        dedup_key=f"experiment:{experiment_id}:{args.action}")
    print(json.dumps({"event_id": eid, "experiment_id": experiment_id}, ensure_ascii=False, indent=2))
    ledger.close(); return 0


def cmd_close(args: argparse.Namespace) -> int:
    home, ledger = _ledger(args); session = _session_id(home, args.session)
    project_id = args.project
    repo = Path(project_config(home, project_id)["repo_path"])
    snap = snapshot(repo)
    payload = {"goal": args.goal, "summary": args.summary, "results": args.result or [],
               "remaining": args.remaining or [], "blockers": args.blocker or [],
               "files": args.file or [], "decisions": args.decision or []}
    eid = ledger.append(event_type="agent_session_completed", source=args.agent, project_id=project_id, task_id=args.task,
                        session_id=session, machine=machine_name(home), repo_path=str(repo), commit_sha=snap["commit_sha"],
                        dirty=snap["dirty"], status=args.status, provenance="reported", payload=payload,
                        dedup_key=f"session_completed:{session}:{snap['commit_sha']}:{args.status}")
    scene = advanced_views.workspace_snapshot(ledger, home, project_id, reason="session_end")
    print(json.dumps({"event_id": eid, "workspace_snapshot": scene["event_id"]}, ensure_ascii=False)); ledger.close(); return 0


def _hook_input() -> dict[str, Any]:
    if sys.stdin.isatty():
        return {}
    text = sys.stdin.read().strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"hook stdin must be JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit("hook stdin must be a JSON object")
    return value


def cmd_hook(args: argparse.Namespace) -> int:
    """Ingest a source-neutral JSON envelope from an agent hook."""
    home, ledger = _ledger(args)
    incoming = _hook_input()
    project_id = args.project or incoming.get("project_id") or incoming.get("project")
    session_id = args.session or incoming.get("session_id") or incoming.get("session")
    if args.kind == "start" and not session_id:
        session_id = f"session_{uuid.uuid4().hex[:12]}"
    if args.kind != "start" and not session_id:
        current = home / ".rd-cockpit" / "current_session"
        session_id = current.read_text(encoding="utf-8").strip() if current.exists() else None
    if not session_id:
        raise SystemExit("hook requires session/session_id")
    if not project_id and session_id:
        row = ledger.db.execute(
            "SELECT project_id FROM events WHERE session_id=? AND project_id IS NOT NULL ORDER BY occurred_at LIMIT 1",
            (session_id,),
        ).fetchone()
        project_id = row[0] if row else None
    if not project_id:
        raise SystemExit("hook requires project/project_id")
    cfg = project_config(home, project_id)
    repo = Path(incoming.get("repo_path") or cfg["repo_path"])
    def field(name: str, arg_value: Any = None, *aliases: str) -> Any:
        if arg_value is not None:
            return arg_value
        for key in (name, *aliases):
            if key in incoming:
                return incoming[key]
        return None

    def values(name: str, arg_value: Any = None, *aliases: str) -> list[str]:
        value = field(name, arg_value, *aliases)
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item) for item in value if str(item).strip()]
        return [str(value)] if str(value).strip() else []

    payload = {
        "goal": field("goal", args.goal),
        "summary": field("summary", args.summary),
        "status": field("status", args.status),
        "results": values("results", args.result, "result"),
        "remaining": values("remaining", args.remaining),
        "blockers": values("blockers", args.blocker, "blocker"),
        "decisions": values("decisions", args.decision, "decision"),
        "files": values("files", args.file, "file"),
        "evidence_refs": values("evidence_refs", args.evidence, "evidence"),
    }
    if args.kind == "start" and not payload.get("goal"):
        raise SystemExit("hook start requires goal")
    hook_event_id = incoming.get("event_id") or incoming.get("hook_event_id")
    event_type = {"start": "agent_session_started", "end": "agent_session_completed"}[args.kind]
    eid = ledger.append(
        event_type=event_type, source=args.agent, project_id=project_id, task_id=args.task,
        session_id=session_id, machine=machine_name(home), repo_path=str(repo),
        commit_sha=incoming.get("commit_sha"), dirty=incoming.get("dirty"),
        status=args.status or incoming.get("status") or ("active" if args.kind == "start" else None),
        provenance="reported", payload=payload,
        dedup_key=f"hook:{hook_event_id}" if hook_event_id else None,
        occurred_at=incoming.get("occurred_at"),
    )
    if args.kind == "start":
        (home / ".rd-cockpit" / "current_session").write_text(session_id or "", encoding="utf-8")
    result = {"accepted": True, "event_id": eid, "session_id": session_id, "project_id": project_id}
    if args.kind == "end":
        result["workspace_snapshot"] = advanced_views.workspace_snapshot(ledger, home, project_id, reason="hook_end")["event_id"]
    print(json.dumps(result,
                     ensure_ascii=False, indent=2))
    ledger.close()
    return 0


def cmd_agent_hook(args: argparse.Namespace) -> int:
    """Ingest one official Codex/Claude Code lifecycle payload."""
    home, ledger = _ledger(args)
    try:
        result = handle_agent_hook(home, ledger, args.source, _hook_input())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        ledger.close()


def cmd_install_hooks(args: argparse.Namespace) -> int:
    home = _home(args.home)
    result = install_user_hooks(home, Path(args.user_home).expanduser().resolve() if args.user_home else Path.home())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_resources(args: argparse.Namespace) -> int:
    home, ledger = _ledger(args)
    try:
        count = 0
        while True:
            print(json.dumps(sample(ledger, machine=machine_name(home)), ensure_ascii=False,
                             indent=None if args.watch else 2), flush=True)
            count += 1
            if not args.watch or (args.count and count >= args.count): return 0
            time.sleep(max(1, args.interval))
    except KeyboardInterrupt:
        return 0
    finally: ledger.close()


def cmd_daily(args: argparse.Namespace) -> int:
    home, ledger = _ledger(args)
    try:
        target = date.fromisoformat(args.date) if args.date else date.today()
        print(json.dumps(write_report(ledger, home, target, use_llm=args.llm), ensure_ascii=False, indent=2)); return 0
    finally: ledger.close()


def cmd_period_report(args: argparse.Namespace) -> int:
    home, ledger = _ledger(args)
    try:
        target = date.fromisoformat(args.date) if args.date else date.today()
        print(json.dumps(write_period_report(ledger, home, args.period, target), ensure_ascii=False, indent=2)); return 0
    finally: ledger.close()


def cmd_stats(args: argparse.Namespace) -> int:
    home, ledger = _ledger(args)
    try:
        target = date.fromisoformat(args.date) if args.date else date.today()
        print(json.dumps(build_period_facts(ledger, args.period, target), ensure_ascii=False, indent=2)); return 0
    finally: ledger.close()


def cmd_retract(args: argparse.Namespace) -> int:
    _, ledger = _ledger(args)
    try: print(ledger.retract(args.event, args.reason)); return 0
    finally: ledger.close()


def cmd_timeline(args: argparse.Namespace) -> int:
    home, ledger = _ledger(args)
    try:
        rows = ledger.events(project_id=args.project)
        if args.json:
            output = []
            for row in rows:
                item = {"event_id": row["event_id"], "occurred_at": row["occurred_at"],
                        "type": row["event_type"], "status": row["status"], "source": row["source"],
                        "commit": row["commit_sha"], "provenance": row["provenance"],
                        "payload": json.loads(row["payload_json"])}
                item["evidence"] = [dict(e) for e in ledger.event_evidence(row["event_id"])]
                output.append(item)
            print(json.dumps(output, ensure_ascii=False, indent=2)); return 0
        for row in rows:
            payload = json.loads(row["payload_json"])
            detail = payload.get("goal") or payload.get("command") or payload.get("stage") or payload.get("text") or ""
            if isinstance(detail, list): detail = " ".join(detail)
            print(f"{row['occurred_at']}  {row['event_type']:<28} {row['status'] or '-':<10} {detail}")
        return 0
    finally: ledger.close()


def cmd_why(args: argparse.Namespace) -> int:
    _, ledger = _ledger(args)
    try:
        query = args.query.lower()
        rows = ledger.events(project_id=args.project,
                             event_types={"decision_proposed", "decision_supported", "decision_confirmed",
                                          "decision_rejected", "decision_superseded", "decision_locally_verified",
                                          "decision_remotely_verified", "decision_conditionally_adopted",
                                          "decision_adopted", "experiment_completed", "experiment_failed"})
        matches = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            haystack = json.dumps(payload, ensure_ascii=False).lower()
            if query in haystack:
                matches.append({"event_id": row["event_id"], "occurred_at": row["occurred_at"],
                                "type": row["event_type"], "project_id": row["project_id"],
                                "status": row["status"], "payload": payload,
                                "evidence": [dict(e) for e in ledger.event_evidence(row["event_id"])]})
        print(json.dumps(matches, ensure_ascii=False, indent=2)); return 0
    finally: ledger.close()


def cmd_anomalies(args: argparse.Namespace) -> int:
    home, ledger = _ledger(args)
    try:
        items = find_anomalies(ledger, home, project_id=args.project, stale_days=args.stale_days)
        if args.json: print(json.dumps(items, ensure_ascii=False, indent=2))
        else:
            for item in items:
                print(f"[{item['level']}] {item['code']}: {item['message']}")
                for evidence in item.get("evidence", []): print(f"  evidence: {evidence}")
        return 1 if any(item["level"] == "critical" for item in items) else 0
    finally: ledger.close()


def cmd_since(args: argparse.Namespace) -> int:
    home, ledger = _ledger(args)
    try:
        query = args.query.strip().lower()
        rows = ledger.events(project_id=args.project)
        if query.startswith("commit:"):
            sha = query.split(":", 1)[1]
            matches = [row for row in rows if row["commit_sha"] and row["commit_sha"].startswith(sha)]
            since_at = matches[0]["occurred_at"] if matches else None
            rows = [row for row in rows if since_at and row["occurred_at"] >= since_at]
        else:
            from datetime import datetime, timedelta, timezone
            if query in {"today", "今天"}: start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            elif query in {"yesterday", "昨天"}: start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
            else:
                try: start = datetime.fromisoformat(query).replace(tzinfo=timezone.utc)
                except ValueError: raise SystemExit("rd since supports ISO dates, today/昨天, or commit:<sha>")
            rows = [row for row in rows if row["occurred_at"] >= start.isoformat()]
        print(json.dumps([{"event_id": row["event_id"], "occurred_at": row["occurred_at"], "type": row["event_type"],
                           "project_id": row["project_id"], "status": row["status"], "commit": row["commit_sha"]} for row in rows],
                         ensure_ascii=False, indent=2))
        return 0
    finally: ledger.close()


def cmd_sessions(args: argparse.Namespace) -> int:
    home, ledger = _ledger(args)
    try:
        output = session_views(ledger, args.project, active=args.active)
        if args.json: print(json.dumps(output, ensure_ascii=False, indent=2))
        else:
            for item in output:
                print(f"{item['session_id']}  {item['status']:<12} {item['project_id'] or '-'}  {item['goal'] or ''}")
        return 0
    finally: ledger.close()


def cmd_state_at(args: argparse.Namespace) -> int:
    home, ledger = _ledger(args)
    try:
        value = state_dict(build_state(ledger, home, args.project, at=args.at))
        print(json.dumps(value, ensure_ascii=False, indent=2)); return 0
    finally: ledger.close()


def cmd_next(args: argparse.Namespace) -> int:
    home, ledger = _ledger(args)
    try:
        config = load_config(home / "config" / "projects.yaml")
        projects = [args.project] if args.project else sorted(config.get("projects", {}))
        suggestions = []
        for pid in projects:
            state = state_dict(build_state(ledger, home, pid))
            priority = str(config.get("projects", {}).get(pid, {}).get("priority", "P3"))
            rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}.get(priority, 4)
            for stage, value in state["verification"].items():
                if value["status"] == "stale":
                    suggestions.append({"rank": rank, "project": pid, "action": f"重新验证 {stage}",
                                        "reason": value.get("stale_reason", "验证依赖发生变化"), "basis": [value.get("event_id")]})
                    break
            else:
                pending = [stage for stage, value in state["verification"].items() if value["status"] == "pending"]
                if pending:
                    stage = pending[0]
                    suggestions.append({"rank": rank, "project": pid, "action": f"推进验证阶段 {stage}",
                                        "reason": "当前漏斗中最早的未完成阶段", "basis": [state.get("head")]})
                elif state["blockers"]:
                    suggestions.append({"rank": rank, "project": pid, "action": f"处理阻塞：{state['blockers'][0]}",
                                        "reason": "阻塞项优先于新增实验", "basis": []})
        suggestions.sort(key=lambda item: (item["rank"], item["project"]))
        print(json.dumps(suggestions[:args.limit], ensure_ascii=False, indent=2)); return 0
    finally: ledger.close()


def cmd_stuck(args: argparse.Namespace) -> int:
    _, ledger = _ledger(args)
    try:
        rows = ledger.events(project_id=args.project)
        findings = []
        failures: dict[str, list[str]] = {}
        for row in rows:
            if row["status"] != "failed": continue
            payload = json.loads(row["payload_json"]); command = json.dumps(payload.get("command", payload.get("name", row["event_type"])), ensure_ascii=False)
            failures.setdefault(command, []).append(row["event_id"])
        for command, evidence in failures.items():
            if len(evidence) >= 3:
                findings.append({"code": "repeated_failure", "message": f"同一命令/实验失败 {len(evidence)} 次：{command}", "evidence": evidence[-5:]})
        deferred = [row for row in rows if row["event_type"] == "plan_closed" and json.loads(row["payload_json"]).get("status") in {"deferred", "blocked", "partially_completed"}]
        if len(deferred) >= 3:
            findings.append({"code": "repeated_defer", "message": f"计划连续 {len(deferred)} 次没有完成", "evidence": [row["event_id"] for row in deferred[-5:]]})
        print(json.dumps(findings, ensure_ascii=False, indent=2)); return 0
    finally: ledger.close()


def cmd_search(args: argparse.Namespace) -> int:
    _, ledger = _ledger(args)
    try:
        query = args.query.lower(); matches = []
        for row in ledger.events(project_id=args.project):
            payload = json.loads(row["payload_json"]); haystack = json.dumps(payload, ensure_ascii=False).lower()
            if query not in haystack and query not in (row["event_type"] or "").lower() and query not in (row["commit_sha"] or "").lower(): continue
            matches.append({"event_id": row["event_id"], "occurred_at": row["occurred_at"], "type": row["event_type"],
                            "project_id": row["project_id"], "status": row["status"], "commit": row["commit_sha"],
                            "payload": payload, "evidence": [dict(e) for e in ledger.event_evidence(row["event_id"])]})
        print(json.dumps(matches[-args.limit:], ensure_ascii=False, indent=2)); return 0
    finally: ledger.close()


def cmd_serve(args: argparse.Namespace) -> int:
    home = _home(args.home)
    try:
        import uvicorn
        from .api import create_app
    except ImportError as exc:
        raise SystemExit("read-only API requires uvicorn and fastapi; install with: pip install -e '.[server]'") from exc
    uvicorn.run(create_app(home), host=args.host, port=args.port, log_level=args.log_level)
    return 0


def cmd_mcp_stdio(args: argparse.Namespace) -> int:
    from .mcp_stdio import run_stdio
    return run_stdio(_home(args.home))


def cmd_dashboard(args: argparse.Namespace) -> int:
    from .dashboard import write_dashboard
    home, ledger = _ledger(args)
    try:
        print(write_dashboard(ledger, home)); return 0
    finally: ledger.close()


def cmd_import_report(args: argparse.Namespace) -> int:
    from .importer import import_report
    home, ledger = _ledger(args)
    try:
        eid = import_report(ledger, Path(args.path), project_id=args.project)
        print(json.dumps({"event_id": eid, "path": str(Path(args.path).expanduser().resolve())},
                         ensure_ascii=False, indent=2))
        return 0
    finally:
        ledger.close()


def cmd_insights(args: argparse.Namespace) -> int:
    home, ledger = _ledger(args)
    try:
        kind = args.kind
        if kind == "lineage": value = insight_views.parameter_lineage(ledger, args.project)
        elif kind == "graph": value = insight_views.decision_graph(ledger, args.project)
        elif kind == "conflicts": value = insight_views.decision_conflicts(ledger, args.project)
        elif kind == "freshness": value = insight_views.decision_freshness(ledger, args.project)
        elif kind == "efficiency": value = insight_views.experiment_efficiency(ledger, args.project)
        elif kind == "gpu": value = insight_views.gpu_report(ledger)
        elif kind == "coverage": value = insight_views.evidence_coverage(ledger, args.project)
        elif kind == "reproducibility": value = insight_views.reproducibility(ledger, args.project)
        elif kind == "impact":
            if not args.project: raise SystemExit("insights impact requires a project")
            value = insight_views.change_impact(ledger, home, args.project)
        elif kind == "context":
            if not args.project: raise SystemExit("insights context requires a project")
            value = insight_views.context_pack(ledger, home, args.project)
        elif kind == "suggest": value = insight_views.suggest_experiments(ledger, args.project)
        elif kind == "counterfactual":
            if not args.project or not args.query: raise SystemExit("counterfactual requires project and query")
            value = insight_views.counterfactual(ledger, args.project, args.query)
        elif kind == "twin": value = insight_views.digital_twin(ledger, home)
        elif kind == "switches": value = insight_views.context_switch_analysis(ledger)
        elif kind == "sessions": value = insight_views.session_efficiency(ledger, args.project)
        elif kind == "replay": value = insight_views.today_replay(ledger, home, date.fromisoformat(args.query) if args.query else date.today())
        elif kind == "wrapped": value = insight_views.research_wrapped(ledger, home, "month", date.fromisoformat(args.query) if args.query else date.today())
        elif kind == "resource-cost": value = insight_views.resource_cost(ledger, args.project)
        elif kind == "changed": value = insight_views.what_changed(ledger, args.query or "today", args.project)
        else: raise SystemExit(f"unknown insight kind: {kind}")
        print(json.dumps(value, ensure_ascii=False, indent=2)); return 0
    finally:
        ledger.close()


def cmd_advanced(args: argparse.Namespace) -> int:
    home, ledger = _ledger(args)
    try:
        kind, project, query = args.kind, args.project, args.query
        if kind == "debt": value = advanced_views.research_debt(ledger, home, project)
        elif kind == "confidence": value = advanced_views.claim_confidence(ledger, project)
        elif kind == "hypotheses": value = advanced_views.hypotheses(ledger, project)
        elif kind == "information-gain": value = advanced_views.information_gain(ledger, project)
        elif kind == "budget": value = advanced_views.budget_roi(ledger, project)
        elif kind == "metric-lineage": value = advanced_views.metric_lineage(ledger, project)
        elif kind == "fingerprints": value = advanced_views.fingerprints(ledger, project)
        elif kind == "health": value = advanced_views.health(ledger, home, project) if project else {"error": "project required"}
        elif kind == "risk": value = advanced_views.risk_radar(ledger, home, project) if project else {"error": "project required"}
        elif kind == "why-not-done": value = advanced_views.why_not_done(ledger, home, project) if project else {"error": "project required"}
        elif kind == "attention": value = advanced_views.attention_budget(ledger, project)
        elif kind == "rhythm": value = advanced_views.rhythm(ledger, project)
        elif kind == "handoff-quality": value = advanced_views.handoff_quality(ledger, project)
        elif kind == "agent-blindspots": value = advanced_views.agent_blindspots(ledger, project)
        elif kind == "memory": value = advanced_views.memory_freshness(ledger, home, project) if project else {"error": "project required"}
        elif kind == "refresh": value = advanced_views.refresh(ledger, home, project) if project else {"error": "project required"}
        elif kind == "knowledge": value = advanced_views.knowledge_cards(ledger, project)
        elif kind == "brief": value = advanced_views.project_brief(ledger, home, project) if project else {"error": "project required"}
        elif kind == "context-pack": value = insight_views.context_pack(ledger, home, project) if project else {"error": "project required"}
        elif kind == "achievements": value = advanced_views.achievements(ledger, home, project)
        elif kind == "card": value = advanced_views.daily_card(ledger, home, date.fromisoformat(query) if query else date.today())
        elif kind == "map": value = advanced_views.research_map(ledger, home)
        elif kind == "dont": value = advanced_views.dont(ledger, home, project)
        elif kind == "countdown": value = advanced_views.decision_countdown(ledger, project)
        else: raise SystemExit(f"unknown advanced kind: {kind}")
        print(json.dumps(value, ensure_ascii=False, indent=2)); return 0
    finally:
        ledger.close()


def cmd_snapshot(args: argparse.Namespace) -> int:
    home, ledger = _ledger(args)
    try:
        print(json.dumps(advanced_views.workspace_snapshot(ledger, home, args.project, args.reason), ensure_ascii=False, indent=2)); return 0
    finally: ledger.close()


def cmd_capsule(args: argparse.Namespace) -> int:
    home, ledger = _ledger(args)
    try:
        print(json.dumps(advanced_views.experiment_capsule(ledger, home, args.experiment, args.project), ensure_ascii=False, indent=2)); return 0
    finally: ledger.close()


def cmd_reproduce(args: argparse.Namespace) -> int:
    home = _home(args.home); print(json.dumps(advanced_views.reproduce_check(home, args.experiment), ensure_ascii=False, indent=2)); return 0


def cmd_hypothesis(args: argparse.Namespace) -> int:
    home, ledger = _ledger(args)
    try:
        payload = {"hypothesis_id": args.hypothesis_id, "statement": args.statement, "scope": args.scope, "classification": args.classification}
        eid = ledger.append(event_type="hypothesis_proposed" if args.action == "propose" else "hypothesis_updated", source="rd_cli", project_id=args.project, status=args.classification or "unresolved", provenance="reported", payload=payload, dedup_key=f"hypothesis:{args.hypothesis_id}:{args.action}:{args.classification or ''}")
        print(json.dumps({"event_id": eid, **payload}, ensure_ascii=False, indent=2)); return 0
    finally: ledger.close()


def cmd_baseline(args: argparse.Namespace) -> int:
    home, ledger = _ledger(args)
    try:
        metrics = {}
        for item in args.metric or []:
            if "=" not in item: raise SystemExit("--metric must use name=value")
            key, value = item.split("=", 1); metrics[key] = value
        print(json.dumps(advanced_views.baseline(ledger, args.project, record=args.record, metrics=metrics), ensure_ascii=False, indent=2)); return 0
    finally: ledger.close()


def cmd_usage_sync(args: argparse.Namespace) -> int:
    try:
        count = 0
        while True:
            # Do not keep a SQLite connection open during the watch sleep. An
            # older long-lived collector once retained the WAL write lock and
            # caused every interactive Agent hook to hit its five-second limit.
            home, ledger = _ledger(args)
            try:
                result = sync_usage(ledger, home, days=args.days)
                result["hook_queue"] = drain_hook_queue(home, ledger)
            finally:
                ledger.close()
            print(json.dumps(result, ensure_ascii=False,
                             indent=None if args.watch else 2), flush=True)
            count += 1
            if not args.watch or (args.count and count >= args.count): return 0
            time.sleep(max(30, args.interval))
    except KeyboardInterrupt:
        return 0


def cmd_activity_import(args: argparse.Namespace) -> int:
    """Import intervals from ActivityWatch or another local activity tracker."""
    home, ledger = _ledger(args)
    path = Path(args.path).expanduser().resolve()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        intervals = value.get("intervals", value) if isinstance(value, dict) else value
        if not isinstance(intervals, list):
            raise SystemExit("activity JSON must be a list or {\"intervals\": [...]}")
        accepted = []
        for index, item in enumerate(intervals):
            if not isinstance(item, dict) or not item.get("start") or not item.get("end"):
                raise SystemExit(f"activity interval {index} requires start and end")
            payload = dict(item)
            payload.setdefault("source_file", str(path))
            source = str(item.get("source", "activity_import"))
            provenance = "observed" if source.lower() in {"activitywatch", "activity_watch", "aw"} else "reported"
            dedup = f"human_activity:{path}:{index}:{item['start']}:{item['end']}"
            accepted.append(ledger.append(event_type="human_activity_interval", source=source,
                                          project_id=item.get("project_id") or args.project,
                                          machine=item.get("machine") or machine_name(home),
                                          status="observed", provenance=provenance, payload=payload,
                                          dedup_key=dedup, occurred_at=item["start"],
                                          evidence=[{"type": "activity_json", "path": str(path),
                                                     "sha256": sha256_file(path)}]))
        print(json.dumps({"accepted": len(accepted), "event_ids": accepted}, ensure_ascii=False, indent=2))
        return 0
    finally:
        ledger.close()


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rd", description="Evidence-first local R&D state ledger")
    p.add_argument("--home", help="R&D Cockpit home directory")
    sub = p.add_subparsers(dest="cmd", required=True)
    init = sub.add_parser("init"); init.set_defaults(func=cmd_init)
    project_cmd = sub.add_parser("project", help="manage the project registry")
    project_sub = project_cmd.add_subparsers(dest="project_action", required=True)
    project_add = project_sub.add_parser("add", help="register a repository as a project")
    project_add.add_argument("project_id", help="stable lowercase id, for example asr_translation")
    project_add.add_argument("--name", required=True, help="readable project name")
    project_add.add_argument("--repo", required=True, help="project repository or working directory")
    project_add.add_argument("--priority", choices=["P0", "P1", "P2", "P3"], default="P2")
    project_add.add_argument("--lifecycle", choices=["active", "dormant", "historical"], default="active")
    project_add.add_argument("--keyword", action="append", help="additional heading/content alias")
    project_add.add_argument("--path", action="append", help="additional concrete path owned by the project")
    project_add.add_argument("--stage", action="append", help="verification stage, repeat in order")
    project_add.add_argument("--allow-missing", action="store_true",
                             help="register the path before its directory exists")
    project_add.set_defaults(func=cmd_project_add)
    project_list = project_sub.add_parser("list", help="list registered projects")
    project_list.add_argument("--json", action="store_true")
    project_list.set_defaults(func=cmd_project_list)
    project_discover = project_sub.add_parser(
        "discover", help="scan recent Agent sessions and let Codex review unregistered repositories",
    )
    project_discover.add_argument("--days", type=int, default=30)
    project_discover.add_argument("--model", default="codex:gpt-5.6-sol@medium")
    project_discover.add_argument("--force", action="store_true", help="review unchanged evidence again")
    project_discover.set_defaults(func=cmd_project_discover)
    project_accept = project_sub.add_parser(
        "accept", help="explicitly register a Codex-approved project candidate",
    )
    project_accept.add_argument("candidate_id")
    project_accept.add_argument("--project-id", help="override the reviewed snake_case id")
    project_accept.add_argument("--name", help="override the reviewed readable name")
    project_accept.add_argument("--priority", choices=["P0", "P1", "P2", "P3"], default="P2")
    project_accept.add_argument("--lifecycle", choices=["active", "dormant", "historical"], default="active")
    project_accept.set_defaults(func=cmd_project_accept)
    project_ignore = project_sub.add_parser(
        "ignore", help="hide a repository from future project discovery",
    )
    project_ignore.add_argument("candidate_id")
    project_ignore.set_defaults(func=cmd_project_ignore)
    intelligence_backfill = sub.add_parser(
        "intelligence-backfill", help="backfill cached, evidence-bound intelligence from Daily Reports",
    )
    intelligence_backfill.add_argument("--directory")
    intelligence_backfill.add_argument("--days", type=int, default=90)
    intelligence_backfill.add_argument("--batch-days", type=int, default=7)
    intelligence_backfill.add_argument("--model", default="codex:gpt-5.6-sol@medium")
    intelligence_backfill.add_argument("--fallback-model", default="deepseek-local")
    intelligence_backfill.add_argument("--target")
    intelligence_backfill.add_argument("--force", action="store_true")
    intelligence_backfill.set_defaults(func=cmd_intelligence_backfill)
    experiment_backfill = sub.add_parser(
        "experiment-backfill", help="extract cached readable experiment records from Daily Reports",
    )
    experiment_backfill.add_argument("--directory")
    experiment_backfill.add_argument("--days", type=int, default=90)
    experiment_backfill.add_argument("--batch-days", type=int, default=7)
    experiment_backfill.add_argument("--project", action="append",
                                     help="project id; repeat to override the five default research projects")
    experiment_backfill.add_argument("--model", default="codex:gpt-5.6-sol@medium")
    experiment_backfill.add_argument("--fallback-model", default="deepseek-local")
    experiment_backfill.add_argument("--target")
    experiment_backfill.add_argument("--force", action="store_true")
    experiment_backfill.set_defaults(func=cmd_experiment_backfill)
    algorithm_analyze = sub.add_parser(
        "algorithm-analyze",
        help="build cached, evidence-grounded algorithm architecture snapshots",
    )
    algorithm_analyze.add_argument("project", nargs="?", help="one configured project id")
    algorithm_analyze.add_argument("--all", action="store_true", help="refresh every configured project")
    algorithm_analyze.add_argument("--model", default="codex:gpt-5.6-sol@medium")
    algorithm_analyze.add_argument("--fallback-model", default="deepseek-local")
    algorithm_analyze.add_argument("--force", action="store_true", help="ignore an unchanged-source cache hit")
    algorithm_analyze.set_defaults(func=cmd_algorithm_analyze)
    status = sub.add_parser("status"); status.add_argument("project", nargs="?"); status.add_argument("--json", action="store_true"); status.set_defaults(func=cmd_status)
    scan = sub.add_parser("scan", help="record read-only Git snapshots; optionally keep watching")
    scan.add_argument("project", nargs="?"); scan.add_argument("--watch", action="store_true")
    scan.add_argument("--interval", type=int, default=60); scan.add_argument("--count", type=int, default=0)
    scan.set_defaults(func=cmd_scan)
    resume = sub.add_parser("resume"); resume.add_argument("project"); resume.add_argument("--json", action="store_true"); resume.set_defaults(func=cmd_resume)
    run = sub.add_parser("run"); run.add_argument("--project", required=True); run.add_argument("--type", choices=["command", "test", "benchmark", "experiment"], default="command"); run.add_argument("--task"); run.add_argument("--session"); run.add_argument("--dataset"); run.add_argument("--model"); run.add_argument("--metrics"); run.add_argument("command", nargs=argparse.REMAINDER); run.set_defaults(func=cmd_run)
    verify = sub.add_parser("verify"); verify.add_argument("--project", required=True); verify.add_argument("--stage", required=True); verify.add_argument("--status", choices=["passed", "partial", "failed", "pending", "stale"], required=True); verify.add_argument("--reason"); verify.add_argument("--evidence"); verify.add_argument("--machine"); verify.add_argument("--task"); verify.add_argument("--confirm", action="store_true"); verify.set_defaults(func=cmd_verify)
    start = sub.add_parser("start"); start.add_argument("--project", required=True); start.add_argument("--goal", required=True); start.add_argument("--task"); start.add_argument("--session"); start.add_argument("--agent", default="agent"); start.set_defaults(func=cmd_start)
    plan = sub.add_parser("plan"); plan.add_argument("--action", choices=["open", "close"], default="open"); plan.add_argument("--project", required=True); plan.add_argument("--text", required=True); plan.add_argument("--acceptance", action="append"); plan.add_argument("--status", choices=["completed", "partially_completed", "blocked", "deferred", "no_evidence", "cancelled"]); plan.add_argument("--reason"); plan.add_argument("--task"); plan.add_argument("--confirm", action="store_true"); plan.set_defaults(func=cmd_plan)
    decision = sub.add_parser("decision"); decision.add_argument("--project", required=True); decision.add_argument("--text", required=True); decision.add_argument("--status", choices=["proposed", "supported", "confirmed", "rejected", "superseded", "locally_verified", "remotely_verified", "conditionally_adopted", "adopted"], required=True); decision.add_argument("--reason"); decision.add_argument("--scope"); decision.add_argument("--key"); decision.add_argument("--model"); decision.add_argument("--dataset"); decision.add_argument("--metric", action="append"); decision.add_argument("--parameter", action="append"); decision.add_argument("--supports", action="append"); decision.add_argument("--evidence", action="append"); decision.add_argument("--decision-id"); decision.add_argument("--task"); decision.add_argument("--session"); decision.add_argument("--confirm", action="store_true"); decision.set_defaults(func=cmd_decision)
    experiment = sub.add_parser("experiment"); experiment.add_argument("--action", choices=["started", "completed", "failed"], required=True); experiment.add_argument("--project", required=True); experiment.add_argument("--name", required=True); experiment.add_argument("--hypothesis"); experiment.add_argument("--dataset"); experiment.add_argument("--model"); experiment.add_argument("--result"); experiment.add_argument("--status"); experiment.add_argument("--classification", choices=["supports_hypothesis", "rejects_hypothesis", "decision_producing", "environment_failure", "configuration_error", "duplicate", "unexplained"]); experiment.add_argument("--parameter", action="append"); experiment.add_argument("--supports", action="append"); experiment.add_argument("--evidence", action="append"); experiment.add_argument("--experiment-id"); experiment.add_argument("--task"); experiment.add_argument("--session"); experiment.set_defaults(func=cmd_experiment)
    close = sub.add_parser("close"); close.add_argument("--project", required=True); close.add_argument("--goal"); close.add_argument("--summary"); close.add_argument("--status", choices=["completed", "partial", "blocked", "interrupted"], default="completed"); close.add_argument("--task"); close.add_argument("--session"); close.add_argument("--agent", default="agent"); close.add_argument("--result", action="append"); close.add_argument("--remaining", action="append"); close.add_argument("--blocker", action="append"); close.add_argument("--file", action="append"); close.add_argument("--evidence", action="append"); close.add_argument("--decision", action="append"); close.set_defaults(func=cmd_close)
    resources = sub.add_parser("resources"); resources.add_argument("--watch", action="store_true"); resources.add_argument("--interval", type=int, default=30); resources.add_argument("--count", type=int, default=0); resources.set_defaults(func=cmd_resources)
    daily = sub.add_parser("daily"); daily.add_argument("--date"); daily.add_argument("--llm", action="store_true",
        help="optionally enrich deterministic facts through RD_LLM_BASE_URL/RD_LLM_MODEL"); daily.set_defaults(func=cmd_daily)
    weekly = sub.add_parser("weekly"); weekly.add_argument("--date"); weekly.set_defaults(func=lambda args: cmd_period_report(argparse.Namespace(**vars(args), period="week")))
    monthly = sub.add_parser("monthly"); monthly.add_argument("--date"); monthly.set_defaults(func=lambda args: cmd_period_report(argparse.Namespace(**vars(args), period="month")))
    stats = sub.add_parser("stats"); stats.add_argument("--period", choices=["week", "month"], default="week"); stats.add_argument("--date"); stats.set_defaults(func=cmd_stats)
    retract = sub.add_parser("retract"); retract.add_argument("event"); retract.add_argument("--reason", required=True); retract.set_defaults(func=cmd_retract)
    timeline = sub.add_parser("timeline"); timeline.add_argument("project"); timeline.add_argument("--json", action="store_true"); timeline.set_defaults(func=cmd_timeline)
    why = sub.add_parser("why"); why.add_argument("query"); why.add_argument("--project"); why.set_defaults(func=cmd_why)
    anomalies = sub.add_parser("anomalies"); anomalies.add_argument("project", nargs="?"); anomalies.add_argument("--stale-days", type=int, default=2); anomalies.add_argument("--json", action="store_true"); anomalies.set_defaults(func=cmd_anomalies)
    since = sub.add_parser("since"); since.add_argument("query"); since.add_argument("--project"); since.set_defaults(func=cmd_since)
    sessions = sub.add_parser("sessions"); sessions.add_argument("project", nargs="?"); sessions.add_argument("--active", action="store_true"); sessions.add_argument("--json", action="store_true"); sessions.set_defaults(func=cmd_sessions)
    state_at = sub.add_parser("state"); state_at.add_argument("project"); state_at.add_argument("--at", required=True, help="ISO timestamp upper bound"); state_at.set_defaults(func=cmd_state_at)
    next_action = sub.add_parser("next"); next_action.add_argument("project", nargs="?"); next_action.add_argument("--limit", type=int, default=5); next_action.set_defaults(func=cmd_next)
    stuck = sub.add_parser("stuck"); stuck.add_argument("project", nargs="?"); stuck.set_defaults(func=cmd_stuck)
    search = sub.add_parser("search"); search.add_argument("query"); search.add_argument("--project"); search.add_argument("--limit", type=int, default=50); search.set_defaults(func=cmd_search)
    hook = sub.add_parser("hook", help="ingest a source-neutral Agent session envelope"); hook.add_argument("--kind", choices=["start", "end"], required=True); hook.add_argument("--project"); hook.add_argument("--session"); hook.add_argument("--goal"); hook.add_argument("--summary"); hook.add_argument("--status"); hook.add_argument("--task"); hook.add_argument("--agent", default="agent-hook"); hook.add_argument("--result", action="append"); hook.add_argument("--remaining", action="append"); hook.add_argument("--blocker", action="append"); hook.add_argument("--file", action="append"); hook.add_argument("--evidence", action="append"); hook.add_argument("--decision", action="append"); hook.set_defaults(func=cmd_hook)
    agent_hook = sub.add_parser("agent-hook", help="ingest an official Codex or Claude Code lifecycle payload")
    agent_hook.add_argument("--source", choices=["codex", "claude-code"], required=True)
    agent_hook.set_defaults(func=cmd_agent_hook)
    install_hooks = sub.add_parser("install-hooks", help="install user-level Codex and Claude Code lifecycle hooks")
    install_hooks.add_argument("--user-home", help=argparse.SUPPRESS)
    install_hooks.set_defaults(func=cmd_install_hooks)
    serve = sub.add_parser("serve", help="run the localhost-only read-only API"); serve.add_argument("--host", default="127.0.0.1"); serve.add_argument("--port", type=int, default=8787); serve.add_argument("--log-level", default="warning"); serve.set_defaults(func=cmd_serve)
    mcp = sub.add_parser("mcp-stdio", help="run read-only MCP JSON-RPC tools over stdin/stdout"); mcp.set_defaults(func=cmd_mcp_stdio)
    dashboard = sub.add_parser("dashboard", help="generate a local read-only HTML dashboard"); dashboard.set_defaults(func=cmd_dashboard)
    insights = sub.add_parser("insights", help="deterministic fancy projections over the evidence ledger")
    insights.add_argument("kind", choices=["lineage", "graph", "conflicts", "freshness", "efficiency", "gpu", "coverage", "reproducibility", "impact", "context", "suggest", "counterfactual", "twin", "switches", "sessions", "replay", "wrapped", "resource-cost", "changed"])
    insights.add_argument("project", nargs="?"); insights.add_argument("query", nargs="?"); insights.set_defaults(func=cmd_insights)
    advanced = sub.add_parser("advanced", help="advanced evidence projections")
    advanced.add_argument("kind", choices=["debt", "confidence", "hypotheses", "information-gain", "budget", "metric-lineage", "fingerprints", "health", "risk", "why-not-done", "attention", "rhythm", "handoff-quality", "agent-blindspots", "memory", "refresh", "knowledge", "brief", "context-pack", "achievements", "card", "map", "dont", "countdown"])
    advanced.add_argument("project", nargs="?"); advanced.add_argument("query", nargs="?"); advanced.set_defaults(func=cmd_advanced)
    snapshot_cmd = sub.add_parser("snapshot", help="save a read-only workspace scene")
    snapshot_cmd.add_argument("--project", required=True); snapshot_cmd.add_argument("--reason", default="manual"); snapshot_cmd.set_defaults(func=cmd_snapshot)
    capsule = sub.add_parser("capsule", help="create a reproducibility capsule for an experiment")
    capsule.add_argument("experiment"); capsule.add_argument("--project"); capsule.set_defaults(func=cmd_capsule)
    reproduce = sub.add_parser("reproduce", help="check a reproducibility capsule")
    reproduce.add_argument("experiment"); reproduce.set_defaults(func=cmd_reproduce)
    hypothesis = sub.add_parser("hypothesis", help="record a research hypothesis")
    hypothesis.add_argument("--action", choices=["propose", "update"], default="propose"); hypothesis.add_argument("--project", required=True); hypothesis.add_argument("--hypothesis-id", required=True); hypothesis.add_argument("--statement", required=True); hypothesis.add_argument("--scope"); hypothesis.add_argument("--classification", choices=["supports_hypothesis", "rejects_hypothesis", "partially_supports", "unresolved"]); hypothesis.set_defaults(func=cmd_hypothesis)
    baseline_cmd = sub.add_parser("baseline", help="record or inspect a project baseline")
    baseline_cmd.add_argument("--project", required=True); baseline_cmd.add_argument("--record", action="store_true"); baseline_cmd.add_argument("--metric", action="append"); baseline_cmd.set_defaults(func=cmd_baseline)
    usage_sync = sub.add_parser("usage-sync", help="import aggregate Codex/Claude Code token counters without message text")
    usage_sync.add_argument("--days", type=int, default=30)
    usage_sync.add_argument("--watch", action="store_true")
    usage_sync.add_argument("--interval", type=int, default=300)
    usage_sync.add_argument("--count", type=int, default=0)
    usage_sync.set_defaults(func=cmd_usage_sync)
    dont_cmd = sub.add_parser("dont", help="show evidence-based things not to repeat")
    dont_cmd.add_argument("project", nargs="?"); dont_cmd.set_defaults(func=lambda args: cmd_advanced(argparse.Namespace(**vars(args), kind="dont", query=None)))
    for alias_name, alias_kind, help_text in [("context-pack", "context-pack", "generate a project context pack"),
                                               ("health", "health", "show project health"),
                                               ("risk", "risk", "show project risk radar"),
                                               ("refresh", "refresh", "perform a read-only project refresh"),
                                               ("why-not-done", "why-not-done", "explain why a project is unfinished"),
                                               ("daily-card", "card", "generate a compact daily research card")]:
        alias = sub.add_parser(alias_name, help=help_text); alias.add_argument("project", nargs="?"); alias.add_argument("--date"); alias.set_defaults(func=lambda args, _kind=alias_kind: cmd_advanced(argparse.Namespace(**vars(args), kind=_kind, query=args.date if getattr(args, "date", None) else None)))
    imported = sub.add_parser("import-report", help="import an existing report as low-confidence historical context")
    imported.add_argument("path"); imported.add_argument("--project"); imported.set_defaults(func=cmd_import_report)
    activity = sub.add_parser("activity-import", help="import human activity intervals from a local tracker")
    activity.add_argument("path"); activity.add_argument("--project"); activity.set_defaults(func=cmd_activity_import)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
