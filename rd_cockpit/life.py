"""Small, readable personal and playful indicators for the overview page."""

from __future__ import annotations

import calendar
import hashlib
import json
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config import load_config
from .daily_source import available_report_dates, iter_reports, load_report
from .daily_supplement import available_supplement_dates, load_supplement
from .ledger import Ledger

LOCAL_TZ = ZoneInfo("Asia/Shanghai")

# 2026 schedule from 国办发明电〔2025〕7号.  The source URL is returned to the
# UI so this does not become an unexplained hard-coded calendar.
HOLIDAY_SOURCE_2026 = "https://www.gov.cn/yaowen/liebiao/202511/content_7047099.htm"
HOLIDAYS_2026 = (
    ("元旦", date(2026, 1, 1), date(2026, 1, 3)),
    ("春节", date(2026, 2, 15), date(2026, 2, 23)),
    ("清明节", date(2026, 4, 4), date(2026, 4, 6)),
    ("劳动节", date(2026, 5, 1), date(2026, 5, 5)),
    ("端午节", date(2026, 6, 19), date(2026, 6, 21)),
    ("中秋节", date(2026, 9, 25), date(2026, 9, 27)),
    ("国庆节", date(2026, 10, 1), date(2026, 10, 7)),
)
ADJUSTED_WORKDAYS_2026 = {
    date(2026, 1, 4), date(2026, 2, 14), date(2026, 2, 28),
    date(2026, 5, 9), date(2026, 9, 20), date(2026, 10, 10),
}


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _progress(target: date) -> dict[str, float]:
    week = (target.weekday() + 1) / 7
    month = target.day / calendar.monthrange(target.year, target.month)[1]
    year = target.timetuple().tm_yday / (366 if calendar.isleap(target.year) else 365)
    return {"week": round(week, 4), "month": round(month, 4), "year": round(year, 4)}


def _holidays(config: dict[str, Any], year: int) -> tuple[list[tuple[str, date, date]], str | None]:
    custom = []
    for item in config.get("holidays", {}).get("custom", []):
        if not isinstance(item, dict):
            continue
        start, end = _parse_date(item.get("start")), _parse_date(item.get("end"))
        if start and end and start.year == year:
            custom.append((str(item.get("name") or "自定义假期"), start, end))
    if custom:
        return sorted(custom, key=lambda value: value[1]), "personal.yaml"
    if year == 2026:
        return list(HOLIDAYS_2026), HOLIDAY_SOURCE_2026
    return [], None


def _next_holiday(target: date, holidays: list[tuple[str, date, date]]) -> dict[str, Any]:
    for name, start, end in holidays:
        if target <= end:
            return {
                "available": True,
                "name": name,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "days": max(0, (start - target).days),
                "in_holiday": start <= target <= end,
                "duration_days": (end - start).days + 1,
            }
    return {"available": False, "name": None, "start": None, "end": None, "days": None,
            "in_holiday": False, "duration_days": None}


def _next_rest_day(target: date, holidays: list[tuple[str, date, date]]) -> dict[str, Any]:
    adjusted = ADJUSTED_WORKDAYS_2026 if target.year == 2026 else set()
    value = target
    for _ in range(14):
        holiday = next((name for name, start, end in holidays if start <= value <= end), None)
        if holiday or (value.weekday() >= 5 and value not in adjusted):
            return {"date": value.isoformat(), "days": (value - target).days,
                    "reason": holiday or "周末"}
        value += timedelta(days=1)
    return {"date": None, "days": None, "reason": "未来两周没有找到休息日"}


def _payday(target: date, day_value: Any) -> dict[str, Any]:
    last_day = str(day_value).strip().casefold() == "last"
    try:
        payday_day = 31 if last_day else int(day_value)
        if not last_day and not 1 <= payday_day <= 31:
            raise ValueError
    except (TypeError, ValueError):
        return {"configured": False, "date": None, "days": None, "day": None}
    year, month = target.year, target.month
    actual = min(payday_day, calendar.monthrange(year, month)[1])
    payday = date(year, month, actual)
    if payday < target:
        month = month + 1
        if month == 13:
            month, year = 1, year + 1
        actual = min(payday_day, calendar.monthrange(year, month)[1])
        payday = date(year, month, actual)
    return {"configured": True, "date": payday.isoformat(), "days": (payday - target).days,
            "day": payday_day, "rule": "last_day" if last_day else "day_of_month"}


