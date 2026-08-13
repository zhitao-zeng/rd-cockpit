from __future__ import annotations

import html
import json
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .ledger import Ledger, utc_now


LOCAL_TZ = ZoneInfo("Asia/Shanghai")


def _local_date(value: str) -> str:
    try:
        return datetime.fromisoformat(value).astimezone(LOCAL_TZ).date().isoformat()
    except ValueError:
        return value[:10]


def build_facts(ledger: Ledger, report_date: date) -> dict[str, Any]:
    start = datetime.combine(report_date, datetime.min.time(), LOCAL_TZ).astimezone(timezone.utc).isoformat()
    end = datetime.combine(report_date + timedelta(days=1), datetime.min.time(), LOCAL_TZ).astimezone(timezone.utc).isoformat()
    rows = ledger.events(since=start, until=end)
    counts = Counter(row["event_type"] for row in rows)
    projects: dict[str, dict[str, Any]] = defaultdict(lambda: {"events": 0, "types": Counter(), "commits": [], "results": []})
    tests = {"passed": 0, "failed": 0}
    anomalies: list[dict[str, Any]] = []
    plans: list[dict[str, Any]] = []
    highlights: list[dict[str, Any]] = []
    sessions = [row for row in rows if row["event_type"] in {"agent_session_started", "agent_session_completed"}]
    from .period import _activity_duration, _duration
    human_activity = [row for row in rows if row["event_type"] == "human_activity_interval"]
    for row in rows:
        project = row["project_id"] or "unassigned"
        item = projects[project]
        item["events"] += 1
        item["types"][row["event_type"]] += 1
        if row["event_type"] == "git_snapshot" and row["commit_sha"]:
            item["commits"].append(row["commit_sha"])
        if row["event_type"] in {"test_completed", "test_failed"}:
            tests["passed" if row["status"] == "passed" else "failed"] += 1
        if row["event_type"] == "orphan_resource_detected":
            anomalies.append(json.loads(row["payload_json"]))
        if row["event_type"] in {"plan_created", "plan_closed"}:
            payload = json.loads(row["payload_json"])
            plans.append({"event_id": row["event_id"], "project_id": row["project_id"],
                          "type": row["event_type"], "text": payload.get("text"),
                          "status": payload.get("status") or row["status"], "reason": payload.get("reason")})
        if row["event_type"] in {"test_completed", "benchmark_completed", "experiment_completed", "decision_adopted",
                                  "decision_confirmed", "milestone_completed"} and row["status"] in {"passed", "adopted", "confirmed", "completed", None}:
            payload = json.loads(row["payload_json"])
            highlights.append({"event_id": row["event_id"], "project_id": row["project_id"],
                               "type": row["event_type"], "status": row["status"],
                               "detail": payload.get("result") or payload.get("text") or payload.get("stage") or payload.get("command")})
    project_facts = {}
    for key, value in projects.items():
        project_facts[key] = {"events": value["events"], "types": dict(value["types"]),
                              "commits": sorted(set(value["commits"])), "results": value["results"]}
    return {
        "schema_version": 1, "report_date": report_date.isoformat(), "generated_at": utc_now(),
        "summary": {"events": len(rows), "event_types": dict(counts), "tests": tests,
                     "projects": project_facts, "resource_anomalies": anomalies, "plans": plans,
                     "highlights": highlights[:20],
                     "time": {"human_active_hours": _activity_duration(human_activity),
                              "agent_hours": _duration(sessions, "agent_session_started", "agent_session_completed"),
                              "command_hours": _duration(rows, "command_started", "command_completed"),
                              "compute_hours": None, "compute_hours_note": "需要 GPU 任务开始/结束事件后才能可靠计算"}},
        "events": [{"event_id": r["event_id"], "occurred_at": r["occurred_at"], "type": r["event_type"],
                    "project_id": r["project_id"], "status": r["status"], "source": r["source"],
                    "commit_sha": r["commit_sha"], "provenance": r["provenance"]} for r in rows],
    }


