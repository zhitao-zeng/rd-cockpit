"""High-density project intelligence derived from audited Daily Reports.

Readable claims come from the report/audit layer. Agent usage is used only as
an effort number; the event ledger is deliberately not exposed here.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import date, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from statistics import median
from typing import Any

from .daily_source import _project_ids, iter_reports
from .development import development_dashboard

IntelligenceByProject = dict[str, list[dict[str, Any]]]

UNKNOWN_RE = re.compile(
    r"(?:尚未|待验证|不确定|未知|无法证明|还需|仍需|需要确认|需确认|是否|缺少|待复现|待评测)", re.I,
)
DATA_QUALITY_RE = re.compile(r"(?:session 叙述|未逐条核对|标注待验证|数据完整性|证据引用)", re.I)
BREAKTHROUGH_RE = re.compile(
    r"(?:提升|降低|超过|最佳|首次|确认|推翻|排除|验证通过|跑通|交付|解决|收敛|突破)", re.I,
)
METRIC_RE = re.compile(r"(?:CER|WER|F1|RTF|ACC|accuracy|latency|延迟|准确率|召回率|精度|得分).{0,10}\d", re.I)
COMPLETED_RE = re.compile(r"(?:completed|已完成|验证通过|完成)", re.I)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _audit_for(report: dict[str, Any]) -> dict[str, Any] | None:
    source = report.get("source_path")
    day = report.get("date")
    if not source or not day:
        return None
    return _read_json(Path(source).parent / "data" / f"{day}_audit_validated.json")


def _intelligence_for(report: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """Prefer the calibrated historical sidecar, then the normal daily audit."""
    source = report.get("source_path")
    day = report.get("date")
    if not source or not day:
        return None, "missing"
    source_path = Path(source)
    calibrated = _read_json(source_path.parent / "data" / f"{day}_intelligence_validated.json")
    if calibrated:
        try:
            digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
        except OSError:
            digest = ""
        if digest and calibrated.get("source_sha256") == digest:
            return calibrated, "historical_audited"
    audit = _audit_for(report)
    return (audit, "audited") if audit else (None, "missing")


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _normalized(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", value.casefold())


def _unknown_id(project_id: str, question: str) -> str:
    digest = hashlib.sha1(f"{project_id}|{_normalized(question)}".encode()).hexdigest()[:12]
    return f"unknown:{project_id}:{digest}"


def _dedupe(items: list[dict[str, Any]], key: str = "text") -> list[dict[str, Any]]:
    output = []
    seen = set()
    for item in items:
        marker = _normalized(str(item.get(key) or ""))
        if not marker or marker in seen:
            continue
        seen.add(marker)
        output.append(item)
    return output


def _report_projects(report: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for group in report.get("groups") or []:
        for task in group.get("tasks") or []:
            values.extend(_strings(task.get("project_ids")))
    return list(dict.fromkeys(value for value in values if value != "unassigned"))


def _item_projects(item: dict[str, Any], report: dict[str, Any], valid: set[str]) -> list[str]:
    raw = [*_strings(item.get("project_ids")), *_strings(item.get("project")), *_strings(item.get("project_id"))]
    explicit = list(dict.fromkeys(value for value in raw if value in valid))
    # Calibrated intelligence is deliberately single-project.  Never fan one
    # model sentence out into multiple storylines.
    if len(explicit) == 1:
        return explicit
    if len(explicit) > 1:
        return []
    text = " ".join([
        *raw,
        str(item.get("question") or ""), str(item.get("summary") or ""),
        str(item.get("title") or ""), str(item.get("change") or ""),
    ])
    projects = [value for value in raw if value in valid]
    projects.extend(value for value in _project_ids(text) if value in valid)
    evidence = set(_strings(item.get("evidence")))
    if evidence:
        for group in report.get("groups") or []:
            for task in group.get("tasks") or []:
                if evidence.intersection(_strings(task.get("evidence"))):
                    projects.extend(value for value in _strings(task.get("project_ids")) if value in valid)
    report_projects = _report_projects(report)
    if not projects and len(report_projects) == 1:
        projects = report_projects
    return list(dict.fromkeys(projects))


def _latest_project_report(reports: list[dict[str, Any]], project_id: str) -> dict[str, Any] | None:
    for report in reversed(reports):
        if project_id in _report_projects(report):
            return report
    return None


def _text_belongs(text: str, project_id: str, report_projects: list[str]) -> bool:
    ids = _project_ids(text)
    if project_id in ids:
        return True
    if ids == ["asr_other"] and project_id.startswith("asr") and project_id in report_projects:
        return True
    return not ids and report_projects == [project_id]


def _structured_intelligence(
    reports: list[dict[str, Any]], valid: set[str],
) -> tuple[IntelligenceByProject, IntelligenceByProject, IntelligenceByProject, IntelligenceByProject,
           set[str], set[str]]:
    unknown_updates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    blocker_updates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    breakthroughs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    updates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    audited_dates: set[str] = set()
    audited_projects: set[str] = set()
    for report in reports:
        audit, source_mode = _intelligence_for(report)
        if not audit:
            continue
        day = str(report["date"])
        audited_dates.add(day)
        audited_projects.update(project_id for project_id in _report_projects(report) if project_id in valid)
        for field, destination in (("unknown_updates", unknown_updates),
                                   ("blocker_updates", blocker_updates)):
            for item in audit.get(field) or []:
                if not isinstance(item, dict):
                    continue
                projects = _item_projects(item, report, valid)
                if len(projects) == 1:
                    destination[projects[0]].append({**item, "project_id": projects[0], "date": day,
                                                     "source_mode": item.get("source_mode") or source_mode})
        for item in audit.get("breakthroughs") or []:
            if not isinstance(item, dict):
                continue
            projects = _item_projects(item, report, valid)
            if len(projects) == 1:
                breakthroughs[projects[0]].append({**item, "project_id": projects[0], "date": day,
                                                   "source_mode": item.get("source_mode") or source_mode})
        for item in audit.get("project_updates") or []:
            if not isinstance(item, dict):
                continue
            projects = _item_projects(item, report, valid)
            if len(projects) == 1:
                updates[projects[0]].append({**item, "project_id": projects[0], "date": day,
                                             "source_mode": item.get("source_mode") or source_mode})
    return unknown_updates, blocker_updates, breakthroughs, updates, audited_dates, audited_projects


def _replay_unknowns(project_id: str, updates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    active: dict[str, dict[str, Any]] = {}
    resolved = 0
    for update in sorted(updates, key=lambda item: item["date"]):
        question = str(update.get("question") or update.get("text") or "").strip()
        if not question:
            continue
        requested_id = str(update.get("unknown_id") or "")
        match_id = requested_id if requested_id in active else None
        if match_id is None and not requested_id:
            best_score = 0.0
            for candidate_id, candidate in active.items():
                score = SequenceMatcher(None, _normalized(question), _normalized(candidate["question"])).ratio()
                if score > best_score:
                    match_id, best_score = candidate_id, score
            if best_score < 0.58:
                match_id = None
        action = str(update.get("action") or "open").casefold()
        if action == "resolve":
            if match_id and match_id in active:
                del active[match_id]
                resolved += 1
            continue
        unknown_id = match_id or _unknown_id(project_id, question)
        previous = active.get(unknown_id, {})
        active[unknown_id] = {
            "unknown_id": unknown_id,
            "project_id": project_id,
            "question": question,
            "priority": str(update.get("priority") or previous.get("priority") or "medium").casefold(),
            "missing_evidence": str(update.get("missing_evidence") or previous.get("missing_evidence") or "").strip(),
            "first_seen": previous.get("first_seen") or update["date"],
            "last_seen": update["date"],
            "evidence": list(dict.fromkeys([*previous.get("evidence", []), *_strings(update.get("evidence"))])),
            "confidence": str(update.get("confidence") or "reported"),
            "source_mode": "audited",
        }
    priority = {"high": 0, "medium": 1, "low": 2}
    recent_first = sorted(active.values(), key=lambda item: str(item["last_seen"]), reverse=True)
    return sorted(recent_first, key=lambda item: priority.get(item["priority"], 1)), resolved


def _replay_blockers(project_id: str, updates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    active: dict[str, dict[str, Any]] = {}
    resolved = 0
    for update in sorted(updates, key=lambda item: item["date"]):
        text = str(update.get("blocker") or update.get("text") or "").strip()
        if not text:
            continue
        requested_id = str(update.get("blocker_id") or "")
        match_id = requested_id if requested_id in active else None
        if match_id is None and not requested_id:
            best_score = 0.0
            for candidate_id, candidate in active.items():
                score = SequenceMatcher(None, _normalized(text), _normalized(candidate["blocker"])).ratio()
                if score > best_score:
                    match_id, best_score = candidate_id, score
            if best_score < 0.62:
                match_id = None
        if str(update.get("action") or "open").casefold() == "resolve":
            if match_id and match_id in active:
                del active[match_id]
                resolved += 1
            continue
        blocker_id = match_id or requested_id or _unknown_id(project_id, "blocker:" + text).replace("unknown:", "blocker:", 1)
        previous = active.get(blocker_id, {})
        active[blocker_id] = {
            "blocker_id": blocker_id, "project_id": project_id, "blocker": text,
            "priority": str(update.get("priority") or previous.get("priority") or "medium"),
            "missing_evidence": str(update.get("missing_evidence") or previous.get("missing_evidence") or ""),
            "first_seen": previous.get("first_seen") or update["date"], "last_seen": update["date"],
            "evidence": list(dict.fromkeys([*previous.get("evidence", []), *_strings(update.get("evidence"))])),
            "source_mode": update.get("source_mode") or "audited",
        }
    priority = {"high": 0, "medium": 1, "low": 2}
    return sorted(active.values(), key=lambda item: (str(item["last_seen"]),
                                                      -priority.get(item["priority"], 1)), reverse=True), resolved


def _fallback_unknowns(report: dict[str, Any], project_id: str) -> list[dict[str, Any]]:
    candidates = [(str(text), True) for text in report.get("blockers", [])]
    candidates.extend((str(text), False) for text in report.get("next", []))
    for group in report.get("groups") or []:
        for task in group.get("tasks") or []:
            if project_id in _strings(task.get("project_ids")):
                candidates.extend((str(text), False) for text in task.get("results") or [])
                candidates.extend((str(text), False) for text in task.get("conclusions") or [])
    output = []
    for text, is_blocker in candidates:
        value = str(text).strip()
        if not value or (not is_blocker and not UNKNOWN_RE.search(value)) or DATA_QUALITY_RE.search(value):
            continue
        if not _text_belongs(value, project_id, _report_projects(report)):
            continue
        output.append({
            "unknown_id": _unknown_id(project_id, value), "project_id": project_id,
            "question": value, "priority": "medium", "missing_evidence": "历史日报未结构化说明",
            "first_seen": report["date"], "last_seen": report["date"], "evidence": [f"report:{report['date']}"],
            "confidence": "reported", "source_mode": "historical_fallback",
        })
    return _dedupe(output, "question")[:8]


def _fallback_breakthroughs(nodes: list[dict[str, Any]], project_id: str) -> list[dict[str, Any]]:
    output = []
    for node in nodes:
        for text in [*node.get("conclusions", []), *node.get("results", [])]:
            score = int(bool(METRIC_RE.search(text))) * 2 + int(bool(BREAKTHROUGH_RE.search(text)))
            score += int(node.get("phase") in {"验证", "交付"})
            if score < 3:
                continue
            output.append({"project_id": project_id, "date": node["date"], "title": node["title"],
                           "change": text, "significance": "日报包含明确指标或方向性结论",
                           "evidence": [node["source"]], "confidence": "reported",
                           "source_mode": "historical_fallback"})
    return _dedupe(output, "change")[-12:]


def _storyline(project_id: str, nodes: list[dict[str, Any]], breakthroughs: list[dict[str, Any]],
               unknowns: list[dict[str, Any]], updates: list[dict[str, Any]]) -> dict[str, Any]:
    if updates:
        summaries = _dedupe([{"text": str(item.get("summary") or ""), **item} for item in updates], "text")
        selected = summaries[-4:]
        text = "".join(item["text"].rstrip("。") + "。" for item in selected if item["text"])
        modes = {str(item.get("source_mode") or "audited") for item in selected}
        mode = "historical_audited" if modes == {"historical_audited"} else "audited"
        evidence = list(dict.fromkeys(ref for item in selected for ref in _strings(item.get("evidence"))))
    elif nodes:
        first = nodes[0]
        latest = nodes[-1]
        parts = [f"项目最早的可读记录是“{first['title']}”"]
        if breakthroughs:
            middle = breakthroughs[-2:]
            parts.append("随后出现的关键变化包括" + "；".join(item["change"] for item in middle))
        latest_result = next((item for item in reversed(nodes) if item.get("results")), None)
        if latest_result:
            parts.append(f"最近的明确结果是“{latest_result['results'][0]}”")
        parts.append(f"当前最新日报事项是“{latest['title']}”")
        if unknowns:
            parts.append(f"当前仍需回答“{unknowns[0]['question']}”")
        text = "。".join(part.rstrip("。") for part in parts) + "。"
        mode = "historical_fallback"
        evidence = list(dict.fromkeys([first["source"], latest["source"], *[item["date"] for item in breakthroughs[-2:]]]))
    else:
        text, mode, evidence = "暂无足够日报记录生成项目故事。", "empty", []
    return {"project_id": project_id, "summary": text, "source_mode": mode, "evidence": evidence}


def _delta(reports: list[dict[str, Any]], nodes: list[dict[str, Any]], project_id: str,
           baseline: str, latest: str, unknown_updates: list[dict[str, Any]],
           blocker_updates: list[dict[str, Any]]) -> dict[str, Any]:
    results = []
    for node in nodes:
        if baseline < node["date"] <= latest:
            results.extend({"date": node["date"], "text": text, "source": node["source"]}
                           for text in node.get("results", []))
    knowledge, blockers, closures = [], [], []
    for report in reports:
        if not baseline < report["date"] <= latest:
            continue
        report_projects = _report_projects(report)
        for text in report.get("knowledge", []):
            if _text_belongs(text, project_id, report_projects):
                knowledge.append({"date": report["date"], "text": text, "source": f"{report['date']}.md"})
        for text in report.get("blockers", []):
            if _text_belongs(text, project_id, report_projects):
                blockers.append({"date": report["date"], "text": text, "source": f"{report['date']}.md"})
        for text in report.get("plan_closure", []):
            if _text_belongs(text, project_id, report_projects):
                closures.append({"date": report["date"], "text": text, "source": f"{report['date']}.md"})
    results, knowledge = _dedupe(results), _dedupe(knowledge)
    blockers, closures = _dedupe(blockers), _dedupe(closures)
    def lifecycle(items: list[dict[str, Any]], action: str, text_key: str) -> list[dict[str, Any]]:
        return _dedupe([
            {"date": item["date"], "text": str(item.get(text_key) or ""),
             "source": (item.get("evidence") or [f"report:{item['date']}"])[0]}
            for item in items
            if baseline < item["date"] <= latest and str(item.get("action") or "open") == action
        ])
    unknowns_opened = lifecycle(unknown_updates, "open", "question")
    unknowns_resolved = lifecycle(unknown_updates, "resolve", "question")
    blockers_opened = lifecycle(blocker_updates, "open", "blocker")
    blockers_resolved = lifecycle(blocker_updates, "resolve", "blocker")
    return {"from": baseline, "to": latest, "results": results, "knowledge": knowledge,
            "blockers": blockers, "plan_closure": closures,
            "unknowns_opened": unknowns_opened, "unknowns_resolved": unknowns_resolved,
            "blockers_opened": blockers_opened, "blockers_resolved": blockers_resolved,
            "change_count": sum(map(len, (results, knowledge, blockers, closures, unknowns_opened,
                                           unknowns_resolved, blockers_opened, blockers_resolved)))}


def project_intelligence(home: Path, *, days: int = 90, baseline: date | None = None,
                         target: date | None = None) -> dict[str, Any]:
    target = target or date.today()
    days = max(7, min(days, 365))
    since = (target - timedelta(days=days - 1)).isoformat()
    reports = [item for item in iter_reports(since=since) if item.get("date") and item["date"] <= target.isoformat()]
    dashboard = development_dashboard(home, days=days, target=target)
    dates = [item["date"] for item in reports]
    latest = dates[-1] if dates else None
    if baseline and latest and baseline.isoformat() <= latest:
        baseline_date = baseline.isoformat()
    elif len(dates) >= 2:
        baseline_date = dates[-2]
    else:
        baseline_date = latest
    valid = {item["project_id"] for item in dashboard.get("lifecycles", []) if item["project_id"] != "unassigned"}
    (structured_unknowns, structured_blockers, structured_breakthroughs,
     structured_updates, audited_dates, audited_projects) = _structured_intelligence(reports, valid)

    unknowns_by_project: dict[str, list[dict[str, Any]]] = {}
    stale_unknown_counts: dict[str, int] = {}
    hidden_unknown_counts: dict[str, int] = {}
    open_unknown_totals: dict[str, int] = {}
    resolved_counts: dict[str, int] = {}
    breakthroughs_by_project: dict[str, list[dict[str, Any]]] = {}
    blockers_by_project: dict[str, list[dict[str, Any]]] = {}
    resolved_blocker_counts: dict[str, int] = {}
    stale_blocker_counts: dict[str, int] = {}
    details: dict[str, dict[str, Any]] = {}
    lifecycles = {item["project_id"]: item for item in dashboard.get("lifecycles", [])}
    efforts = {item["project_id"]: item for item in dashboard.get("effort_output", [])}
    nodes_by_project = dashboard.get("storylines", {})
    freshness_cutoff = (
        date.fromisoformat(latest) - timedelta(days=30)
    ).isoformat() if latest else ""

    for project_id in sorted(valid):
        unknowns, resolved = _replay_unknowns(project_id, structured_unknowns.get(project_id, []))
        if not unknowns and project_id not in audited_projects:
            latest_report = _latest_project_report(reports, project_id)
            unknowns = _fallback_unknowns(latest_report, project_id) if latest_report else []
        fresh_unknowns = [item for item in unknowns if str(item.get("last_seen") or "") >= freshness_cutoff]
        open_unknown_totals[project_id] = len(fresh_unknowns)
        unknowns_by_project[project_id] = fresh_unknowns[:8]
        hidden_unknown_counts[project_id] = max(0, len(fresh_unknowns) - 8)
        stale_unknown_counts[project_id] = len(unknowns) - len(fresh_unknowns)
        resolved_counts[project_id] = resolved
        active_blockers, resolved_blockers = _replay_blockers(
            project_id, structured_blockers.get(project_id, []),
        )
        fresh_blockers = [item for item in active_blockers
                          if str(item.get("last_seen") or "") >= freshness_cutoff]
        blockers_by_project[project_id] = fresh_blockers
        stale_blocker_counts[project_id] = len(active_blockers) - len(fresh_blockers)
        resolved_blocker_counts[project_id] = resolved_blockers
        breakthroughs = list(structured_breakthroughs.get(project_id, []))
        if not breakthroughs and project_id not in audited_projects:
            breakthroughs = _fallback_breakthroughs(nodes_by_project.get(project_id, []), project_id)
        breakthroughs_by_project[project_id] = breakthroughs
        details[project_id] = {
            "delta": _delta(reports, nodes_by_project.get(project_id, []), project_id,
                            baseline_date or "", latest or "",
                            structured_unknowns.get(project_id, []), structured_blockers.get(project_id, [])),
            "unknowns": unknowns_by_project[project_id],
            "breakthroughs": breakthroughs,
            "storyline": _storyline(project_id, nodes_by_project.get(project_id, []), breakthroughs,
                                     unknowns_by_project[project_id],
                                     structured_updates.get(project_id, [])),
            "stale_unknown_count": stale_unknown_counts[project_id],
            "hidden_unknown_count": hidden_unknown_counts[project_id],
            "stale_blocker_count": stale_blocker_counts[project_id],
        }

    completed_by_project: dict[str, int] = defaultdict(int)
    for item in dashboard.get("plans", {}).get("items", []):
        if COMPLETED_RE.search(item.get("text", "")):
            for project_id in item.get("project_ids", []):
                if project_id in valid:
                    completed_by_project[project_id] += 1
    effort_progress = []
    for project_id in sorted(valid):
        lifecycle = lifecycles[project_id]
        effort = efforts.get(project_id, {})
        progress = int(lifecycle.get("result_count", 0)) + completed_by_project[project_id]
        audited_breakthroughs = sum(
            item.get("source_mode") != "historical_fallback"
            for item in breakthroughs_by_project[project_id]
        )
        progress += audited_breakthroughs + resolved_counts[project_id] + resolved_blocker_counts[project_id]
        effort_progress.append({"project_id": project_id, "name": lifecycle["name"],
                                "tokens": int(effort.get("tokens", 0) or 0),
                                "agent_minutes": float(effort.get("agent_minutes", 0) or 0),
                                "progress_items": progress, "result_items": lifecycle.get("result_count", 0),
                                "completed_plans": completed_by_project[project_id],
                                "breakthroughs": audited_breakthroughs,
                                "resolved_unknowns": resolved_counts[project_id],
                                "resolved_blockers": resolved_blocker_counts[project_id]})
    if effort_progress:
        token_mid = median(item["tokens"] for item in effort_progress)
        progress_mid = median(item["progress_items"] for item in effort_progress)
        for item in effort_progress:
            high_effort = item["tokens"] > token_mid
            high_progress = item["progress_items"] > progress_mid
            item["quadrant"] = ("heavy_wins" if high_effort and high_progress else
                                "attention_needed" if high_effort else
                                "efficient_wins" if high_progress else "low_activity")

    pulses = []
    for project_id in sorted(valid):
        lifecycle = lifecycles[project_id]
        nodes = nodes_by_project.get(project_id, [])
        latest_result_node = next((node for node in reversed(nodes) if node.get("results")), None)
        latest_report = _latest_project_report(reports, project_id)
        current_blockers = [item["blocker"] for item in blockers_by_project[project_id]]
        next_items = []
        if latest_report:
            if not current_blockers and project_id not in audited_projects:
                for text in latest_report.get("blockers", []):
                    if _text_belongs(text, project_id, _report_projects(latest_report)):
                        current_blockers.append(text)
            if project_id not in audited_projects:
                for text in latest_report.get("next", []):
                    if _text_belongs(text, project_id, _report_projects(latest_report)):
                        next_items.append(text)
        effort = efforts.get(project_id, {})
        source_mode = details[project_id]["storyline"]["source_mode"]
        latest_update = structured_updates.get(project_id, [])[-1] if structured_updates.get(project_id) else None
        pulses.append({"project_id": project_id, "name": lifecycle["name"],
                       "phase": lifecycle["current_phase"],
                       "status": "blocked" if current_blockers else lifecycle.get("status", "active"),
                       "latest_result": (str(latest_update.get("summary")) if latest_update
                                         else latest_result_node["results"][0] if latest_result_node else None),
                       "current_blocker": current_blockers[0] if current_blockers else None,
                       "next_action": next_items[0] if next_items else None,
                       "open_unknowns": open_unknown_totals[project_id],
                       "last_meaningful": lifecycle["last_activity"],
                       "tokens": int(effort.get("tokens", 0) or 0),
                       "result_items": lifecycle["result_count"], "source_mode": source_mode})
    pulses.sort(key=lambda item: (item["last_meaningful"], item["result_items"]), reverse=True)

    quality = []
    fallback_projects = [item["project_id"] for item in pulses if item["source_mode"] == "historical_fallback"]
    if fallback_projects:
        quality.append(f"{len(fallback_projects)} 个项目尚无新版情报字段，使用历史日报保守回退。")
    if any(item["tokens"] == 0 for item in effort_progress):
        quality.append("部分项目没有可可靠归属的 Agent Token；未补零以外的估算值。")
    fallback_dates = [value for value in dates if value not in audited_dates]
    status = _read_json(Path(reports[-1]["source_path"]).parent / "data" /
                        "intelligence_backfill_status.json") if reports else None
    failed_dates = [
        str(item.get("date")) for item in (status or {}).get("failed", [])
        if isinstance(item, dict) and str(item.get("date")) in dates
    ]
    return {"generated_for": target.isoformat(), "days": days, "latest_report_date": latest,
            "baseline_date": baseline_date, "available_dates": list(reversed(dates)),
            "pulses": pulses, "effort_progress": effort_progress, "project_details": details,
            "audit_coverage": {"report_count": len(dates), "audited_count": len(audited_dates),
                               "fallback_count": len(fallback_dates), "failed_dates": failed_dates,
                               "last_audited_date": max(audited_dates) if audited_dates else None},
            "data_quality": quality,
            "explanation": "自然语言来自审计后的 Daily Report；Token 仅表示 Agent 用量，运行时不调用模型。"}