def _first_commit(repo_path: str) -> date | None:
    path = Path(repo_path)
    if not (path / ".git").exists():
        return None
    try:
        run = subprocess.run(
            ["git", "-C", str(path), "log", "--reverse", "--format=%cs"],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=8, check=False,
        )
        first = next((line.strip() for line in run.stdout.splitlines() if line.strip()), None)
        return _parse_date(first)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _project_ages(home: Path, target: date, personal: dict[str, Any]) -> list[dict[str, Any]]:
    projects = load_config(home / "config" / "projects.yaml").get("projects", {})
    configured = personal.get("project_start_dates", {})
    output = []
    for project_id, value in projects.items():
        started = _parse_date(configured.get(project_id)) or _first_commit(str(value.get("repo_path") or ""))
        output.append({
            "project_id": project_id,
            "name": str(value.get("name") or project_id),
            "start_date": started.isoformat() if started else None,
            "days": (target - started).days + 1 if started and started <= target else None,
            "source": "personal.yaml" if _parse_date(configured.get(project_id)) else ("first_git_commit" if started else None),
        })
    return output


def _report_streak(target: date) -> dict[str, Any]:
    values = sorted(_parse_date(value) for value in available_report_dates())
    days = [value for value in values if value and value <= target]
    if not days:
        return {"current": 0, "longest": 0, "through": None, "total_reports": 0}
    day_set = set(days)
    through = max(days)
    current = 0
    value = through
    while value in day_set:
        current += 1
        value -= timedelta(days=1)
    longest = 0
    run = 0
    previous = None
    for value in days:
        run = run + 1 if previous and value == previous + timedelta(days=1) else 1
        longest = max(longest, run)
        previous = value
    return {"current": current, "longest": longest, "through": through.isoformat(), "total_reports": len(days)}


def _longest_agent_day() -> dict[str, Any]:
    best: dict[str, Any] | None = None
    for value in available_supplement_dates():
        supplement = load_supplement(value)
        minutes = float(supplement.get("totals", {}).get("duration_minutes", 0) or 0)
        if best is None or minutes > best["minutes"]:
            projects = sorted(supplement.get("projects", []), key=lambda item: item.get("duration_minutes", 0), reverse=True)
            best = {"date": value, "minutes": round(minutes, 1),
                    "top_project": projects[0].get("name") if projects else None}
    return best or {"date": None, "minutes": 0, "top_project": None}


def _token_books(report: dict[str, Any], personal: dict[str, Any]) -> dict[str, Any]:
    tokens = int(report.get("token", {}).get("total_tokens", 0) or 0)
    if not tokens and report.get("date"):
        tokens = int(load_supplement(report["date"]).get("totals", {}).get("tokens", 0) or 0)
    per_book = int(personal.get("fun", {}).get("token_per_book", 100_000) or 100_000)
    return {"tokens": tokens, "token_per_book": per_book,
            "books": round(tokens / per_book, 1) if tokens else 0,
            "note": f"纯趣味换算：按每本 {per_book:,} Token 计算，不代表真实阅读量。"}


def _research_weather(report: dict[str, Any]) -> dict[str, str]:
    if not report.get("available"):
        return {"icon": "🌙", "name": "等待日报", "detail": "今天的正式日报还没有生成"}
    blockers = len(report.get("blockers", []))
    tasks = int(report.get("task_count", 0) or 0)
    if blockers >= 4:
        return {"icon": "⛈️", "name": "雷阵雨", "detail": f"有 {blockers} 个阻塞，适合先清理主线"}
    if blockers >= 2:
        return {"icon": "🌧️", "name": "有雨", "detail": f"推进了 {tasks} 项工作，但仍有 {blockers} 个阻塞"}
    if blockers == 1:
        return {"icon": "⛅", "name": "多云转晴", "detail": f"有 {tasks} 项记录和 1 个待解决阻塞"}
    if tasks:
        return {"icon": "☀️", "name": "晴", "detail": f"记录了 {tasks} 项工作，目前没有日报阻塞"}
    return {"icon": "☁️", "name": "多云", "detail": "日报存在，但还没有明确研究事项"}


