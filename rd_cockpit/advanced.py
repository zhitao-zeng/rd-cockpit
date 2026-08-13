"""Advanced, evidence-linked projections for the R&D cockpit.

The functions here intentionally prefer a useful approximation with an
explicit confidence label over fabricated precision.  They are all derived
from append-only events and can be regenerated without a separate database.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config import load_config, project_config
from .git_collect import snapshot
from .insights import (context_switch_analysis, decision_conflicts, decision_freshness,
                       evidence_coverage, experiment_efficiency, parameter_lineage,
                       reproducibility, session_efficiency, suggest_experiments)
from .ledger import Ledger, sha256_file, utc_now
from .state import build_state, state_dict

LOCAL_TZ = ZoneInfo("Asia/Shanghai")


def payload(row: Any) -> dict[str, Any]:
    try:
        value = json.loads(row["payload_json"])
        return value if isinstance(value, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def rows(ledger: Ledger, project: str | None = None) -> list[Any]:
    return ledger.events(project_id=project)


def evidence(ledger: Ledger, event_id: str) -> list[dict[str, Any]]:
    return [dict(item) for item in ledger.event_evidence(event_id)]


def workspace_snapshot(ledger: Ledger, home: Path, project: str, reason: str = "manual") -> dict[str, Any]:
    cfg = project_config(home, project); repo = Path(cfg["repo_path"])
    snap = snapshot(repo)
    state = state_dict(build_state(ledger, home, project))
    recent = rows(ledger, project)[-20:]
    resource_rows = ledger.events(event_types={"resource_snapshot"})[-3:]
    open_files = [item for item in os.environ.get("RD_OPEN_FILES", "").split(os.pathsep) if item]
    terminals = [item for item in os.environ.get("RD_TERMINALS", "").split(os.pathsep) if item]
    try:
        tmux = subprocess.run(["tmux", "list-sessions", "-F", "#S"], text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, timeout=2)
        if tmux.returncode == 0: terminals.extend(item for item in tmux.stdout.splitlines() if item not in terminals)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    item = {"reason": reason, "captured_at": utc_now(), "repo": snap,
            "recent_events": [{"event_id": row["event_id"], "type": row["event_type"],
                               "occurred_at": row["occurred_at"], "status": row["status"]} for row in recent],
            "resources": [payload(row) for row in resource_rows],
            "open_files": open_files, "terminals": terminals,
            "next_intent": state["remaining"][0] if state["remaining"] else state["goal"]}
    event_id = ledger.append(event_type="workspace_snapshot", source="advanced", project_id=project,
                             machine="local", repo_path=str(repo), commit_sha=snap["commit_sha"], dirty=snap["dirty"],
                             payload=item, evidence=[{"type": "git_snapshot", "path": str(repo)}],
                             dedup_key=f"workspace_snapshot:{project}:{snap['tree_hash']}:{reason}")
    item["event_id"] = event_id
    return item


def _find_experiment(ledger: Ledger, experiment_id: str, project: str | None = None) -> Any | None:
    for row in reversed(rows(ledger, project)):
        p = payload(row)
        if p.get("experiment_id") == experiment_id or p.get("run_id") == experiment_id or row["event_id"] == experiment_id: return row
    return None


def experiment_capsule(ledger: Ledger, home: Path, experiment_id: str, project: str | None = None) -> dict[str, Any]:
    row = _find_experiment(ledger, experiment_id, project)
    if row is None: raise ValueError(f"unknown experiment: {experiment_id}")
    p = payload(row); project = project or row["project_id"] or "unassigned"
    capsule_id = str(p.get("experiment_id") or p.get("run_id") or experiment_id); out = home / "experiments" / capsule_id
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"schema_version": 1, "experiment_id": capsule_id, "event_id": row["event_id"],
                "project_id": project, "created_at": utc_now(), "status": row["status"],
                "commit": row["commit_sha"], "repo_path": row["repo_path"], "payload": p,
                "evidence": evidence(ledger, row["event_id"]), "reproducibility": reproducibility(ledger, project)}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    command = p.get("command") or "# command was not recorded for this experiment\n"
    (out / "command.sh").write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + (" ".join(command) if isinstance(command, list) else str(command)) + "\n", encoding="utf-8")
    (out / "environment.txt").write_text("python=" + os.sys.version.replace("\n", " ") + "\n", encoding="utf-8")
    (out / "metrics.json").write_text(json.dumps(p.get("metrics", p.get("result", {})), ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "config.yaml").write_text(json.dumps({"dataset": p.get("dataset"), "model": p.get("model"), "parameters": p.get("parameters", {})}, ensure_ascii=False, indent=2), encoding="utf-8")
    if row["repo_path"]:
        try:
            diff = subprocess.run(["git", "-C", row["repo_path"], "diff", "--binary", "--no-ext-diff"], text=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10)
            (out / "git.patch").write_text(diff.stdout, encoding="utf-8", errors="replace")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            (out / "git.patch").write_text("# git diff unavailable\n", encoding="utf-8")
    (out / "artifacts").mkdir(exist_ok=True)
    (out / "README.md").write_text(f"# Experiment capsule {capsule_id}\n\nGenerated from `{row['event_id']}`.\n\nStatus: `{row['status']}`\n", encoding="utf-8")
    return {"capsule": str(out), "manifest": str(out / "manifest.json"), "experiment_id": capsule_id,
            "reproducibility": manifest["reproducibility"][-1:]}


def reproduce_check(home: Path, capsule_id: str) -> dict[str, Any]:
    out = home / "experiments" / capsule_id; manifest_path = out / "manifest.json"
    if not manifest_path.exists(): raise FileNotFoundError(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")); missing = []
    for required in ("command.sh", "environment.txt", "metrics.json", "config.yaml", "git.patch"):
        if not (out / required).exists(): missing.append(required)
    if not manifest.get("commit"): missing.append("commit")
    for item in manifest.get("evidence", []):
        if item.get("path") and not Path(item["path"]).exists(): missing.append(item["path"])
    return {"experiment_id": capsule_id, "reproducible": not missing, "missing": missing,
            "estimated": "unknown_without_lifecycle_data", "manifest": str(manifest_path)}


def research_debt(ledger: Ledger, home: Path, project: str | None = None) -> dict[str, Any]:
    config = load_config(home / "config" / "projects.yaml")
    projects = [project] if project else sorted(config.get("projects", {})); items = []
    for pid in projects:
        state = state_dict(build_state(ledger, home, pid)); project_rows = rows(ledger, pid)
        for stage, value in state["verification"].items():
            if value["status"] in {"pending", "stale"}:
                items.append({"project_id": pid, "category": "unverified_stage", "severity": "high" if stage in {"jetson", "judge"} else "medium", "text": stage, "evidence": [value.get("event_id")]})
        for row in project_rows:
            if row["event_type"] in {"experiment_completed", "experiment_failed"} and not evidence(ledger, row["event_id"]) and not payload(row).get("supports"):
                items.append({"project_id": pid, "category": "experiment_without_evidence", "severity": "medium", "text": row["event_id"], "evidence": [row["event_id"]]})
            if row["event_type"].startswith("decision_") and not evidence(ledger, row["event_id"]) and not payload(row).get("supports"):
                items.append({"project_id": pid, "category": "decision_without_evidence", "severity": "high", "text": payload(row).get("text"), "evidence": [row["event_id"]]})
    counts = Counter(item["category"] for item in items)
    return {"total": len(items), "by_category": dict(counts), "high_risk": sum(item["severity"] == "high" for item in items), "items": items}


def claim_confidence(ledger: Ledger, project: str | None = None) -> list[dict[str, Any]]:
    output = []
    for row in rows(ledger, project):
        if not (row["event_type"].startswith("decision_") or row["event_type"] in {"experiment_completed", "benchmark_completed"}): continue
        p = payload(row); score = 0; reasons = []
        if row["commit_sha"]: score += 15; reasons.append("commit")
        if p.get("dataset"): score += 12; reasons.append("dataset")
        if p.get("model"): score += 10; reasons.append("model")
        if p.get("metrics") or p.get("result"): score += 18; reasons.append("metric/result")
        if evidence(ledger, row["event_id"]) or p.get("supports"): score += 20; reasons.append("evidence")
        if row["verification"] == "user_confirmed": score += 15; reasons.append("user_confirmed")
        if row["event_type"].endswith("rejected") or row["event_type"].endswith("superseded"): score -= 20
        output.append({"event_id": row["event_id"], "project_id": row["project_id"], "score": max(0, min(100, score)),
                       "claim": p.get("text") or p.get("name"), "reasons": reasons, "confidence": "observed" if score >= 70 else "partial"})
    return output


def hypotheses(ledger: Ledger, project: str | None = None) -> list[dict[str, Any]]:
    all_rows = rows(ledger, project); output = {}
    for row in all_rows:
        p = payload(row); hid = p.get("hypothesis_id")
        if not hid: continue
        item = output.setdefault(hid, {"hypothesis_id": hid, "statement": p.get("statement") or p.get("hypothesis"), "scope": p.get("scope"), "events": [], "status": "unresolved"})
        item["events"].append({"event_id": row["event_id"], "type": row["event_type"], "status": row["status"], "evidence": [row["event_id"]]})
        classification = p.get("classification")
        if classification in {"supports_hypothesis", "rejects_hypothesis", "partially_supports", "unresolved"}: item["status"] = classification
    return list(output.values())


def information_gain(ledger: Ledger, project: str | None = None) -> list[dict[str, Any]]:
    experiments = [row for row in rows(ledger, project) if row["event_type"] in {"experiment_started", "experiment_completed", "experiment_failed"}]
    seen: dict[str, Any] = {}; output = []
    for row in experiments:
        p = payload(row); comparable = {"dataset": p.get("dataset"), "model": p.get("model"), "parameters": p.get("parameters") or {}, "hypothesis": p.get("hypothesis")}
        fp = hashlib.sha256(json.dumps(comparable, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
        score = 100 if fp not in seen else 15
        output.append({"event_id": row["event_id"], "fingerprint": fp, "information_gain": score,
                       "classification": "high" if score >= 70 else "low", "similar_to": seen.get(fp), "evidence": [row["event_id"]]})
        seen.setdefault(fp, row["event_id"])
    return output


def budget_roi(ledger: Ledger, project: str | None = None) -> dict[str, Any]:
    experiments = [row for row in rows(ledger, project) if row["event_type"] in {"experiment_completed", "experiment_failed"}]
    samples = ledger.events(event_types={"resource_snapshot"}); gpu_observations = 0
    for sample in samples: gpu_observations += len(payload(sample).get("gpus", []))
    useful = sum(payload(row).get("classification") in {"supports_hypothesis", "rejects_hypothesis", "decision_producing"} for row in experiments)
    return {"experiments": len(experiments), "useful_experiments": useful, "gpu_observations": gpu_observations,
            "gpu_hours": None, "unit_cost": None if not useful else "requires GPU lifecycle/cost configuration",
            "confidence": "approximate", "basis": [row["event_id"] for row in experiments[-20:]]}


def baseline(ledger: Ledger, project: str, *, record: bool = False, metrics: dict[str, Any] | None = None) -> dict[str, Any]:
    all_rows = rows(ledger, project); records = [row for row in all_rows if row["event_type"] == "baseline_recorded"]
    if record:
        latest = next((row for row in reversed(all_rows) if row["event_type"] == "git_snapshot"), None)
        eid = ledger.append(event_type="baseline_recorded", source="advanced", project_id=project,
                            commit_sha=latest["commit_sha"] if latest else None, dirty=latest["dirty"] if latest else None,
                            payload={"metrics": metrics or {}, "tree_hash": payload(latest).get("tree_hash") if latest else None},
                            verification="user_confirmed", dedup_key=f"baseline:{project}:{latest['commit_sha'] if latest else 'none'}:{json.dumps(metrics or {}, sort_keys=True)}")
        return {"event_id": eid, "project_id": project, "metrics": metrics or {}}
    if not records: return {"project_id": project, "baseline": None, "drift": [], "confidence": "unknown"}
    latest = records[-1]; bp = payload(latest); drift = []
    current = next((row for row in reversed(all_rows) if row["event_type"] == "git_snapshot"), None)
    if current and bp.get("commit_sha") and current["commit_sha"] != bp["commit_sha"]: drift.append("commit changed")
    return {"project_id": project, "baseline": bp, "drift": drift, "event_id": latest["event_id"], "confidence": "observed"}


def metric_lineage(ledger: Ledger, project: str | None = None) -> dict[str, Any]:
    nodes = []; edges = []
    for row in rows(ledger, project):
        p = payload(row); metrics = p.get("metrics") or {}
        if not isinstance(metrics, dict): continue
        source = p.get("experiment_id") or row["event_id"]
        for name, value in metrics.items():
            mid = f"metric:{name}:{row['event_id']}"; nodes.append({"id": mid, "type": "metric", "name": name, "value": value, "event_id": row["event_id"]})
            edges.append({"from": mid, "to": source, "relation": "generated_by", "evidence": [row["event_id"]]})
            for key in ("dataset", "model", "commit_sha", "tree_hash"):
                if p.get(key) or (key == "commit_sha" and row["commit_sha"]): edges.append({"from": source, "to": f"{key}:{p.get(key) or row[key]}", "relation": "based_on", "evidence": [row["event_id"]]})
    return {"nodes": nodes, "edges": edges}


def fingerprints(ledger: Ledger, project: str | None = None) -> list[dict[str, Any]]:
    groups = defaultdict(list)
    for row in rows(ledger, project):
        if row["event_type"] not in {"experiment_started", "experiment_completed", "experiment_failed"}: continue
        p = payload(row); normalized = {key: p.get(key) for key in ("dataset", "model", "parameters", "hypothesis", "result")}
        fp = hashlib.sha256(json.dumps(normalized, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        groups[fp].append(row)
    return [{"fingerprint": fp, "count": len(items), "duplicate": len(items) > 1,
             "experiments": [{"event_id": row["event_id"], "name": payload(row).get("name"), "status": row["status"]} for row in items]}
            for fp, items in groups.items()]


def health(ledger: Ledger, home: Path, project: str) -> dict[str, Any]:
    state = state_dict(build_state(ledger, home, project)); coverage = evidence_coverage(ledger, project)["coverage"]
    repro = reproducibility(ledger, project); repro_score = sum(item["score"] for item in repro) / len(repro) if repro else 0
    stages = list(state["verification"].values()); verification = sum(value["status"] == "passed" for value in stages) / len(stages) if stages else 0
    score = round((coverage * 30) + (repro_score / 100 * 25) + (verification * 30) + (0 if state["blockers"] else 15))
    return {"project_id": project, "score": score, "dimensions": {"evidence": coverage, "reproducibility": round(repro_score / 100, 3), "verification": round(verification, 3), "blockers": len(state["blockers"])}, "basis": state["recent_events"]}


def risk_radar(ledger: Ledger, home: Path, project: str) -> dict[str, Any]:
    state = state_dict(build_state(ledger, home, project)); risks = {}
    risks["correctness"] = "high" if any(v["status"] == "stale" for v in state["verification"].values()) else "medium"
    risks["progress"] = "high" if state["blockers"] else ("medium" if any(v["status"] == "pending" for v in state["verification"].values()) else "low")
    risks["reproducibility"] = "high" if evidence_coverage(ledger, project)["coverage"] < 0.5 else "medium"
    risks["resource"] = "medium" if ledger.events(event_types={"resource_snapshot"}) else "unknown"
    return {"project_id": project, "risks": risks, "confidence": "observed+heuristic", "basis": state["recent_events"]}


def why_not_done(ledger: Ledger, home: Path, project: str) -> dict[str, Any]:
    state = state_dict(build_state(ledger, home, project)); reasons = []
    for stage, value in state["verification"].items():
        if value["status"] in {"pending", "stale"}: reasons.append({"priority": 1 if stage in {"jetson", "judge"} else 2, "reason": f"验证阶段 {stage}: {value['status']}", "evidence": [value.get("event_id")]})
    reasons.extend({"priority": 1, "reason": blocker, "evidence": []} for blocker in state["blockers"])
    return {"project_id": project, "primary_reasons": sorted(reasons, key=lambda item: item["priority"]), "completed": [stage for stage, value in state["verification"].items() if value["status"] == "passed"]}


def attention_budget(ledger: Ledger, project: str | None = None) -> dict[str, Any]:
    events = rows(ledger, project); counts = Counter()
    for row in events:
        typ = row["event_type"]
        category = "implementation" if typ == "git_snapshot" else "verification" if typ in {"test_completed", "benchmark_completed", "experiment_completed"} else "failure_handling" if row["status"] == "failed" else "context"
        counts[category] += 1
    total = sum(counts.values())
    return {"event_proxy": dict(counts), "shares": {key: round(value / total, 3) for key, value in counts.items()} if total else {}, "note": "没有键盘/窗口追踪时使用事件数量作为代理"}


def rhythm(ledger: Ledger, project: str | None = None) -> dict[str, Any]:
    buckets = defaultdict(lambda: Counter())
    for row in rows(ledger, project):
        try: hour = datetime.fromisoformat(row["occurred_at"]).astimezone(LOCAL_TZ).hour
        except (TypeError, ValueError): continue
        buckets[hour]["events"] += 1
        if row["status"] == "passed": buckets[hour]["success"] += 1
        if row["status"] == "failed": buckets[hour]["failure"] += 1
    return {"hours": [{"hour": hour, **counts} for hour, counts in sorted(buckets.items())], "note": "基于账本事件，不代表完整工作时间"}


def handoff_quality(ledger: Ledger, project: str | None = None) -> list[dict[str, Any]]:
    output = []
    for item in session_efficiency(ledger, project):
        row = next((row for row in reversed(rows(ledger, project)) if row["session_id"] == item["session_id"] and row["event_type"] == "agent_session_completed"), None)
        p = payload(row) if row else {}; fields = {key: bool(p.get(key)) for key in ("summary", "remaining", "blockers", "decisions")}
        score = round(sum(fields.values()) / len(fields) * 100)
        output.append({"session_id": item["session_id"], "score": score, "fields": fields, "evidence": item["evidence"]})
    return output


def agent_blindspots(ledger: Ledger, project: str | None = None) -> list[dict[str, Any]]:
    output = []
    for agent in sorted({row["source"] for row in rows(ledger, project) if row["event_type"].startswith("agent_")}):
        agent_rows = [row for row in rows(ledger, project) if row["source"] == agent]
        missing_remote = sum(1 for row in agent_rows if row["event_type"] == "agent_session_completed" and "jetson" not in json.dumps(payload(row), ensure_ascii=False).lower())
        output.append({"agent": agent, "sessions": len(agent_rows), "possible_remote_verification_omissions": missing_remote, "confidence": "heuristic"})
    return output


def memory_freshness(ledger: Ledger, home: Path, project: str) -> dict[str, Any]:
    events = rows(ledger, project); last = events[-1]["occurred_at"] if events else None
    try: age = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).days if last else None
    except (TypeError, ValueError): age = None
    score = max(0, 100 - (age or 0) * 3)
    state = state_dict(build_state(ledger, home, project))
    if any(v["status"] == "stale" for v in state["verification"].values()): score -= 20
    return {"project_id": project, "score": max(0, score), "age_days": age, "stale_stages": [stage for stage, value in state["verification"].items() if value["status"] == "stale"], "confidence": "observed"}


def refresh(ledger: Ledger, home: Path, project: str, *, record: bool = True) -> dict[str, Any]:
    if record:
        git_state = workspace_snapshot(ledger, home, project, reason="refresh")["repo"]
    else:
        cfg = project_config(home, project); git_state = snapshot(Path(cfg["repo_path"]))
    return {"project_id": project, "refreshed_at": utc_now(), "git": git_state, "checks": {"git": True, "remote": False, "models": "not_checked", "docker": "not_checked"}, "note": "只读刷新；不连接或修改远程资源"}


def knowledge_cards(ledger: Ledger, project: str | None = None) -> list[dict[str, Any]]:
    cards = []
    for row in rows(ledger, project):
        if not row["event_type"].startswith("decision_"): continue
        p = payload(row); cards.append({"title": p.get("text", "未命名经验"), "experience": p.get("reason"), "scope": p.get("scope"), "status": row["status"], "source": [row["event_id"]], "confidence": "project_specific" if row["project_id"] else "unknown"})
    return cards


def project_brief(ledger: Ledger, home: Path, project: str) -> dict[str, Any]:
    state = state_dict(build_state(ledger, home, project)); return {"generated_at": utc_now(), "project": state, "health": health(ledger, home, project), "risks": risk_radar(ledger, home, project), "parameters": parameter_lineage(ledger, project), "knowledge_cards": knowledge_cards(ledger, project)}


def achievements(ledger: Ledger, home: Path, project: str | None = None) -> list[dict[str, Any]]:
    output = []
    config = load_config(home / "config" / "projects.yaml"); projects = [project] if project else sorted(config.get("projects", {}))
    for pid in projects:
        state = state_dict(build_state(ledger, home, pid)); stages = list(state["verification"].values())
        if stages and all(item["status"] == "passed" for item in stages): output.append({"achievement": "完整闭环", "project_id": pid, "evidence": state["recent_events"]})
        if len(reproducibility(ledger, pid)) >= 10 and all(item["score"] >= 80 for item in reproducibility(ledger, pid)[-10:]): output.append({"achievement": "可复现大师", "project_id": pid})
    return output


def daily_card(ledger: Ledger, home: Path, target: date) -> dict[str, Any]:
    from .semantic import build_semantic_facts
    semantic = build_semantic_facts(ledger, home, target); return {"date": target.isoformat(), "mainline": semantic.get("today_results", [])[:1], "results": semantic.get("today_results", [])[:3], "blockers": semantic.get("current_blockers", [])[:3], "next": semantic.get("next_actions", [])[:3], "evidence": [item.get("event_id") for item in semantic.get("today_results", [])]}


def research_map(ledger: Ledger, home: Path) -> list[dict[str, Any]]:
    config = load_config(home / "config" / "projects.yaml"); output = []
    for pid in sorted(config.get("projects", {})):
        state = state_dict(build_state(ledger, home, pid)); values = list(state["verification"].values()); passed = sum(v["status"] == "passed" for v in values); progress = round(passed / len(values), 3) if values else 0
        output.append({"project_id": pid, "progress": progress, "risk": risk_radar(ledger, home, pid)["risks"], "status": "blocked" if state["blockers"] else "active" if progress < 1 else "done", "bubble": len(rows(ledger, pid))})
    return output


def dont(ledger: Ledger, home: Path, project: str | None = None) -> list[dict[str, Any]]:
    output = []
    for item in suggest_experiments(ledger, project): output.append({"project_id": item.get("project_id"), "dont": item["suggestion"], "reason": item["reason"], "basis": item["basis"]})
    for item in fingerprints(ledger, project):
        if item["duplicate"]: output.append({"project_id": project, "dont": "重复运行相同实验配置", "reason": f"fingerprint {item['fingerprint']} 已出现 {item['count']} 次", "basis": [entry["event_id"] for entry in item["experiments"]]})
    return output


def decision_countdown(ledger: Ledger, project: str | None = None) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc); output = []
    for row in rows(ledger, project):
        if row["event_type"] not in {"decision_proposed", "decision_supported"}: continue
        try: age = (now - datetime.fromisoformat(row["occurred_at"])).days
        except (TypeError, ValueError): continue
        if age >= 3:
            dependents = sum(1 for item in rows(ledger, row["project_id"]) if item["occurred_at"] > row["occurred_at"] and item["event_type"].startswith(("experiment_", "verification_")))
            output.append({"decision_id": payload(row).get("decision_id", row["event_id"]), "waiting_days": age, "dependent_events": dependents, "cost": "approximate", "evidence": [row["event_id"]]})
    return output
