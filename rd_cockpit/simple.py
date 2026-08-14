"""User-facing research records built from the internal event ledger."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config import load_config
from .ledger import Ledger
from .project_identity import (
    canonical_project_id, canonical_project_ids, canonicalize_report,
    visible_project_names,
)
from .state import build_state, state_dict

LOCAL_TZ = ZoneInfo("Asia/Shanghai")


def _payload(row: Any) -> dict[str, Any]:
    try:
        value = json.loads(row["payload_json"])
        return value if isinstance(value, dict) else {}
    except (TypeError, json.JSONDecodeError): return {}


def _bounds(target: date) -> tuple[str, str]:
    start = datetime.combine(target, datetime.min.time(), LOCAL_TZ).astimezone(timezone.utc)
    end = start + timedelta(days=1)
    return start.isoformat(), end.isoformat()


def _human_work(row: Any) -> str | None:
    p = _payload(row); typ = row["event_type"]
    if typ == "agent_session_completed": return p.get("goal") or "完成一次 Agent 工作会话"
    if typ == "git_snapshot" and row["dirty"]: return "修改了项目代码（尚未提交）"
    if typ == "test_completed" and row["status"] == "passed": return "完成测试并通过"
    if typ == "benchmark_completed" and row["status"] == "passed": return "完成性能评测"
    if typ == "experiment_completed": return p.get("name") or "完成一次实验"
    if typ.startswith("decision_"): return p.get("text")
    if typ == "plan_closed": return p.get("text")
    return None


def _latest_usage_rows(rows: list[Any]) -> list[Any]:
    """Keep one aggregate per Agent session.

    Usage imports are append-only, so a growing transcript can create a newer
    aggregate for the same session.  User-facing totals must show the newest
    snapshot, not add every historical snapshot together.
    """
    latest: dict[tuple[str, str], Any] = {}
    for row in rows:
        if row["event_type"] != "agent_usage_observed":
            continue
        p = _payload(row)
        key = (str(p.get("agent") or row["source"]), str(p.get("session_id") or row["session_id"] or row["event_id"]))
        current = latest.get(key)
        if current is None or (row["ingested_at"], row["event_id"]) > (current["ingested_at"], current["event_id"]):
            latest[key] = row
    return list(latest.values())


def _current_usage_rows(
    ledger: Ledger, *, since_day: str | None = None, until_day: str | None = None,
) -> list[dict[str, Any]]:
    clauses, args = ["1=1"], []
    if since_day:
        clauses.append("activity_day>=?"); args.append(since_day)
    if until_day:
        clauses.append("activity_day<?"); args.append(until_day)
    rows = ledger.db.execute(
        f"SELECT * FROM current_session_usage WHERE {' AND '.join(clauses)}", args,
    ).fetchall()
    return [{
        "event_id": f"live:{row['agent']}:{row['session_id']}",
        "event_type": "agent_usage_observed", "source": row["source"],
        "project_id": row["project_id"], "session_id": row["session_id"],
        "occurred_at": row["occurred_at"], "ingested_at": row["updated_at"],
        "payload_json": row["payload_json"], "status": "observed",
        "dirty": None, "commit_sha": None,
    } for row in rows]


def _usage(rows: list[Any]) -> dict[str, Any]:
    agents: dict[str, dict[str, int]] = defaultdict(lambda: {"sessions": 0, "input_tokens": 0, "output_tokens": 0,
                                                              "cached_tokens": 0, "reasoning_tokens": 0, "total_tokens": 0})
    for row in _latest_usage_rows(rows):
        p = _payload(row); item = agents[p.get("agent", row["source"])]
        item["sessions"] += 1
        for key in ("input_tokens", "output_tokens", "cached_tokens", "reasoning_tokens", "total_tokens"):
            item[key] += int(p.get(key, 0) or 0)
    return {"available": bool(agents), "agents": dict(agents),
            "total_tokens": sum(item["total_tokens"] for item in agents.values()),
            "note": None if agents else "尚未同步 Codex / Claude Code Token 用量"}


def _usage_work(rows: list[Any], *, limit: int = 16) -> list[str]:
    # Raw user prompts are not research summaries.  Keep this compatibility
    # field empty; the readable content now comes from the existing Markdown
    # daily report through /simple/report.
    return []


def daily_records(ledger: Ledger, home: Path, target: date, project: str | None = None) -> dict[str, Any]:
    start, end = _bounds(target); all_rows = ledger.events(since=start, until=end)
    all_rows.extend(_current_usage_rows(
        ledger, since_day=target.isoformat(), until_day=(target + timedelta(days=1)).isoformat(),
    ))
    all_rows = [row for row in all_rows if row["event_type"] != "agent_usage_observed"] + _latest_usage_rows(all_rows)
    config = load_config(home / "config" / "projects.yaml")
    ids = [project] if project else sorted(config.get("projects", {}))
    records = []
    for pid in ids:
        project_rows = [row for row in all_rows if row["project_id"] == pid]
        visible_rows = [row for row in project_rows if row["event_type"] != "agent_usage_observed"] + _latest_usage_rows(project_rows)
        state = state_dict(build_state(ledger, home, pid)); work = []
        for row in visible_rows:
            if row["event_type"] == "agent_usage_observed": continue
            text = _human_work(row)
            if text and text not in work: work.append(text)
        results = []
        for row in visible_rows:
            p = _payload(row)
            if row["event_type"] in {"experiment_completed", "benchmark_completed"} and row["status"] == "passed":
                results.append(str(p.get("result") or p.get("metrics") or p.get("name") or "验证通过"))
            elif row["event_type"].startswith("decision_") and p.get("text"): results.append(str(p["text"]))
        problems = list(state["blockers"])
        problems.extend(str(_payload(row).get("error") or _payload(row).get("result") or row["event_type"])
                        for row in project_rows if row["status"] == "failed")
        next_items = list(state["remaining"])
        pending = next((stage for stage, value in state["verification"].items() if value["status"] in {"pending", "stale"}), None)
        if pending and not next_items: next_items.append(f"继续完成 {pending} 阶段")
        records.append({"date": target.isoformat(), "project_id": pid, "project_name": state["name"],
                        "goal": state["goal"], "work": work, "results": results,
                        "problems": problems, "next": next_items, "usage": _usage(project_rows),
                        "has_activity": bool(visible_rows), "source_count": len(visible_rows)})
    unassigned = [row for row in all_rows if row["project_id"] is None]
    return {"date": target.isoformat(), "records": records, "unassigned_usage": _usage(unassigned),
            "unassigned_work": _usage_work(unassigned),
            "explanation": "这是实时补充统计。正式研究总结以 /simple/report 读取的现有 Markdown 日报为准；Agent 对话原文不会作为工作摘要。"}


def analytics(ledger: Ledger, home: Path, *, days: int = 30) -> dict[str, Any]:
    end_date = date.today() + timedelta(days=1); start_date = end_date - timedelta(days=days)
    start, _ = _bounds(start_date); _, end = _bounds(end_date - timedelta(days=1))
    rows = ledger.events(since=start, until=end); buckets: dict[tuple[str, str], dict[str, Any]] = {}
    rows.extend(_current_usage_rows(
        ledger, since_day=start_date.isoformat(), until_day=end_date.isoformat(),
    ))
    visible_rows = [row for row in rows if row["event_type"] != "agent_usage_observed"] + _latest_usage_rows(rows)
    for row in visible_rows:
        try: day = datetime.fromisoformat(row["occurred_at"]).astimezone(LOCAL_TZ).date().isoformat()
        except (TypeError, ValueError): continue
        pid = row["project_id"] or "unassigned"; key = (day, pid)
        item = buckets.setdefault(key, {"date": day, "project_id": pid, "activities": 0, "experiments": 0,
                                        "conclusions": 0, "tokens": 0, "codex_tokens": 0, "claude_tokens": 0})
        if _human_work(row): item["activities"] += 1
        if row["event_type"] in {"experiment_completed", "benchmark_completed"}: item["experiments"] += 1
        if row["event_type"].startswith("decision_"): item["conclusions"] += 1
        if row["event_type"] == "agent_usage_observed":
            p = _payload(row); tokens = int(p.get("total_tokens", 0) or 0); item["tokens"] += tokens
            if p.get("agent") == "codex": item["codex_tokens"] += tokens
            elif p.get("agent") == "claude_code": item["claude_tokens"] += tokens

    # The existing daily report is authoritative for human-readable work and
    # conclusion counts. Its token table also wins over reconstructed usage for
    # that date, avoiding double counting multiple append-only snapshots.
    from .daily_source import iter_reports
    from .daily_supplement import available_supplement_dates, load_supplement
    report_dates: set[str] = set()
    for report in iter_reports(since=start_date.isoformat(), cache_home=home):
        day = report.get("date")
        if not day or day >= end_date.isoformat():
            continue
        report_dates.add(day)
        for (bucket_day, _), item in buckets.items():
            if bucket_day == day:
                item["activities"] = 0
                item["conclusions"] = 0
                item["experiments"] = 0
        from .daily_source import _project_ids
        report_claims: set[tuple[str, str, str]] = set()
        report_claim_texts: list[tuple[str, str, str]] = []
        for kind, texts in (("结论", report.get("knowledge") or []),
                            ("决策", report.get("decisions") or [])):
            for text in texts:
                project_ids = _claim_project_ids(text, _project_ids, report) or ["unassigned"]
                pid = project_ids[0]
                claim_key = (pid, kind, _claim_key(text))
                if claim_key in report_claims:
                    continue
                report_claims.add(claim_key)
                report_claim_texts.append((pid, kind, text))
                item = buckets.setdefault((day, pid), {"date": day, "project_id": pid,
                                                        "activities": 0, "experiments": 0,
                                                        "conclusions": 0, "tokens": 0,
                                                        "codex_tokens": 0, "claude_tokens": 0})
                item["conclusions"] += 1
        for group in report["groups"]:
            for task in group["tasks"]:
                project_ids = task["project_ids"] or ["unassigned"]
                for pid in project_ids:
                    key = (day, pid)
                    item = buckets.setdefault(key, {"date": day, "project_id": pid, "activities": 0,
                                                     "experiments": 0, "conclusions": 0, "tokens": 0,
                                                     "codex_tokens": 0, "claude_tokens": 0})
                    item["activities"] += 1
                    for text in task.get("conclusions") or []:
                        claim_key = (pid, "结论", _claim_key(text))
                        if claim_key in report_claims or any(
                            existing_pid == pid and existing_kind == "结论"
                            and _same_claim(text, existing_text)
                            for existing_pid, existing_kind, existing_text in report_claim_texts
                        ):
                            continue
                        report_claims.add(claim_key)
                        report_claim_texts.append((pid, "结论", text))
                        item["conclusions"] += 1
                    experiment_text = f"{task['title']} {' '.join(task['did'])}".casefold()
                    if any(keyword in experiment_text for keyword in
                           ("实验", "评测", "测试", "验证", "对比", "跑分", "调参", "sweep", "benchmark", "标定")):
                        item["experiments"] += 1
        for (bucket_day, _), item in buckets.items():
            if bucket_day == day:
                item["tokens"] = 0
                item["codex_tokens"] = 0
                item["claude_tokens"] = 0
        supplement = report.get("_supplement") or load_supplement(day)
        if supplement["available"]:
            for project_usage in supplement["projects"]:
                pid = project_usage["project_id"]
                item = buckets.setdefault((day, pid), {"date": day, "project_id": pid,
                                                        "activities": 0, "experiments": 0,
                                                        "conclusions": 0, "tokens": 0,
                                                        "codex_tokens": 0, "claude_tokens": 0})
                item["tokens"] = project_usage["tokens"]
                item["codex_tokens"] = project_usage["codex_tokens"]
                item["claude_tokens"] = project_usage["claude_tokens"]
        else:
            token_item = buckets.setdefault((day, "unassigned"), {"date": day, "project_id": "unassigned",
                                                                    "activities": 0, "experiments": 0,
                                                                    "conclusions": 0, "tokens": 0,
                                                                    "codex_tokens": 0, "claude_tokens": 0})
            token_item["tokens"] = report["token"]["total_tokens"]
            for token_row in report["token"]["rows"]:
                value = str(token_row.get("总量", "0")).replace(",", "")
                amount = int(value) if value.isdigit() else 0
                source = str(token_row.get("来源", "")).casefold()
                if "codex" in source: token_item["codex_tokens"] += amount
                elif "claude" in source: token_item["claude_tokens"] += amount

    # Collector data may already exist before the Markdown report is generated
    # (for example during the hour before the 01:00 cron run). Use it for
    # project/token charts without pretending that a readable report exists.
    for day in available_supplement_dates():
        if day < start_date.isoformat() or day >= end_date.isoformat():
            continue
        supplement = load_supplement(day)
        if not supplement["available"]:
            continue
        for (bucket_day, _), item in buckets.items():
            if bucket_day == day:
                item["tokens"] = 0
                item["codex_tokens"] = 0
                item["claude_tokens"] = 0
        for project_usage in supplement["projects"]:
            pid = project_usage["project_id"]
            item = buckets.setdefault((day, pid), {"date": day, "project_id": pid,
                                                    "activities": 0, "experiments": 0,
                                                    "conclusions": 0, "tokens": 0,
                                                    "codex_tokens": 0, "claude_tokens": 0})
            item["tokens"] = project_usage["tokens"]
            item["codex_tokens"] = project_usage["codex_tokens"]
            item["claude_tokens"] = project_usage["claude_tokens"]
    # Raw collectors and older reports can contain retired heuristic buckets.
    # Merge them into the canonical registry before any chart sees them.
    canonical: dict[tuple[str, str], dict[str, Any]] = {}
    numeric_fields = (
        "activities", "experiments", "conclusions", "tokens",
        "codex_tokens", "claude_tokens",
    )
    for item in buckets.values():
        project_id = canonical_project_id(item.get("project_id"), home)
        target_item = canonical.setdefault((item["date"], project_id), {
            "date": item["date"], "project_id": project_id,
            **{field: 0 for field in numeric_fields},
        })
        for field in numeric_fields:
            target_item[field] += item.get(field, 0) or 0
    names = visible_project_names(home)
    daily = sorted(canonical.values(), key=lambda item: (item["date"], item["project_id"]))
    totals = {"tokens": sum(item["tokens"] for item in daily), "activities": sum(item["activities"] for item in daily),
              "experiments": sum(item["experiments"] for item in daily), "conclusions": sum(item["conclusions"] for item in daily)}
    activity_rows = ledger.db.execute(
        "SELECT activity_day,source,session_id,project_key,semantic_kind,completed_count,"
        "failed_count,total_duration_ms FROM agent_activity_rollups "
        "WHERE activity_day>=? AND activity_day<?",
        (start_date.isoformat(), end_date.isoformat()),
    ).fetchall()
    activity_daily: dict[str, dict[str, Any]] = {}
    activity_projects: dict[str, dict[str, Any]] = {}
    activity_sessions: set[tuple[str, str]] = set()
    for row in activity_rows:
        day = str(row["activity_day"])
        project_id = canonical_project_id(row["project_key"], home)
        completed = int(row["completed_count"] or 0)
        failed = int(row["failed_count"] or 0)
        duration_ms = int(row["total_duration_ms"] or 0)
        session_key = (str(row["source"]), str(row["session_id"]))
        activity_sessions.add(session_key)
        day_item = activity_daily.setdefault(day, {
            "date": day, "completed": 0, "failed": 0, "duration_minutes": 0.0,
            "sessions": set(),
        })
        project_item = activity_projects.setdefault(project_id, {
            "project_id": project_id, "name": names.get(project_id, project_id),
            "completed": 0, "failed": 0, "duration_minutes": 0.0, "sessions": set(),
        })
        for item in (day_item, project_item):
            item["completed"] += completed
            item["failed"] += failed
            item["duration_minutes"] += duration_ms / 60_000
            item["sessions"].add(session_key)

    def finish_activity(item: dict[str, Any]) -> dict[str, Any]:
        return {**item, "duration_minutes": round(float(item["duration_minutes"]), 1),
                "sessions": len(item["sessions"])}

    activity_completed = sum(int(row["completed_count"] or 0) for row in activity_rows)
    activity_failed = sum(int(row["failed_count"] or 0) for row in activity_rows)
    activity_duration = sum(int(row["total_duration_ms"] or 0) for row in activity_rows)
    agent_activity = {
        "totals": {
            "completed": activity_completed, "failed": activity_failed,
            "duration_minutes": round(activity_duration / 60_000, 1),
            "sessions": len(activity_sessions),
        },
        "daily": [finish_activity(item) for _, item in sorted(activity_daily.items())],
        "projects": sorted(
            (finish_activity(item) for item in activity_projects.values()),
            key=lambda item: (-(item["completed"] + item["failed"]), item["project_id"]),
        ),
        "explanation": "来自 Codex / Claude Code 生命周期 Hook 的聚合计数；不展示原始工具事件，也不把操作次数当作工作质量。",
    }
    return {"days": days, "daily": daily, "project_names": names, "totals": totals,
            "agent_activity": agent_activity,
            "token_available": totals["tokens"] > 0,
            "token_note": None if totals["tokens"] else "尚未同步 Agent Token；运行 rd usage-sync 后显示。"}


def _claim_parts(text: str) -> tuple[str, str | None]:
    """Use a short label only when the report explicitly supplied one."""
    value = str(text).strip()
    title, separator, detail = value.partition("：")
    if separator and title.strip() and detail.strip() and len(title.strip()) <= 48:
        return title.strip(), detail.strip()
    return value, None


def _claim_key(text: str) -> str:
    value = re.sub(r"[`*_#]", "", str(text)).casefold()
    return re.sub(r"[\s，。；：、,.!:;（）()\[\]{}<>‘’“”\"'—–-]+", "", value)


def _same_claim(left: str, right: str) -> bool:
    """Detect exact or containment duplicates without fuzzy guesswork."""
    a, b = _claim_key(left), _claim_key(right)
    if not a or not b:
        return False
    return a == b or (min(len(a), len(b)) >= 12 and (a in b or b in a))


def _claim_project_ids(text: str, classifier: Any, report: dict[str, Any] | None = None) -> list[str]:
    project_ids = list(classifier(text) or [])
    normalized = str(text).casefold()
    if project_ids == ["asr_other"] and any(
        marker in normalized for marker in ("embodied-ai asr", "embodied ai asr", "机器人指令识别")
    ):
        return ["asr"]
    # A claim about a video project may mention its ASR backend. The named
    # product is the claim's owner; generic ASR is merely a dependency.
    if "asr_other" in project_ids and len(project_ids) > 1:
        project_ids.remove("asr_other")
    if report:
        matched: set[str] = set()
        all_report_projects: set[str] = set()
        for group in report.get("groups") or []:
            for task in group.get("tasks") or []:
                task_projects = set(task.get("project_ids") or [])
                all_report_projects.update(task_projects)
                related_texts = [
                    task.get("title") or "",
                    *(task.get("conclusions") or []),
                    *(task.get("results") or []),
                ]
                if any(_same_claim(text, value) for value in related_texts if value):
                    matched.update(task_projects)
        if len(matched) == 1 and (not project_ids or project_ids == ["asr_other"]):
            return list(matched)
        if not project_ids and len(all_report_projects) == 1:
            return list(all_report_projects)
    return project_ids


def knowledge(ledger: Ledger, home: Path, project: str | None = None) -> dict[str, Any]:
    output: list[dict[str, Any]] = []
    hidden_task_results = 0
    deduplicated = 0
    from .daily_source import _project_ids, iter_reports
    for source_report in iter_reports(cache_home=home):
        report = canonicalize_report(source_report, home)
        explicit_claims: list[str] = []
        for text in report.get("knowledge") or []:
            project_ids = canonical_project_ids(
                _claim_project_ids(text, _project_ids, report), home,
                default_unassigned=False,
            ) or [None]
            if project and project not in project_ids:
                continue
            title, detail = _claim_parts(text)
            output.append({"project_id": project_ids[0], "kind": "研究结论", "title": title,
                           "detail": detail, "scope": "日报明确提炼的关键知识",
                           "date": report["date"], "confidence": "来自正式日报",
                           "_claim": text})
            explicit_claims.append(text)
        for text in report.get("decisions") or []:
            project_ids = canonical_project_ids(
                _claim_project_ids(text, _project_ids, report), home,
                default_unassigned=False,
            ) or [None]
            if project and project not in project_ids:
                continue
            title, detail = _claim_parts(text)
            output.append({"project_id": project_ids[0], "kind": "研究决策", "title": title,
                           "detail": detail, "scope": "日报明确记录的采用、拒绝或条件采用",
                           "date": report["date"], "confidence": "来自正式日报",
                           "_claim": text})
            explicit_claims.append(text)
        for group in report["groups"]:
            for task in group["tasks"]:
                # A successful build, commit or file upload is a daily result,
                # not reusable knowledge.  It remains available in the report
                # view and must not be promoted merely because it is non-empty.
                hidden_task_results += len(task.get("results") or [])
                conclusions = list(dict.fromkeys(task.get("conclusions") or []))
                if not conclusions:
                    continue
                project_ids = task["project_ids"] or [None]
                if project and project not in project_ids:
                    continue
                for text in conclusions:
                    if any(_same_claim(text, existing) for existing in explicit_claims):
                        deduplicated += 1
                        continue
                    output.append({"project_id": project_ids[0], "kind": "研究结论",
                                   "title": text, "detail": None,
                                   "scope": task.get("display_title") or task["title"],
                                   "date": report["date"], "confidence": "来自正式日报",
                                   "_claim": text})
    for row in ledger.events(project_id=project):
        p = _payload(row); typ = row["event_type"]
        if typ.startswith("decision_") and p.get("text"):
            output.append({"project_id": canonical_project_id(row["project_id"], home), "kind": "研究决策", "title": p["text"],
                           "detail": p.get("reason"), "scope": p.get("scope"), "date": row["occurred_at"][:10],
                           "confidence": "已确认" if row["verification"] == "user_confirmed" else "待确认",
                           "_claim": p["text"]})
        elif typ.startswith("hypothesis_") and p.get("statement"):
            output.append({"project_id": canonical_project_id(row["project_id"], home), "kind": "假设", "title": p["statement"],
                           "detail": p.get("classification"), "scope": p.get("scope"), "date": row["occurred_at"][:10],
                           "confidence": "研究中", "_claim": p["statement"]})

    # Append-only reports and ledger events can repeat the same explicit claim.
    # Keep the newest copy per project and semantic kind.
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str | None, str, str]] = set()
    for item in sorted(output, key=lambda value: value["date"], reverse=True):
        semantic_kind = "假设" if item["kind"] == "假设" else ("决策" if "决策" in item["kind"] else "结论")
        key = (item["project_id"], semantic_kind, _claim_key(item.pop("_claim")))
        if key in seen:
            deduplicated += 1
            continue
        seen.add(key)
        unique.append(item)
    return {
        "items": unique,
        "summary": {
            "shown": len(unique),
            "hidden_task_results": hidden_task_results,
            "deduplicated": deduplicated,
        },
        "explanation": "只汇总日报明确写出的研究结论、研究决策和假设；构建、提交、上传、测试通过等普通结果仍保留在每日研究记录中。",
    }
