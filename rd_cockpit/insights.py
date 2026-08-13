"""Deterministic higher-level projections over the event ledger.

These projections intentionally return facts plus basis event IDs.  They are
the backend for the future fancy UI and are safe to regenerate at any time.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import load_config
from .ledger import Ledger
from .state import build_state, state_dict


def _day_bounds(target: Any) -> tuple[str, str]:
    from zoneinfo import ZoneInfo
    local = ZoneInfo("Asia/Shanghai")
    start = datetime.combine(target, datetime.min.time(), local).astimezone(timezone.utc).isoformat()
    end = datetime.combine(target + timedelta(days=1), datetime.min.time(), local).astimezone(timezone.utc).isoformat()
    return start, end


def payload(row: Any) -> dict[str, Any]:
    try:
        value = json.loads(row["payload_json"])
        return value if isinstance(value, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _rows(ledger: Ledger, project: str | None = None) -> list[Any]:
    return ledger.events(project_id=project)


def _evidence(ledger: Ledger, event_id: str) -> list[dict[str, Any]]:
    return [dict(row) for row in ledger.event_evidence(event_id)]


def parameter_lineage(ledger: Ledger, project: str | None = None) -> list[dict[str, Any]]:
    output: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _rows(ledger, project):
        p = payload(row)
        params = p.get("parameters") or p.get("params") or {}
        if isinstance(params, list):
            params = {str(item).split("=", 1)[0]: str(item).split("=", 1)[1]
                      for item in params if isinstance(item, str) and "=" in item}
        if not isinstance(params, dict): continue
        for name, value in params.items():
            output[str(name)].append({"value": value, "occurred_at": row["occurred_at"],
                                      "event_id": row["event_id"], "type": row["event_type"],
                                      "project_id": row["project_id"], "commit": row["commit_sha"],
                                      "status": row["status"], "reason": p.get("reason") or p.get("hypothesis"),
                                      "evidence": [row["event_id"]]})
    return [{"parameter": name, "history": history, "current": history[-1]["value"],
             "changed": len({json.dumps(item["value"], sort_keys=True, ensure_ascii=False) for item in history}) > 1}
            for name, history in sorted(output.items())]


def decision_graph(ledger: Ledger, project: str | None = None) -> dict[str, Any]:
    rows = _rows(ledger, project)
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    decisions = [row for row in rows if row["event_type"].startswith("decision_")]
    for row in decisions:
        p = payload(row); did = p.get("decision_id", row["event_id"])
        nodes.append({"id": did, "type": "decision", "label": p.get("text", did),
                      "status": row["status"], "event_id": row["event_id"]})
        supports = p.get("supports") or p.get("supported_by") or []
        if isinstance(supports, str): supports = [supports]
        for target in supports:
            edges.append({"from": did, "to": target, "relation": "supported_by", "evidence": [row["event_id"]]})
    event_by_id = {row["event_id"]: row for row in rows}
    for row in rows:
        if row["event_type"] not in {"experiment_completed", "experiment_failed", "metric_observed", "artifact_created"}: continue
        p = payload(row); node_id = p.get("experiment_id") or p.get("metric_id") or p.get("artifact_id") or row["event_id"]
        nodes.append({"id": node_id, "type": row["event_type"].removesuffix("_completed").removesuffix("_failed"),
                      "label": p.get("name") or p.get("result") or node_id, "status": row["status"],
                      "event_id": row["event_id"]})
    # When explicit supports are absent, connect a decision to experiments in
    # the preceding 24 hours in the same project as a transparent heuristic.
    for decision in decisions:
        dp = payload(decision); did = dp.get("decision_id", decision["event_id"])
        if dp.get("supports") or dp.get("supported_by"): continue
        try: at = datetime.fromisoformat(decision["occurred_at"])
        except ValueError: continue
        for row in rows:
            if row["event_type"] not in {"experiment_completed", "experiment_failed", "metric_observed"}: continue
            if row["project_id"] != decision["project_id"]: continue
            try: delta = at - datetime.fromisoformat(row["occurred_at"])
            except ValueError: continue
            if timedelta(0) <= delta <= timedelta(hours=24):
                target = payload(row).get("experiment_id") or payload(row).get("metric_id") or row["event_id"]
                edges.append({"from": did, "to": target, "relation": "nearby_evidence",
                              "evidence": [decision["event_id"], row["event_id"]]})
    unique_nodes = {node["id"]: node for node in nodes}
    return {"nodes": list(unique_nodes.values()), "edges": edges}


def decision_conflicts(ledger: Ledger, project: str | None = None) -> list[dict[str, Any]]:
    groups: dict[str, list[Any]] = defaultdict(list)
    for row in _rows(ledger, project):
        if not row["event_type"].startswith("decision_"): continue
        p = payload(row); key = p.get("decision_key")
        if not key:
            text = str(p.get("text", "")); key = text.split("=", 1)[0].strip().split(":", 1)[0]
        if key: groups[str(key)].append(row)
    output = []
    for key, rows in groups.items():
        values = [payload(row).get("text", "") for row in rows]
        if len(set(values)) <= 1: continue
        scopes = {json.dumps(payload(row).get("scope"), sort_keys=True, ensure_ascii=False) for row in rows}
        output.append({"decision_key": key, "possible_conflict": True, "different_scope": len(scopes) > 1,
                       "decisions": [{"event_id": row["event_id"], "occurred_at": row["occurred_at"],
                                      "status": row["status"], "payload": payload(row), "evidence": [row["event_id"]]}
                                     for row in rows],
                       "recommendation": "比较 dataset/model/metric/scope 后决定是否改为 conditionally_adopted"})
    return output


def decision_freshness(ledger: Ledger, project: str | None = None) -> list[dict[str, Any]]:
    output = []
    for row in _rows(ledger, project):
        if not row["event_type"].startswith("decision_") or row["event_type"] in {"decision_rejected", "decision_superseded"}: continue
        p = payload(row); later = [e for e in _rows(ledger, row["project_id"]) if e["occurred_at"] > row["occurred_at"]]
        reasons = []
        for event in later:
            ep = payload(event)
            if event["event_type"] == "git_snapshot" and row["commit_sha"] and event["commit_sha"] != row["commit_sha"]:
                reasons.append("代码 commit 已变化")
            for field in ("model", "dataset", "metric_definition"):
                if p.get(field) and ep.get(field) and p[field] != ep[field]: reasons.append(f"{field} 已变化")
        if reasons:
            output.append({"event_id": row["event_id"], "project_id": row["project_id"],
                           "text": p.get("text"), "status": "stale_candidate", "reasons": sorted(set(reasons)),
                           "evidence": [row["event_id"]]})
    return output


def experiment_efficiency(ledger: Ledger, project: str | None = None) -> dict[str, Any]:
    rows = [row for row in _rows(ledger, project) if row["event_type"] in {"experiment_completed", "experiment_failed"}]
    counts = Counter()
    items = []
    for row in rows:
        p = payload(row); classification = p.get("classification")
        if not classification:
            classification = "environment_failure" if row["status"] == "failed" else ("decision_producing" if p.get("decision_id") else "completed_without_decision")
        counts[classification] += 1
        items.append({"event_id": row["event_id"], "name": p.get("name"), "classification": classification,
                      "status": row["status"], "project_id": row["project_id"], "evidence": [row["event_id"]]})
    total = len(rows); useful = sum(counts[key] for key in ("supports_hypothesis", "rejects_hypothesis", "decision_producing"))
    return {"total": total, "counts": dict(counts), "effective_rate": round(useful / total, 3) if total else 0.0,
            "items": items}


def gpu_report(ledger: Ledger) -> dict[str, Any]:
    samples = [row for row in ledger.events(event_types={"resource_snapshot"})]
    by_gpu: dict[str, list[tuple[datetime, dict[str, Any], str]]] = defaultdict(list)
    for row in samples:
        p = payload(row)
        try: at = datetime.fromisoformat(p.get("sampled_at", row["occurred_at"]))
        except (TypeError, ValueError): continue
        for gpu in p.get("gpus", []): by_gpu[str(gpu.get("index"))].append((at, gpu, row["event_id"]))
    output = []
    for index, entries in sorted(by_gpu.items()):
        utils = [float(g.get("utilization_pct", 0)) for _, g, _ in entries if str(g.get("utilization_pct", "")).replace(".", "", 1).isdigit()]
        memory = [float(g.get("memory_used_mb", 0)) for _, g, _ in entries if str(g.get("memory_used_mb", "")).replace(".", "", 1).isdigit()]
        idle = sum(1 for _, g, _ in entries if float(g.get("utilization_pct", 0) or 0) == 0 and float(g.get("memory_used_mb", 0) or 0) > 1024)
        output.append({"gpu": index, "samples": len(entries), "avg_utilization_pct": round(sum(utils) / len(utils), 2) if utils else 0,
                       "peak_memory_mb": max(memory) if memory else 0, "idle_allocated_samples": idle,
                       "evidence": [event_id for _, _, event_id in entries[-5:]]})
    return {"samples": len(samples), "gpus": output, "note": "快照可计算趋势；没有任务生命周期事件时不虚构 GPU-hours"}


def evidence_coverage(ledger: Ledger, project: str | None = None) -> dict[str, Any]:
    rows = [row for row in _rows(ledger, project) if row["event_type"].startswith("decision_") or row["event_type"] in {"experiment_completed", "experiment_failed"}]
    covered = sum(bool(_evidence(ledger, row["event_id"]) or payload(row).get("supports") or payload(row).get("evidence")) for row in rows)
    return {"total_claims": len(rows), "covered_claims": covered,
            "coverage": round(covered / len(rows), 3) if rows else 0.0,
            "claims_without_evidence": [row["event_id"] for row in rows if not (_evidence(ledger, row["event_id"]) or payload(row).get("supports") or payload(row).get("evidence"))]}


def reproducibility(ledger: Ledger, project: str | None = None) -> list[dict[str, Any]]:
    output = []
    for row in _rows(ledger, project):
        if row["event_type"] not in {"experiment_completed", "experiment_failed", "benchmark_completed", "test_completed"}: continue
        p = payload(row); checks = {"commit": bool(row["commit_sha"]), "repo": bool(row["repo_path"]),
                                   "command": bool(p.get("command")), "dataset": bool(p.get("dataset")),
                                   "model": bool(p.get("model")), "tree_hash": bool(p.get("tree_hash")),
                                   "artifact": bool(_evidence(ledger, row["event_id"]))}
        score = round(sum(checks.values()) / len(checks) * 100)
        output.append({"event_id": row["event_id"], "project_id": row["project_id"], "score": score,
                       "checks": checks, "missing": [key for key, value in checks.items() if not value]})
    return output


def change_impact(ledger: Ledger, home: Path, project: str) -> dict[str, Any]:
    state = state_dict(build_state(ledger, home, project))
    return {"project_id": project, "head": state["head"], "dirty": state["dirty"],
            "stages": [{"stage": stage, "status": value["status"], "reason": value.get("stale_reason"),
                        "basis": [value.get("event_id"), value.get("commit")]}
                       for stage, value in state["verification"].items()],
            "recommendation": "优先重新验证 stale 阶段" if any(v["status"] == "stale" for v in state["verification"].values()) else "没有发现受影响的已通过阶段"}


def context_pack(ledger: Ledger, home: Path, project: str) -> dict[str, Any]:
    state = state_dict(build_state(ledger, home, project))
    rows = _rows(ledger, project)
    decisions = [row for row in rows if row["event_type"].startswith("decision_")][-10:]
    return {"project": state, "recent_events": state["recent_events"],
            "decisions": [{"event_id": row["event_id"], "type": row["event_type"], "payload": payload(row)} for row in decisions],
            "parameter_lineage": parameter_lineage(ledger, project),
            "reproducibility": reproducibility(ledger, project)[-10:]}


def suggest_experiments(ledger: Ledger, project: str | None = None) -> list[dict[str, Any]]:
    suggestions = []
    for item in decision_freshness(ledger, project):
        suggestions.append({"project_id": item["project_id"], "suggestion": "重新运行该决策的最小回归实验",
                            "reason": "; ".join(item["reasons"]), "basis": item["evidence"], "kind": "stale_decision"})
    for conflict in decision_conflicts(ledger, project):
        suggestions.append({"project_id": project, "suggestion": "在统一数据集、模型和评测脚本下复现实验",
                            "reason": conflict["recommendation"], "basis": [d["event_id"] for d in conflict["decisions"]], "kind": "decision_conflict"})
    return suggestions


def counterfactual(ledger: Ledger, project: str, query: str) -> dict[str, Any]:
    matches = []
    for row in _rows(ledger, project):
        p = payload(row)
        if row["event_type"].startswith("decision_") and query.lower() in json.dumps(p, ensure_ascii=False).lower(): matches.append(row)
    if not matches: return {"query": query, "answer": "没有找到对应决策", "confidence": "unknown", "evidence": []}
    decision = matches[-1]; dp = payload(decision)
    experiments = [row for row in _rows(ledger, project) if row["event_type"] == "experiment_completed" and row["status"] == "passed"]
    return {"query": query, "answer": "历史账本只能给出已观测的替代结果，不能证明未采用方案的真实反事实结果。",
            "observed_decision": dp, "alternative_observations": [payload(row) for row in experiments[-10:]],
            "confidence": "inferred", "evidence": [decision["event_id"]] + [row["event_id"] for row in experiments[-10:]]}


def digital_twin(ledger: Ledger, home: Path) -> dict[str, Any]:
    config = load_config(home / "config" / "projects.yaml")
    projects = []
    for project in sorted(config.get("projects", {})):
        state = state_dict(build_state(ledger, home, project))
        projects.append({"project_id": project, "goal": state["goal"], "verification": state["verification"],
                         "blockers": state["blockers"], "remaining": state["remaining"],
                         "head": state["head"], "dirty": state["dirty"],
                         "evidence_coverage": evidence_coverage(ledger, project)["coverage"]})
    return {"generated_from": "event_ledger", "projects": projects}


def context_switch_analysis(ledger: Ledger) -> dict[str, Any]:
    rows = [row for row in ledger.events() if row["project_id"]]
    sequence: list[str] = []
    switch_events = []
    for row in rows:
        pid = row["project_id"]
        if sequence and sequence[-1] != pid:
            switch_events.append({"from": sequence[-1], "to": pid, "occurred_at": row["occurred_at"], "event_id": row["event_id"]})
        if not sequence or sequence[-1] != pid: sequence.append(pid)
    return {"switches": len(switch_events), "sequence": sequence, "events": switch_events[-100:],
            "basis": [item["event_id"] for item in switch_events[-100:]]}


def session_efficiency(ledger: Ledger, project: str | None = None) -> list[dict[str, Any]]:
    rows = _rows(ledger, project)
    sessions: dict[str, dict[str, Any]] = {}
    for row in rows:
        sid = row["session_id"]
        if not sid: continue
        item = sessions.setdefault(sid, {"session_id": sid, "project_id": row["project_id"], "started_at": None,
                                        "ended_at": None, "status": "active", "events": 0, "tests": 0,
                                        "failures": 0, "first_effective_at": None, "evidence": []})
        item["events"] += 1; item["evidence"].append(row["event_id"])
        if row["event_type"] == "agent_session_started": item["started_at"] = row["occurred_at"]
        elif row["event_type"] == "agent_session_completed": item["ended_at"] = row["occurred_at"]; item["status"] = row["status"] or "completed"
        if row["event_type"] in {"test_completed", "test_failed", "benchmark_completed", "experiment_completed", "experiment_failed"}: item["tests"] += 1
        if row["status"] == "failed": item["failures"] += 1
        if row["event_type"] in {"git_snapshot", "command_completed", "test_completed", "benchmark_completed", "experiment_completed"} and item["first_effective_at"] is None:
            item["first_effective_at"] = row["occurred_at"]
    for item in sessions.values():
        try:
            start = datetime.fromisoformat(item["started_at"]); end = datetime.fromisoformat(item["ended_at"] or item["started_at"])
            item["duration_hours"] = round(max(0, (end - start).total_seconds()) / 3600, 3)
        except (TypeError, ValueError): item["duration_hours"] = None
        item["evidence"] = item["evidence"][-20:]
    return list(sessions.values())


def today_replay(ledger: Ledger, home: Path, target: Any) -> dict[str, Any]:
    from .semantic import build_semantic_facts
    semantic = build_semantic_facts(ledger, home, target)
    start, end = _day_bounds(target)
    events = ledger.events(since=start, until=end)
    timeline = []
    for row in events:
        p = payload(row); detail = p.get("text") or p.get("name") or p.get("command") or p.get("stage") or row["event_type"]
        timeline.append({"at": row["occurred_at"], "project_id": row["project_id"], "type": row["event_type"],
                         "status": row["status"], "detail": detail, "evidence": [row["event_id"]]})
    return {"date": target.isoformat(), "summary": semantic, "timeline": timeline}


def research_wrapped(ledger: Ledger, home: Path, period: str, target: Any) -> dict[str, Any]:
    from .period import build_period_facts
    facts = build_period_facts(ledger, period, target)
    project = max(facts["projects"].items(), key=lambda item: item[1]["events"])[0] if facts["projects"] else None
    rejected = len([row for row in ledger.events(since=None) if row["event_type"] in {"decision_rejected", "decision_superseded"}])
    failed = sum(1 for row in facts["events"] if row["status"] == "failed")
    return {"period": facts["label"], "most_active_project": project,
            "outputs": facts["outputs"], "time": facts["time"], "trend": facts.get("trend", []),
            "failed_events": failed, "rejected_or_superseded_decisions": rejected,
            "basis": [event["event_id"] for event in ledger.events()[-20:]]}


def resource_cost(ledger: Ledger, project: str | None = None) -> list[dict[str, Any]]:
    decisions = [row for row in _rows(ledger, project) if row["event_type"].startswith("decision_")]
    samples = ledger.events(event_types={"resource_snapshot"})
    output = []
    for decision in decisions:
        try: at = datetime.fromisoformat(decision["occurred_at"])
        except ValueError: continue
        nearby = []
        for sample in samples:
            try: sample_at = datetime.fromisoformat(payload(sample).get("sampled_at", sample["occurred_at"]))
            except (TypeError, ValueError): continue
            if timedelta(hours=-24) <= sample_at - at <= timedelta(hours=0): nearby.append(sample)
        gpu_ids = sorted({str(gpu.get("index")) for sample in nearby for gpu in payload(sample).get("gpus", [])})
        output.append({"decision_id": payload(decision).get("decision_id", decision["event_id"]),
                       "project_id": decision["project_id"], "resource_samples": len(nearby),
                       "gpu_observed": gpu_ids, "cost_is_approximate": True,
                       "evidence": [decision["event_id"]] + [sample["event_id"] for sample in nearby[-10:]]})
    return output


def what_changed(ledger: Ledger, query: str, project: str | None = None) -> dict[str, Any]:
    rows = _rows(ledger, project); query = query.lower().strip()
    if query.startswith("commit:"):
        sha = query.split(":", 1)[1]; matched = [row for row in rows if row["commit_sha"] and row["commit_sha"].startswith(sha)]
        start = matched[0]["occurred_at"] if matched else None
        selected = [row for row in rows if start and row["occurred_at"] >= start]
    else:
        try:
            start = datetime.fromisoformat(query).replace(tzinfo=timezone.utc).isoformat()
            selected = [row for row in rows if row["occurred_at"] >= start]
        except ValueError:
            selected = [row for row in rows if query in json.dumps(payload(row), ensure_ascii=False).lower()]
    groups = Counter(row["event_type"] for row in selected)
    return {"query": query, "counts": dict(groups), "events": [{"event_id": row["event_id"], "occurred_at": row["occurred_at"],
        "project_id": row["project_id"], "type": row["event_type"], "status": row["status"], "commit": row["commit_sha"],
        "payload": payload(row), "evidence": [row["event_id"]]} for row in selected]}