def _memory_card(target: date) -> dict[str, Any]:
    try:
        previous = target.replace(year=target.year - 1)
    except ValueError:
        previous = target.replace(year=target.year - 1, day=28)
    report = load_report(previous.isoformat())
    return {
        "date": previous.isoformat(),
        "available": bool(report.get("available")),
        "summary": report.get("push_summary") or (f"记录了 {report.get('task_count', 0)} 项工作" if report.get("available") else "去年今日没有日报"),
    }


def _random_knowledge(target: date, home: Path | None = None) -> dict[str, Any]:
    candidates: list[dict[str, str]] = []
    for report in iter_reports(cache_home=home):
        for text in report.get("knowledge", []):
            candidates.append({"text": str(text), "date": str(report.get("date") or "")})
        for group in report.get("groups", []):
            for task in group.get("tasks", []):
                # A build, upload, test pass or other task result belongs in
                # the daily record.  The playful knowledge card should only
                # surface text the report explicitly promoted to a reusable
                # conclusion.
                for text in task.get("conclusions", []):
                    candidates.append({"text": str(text), "date": str(report.get("date") or "")})
    if not candidates:
        return {"available": False, "text": None, "date": None}
    digest = hashlib.sha256(target.isoformat().encode()).digest()
    return {"available": True, **candidates[int.from_bytes(digest[:4], "big") % len(candidates)]}


def _gpu_pet_state(gpu: dict[str, Any], history: list[tuple[datetime, dict[str, Any]]], *,
                   stale: bool, age_minutes: float) -> dict[str, Any]:
    """Turn one observed GPU sample into a playful, but still literal, pet state."""
    index = str(gpu.get("index", "?"))
    utilization = float(gpu.get("utilization_pct", 0) or 0)
    memory = float(gpu.get("memory_used_mb", 0) or 0)
    temperature = float(gpu.get("temperature_c", 0) or 0)
    history = history[-3:]
    span_minutes = ((history[-1][0] - history[0][0]).total_seconds() / 60
                    if len(history) >= 2 else 0)
    sustained = len(history) >= 3 and span_minutes >= 9
    history_utils = [float(value.get("utilization_pct", 0) or 0) for _, value in history]
    history_memory = [float(value.get("memory_used_mb", 0) or 0) for _, value in history]
    sustained_idle = sustained and all(value < 5 for value in history_utils) and all(value > 1024 for value in history_memory)
    sustained_busy = sustained and sum(history_utils) / len(history_utils) >= 50
    if stale:
        icon, state = "💤", "快照过期"
    elif temperature >= 80:
        icon, state = "🥵", "热得冒烟"
    elif sustained_idle:
        icon, state = "🐉", "显存驻留 · 持续低利用率"
    elif sustained_busy:
        icon, state = "🐆", "持续奔跑"
    elif utilization >= 50:
        icon, state = "🐎", "此刻忙碌"
    elif memory > 1024 and utilization < 5:
        icon, state = "🐲", "显存已分配"
    elif utilization > 0:
        icon, state = "🐈", "此刻有负载"
    else:
        icon, state = "🦥", "当前空闲"
    detail = f"利用率 {utilization:.0f}% · 显存 {memory / 1024:.1f} GB"
    if temperature:
        detail += f" · {temperature:.0f}°C"
    if stale:
        detail += f" · {age_minutes / 60:.1f} 小时前"
    elif len(history) >= 2:
        detail += f" · 最近 {len(history)} 次/{span_minutes:.0f} 分钟"
    return {
        "gpu": index,
        "icon": icon,
        "state": state,
        "detail": detail,
        "utilization_pct": utilization,
        "memory_used_mb": memory,
        "temperature_c": temperature,
        "stale": stale,
    }


