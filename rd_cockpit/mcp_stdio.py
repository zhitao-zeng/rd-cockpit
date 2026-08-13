"""Small dependency-free MCP stdio tool adapter.

It implements the read-only subset needed by an agent: initialize, tools/list,
tools/call, and ping. Every tool result is derived from the SQLite ledger and
is returned with both text and structured JSON content.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .anomalies import find_anomalies
from .config import load_config
from .ledger import Ledger
from .period import build_period_facts
from .report import build_facts
from .semantic import build_semantic_facts
from .state import build_state, state_dict
from .sessions import session_views


TOOL_DEFS = [
    {"name": "rd_status", "description": "Read current state for all configured projects or one project.",
     "inputSchema": {"type": "object", "properties": {"project": {"type": "string"}}}},
    {"name": "rd_resume", "description": "Prepare a project handoff for a new agent session.",
     "inputSchema": {"type": "object", "required": ["project"], "properties": {"project": {"type": "string"}}}},
    {"name": "rd_timeline", "description": "Read a project event timeline with evidence references.",
     "inputSchema": {"type": "object", "required": ["project"], "properties": {"project": {"type": "string"}}}},
    {"name": "rd_anomalies", "description": "Find stale verification, untested changes, and resource anomalies.",
     "inputSchema": {"type": "object", "properties": {"project": {"type": "string"}, "stale_days": {"type": "integer"}}}},
    {"name": "rd_sessions", "description": "List agent sessions and handoffs.",
     "inputSchema": {"type": "object", "properties": {"project": {"type": "string"}, "active": {"type": "boolean"}}}},
    {"name": "rd_since", "description": "List events since an ISO date, today, yesterday, or commit:<sha>.",
     "inputSchema": {"type": "object", "required": ["query"], "properties": {"query": {"type": "string"}, "project": {"type": "string"}}}},
    {"name": "rd_why", "description": "Search decisions and experiments by text.",
     "inputSchema": {"type": "object", "required": ["query"], "properties": {"query": {"type": "string"}, "project": {"type": "string"}}}},
    {"name": "rd_stats", "description": "Return a week or month of derived activity facts.",
     "inputSchema": {"type": "object", "properties": {"period": {"type": "string", "enum": ["week", "month"]}, "date": {"type": "string"}}}},
    {"name": "rd_next", "description": "Suggest evidence-based next verification actions.",
     "inputSchema": {"type": "object", "properties": {"project": {"type": "string"}, "limit": {"type": "integer"}}}},
    {"name": "rd_search", "description": "Search ledger facts, decisions, metrics, commits, and evidence.",
     "inputSchema": {"type": "object", "required": ["query"], "properties": {"query": {"type": "string"}, "project": {"type": "string"}, "limit": {"type": "integer"}}}},
    {"name": "rd_daily", "description": "Return deterministic daily facts, plan closure, blockers, and next actions.",
     "inputSchema": {"type": "object", "properties": {"date": {"type": "string"}}}},
    {"name": "rd_insights", "description": "Run a deterministic fancy insight projection.",
     "inputSchema": {"type": "object", "required": ["kind"], "properties": {"kind": {"type": "string"}, "project": {"type": "string"}, "query": {"type": "string"}}}},
    {"name": "rd_advanced", "description": "Run advanced evidence projections such as debt, health, risk, and reproducibility.",
     "inputSchema": {"type": "object", "required": ["kind"], "properties": {"kind": {"type": "string"}, "project": {"type": "string"}, "query": {"type": "string"}}}},
]


def _read_events(ledger: Ledger, project: str | None = None) -> list[dict[str, Any]]:
    output = []
    for row in ledger.events(project_id=project):
        item = {"event_id": row["event_id"], "occurred_at": row["occurred_at"], "type": row["event_type"],
                "status": row["status"], "source": row["source"], "commit": row["commit_sha"],
                "provenance": row["provenance"], "payload": json.loads(row["payload_json"])}
        item["evidence"] = [dict(e) for e in ledger.event_evidence(row["event_id"])]
        output.append(item)
    return output


def _sessions(ledger: Ledger, project: str | None, active: bool) -> list[dict[str, Any]]:
    return session_views(ledger, project, active=active)


def _dispatch(name: str, arguments: dict[str, Any], home: Path) -> Any:
    ledger = Ledger(home / ".rd-cockpit" / "events.sqlite")
    try:
        if name == "rd_status":
            config = load_config(home / "config" / "projects.yaml")
            ids = [arguments["project"]] if arguments.get("project") else sorted(config.get("projects", {}))
            return {pid: state_dict(build_state(ledger, home, pid)) for pid in ids}
        if name == "rd_resume":
            return state_dict(build_state(ledger, home, arguments["project"]))
        if name == "rd_timeline":
            return _read_events(ledger, arguments["project"])
        if name == "rd_anomalies":
            return find_anomalies(ledger, home, project_id=arguments.get("project"), stale_days=int(arguments.get("stale_days", 2)))
        if name == "rd_sessions":
            return _sessions(ledger, arguments.get("project"), bool(arguments.get("active", False)))
        if name == "rd_since":
            query = str(arguments["query"]).lower(); rows = ledger.events(project_id=arguments.get("project"))
            if query.startswith("commit:"):
                matches = [row for row in rows if row["commit_sha"] and row["commit_sha"].startswith(query.split(":", 1)[1])]
                since_at = matches[0]["occurred_at"] if matches else None
                rows = [row for row in rows if since_at and row["occurred_at"] >= since_at]
            else:
                from datetime import datetime, timedelta, timezone
                if query in {"today", "今天"}: start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
                elif query in {"yesterday", "昨天"}: start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
                else: start = datetime.fromisoformat(query).replace(tzinfo=timezone.utc)
                rows = [row for row in rows if row["occurred_at"] >= start.isoformat()]
            return [{"event_id": row["event_id"], "occurred_at": row["occurred_at"], "type": row["event_type"],
                     "project_id": row["project_id"], "status": row["status"], "commit": row["commit_sha"]} for row in rows]
        if name == "rd_why":
            query = str(arguments["query"]).lower(); matches = []
            for item in _read_events(ledger, arguments.get("project")):
                if not item["type"].startswith("decision_") and not item["type"].startswith("experiment_"): continue
                if query in json.dumps(item["payload"], ensure_ascii=False).lower(): matches.append(item)
            return matches
        if name == "rd_stats":
            from datetime import date
            target = date.fromisoformat(arguments["date"]) if arguments.get("date") else date.today()
            return build_period_facts(ledger, arguments.get("period", "week"), target)
        if name == "rd_next":
            config = load_config(home / "config" / "projects.yaml")
            ids = [arguments["project"]] if arguments.get("project") else sorted(config.get("projects", {}))
            suggestions = []
            for pid in ids:
                state = state_dict(build_state(ledger, home, pid))
                priority = str(config.get("projects", {}).get(pid, {}).get("priority", "P3"))
                rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}.get(priority, 4)
                stale = next(((stage, value) for stage, value in state["verification"].items()
                               if value["status"] == "stale"), None)
                if stale:
                    stage, value = stale
                    suggestions.append({"rank": rank, "project": pid, "action": f"重新验证 {stage}",
                                        "reason": value.get("stale_reason", "验证依赖发生变化"),
                                        "basis": [value.get("event_id")]})
                    continue
                pending = [stage for stage, value in state["verification"].items() if value["status"] == "pending"]
                if pending:
                    suggestions.append({"rank": rank, "project": pid, "action": f"推进验证阶段 {pending[0]}",
                                        "reason": "当前漏斗中最早的未完成阶段", "basis": [state.get("head")]})
                elif state["blockers"]:
                    suggestions.append({"rank": rank, "project": pid, "action": f"处理阻塞：{state['blockers'][0]}",
                                        "reason": "阻塞项优先于新增实验", "basis": []})
            suggestions.sort(key=lambda item: (item["rank"], item["project"]))
            return suggestions[:int(arguments.get("limit", 5))]
        if name == "rd_search":
            query = str(arguments["query"]).lower(); matches = []
            for item in _read_events(ledger, arguments.get("project")):
                haystack = json.dumps(item, ensure_ascii=False).lower()
                if query in haystack:
                    matches.append(item)
            return matches[-int(arguments.get("limit", 50)):]
        if name == "rd_daily":
            from datetime import date
            target = date.fromisoformat(arguments["date"]) if arguments.get("date") else date.today()
            facts = build_facts(ledger, target)
            facts["semantic"] = build_semantic_facts(ledger, home, target)
            return facts
        if name == "rd_insights":
            from . import insights as views
            kind = str(arguments["kind"]); project = arguments.get("project"); query = arguments.get("query") or ""
            if kind == "lineage": return views.parameter_lineage(ledger, project)
            if kind == "graph": return views.decision_graph(ledger, project)
            if kind == "conflicts": return views.decision_conflicts(ledger, project)
            if kind == "freshness": return views.decision_freshness(ledger, project)
            if kind == "efficiency": return views.experiment_efficiency(ledger, project)
            if kind == "gpu": return views.gpu_report(ledger)
            if kind == "coverage": return views.evidence_coverage(ledger, project)
            if kind == "reproducibility": return views.reproducibility(ledger, project)
            if kind == "impact": return views.change_impact(ledger, home, project) if project else {"error": "project required"}
            if kind == "context": return views.context_pack(ledger, home, project) if project else {"error": "project required"}
            if kind == "suggest": return views.suggest_experiments(ledger, project)
            if kind == "counterfactual": return views.counterfactual(ledger, project, query) if project else {"error": "project required"}
            if kind == "switches": return views.context_switch_analysis(ledger)
            if kind == "sessions": return views.session_efficiency(ledger, project)
            if kind == "replay":
                from datetime import date
                return views.today_replay(ledger, home, date.fromisoformat(query) if query else date.today())
            if kind == "wrapped":
                from datetime import date
                return views.research_wrapped(ledger, home, "month", date.fromisoformat(query) if query else date.today())
            if kind == "resource-cost": return views.resource_cost(ledger, project)
            if kind == "changed": return views.what_changed(ledger, query or "today", project)
            if kind == "twin": return views.digital_twin(ledger, home)
            raise KeyError(f"unknown insight kind: {kind}")
        if name == "rd_advanced":
            from . import advanced as views
            kind = str(arguments["kind"]); project = arguments.get("project"); query = arguments.get("query")
            if kind == "debt": return views.research_debt(ledger, home, project)
            if kind == "confidence": return views.claim_confidence(ledger, project)
            if kind == "hypotheses": return views.hypotheses(ledger, project)
            if kind == "information-gain": return views.information_gain(ledger, project)
            if kind == "budget": return views.budget_roi(ledger, project)
            if kind == "metric-lineage": return views.metric_lineage(ledger, project)
            if kind == "fingerprints": return views.fingerprints(ledger, project)
            if kind == "health": return views.health(ledger, home, project) if project else {"error": "project required"}
            if kind == "risk": return views.risk_radar(ledger, home, project) if project else {"error": "project required"}
            if kind == "why-not-done": return views.why_not_done(ledger, home, project) if project else {"error": "project required"}
            if kind == "attention": return views.attention_budget(ledger, project)
            if kind == "rhythm": return views.rhythm(ledger, project)
            if kind == "handoff-quality": return views.handoff_quality(ledger, project)
            if kind == "agent-blindspots": return views.agent_blindspots(ledger, project)
            if kind == "memory": return views.memory_freshness(ledger, home, project) if project else {"error": "project required"}
            if kind == "refresh": return views.refresh(ledger, home, project, record=False) if project else {"error": "project required"}
            if kind == "knowledge": return views.knowledge_cards(ledger, project)
            if kind == "brief": return views.project_brief(ledger, home, project) if project else {"error": "project required"}
            if kind == "context-pack":
                from .insights import context_pack
                return context_pack(ledger, home, project) if project else {"error": "project required"}
            if kind == "achievements": return views.achievements(ledger, home, project)
            if kind == "card":
                from datetime import date
                return views.daily_card(ledger, home, date.fromisoformat(query) if query else date.today())
            if kind == "map": return views.research_map(ledger, home)
            if kind == "dont": return views.dont(ledger, home, project)
            if kind == "countdown": return views.decision_countdown(ledger, project)
            raise KeyError(f"unknown advanced kind: {kind}")
        raise KeyError(f"unknown tool: {name}")
    finally:
        ledger.close()


def handle_message(message: dict[str, Any], home: Path) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = message.get("id")
    if method in {"notifications/initialized", "notifications/cancelled"}:
        return None
    if method == "initialize":
        params = message.get("params") or {}
        return {"jsonrpc": "2.0", "id": request_id, "result": {
            "protocolVersion": params.get("protocolVersion", "2024-11-05"),
            "capabilities": {"tools": {}}, "serverInfo": {"name": "rd-cockpit", "version": "0.1.0"},
        }}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOL_DEFS}}
    if method == "tools/call":
        params = message.get("params") or {}; name = params.get("name"); args = params.get("arguments") or {}
        try:
            value = _dispatch(name, args, home)
            text = json.dumps(value, ensure_ascii=False, indent=2)
            return {"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": text}],
                    "structuredContent": {"result": value}, "isError": False}}
        except Exception as exc:
            return {"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": str(exc)}], "isError": True}}
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"method not found: {method}"}}


def run_stdio(home: Path) -> int:
    for line in sys.stdin:
        if not line.strip(): continue
        try: message = json.loads(line)
        except json.JSONDecodeError as exc:
            print(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}), flush=True); continue
        response = handle_message(message, home)
        if response is not None: print(json.dumps(response, ensure_ascii=False), flush=True)
    return 0
