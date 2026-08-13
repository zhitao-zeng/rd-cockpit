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


def bounds(period: str, target: date) -> tuple[datetime, datetime, str]:
    if period == "week":
        start_date = target - timedelta(days=target.weekday())
        end_date = start_date + timedelta(days=7)
        label = f"{start_date.isocalendar().year}-W{start_date.isocalendar().week:02d}"
    elif period == "month":
        start_date = target.replace(day=1)
        end_date = (start_date.replace(day=28) + timedelta(days=4)).replace(day=1)
        label = start_date.strftime("%Y-%m")
    else:
        start_date, end_date, label = target, target + timedelta(days=1), target.isoformat()
    return (
        datetime.combine(start_date, datetime.min.time(), LOCAL_TZ).astimezone(timezone.utc),
        datetime.combine(end_date, datetime.min.time(), LOCAL_TZ).astimezone(timezone.utc),
        label,
    )


def _payload(row: Any) -> dict[str, Any]:
    try: return json.loads(row["payload_json"])
    except (TypeError, json.JSONDecodeError): return {}


def _duration(events: list[Any], start_type: str, end_type: str) -> float:
    starts: dict[str, datetime] = {}
    seconds = 0.0
    for event in events:
        key = _payload(event).get("run_id") or event["session_id"]
        if not key: continue
        try: at = datetime.fromisoformat(event["occurred_at"])
        except ValueError: continue
        if event["event_type"] == start_type: starts[key] = at
        elif event["event_type"] == end_type and key in starts:
            seconds += max(0.0, (at - starts.pop(key)).total_seconds())
    return round(seconds / 3600, 3)


def _activity_duration(events: list[Any]) -> float:
    seconds = 0.0
    for event in events:
        payload = _payload(event)
        try:
            start = datetime.fromisoformat(str(payload.get("start", event["occurred_at"])))
            end = datetime.fromisoformat(str(payload["end"]))
            seconds += max(0.0, (end - start).total_seconds())
        except (KeyError, TypeError, ValueError):
            continue
    return round(seconds / 3600, 3)


def build_period_facts(ledger: Ledger, period: str, target: date) -> dict[str, Any]:
    start, end, label = bounds(period, target)
    rows = ledger.events(since=start.isoformat(), until=end.isoformat())
    counts = Counter(row["event_type"] for row in rows)
    projects: dict[str, dict[str, Any]] = defaultdict(lambda: {"events": 0, "types": Counter(), "commits": set()})
    project_sequence: list[str] = []
    for row in rows:
        pid = row["project_id"] or "unassigned"
        projects[pid]["events"] += 1; projects[pid]["types"][row["event_type"]] += 1
        if row["commit_sha"]: projects[pid]["commits"].add(row["commit_sha"])
        if not project_sequence or project_sequence[-1] != pid: project_sequence.append(pid)
    tests = {"passed": sum(row["status"] == "passed" for row in rows if row["event_type"] in {"test_completed", "benchmark_completed"}),
             "failed": sum(row["status"] == "failed" for row in rows if row["event_type"] in {"test_completed", "benchmark_completed", "command_failed"})}
    decisions = [row for row in rows if row["event_type"].startswith("decision_")]
    experiments = [row for row in rows if row["event_type"] in {"experiment_completed", "experiment_failed"}]
    sessions = [row for row in rows if row["event_type"] in {"agent_session_started", "agent_session_completed"}]
    human_activity = [row for row in rows if row["event_type"] == "human_activity_interval"]
    trend: dict[str, dict[str, Any]] = {}
    for row in rows:
        try: day = datetime.fromisoformat(row["occurred_at"]).astimezone(LOCAL_TZ).date().isoformat()
        except (TypeError, ValueError): day = row["occurred_at"][:10]
        bucket = trend.setdefault(day, {"events": 0, "projects": set(), "tests_passed": 0,
                                        "tests_failed": 0, "experiments": 0, "decisions": 0})
        bucket["events"] += 1
        if row["project_id"]: bucket["projects"].add(row["project_id"])
        if row["event_type"] in {"test_completed", "benchmark_completed"}:
            bucket["tests_passed" if row["status"] == "passed" else "tests_failed"] += 1
        if row["event_type"] in {"experiment_completed", "experiment_failed"}: bucket["experiments"] += 1
        if row["event_type"].startswith("decision_"): bucket["decisions"] += 1
    return {
        "schema_version": 1, "period": period, "label": label, "generated_at": utc_now(),
        "time": {"human_active_hours": _activity_duration(human_activity),
                 "agent_hours": _duration(sessions, "agent_session_started", "agent_session_completed"),
                 "command_hours": _duration(rows, "command_started", "command_completed"),
                 "context_switches": max(0, len(project_sequence) - 1),
                 "active_span_hours": round((end - start).total_seconds() / 3600, 3)},
        "outputs": {"events": len(rows), "commits": len({row["commit_sha"] for row in rows if row["commit_sha"]}),
                    "tests": tests, "experiments": len(experiments), "decisions": len(decisions),
                    "completed_milestones": counts.get("milestone_completed", 0)},
        "projects": {pid: {"events": value["events"], "types": dict(value["types"]), "commits": sorted(value["commits"])}
                     for pid, value in projects.items()},
        "trend": [{**value, "date": day, "projects": sorted(value["projects"])} for day, value in sorted(trend.items())],
        "unfinished": [{"project_id": row["project_id"], "text": _payload(row).get("text"),
                        "status": _payload(row).get("status") or row["status"]}
                       for row in rows if row["event_type"] == "plan_closed" and _payload(row).get("status") not in {"completed", "cancelled"}],
        "events": [{"event_id": row["event_id"], "occurred_at": row["occurred_at"], "type": row["event_type"],
                    "project_id": row["project_id"], "status": row["status"]} for row in rows],
    }