def _gpu_pet(ledger: Ledger, now: datetime) -> dict[str, Any]:
    rows = ledger.events(event_types={"resource_snapshot"})
    if not rows:
        return {"icon": "🥚", "state": "还没孵化", "detail": "采集一次 GPU 快照后它就会出现",
                "observed_at": None, "pets": []}
    parsed_rows: list[tuple[datetime, dict[str, Any], Any]] = []
    for candidate in rows[-12:]:
        candidate_payload = json.loads(candidate["payload_json"])
        candidate_sampled = candidate_payload.get("sampled_at") or candidate["occurred_at"]
        try:
            candidate_at = datetime.fromisoformat(candidate_sampled)
            if candidate_at.tzinfo is None:
                candidate_at = candidate_at.replace(tzinfo=LOCAL_TZ)
        except (TypeError, ValueError):
            continue
        parsed_rows.append((candidate_at, candidate_payload, candidate))
    if not parsed_rows:
        return {"icon": "🥚", "state": "还没孵化", "detail": "GPU 快照时间无法解析",
                "observed_at": None, "pets": []}
    observed, payload, row = parsed_rows[-1]
    sampled_at = payload.get("sampled_at") or row["occurred_at"]
    try:
        age_minutes = max(0, (now.astimezone(LOCAL_TZ) - observed.astimezone(LOCAL_TZ)).total_seconds() / 60)
    except (TypeError, ValueError):
        observed, age_minutes = None, 10_000
    gpus = payload.get("gpus") or []
    stale = age_minutes > 60
    histories: dict[str, list[tuple[datetime, dict[str, Any]]]] = {}
    for sampled, sample_payload, _ in parsed_rows:
        if (observed - sampled).total_seconds() > 30 * 60:
            continue
        for sample_gpu in sample_payload.get("gpus") or []:
            histories.setdefault(str(sample_gpu.get("index", "?")), []).append((sampled, sample_gpu))
    pets = [_gpu_pet_state(gpu, histories.get(str(gpu.get("index", "?")), []),
                           stale=stale, age_minutes=age_minutes) for gpu in gpus]
    if age_minutes > 60:
        return {"icon": "💤", "state": "睡着了", "detail": f"GPU 状态已经 {age_minutes / 60:.1f} 小时没有更新",
                "observed_at": sampled_at, "pets": pets}
    hottest = max((float(gpu.get("temperature_c", 0) or 0) for gpu in gpus), default=0)
    busiest = max((float(gpu.get("utilization_pct", 0) or 0) for gpu in gpus), default=0)
    idle_allocated = sum(pet["state"] == "显存驻留 · 持续低利用率" for pet in pets)
    if hottest >= 80:
        return {"icon": "🥵", "state": "热坏了", "detail": f"最高温度 {hottest:.0f}°C", "observed_at": sampled_at,
                "pets": pets}
    if idle_allocated:
        return {"icon": "🐉", "state": "显存驻留 · 持续低利用率", "detail": f"{idle_allocated} 张卡连续 3 次有显存但利用率低于 5%；不据此判断浪费", "observed_at": sampled_at,
                "pets": pets}
    if any(pet["state"] == "持续奔跑" for pet in pets):
        return {"icon": "🐆", "state": "持续奔跑", "detail": "至少一张卡最近 3 次平均利用率达到 50%", "observed_at": sampled_at,
                "pets": pets}
    if busiest >= 50:
        return {"icon": "🐎", "state": "此刻忙碌", "detail": f"当前最高利用率 {busiest:.0f}%（尚未形成趋势）", "observed_at": sampled_at,
                "pets": pets}
    allocated = sum(float(gpu.get("memory_used_mb", 0) or 0) > 1024 for gpu in gpus)
    if allocated:
        return {"icon": "🐲", "state": "显存已分配", "detail": f"{allocated} 张卡当前有显存分配；单次快照不判断浪费", "observed_at": sampled_at,
                "pets": pets}
    if busiest > 0:
        return {"icon": "🐈", "state": "慢慢干活", "detail": f"最高利用率 {busiest:.0f}%", "observed_at": sampled_at,
                "pets": pets}
    return {"icon": "🦥", "state": "当前空闲", "detail": "GPU 当前基本空闲", "observed_at": sampled_at,
            "pets": pets}


