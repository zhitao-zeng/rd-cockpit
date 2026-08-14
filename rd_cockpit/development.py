"""Readable project-development projections built from Markdown daily reports.

This module intentionally avoids exposing the internal event ledger.  Every
node shown to the user comes from a daily-report task, result, blocker, plan or
knowledge item.  Similar-task grouping is explicitly marked as inferred.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .config import load_config
from .daily_source import _project_ids, iter_reports
from .daily_supplement import load_supplement
from .project_identity import (
    canonical_project_ids, canonicalize_report, visible_project_names,
)

PHASES = ("探索", "实现", "执行", "验证", "交付", "运维")
PHASE_WORDS = {
    "交付": ("交付", "上线", "正式发布", "部署完成", "提交榜", "平台提交完成", "合入主干"),
    "验证": ("实验", "评测", "测试", "验证", "benchmark", "对比", "跑分", "回归", "decode"),
    "实现": ("实现", "修复", "修改", "重构", "开发", "编写", "新增", "接入", "配置", "优化", "构建"),
    "执行": ("执行", "运行", "生成", "训练", "推理", "下载", "批处理", "补跑", "跑任务", "调度"),
    "探索": ("调研", "分析", "定位", "阅读", "梳理", "设计", "探索", "理解", "确认"),
    "运维": ("服务恢复", "服务重启", "重启服务", "迁移到", "迁移至", "资源分配", "资源检查",
           "部署服务", "重新部署", "清理容器", "清理进程", "服务器维护"),
}
SUCCESS_WORDS = ("完成", "通过", "成功", "提升", "降低", "解决", "确认", "达到", "跑通")

FAILURE_WORDS = re.compile(r"失败|未通过|异常|回退|退化|崩溃|卡住|阻塞|不可用|超时|OOM|报错|错误|不匹配|死锁", re.I)
OPEN_FAILURE = re.compile(
    r"仍(?:然)?(?:无法|失败|不可用|阻塞)|未解决|"
    r"需要重跑|需(?:要)?修复|无法(?:跑|使用|完成|访问)|当前.{0,12}(?:失败|异常|不可用)|卡住|阻塞",
    re.I,
)
RESOLVED_FAILURE = re.compile(
    r"(?:已|成功)?(?:修复|解决|恢复正常|规避|排除)|全部(?:成功|通过)|稳定|跑通|"
    r"(?:0|零)\s*(?:次)?\s*(?:失败|错误|异常|error|timeout)|不\s*OOM|无(?:语法)?错误|没有新增失败",
    re.I,
)
NEGATED_FAILURE = re.compile(
    r"不\s*(?:会|再)?\s*(?:OOM|失败|超时|崩溃|报错)|无(?:语法)?错误|"
    r"(?:0|零)\s*(?:次)?\s*(?:失败|错误|异常|error|timeout)|没有新增失败",
    re.I,
)

METRIC_RE = re.compile(
    r"(?P<name>1-CER|CER|WER|F1|RMSE|RTF|ACC(?:URACY)?|accuracy|precision|recall|score|"
    r"准确率|召回率|精度|得分|延迟|耗时|成功率|可用率|标点率)"
    r"\s*(?:为|达|达到|约|=|:|：)?\s*"
    r"(?P<value>-?\d+(?:\.\d+)?)\s*(?P<unit>%|pp|ms|s|秒|分钟|小时|倍|x)?",
    re.IGNORECASE,
)


def _work_types(text: str) -> list[str]:
    value = text.casefold()
    matched = [phase for phase in PHASES if any(word.casefold() in value for word in PHASE_WORDS[phase])]
    return matched or ["探索"]


def _phase(text: str, title: str | None = None) -> str:
    """Choose a primary work type without erasing the other matched types.

    The title states intent more reliably than a result paragraph that happens
    to mention tests.  A task called "实现 HTTP 服务" therefore remains 实现,
    while its ``work_types`` can still include 验证.
    """
    types = _work_types(text)
    title_value = (title or "").casefold()
    title_types = [phase for phase in PHASES
                   if any(word.casefold() in title_value for word in PHASE_WORDS[phase])]
    priorities = ("交付", "运维", "实现", "执行", "验证", "探索")
    for phase in priorities:
        if phase in title_types:
            return phase
    for phase in priorities:
        if phase in types:
            return phase
    return "探索"


def _failure_state(text: str) -> str:
    """Classify failure language while respecting negation and recovery."""
    cleaned = NEGATED_FAILURE.sub("", text)
    has_failure = bool(FAILURE_WORDS.search(cleaned))
    is_open = bool(OPEN_FAILURE.search(cleaned))
    is_resolved = bool(RESOLVED_FAILURE.search(text))
    if is_open:
        return "open"
    if has_failure and is_resolved:
        return "resolved"
    if has_failure:
        return "historical"
    return "none"


def _status(text: str, has_results: bool) -> str:
    value = text.casefold()
    if _failure_state(text) == "open":
        return "blocked"
    if has_results and any(word.casefold() in value for word in SUCCESS_WORDS):
        return "result"
    return "result" if has_results else "working"


def _normalized(text: str) -> str:
    value = re.sub(r"^(?:\d+[.)、]\s*|exp\s*\d+\s*)", "", text.casefold())
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", value)


def _similarity(left: str, right: str) -> float:
    a, b = _normalized(left), _normalized(right)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return min(len(a), len(b)) / max(len(a), len(b))
    return SequenceMatcher(None, a, b).ratio()


def _project_names(home: Path) -> dict[str, str]:
    return visible_project_names(home)


def _configured_lifecycle_statuses(home: Path) -> dict[str, str]:
    config = load_config(home / "config" / "projects.yaml")
    return {
        str(project_id): str(value.get("lifecycle_status") or "active")
        for project_id, value in (config.get("projects") or {}).items()
        if isinstance(value, dict)
    }


def _primary_project_ids(text: str) -> list[str]:
    """Prefer an explicitly leading project label for blocker attribution.

    A blocker such as ``video-generator: real ASR backend missing`` belongs
    to the video project; the dependency word ASR must not create a second ASR
    blocker.  Multi-project platform blockers without a leading project label
    still keep every project identified from the whole prefix.
    """
    prefix = re.split(r"[:：]", text, maxsplit=1)[0].strip()
    leading = re.split(r"[\s（(\[/]", prefix, maxsplit=1)[0].strip("-—")
    leading_ids = canonical_project_ids(_project_ids(leading), default_unassigned=False)
    if leading_ids:
        return leading_ids
    prefix_ids = canonical_project_ids(_project_ids(prefix), default_unassigned=False)
    return prefix_ids or canonical_project_ids(_project_ids(text), default_unassigned=False)


def _task_nodes(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    nodes = []
    for report in reports:
        for group_index, group in enumerate(report.get("groups", [])):
            for task_index, task in enumerate(group.get("tasks", [])):
                project_ids = task.get("project_ids") or _project_ids(
                    f"{group.get('title', '')} {task.get('title', '')}"
                ) or ["unassigned"]
                readable_results = list(dict.fromkeys(task.get("results", [])))
                conclusions = list(dict.fromkeys(task.get("conclusions", [])))
                combined = " ".join([task.get("title", ""), *task.get("did", []), *task.get("why", []),
                                     *readable_results, *conclusions])
                status_text = " ".join([task.get("title", ""), *readable_results, *conclusions])
                work_types = _work_types(combined)
                for project_id in project_ids:
                    nodes.append({
                        "id": f"{report['date']}:{group_index}:{task_index}:{project_id}",
                        "date": report["date"],
                        "project_id": project_id,
                        "group": group.get("title") or "其他",
                        "title": task.get("display_title") or task.get("title") or "未命名事项",
                        "original_title": task.get("title") or "未命名事项",
                        "did": task.get("did", []),
                        "why": task.get("why", []),
                        "results": readable_results,
                        "conclusions": conclusions,
                        "files": task.get("files", []),
                        "phase": _phase(combined, task.get("title", "")),
                        "work_types": work_types,
                        "status": _status(status_text, bool(task.get("results"))),
                        "source": f"{report['date']}.md",
                    })
    return sorted(nodes, key=lambda item: (item["date"], item["id"]))


def _threads(nodes: list[dict[str, Any]], project_id: str) -> list[dict[str, Any]]:
    candidates = [node for node in nodes if node["project_id"] == project_id][-60:]
    groups: list[dict[str, Any]] = []
    for node in candidates:
        best = None
        best_score = 0.0
        for group in groups:
            score = max(_similarity(node["original_title"], title) for title in group["titles"][-4:])
            if score > best_score:
                best, best_score = group, score
        if best is not None and best_score >= 0.48:
            best["nodes"].append(node)
            best["titles"].append(node["original_title"])
            best["confidence"] = "标题相似，自动归为同一任务线"
        else:
            groups.append({"id": f"thread:{project_id}:{len(groups)}", "title": node["title"],
                           "titles": [node["original_title"]], "nodes": [node],
                           "confidence": "独立日报事项"})
    groups.sort(key=lambda item: item["nodes"][-1]["date"], reverse=True)
    return [{key: value for key, value in group.items() if key != "titles"} for group in groups[:16]]


def _metric_name(value: str) -> str:
    key = value.upper().replace("ACCURACY", "ACC")
    if key in {"SCORE", "ACC", "CER", "WER", "F1", "RMSE", "RTF", "1-CER"}:
        return "Score" if key == "SCORE" else key
    return {"准确率": "ACC", "召回率": "Recall", "精度": "Precision", "得分": "Score",
            "延迟": "Latency", "耗时": "Duration", "成功率": "Success rate", "可用率": "Availability",
            "标点率": "Punctuation rate"}.get(value, key)


def _metrics(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    points = []
    seen: set[tuple[str, str, str, float, str]] = set()
    for node in nodes:
        for text in node["results"]:
            for match in METRIC_RE.finditer(text):
                name = _metric_name(match.group("name"))
                unit = match.group("unit") or ""
                value = float(match.group("value"))
                key = (node["project_id"], node["date"], name, value, unit)
                if key in seen:
                    continue
                seen.add(key)
                points.append({"project_id": node["project_id"], "date": node["date"], "name": name,
                               "value": value, "unit": unit, "task": node["title"], "source": node["source"],
                               "context": text})
    return points


def _lifecycles(nodes: list[dict[str, Any]], names: dict[str, str], reports: list[dict[str, Any]],
                configured_statuses: dict[str, str] | None = None) -> list[dict[str, Any]]:
    configured_statuses = configured_statuses or {}
    output = []
    for project_id in sorted({*names, *(node["project_id"] for node in nodes)}):
        project_nodes = [node for node in nodes if node["project_id"] == project_id]
        if not project_nodes:
            continue
        counts = Counter(node["phase"] for node in project_nodes)
        work_type_counts = Counter(kind for node in project_nodes for kind in node.get("work_types", [node["phase"]]))
        recent = project_nodes[-8:]
        # Projects iterate: a delivered component can legitimately return to
        # exploration for the next problem.  This is the latest work stage,
        # not a monotonic completion percentage.
        current = recent[-1]["phase"]
        blockers = []
        for report in reports[-14:]:
            for blocker in report.get("blockers", []):
                ids = _primary_project_ids(blocker)
                if project_id in ids:
                    blockers.append({"date": report["date"], "text": blocker})
        current_blockers = [text for text in (reports[-1].get("blockers", []) if reports else [])
                            if project_id in _primary_project_ids(text)]
        output.append({"project_id": project_id, "name": names.get(project_id, project_id),
                       "current_phase": current, "phase_counts": {phase: counts[phase] for phase in PHASES},
                       "work_type_counts": {phase: work_type_counts[phase] for phase in PHASES},
                       "status": "blocked" if current_blockers else configured_statuses.get(project_id, "active"),
                       "blockers": blockers[-3:],
                       "last_activity": project_nodes[-1]["date"], "task_count": len(project_nodes),
                       "result_count": sum(bool(node["results"]) for node in project_nodes)})
    return output


def _effort(nodes: list[dict[str, Any]], reports: list[dict[str, Any]], names: dict[str, str]) -> list[dict[str, Any]]:
    values: dict[str, dict[str, Any]] = defaultdict(lambda: {"tokens": 0, "agent_minutes": 0.0})
    for report in reports:
        supplement = report.get("_supplement") or load_supplement(report["date"])
        for project in supplement.get("projects", []):
            item = values[project["project_id"]]
            item["tokens"] += int(project.get("tokens", 0) or 0)
            item["agent_minutes"] += float(project.get("duration_minutes", 0) or 0)
    for project_id in {node["project_id"] for node in nodes}:
        project_nodes = [node for node in nodes if node["project_id"] == project_id]
        values[project_id].update({"project_id": project_id, "name": names.get(project_id, project_id),
                                   "tasks": len(project_nodes),
                                   "results": sum(bool(node["results"]) for node in project_nodes)})
    for project_id, item in values.items():
        item.setdefault("project_id", project_id)
        item.setdefault("name", names.get(project_id, project_id))
        item.setdefault("tasks", 0)
        item.setdefault("results", 0)
    return sorted(values.values(), key=lambda item: item.get("tokens", 0), reverse=True)


def _activity(nodes: list[dict[str, Any]], reports: list[dict[str, Any]], names: dict[str, str]) -> dict[str, Any]:
    by_day: dict[str, Counter] = defaultdict(Counter)
    tokens: dict[str, Counter] = defaultdict(Counter)
    for node in nodes:
        by_day[node["date"]][node["project_id"]] += 1
    for report in reports:
        supplement = report.get("_supplement") or load_supplement(report["date"])
        for project in supplement.get("projects", []):
            tokens[report["date"]][project["project_id"]] += int(project.get("tokens", 0) or 0)
    dates = sorted({*by_day, *tokens})
    project_ids = sorted({pid for values in by_day.values() for pid in values} |
                         {pid for values in tokens.values() for pid in values})
    return {"dates": dates, "projects": [{"project_id": pid, "name": names.get(pid, pid),
                                             "activities": [by_day[day][pid] for day in dates],
                                             "tokens": [tokens[day][pid] for day in dates]} for pid in project_ids]}


def _closure_status(text: str) -> str:
    value = text.casefold()
    for status, words in (
        ("部分完成", ("partially", "部分完成")),
        ("阻塞", ("blocked", "阻塞")),
        ("延后", ("deferred", "顺延", "延后")),
        ("无证据", ("no_evidence", "无证据")),
        ("取消", ("cancelled", "取消")),
        ("完成", ("completed", "已完成", "完成")),
    ):
        if any(word in value for word in words):
            return status
    return "未标明"


def _plans(reports: list[dict[str, Any]]) -> dict[str, Any]:
    items = []
    counts: Counter = Counter()
    daily = []
    for report in reports:
        day_counts: Counter = Counter()
        for text in report.get("plan_closure", []):
            status = _closure_status(text)
            counts[status] += 1
            day_counts[status] += 1
            items.append({"date": report["date"], "text": text, "status": status,
                          "project_ids": canonical_project_ids(
                              _project_ids(text), default_unassigned=False,
                          )})
        if day_counts:
            daily.append({"date": report["date"], "counts": dict(day_counts)})
    return {"counts": dict(counts), "items": list(reversed(items[-40:])), "daily": daily,
            "total": sum(counts.values())}


def _knowledge(reports: list[dict[str, Any]], nodes: list[dict[str, Any]], names: dict[str, str]) -> dict[str, Any]:
    graph_nodes = [{"id": f"project:{pid}", "name": name, "category": "项目", "symbol_size": 42}
                   for pid, name in names.items() if any(node["project_id"] == pid for node in nodes)]
    edges = []
    candidates = []
    for report in reports:
        for text in report.get("knowledge", []):
            candidates.append((report["date"], text, canonical_project_ids(
                _project_ids(text), default_unassigned=False,
            )))
    for node in nodes:
        for conclusion in node["conclusions"][:2]:
            candidates.append((node["date"], conclusion, [node["project_id"]]))
    for index, (day, text, project_ids) in enumerate(candidates[-70:]):
        knowledge_id = f"knowledge:{index}"
        graph_nodes.append({"id": knowledge_id, "name": text[:42], "full_text": text, "date": day,
                            "category": "结论", "symbol_size": 16})
        for project_id in project_ids or ["unassigned"]:
            project_node = f"project:{project_id}"
            if not any(item["id"] == project_node for item in graph_nodes):
                graph_nodes.append({"id": project_node, "name": names.get(project_id, project_id),
                                    "category": "项目", "symbol_size": 42})
            edges.append({"source": project_node, "target": knowledge_id})
    return {"nodes": graph_nodes, "edges": edges,
            "explanation": "结论节点只来自日报明确的关键知识和任务结论；普通构建、测试、提交结果不会进入知识图。"}


def _time_travel(reports: list[dict[str, Any]], nodes: list[dict[str, Any]], names: dict[str, str]) -> list[dict[str, Any]]:
    snapshots = []
    project_ids = sorted({node["project_id"] for node in nodes})
    for report in reports:
        projects = []
        for project_id in project_ids:
            known = [node for node in nodes if node["project_id"] == project_id and node["date"] <= report["date"]]
            today = [node for node in known if node["date"] == report["date"]]
            if not known:
                continue
            last_result_node = next((node for node in reversed(known) if node["results"]), None)
            future = next((node for node in nodes if node["project_id"] == project_id and node["date"] > report["date"] and node["results"]), None)
            blockers = [text for text in report.get("blockers", [])
                        if project_id in canonical_project_ids(
                            _project_ids(text), default_unassigned=False,
                        )]
            next_items = [text for text in report.get("next", [])
                          if project_id in canonical_project_ids(
                              _project_ids(text), default_unassigned=False,
                          )]
            projects.append({"project_id": project_id, "name": names.get(project_id, project_id),
                             "phase": known[-1]["phase"], "latest_task": known[-1]["title"],
                             "latest_result": last_result_node["results"][0] if last_result_node else None,
                             "known_results": sum(bool(node["results"]) for node in known),
                             "today_tasks": [node["title"] for node in today], "blockers": blockers,
                             "next": next_items,
                             "not_known_yet": future["results"][0] if future else None,
                             "not_known_until": future["date"] if future else None})
        snapshots.append({"date": report["date"], "projects": projects})
    return snapshots


def development_dashboard(home: Path, *, days: int = 90, target: date | None = None) -> dict[str, Any]:
    target = target or date.today()
    since = (target - timedelta(days=max(1, days) - 1)).isoformat()
    reports = [canonicalize_report(report, home)
               for report in iter_reports(since=since, cache_home=home)
               if report.get("date") and report["date"] <= target.isoformat()]
    names = _project_names(home)
    nodes = _task_nodes(reports)
    active_ids = sorted({node["project_id"] for node in nodes})
    return {
        "generated_for": target.isoformat(),
        "days": max(1, days),
        "source": "Markdown Daily Report",
        "report_count": len(reports),
        "project_names": names,
        "storylines": {project_id: [node for node in nodes if node["project_id"] == project_id] for project_id in active_ids},
        "threads": {project_id: _threads(nodes, project_id) for project_id in active_ids},
        "metrics": _metrics(nodes),
        "lifecycles": _lifecycles(nodes, names, reports, _configured_lifecycle_statuses(home)),
        "effort_output": _effort(nodes, reports, names),
        "activity": _activity(nodes, reports, names),
        "plans": _plans(reports),
        "knowledge": _knowledge(reports, nodes, names),
        "time_travel": _time_travel(reports, nodes, names),
        "project_identity": {
            "registered": len(names) - 1,
            "unmapped_ids": sorted({
                value for report in reports for value in report.get("unmapped_project_ids", [])
            }),
        },
        "explanation": "所有可读节点来自正式日报；相似任务线为标题相似度推断，指标只提取明确写出的数值。",
    }


def development_summary_view(dashboard: dict[str, Any]) -> dict[str, Any]:
    """Return the compact first-screen payload used by the browser."""
    storylines = dashboard.get("storylines") or {}
    return {
        "generated_for": dashboard.get("generated_for"),
        "days": dashboard.get("days"),
        "source": dashboard.get("source"),
        "report_count": dashboard.get("report_count", 0),
        "project_names": dashboard.get("project_names") or {},
        "lifecycles": dashboard.get("lifecycles") or [],
        "effort_output": dashboard.get("effort_output") or [],
        "activity": dashboard.get("activity") or {"dates": [], "projects": []},
        "counts": {
            "nodes": sum(len(items) for items in storylines.values()),
            "projects": sum(bool(items) for key, items in storylines.items() if key != "unassigned"),
            "metrics": len(dashboard.get("metrics") or []),
            "plans": int((dashboard.get("plans") or {}).get("total", 0) or 0),
        },
        "project_identity": dashboard.get("project_identity") or {"registered": 0, "unmapped_ids": []},
        "explanation": dashboard.get("explanation"),
    }


def development_project_view(
    dashboard: dict[str, Any], project_id: str, *, timeline_limit: int = 120,
) -> dict[str, Any]:
    storylines = dashboard.get("storylines") or {}
    all_nodes = list(storylines.get(project_id) or [])
    limit = max(12, min(int(timeline_limit), 500))
    lifecycle = next(
        (item for item in dashboard.get("lifecycles") or [] if item.get("project_id") == project_id),
        None,
    )
    effort = next(
        (item for item in dashboard.get("effort_output") or [] if item.get("project_id") == project_id),
        None,
    )
    activity = dashboard.get("activity") or {"dates": [], "projects": []}
    project_activity = [
        item for item in activity.get("projects") or [] if item.get("project_id") == project_id
    ]
    latest_snapshot = None
    for snapshot in reversed(dashboard.get("time_travel") or []):
        project = next(
            (item for item in snapshot.get("projects") or [] if item.get("project_id") == project_id),
            None,
        )
        if project:
            latest_snapshot = {"date": snapshot.get("date"), "project": project}
            break
    return {
        "generated_for": dashboard.get("generated_for"),
        "days": dashboard.get("days"),
        "project_id": project_id,
        "project_name": (dashboard.get("project_names") or {}).get(project_id, project_id),
        "storyline": all_nodes[-limit:],
        "timeline_total": len(all_nodes),
        "timeline_limit": limit,
        "threads": list((dashboard.get("threads") or {}).get(project_id) or [])[:16],
        "metrics": [
            item for item in dashboard.get("metrics") or [] if item.get("project_id") == project_id
        ],
        "lifecycle": lifecycle,
        "effort": effort,
        "activity": {"dates": activity.get("dates") or [], "projects": project_activity},
        "latest_snapshot": latest_snapshot,
        "explanation": dashboard.get("explanation"),
    }


def development_global_view(dashboard: dict[str, Any]) -> dict[str, Any]:
    """Return global secondary views without duplicating project timelines."""
    return {
        "generated_for": dashboard.get("generated_for"),
        "days": dashboard.get("days"),
        "plans": dashboard.get("plans") or {"counts": {}, "items": [], "daily": [], "total": 0},
        "explanation": dashboard.get("explanation"),
    }


def development_timeline_view(
    dashboard: dict[str, Any], *, project_id: str | None = None,
    offset: int = 0, limit: int = 50,
) -> dict[str, Any]:
    """Page readable Daily Report nodes newest-first."""
    storylines = dashboard.get("storylines") or {}
    if project_id:
        items = list(storylines.get(project_id) or [])
    else:
        items = [item for values in storylines.values() for item in values]
    items.sort(key=lambda item: (str(item.get("date") or ""), str(item.get("id") or "")), reverse=True)
    start = max(0, int(offset))
    size = max(1, min(int(limit), 200))
    page = items[start:start + size]
    return {
        "project_id": project_id,
        "offset": start,
        "limit": size,
        "total": len(items),
        "has_more": start + len(page) < len(items),
        "items": page,
    }


def development_history_view(
    dashboard: dict[str, Any], *, offset: int = 0, limit: int = 10,
) -> dict[str, Any]:
    """Page historical knowledge snapshots without returning the whole year."""
    snapshots = list(reversed(dashboard.get("time_travel") or []))
    start = max(0, int(offset))
    size = max(1, min(int(limit), 31))
    page = [{
        "date": snapshot.get("date"),
        "projects": [{
            key: project.get(key) for key in (
                "project_id", "name", "phase", "latest_task", "latest_result", "blockers",
            )
        } for project in snapshot.get("projects") or []],
    } for snapshot in snapshots[start:start + size]]
    return {
        "offset": start,
        "limit": size,
        "total": len(snapshots),
        "has_more": start + len(page) < len(snapshots),
        "items": page,
    }