def markdown(facts: dict[str, Any]) -> str:
    summary = facts["summary"]
    lines = [f"# R&D Daily Report — {facts['report_date']}", "", "## Facts", "",
             f"- Events: {summary['events']}",
             f"- Tests: {summary['tests']['passed']} passed / {summary['tests']['failed']} failed",
             f"- Human active hours: {summary['time']['human_active_hours']}",
             f"- Agent hours: {summary['time']['agent_hours']}",
             f"- Command hours: {summary['time']['command_hours']}", ""]
    if summary.get("highlights"):
        lines += ["## Highlights", ""]
        lines.extend(f"- `{item['project_id'] or 'unassigned'}` {item['type']}: {item['detail'] or item['status'] or ''}"
                     for item in summary["highlights"])
    semantic = facts.get("semantic", {})
    if semantic.get("yesterday_plan_closure"):
        lines += ["", "## Yesterday plan closure", ""]
        lines.extend(f"- {item['plan']} — **{item['status']}**{': ' + item['reason'] if item.get('reason') else ''}"
                     for item in semantic["yesterday_plan_closure"])
    if semantic.get("current_blockers"):
        lines += ["", "## Current blockers", ""]
        lines.extend(f"- `{item.get('project_id') or 'unassigned'}` {item['text']}"
                     for item in semantic["current_blockers"])
    if semantic.get("next_actions"):
        lines += ["", "## Next actions", ""]
        lines.extend(f"- `{item.get('project_id') or 'unassigned'}` {item['action']} — {item['reason']}"
                     for item in semantic["next_actions"])
    lines += ["## Projects", "", "| Project | Events | Commits | Event types |", "|---|---:|---:|---|"]
    for project, value in sorted(summary["projects"].items()):
        types = ", ".join(f"{k}={v}" for k, v in sorted(value["types"].items()))
        lines.append(f"| {project} | {value['events']} | {len(value['commits'])} | {types} |")
    if summary["resource_anomalies"]:
        lines += ["", "## Resource anomalies", ""]
        lines.extend(f"- {json.dumps(item, ensure_ascii=False)}" for item in summary["resource_anomalies"])
    if summary.get("anomalies"):
        lines += ["", "## Detected anomalies", ""]
        for item in summary["anomalies"]:
            lines.append(f"- **{item['level']}** `{item['code']}`: {item['message']}")
    if summary["plans"]:
        lines += ["", "## Plan closure", ""]
        for plan in summary["plans"]:
            lines.append(f"- `{plan['type']}` {plan['project_id']}: {plan['text']} ({plan['status'] or 'open'})")
    lines += ["", "## Event timeline", ""]
    lines.extend(f"- `{event['occurred_at']}` `{event['type']}` `{event['project_id'] or 'unassigned'}` `{event['status'] or ''}`" for event in facts["events"])
    return "\n".join(lines) + "\n"


def write_report(ledger: Ledger, home: Path, report_date: date, *, use_llm: bool = False) -> dict[str, str]:
    facts = build_facts(ledger, report_date)
    from .semantic import build_semantic_facts
    facts["semantic"] = build_semantic_facts(ledger, home, report_date)
    if use_llm:
        from .llm import enrich_semantic
        facts["semantic"]["llm"] = enrich_semantic(facts["semantic"])
    # Anomalies are a derived view and are intentionally recomputed at report time.
    from .anomalies import find_anomalies
    facts["summary"]["anomalies"] = find_anomalies(ledger, home)
    output_dir = home / "reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = report_date.isoformat()
    json_path, md_path, html_path = output_dir / f"{stem}.json", output_dir / f"{stem}.md", output_dir / f"{stem}.html"
    md = markdown(facts)
    json_path.write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(md, encoding="utf-8")
    html_path.write_text(f"<!doctype html><meta charset='utf-8'><title>{html.escape(stem)}</title><pre>{html.escape(md)}</pre>", encoding="utf-8")
    ledger.db.execute(
        """INSERT INTO report_runs
           (report_id,report_date,generated_at,output_json,output_markdown,output_html)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(report_id) DO UPDATE SET
             generated_at=excluded.generated_at,
             output_json=excluded.output_json,
             output_markdown=excluded.output_markdown,
             output_html=excluded.output_html""",
        (f"report_{stem}", stem, utc_now(), str(json_path), str(md_path), str(html_path)),
    )
    ledger.db.commit()
    return {"json": str(json_path), "markdown": str(md_path), "html": str(html_path)}