def _next_anniversary(start: date, target: date) -> date:
    year = target.year
    try:
        value = start.replace(year=year)
    except ValueError:
        value = date(year, 2, 28)
    if value < target:
        try:
            value = start.replace(year=year + 1)
        except ValueError:
            value = date(year + 1, 2, 28)
    return value


def _milestones(target: date, personal: dict[str, Any], employment: date | None,
                projects: list[dict[str, Any]], streak: dict[str, Any]) -> list[dict[str, Any]]:
    output = []
    if employment:
        value = _next_anniversary(employment, target)
        output.append({"name": "入职周年", "date": value.isoformat(), "days": (value - target).days})
    for item in personal.get("profile", {}).get("anniversaries", []):
        if not isinstance(item, dict) or not (started := _parse_date(item.get("date"))):
            continue
        value = _next_anniversary(started, target)
        output.append({"name": str(item.get("name") or "纪念日"), "date": value.isoformat(), "days": (value - target).days})
    for item in projects:
        if started := _parse_date(item.get("start_date")):
            value = _next_anniversary(started, target)
            output.append({"name": f"{item['name']} 周年", "date": value.isoformat(), "days": (value - target).days})
    for threshold in (7, 30, 100, 365):
        if streak["current"] < threshold:
            output.append({"name": f"连续日报 {threshold} 天", "date": None, "days": threshold - streak["current"]})
            break
    return sorted(output, key=lambda item: item["days"])[:6]


def life_dashboard(ledger: Ledger, home: Path, target: date | None = None, now: datetime | None = None) -> dict[str, Any]:
    target = target or datetime.now(LOCAL_TZ).date()
    now = now or datetime.now(LOCAL_TZ)
    personal = load_config(home / "config" / "personal.yaml")
    profile = personal.get("profile", {})
    employment = _parse_date(profile.get("employment_start"))
    holidays, holiday_source = _holidays(personal, target.year)
    latest_report = load_report()
    projects = _project_ages(home, target, personal)
    streak = _report_streak(target)
    leave_total = profile.get("annual_leave_total")
    leave_used = profile.get("annual_leave_used")
    leave_remaining_value = profile.get("annual_leave_remaining")
    leave_configured = leave_remaining_value is not None or (leave_total is not None and leave_used is not None)
    try:
        leave_remaining = round(float(leave_remaining_value), 1) if leave_remaining_value is not None else (
            round(float(leave_total) - float(leave_used), 1) if leave_configured else None
        )
    except (TypeError, ValueError):
        leave_configured, leave_remaining = False, None
    return {
        "date": target.isoformat(),
        "timezone": "Asia/Shanghai",
        "config_path": str(home / "config" / "personal.yaml"),
        "employment": {
            "configured": bool(employment),
            "start_date": employment.isoformat() if employment else None,
            "day_number": (target - employment).days + 1 if employment and employment <= target else None,
        },
        "next_rest": _next_rest_day(target, holidays),
        "next_holiday": {**_next_holiday(target, holidays), "source": holiday_source},
        "progress": _progress(target),
        "payday": _payday(target, profile.get("payday_day")),
        "annual_leave": {"configured": leave_configured, "total": leave_total, "used": leave_used,
                         "remaining": leave_remaining},
        "projects": projects,
        "report_streak": streak,
        "longest_agent_day": _longest_agent_day(),
        "token_books": _token_books(latest_report, personal),
        "research_weather": _research_weather(latest_report),
        "last_year_today": _memory_card(target),
        "random_knowledge": _random_knowledge(target, home),
        "gpu_pet": _gpu_pet(ledger, now),
        "milestones": _milestones(target, personal, employment, projects, streak),
        "notes": [
            "Agent 会话跨度可能包含等待和并行时间，不等同于人工专注时长。",
            "Token 换算和科研天气属于趣味展示，不作为效率评价。",
        ],
    }