def markdown(facts: dict[str, Any]) -> str:
    out = [f"# R&D {facts['period'].title()} Report — {facts['label']}", "", "## Summary", "",
           f"- Events: {facts['outputs']['events']}", f"- Commits: {facts['outputs']['commits']}",
           f"- Tests: {facts['outputs']['tests']['passed']} passed / {facts['outputs']['tests']['failed']} failed",
           f"- Experiments: {facts['outputs']['experiments']}", f"- Decisions: {facts['outputs']['decisions']}",
           f"- Human active hours: {facts['time']['human_active_hours']}",
           f"- Agent hours: {facts['time']['agent_hours']}", f"- Command hours: {facts['time']['command_hours']}",
           f"- Context switches: {facts['time']['context_switches']}", "", "## Projects", "",
           "| Project | Events | Commits | |", "|---|---:|---:|---|"]
    for pid, value in sorted(facts["projects"].items()):
        out.append(f"| {pid} | {value['events']} | {len(value['commits'])} | {', '.join(f'{k}={v}' for k,v in sorted(value['types'].items()))} |")
    if facts.get("trend"):
        out += ["", "## Daily trend", "", "| Date | Events | Projects | Tests | Experiments | Decisions |",
                "|---|---:|---:|---:|---:|---:|"]
        for item in facts["trend"]:
            tests = f"{item['tests_passed']}✓ / {item['tests_failed']}✗"
            out.append(f"| {item['date']} | {item['events']} | {len(item['projects'])} | {tests} | {item['experiments']} | {item['decisions']} |")
    if facts["unfinished"]:
        out += ["", "## Unfinished", ""]
        out.extend(f"- {item['project_id']}: {item['text']} ({item['status']})" for item in facts["unfinished"])
    return "\n".join(out) + "\n"


def write_period_report(ledger: Ledger, home: Path, period: str, target: date) -> dict[str, str]:
    facts = build_period_facts(ledger, period, target)
    output_dir = home / "reports" / period
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = facts["label"]
    json_path, md_path, html_path = output_dir / f"{stem}.json", output_dir / f"{stem}.md", output_dir / f"{stem}.html"
    md = markdown(facts)
    json_path.write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(md, encoding="utf-8")
    html_path.write_text(f"<!doctype html><meta charset='utf-8'><title>{html.escape(stem)}</title><pre>{html.escape(md)}</pre>", encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path), "html": str(html_path)}
