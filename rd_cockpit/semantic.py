"""Evidence-first daily semantic projection.

This module deliberately starts with deterministic rules.  It produces the
same four answers a future LLM summarizer must produce, while every sentence
keeps a list of event IDs.  An optional OpenAI-compatible enrichment hook is
provided separately; it can only rewrite these candidates and cannot invent
new evidence.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config import load_config
from .ledger import Ledger
from .state import build_state, state_dict

LOCAL_TZ = ZoneInfo("Asia/Shanghai")


def _day_bounds(target: date) -> tuple[str, str]:
    start = datetime.combine(target, datetime.min.time(), LOCAL_TZ).astimezone(timezone.utc).isoformat()
    end = datetime.combine(target + timedelta(days=1), datetime.min.time(), LOCAL_TZ).astimezone(timezone.utc).isoformat()
    return start, end


def _payload(row: Any) -> dict[str, Any]:
    try:
        value = json.loads(row["payload_json"])
        return value if isinstance(value, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _item(row: Any, text: str, *, kind: str, status: str | None = None) -> dict[str, Any]:
    return {"kind": kind, "text": text, "status": status or row["status"],
            "project_id": row["project_id"], "evidence": [row["event_id"]],
            "confidence": "observed" if row["provenance"] == "observed" else row["provenance"]}


def _plan_closure(ledger: Ledger, target: date) -> list[dict[str, Any]]:
    """Match yesterday's open plans with today's explicit close events."""
    yesterday_start, yesterday_end = _day_bounds(target - timedelta(days=1))
    today_start, today_end = _day_bounds(target)
    yesterday = [row for row in ledger.events(since=yesterday_start, until=yesterday_end)
                 if row["event_type"] == "plan_created"]
    today_closed = [row for row in ledger.events(since=today_start, until=today_end)
                    if row["event_type"] == "plan_closed"]
    closed_by_text: dict[str, Any] = {}
    for row in today_closed:
        payload = _payload(row)
        if payload.get("text"):
            closed_by_text[str(payload["text"])] = row
    output = []
    for row in yesterday:
        payload = _payload(row); text = str(payload.get("text") or "未命名计划")
        close = closed_by_text.get(text)
        if close:
            close_payload = _payload(close)
            output.append({"plan": text, "status": close_payload.get("status") or close["status"] or "completed",
                           "reason": close_payload.get("reason"),
                           "evidence": [row["event_id"], close["event_id"]], "confidence": "reported"})
        else:
            output.append({"plan": text, "status": "no_evidence", "reason": "今天没有找到对应的 plan_closed 事件",
                           "evidence": [row["event_id"]], "confidence": "reported"})
    return output


def build_semantic_facts(ledger: Ledger, home: Path, target: date) -> dict[str, Any]:
    start, end = _day_bounds(target)
    rows = ledger.events(since=start, until=end)
    results: list[dict[str, Any]] = []
    for row in rows:
        payload = _payload(row)
        event_type = row["event_type"]
        if event_type in {"test_completed", "benchmark_completed", "experiment_completed"} and row["status"] == "passed":
            detail = payload.get("result") or payload.get("metrics") or payload.get("command") or "验证通过"
            results.append(_item(row, f"{event_type}: {detail}", kind="result", status="completed"))
        elif event_type.startswith("decision_") and row["status"] in {"adopted", "confirmed", "supported", "conditionally_adopted"}:
            results.append(_item(row, str(payload.get("text") or "形成一项研发决策"), kind="decision", status=row["status"]))
        elif event_type == "milestone_completed":
            results.append(_item(row, str(payload.get("text") or "完成里程碑"), kind="milestone", status="completed"))

    config = load_config(home / "config" / "projects.yaml")
    blockers: list[dict[str, Any]] = []
    next_actions: list[dict[str, Any]] = []
    for project_id in sorted(config.get("projects", {})):
        state = state_dict(build_state(ledger, home, project_id))
        for blocker in state["blockers"]:
            blockers.append({"project_id": project_id, "text": blocker, "confidence": "reported"})
        stale = next(((stage, value) for stage, value in state["verification"].items() if value["status"] == "stale"), None)
        if stale:
            stage, value = stale
            next_actions.append({"project_id": project_id, "action": f"重新验证 {stage}",
                                 "reason": value.get("stale_reason", "验证依赖变化"), "basis": [value.get("event_id")]})
        else:
            pending = next((stage for stage, value in state["verification"].items() if value["status"] == "pending"), None)
            if pending:
                next_actions.append({"project_id": project_id, "action": f"推进验证阶段 {pending}",
                                     "reason": "验证漏斗中最早的未完成阶段", "basis": [state.get("head")]})
        if state["remaining"]:
            next_actions.append({"project_id": project_id, "action": state["remaining"][0],
                                 "reason": "来自最近一次 handoff 的 remaining", "basis": state["recent_events"][-1:]})
    anomalies = []
    from .anomalies import find_anomalies
    anomalies = find_anomalies(ledger, home)
    for anomaly in anomalies:
        if anomaly.get("level") in {"warning", "critical"}:
            blockers.append({"project_id": anomaly.get("project_id"), "text": anomaly.get("message"),
                             "confidence": "observed", "evidence": anomaly.get("evidence", [])})
    return {"generator": "deterministic", "target_date": target.isoformat(),
            "today_results": results[:10], "yesterday_plan_closure": _plan_closure(ledger, target),
            "current_blockers": blockers[:20], "next_actions": next_actions[:10],
            "anomalies": anomalies}
