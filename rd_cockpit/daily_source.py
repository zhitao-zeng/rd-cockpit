"""Read the user's existing Markdown daily reports as the primary UI source.

The daily-report skill already produces a useful, human-edited structure.  The
cockpit must preserve that structure instead of trying to reconstruct a report
from low-level ledger events or snippets of Agent conversations.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .runtime import daily_report_directory


DEFAULT_REPORT_DIR = daily_report_directory()
DEFAULT_LEGACY_REPORT_DIRS: tuple[Path, ...] = ()
REPORT_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")
# Current audited reports annotate a field's provenance between the bold label
# and the colon (for example ``**结果**（reported）：``).  Treat the qualifier
# as metadata instead of accidentally appending the result to the previous
# ``为什么`` field.
FIELD = re.compile(
    r"^-\s+\*\*(做了什么|为什么|结果|关键文件|证据)\*\*"
    r"(?:[（(][^)）]+[)）])?[：:]\s*(.*)$"
)
MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")

FIELD_KEYS = {
    "做了什么": "did",
    "为什么": "why",
    "结果": "results",
    "关键文件": "files",
    "证据": "evidence",
}

PROJECT_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("image_identify", ("image verification", "图像鉴伪", "图像识别与融合", "trufor")),
    ("avatar_video", ("video generation", "video-generator", "视频生成", "视频批量生成")),
    ("investment_research", ("company research", "企业研究", "研究报告生成")),
    ("research_tools", ("rd-cockpit", "研发驾驶舱", "r&d cockpit", "daily report")),
    ("infrastructure", ("gpu resource", "gpu 资源", "docker maintenance", "研发基础设施")),
    ("ocr", ("ocr", "文字识别", "text recognition")),
    ("obstacle", ("obstacle", "障碍物", "避障")),
    ("resume_copilot", ("document-assistant", "document assistant")),
)

# ASR is not one project in this workspace.  Keep these rules ordered from
# specific repositories/research goals to the generic fallback.  A technique
# such as VAD or hotword boosting stays inside embodied ASR; it is not promoted
# to a project of its own.
ASR_DIALECT_MARKERS = ("dialect asr", "方言模型", "方言识别", "方言 lid")
ASR_ALIGNMENT_PATH_MARKERS: tuple[str, ...] = ()
ASR_ALIGNMENT_MARKERS = ("forced alignment", "强制对齐", "歌词时间戳", "lrc")
ASR_EMBODIED_PATH_MARKERS: tuple[str, ...] = ()
ASR_MODEL_EVAL_PATH_MARKERS: tuple[str, ...] = ()
ASR_EMBODIED_MARKERS = (
    "robot asr", "机器人 asr", "机器人热词", "机器人通道", "hotwords", "hotword", "vad",
)
ASR_MODEL_EVAL_MARKERS = ("asr benchmark", "asr 模型评测", "模型批量评测", "批量评测模型")
ASR_GENERIC_MARKERS = ("asr", "语音识别", "语音感知", "x-asr", "sherpa")

PROJECT_DISPLAY_NAMES = {
    "asr": "ASR",
    "asr_dialect": "Dialect ASR",
    "asr_model_eval": "ASR Evaluation",
    "asr_alignment": "Speech Alignment",
    "asr_other": "ASR / Other",
    "ocr": "OCR",
    "image_identify": "Image Verification",
    "avatar_video": "Video Generation",
    "investment_research": "Company Research",
    "research_tools": "Research Tools",
    "infrastructure": "Infrastructure",
}


def project_display_names() -> dict[str, str]:
    """Merge legacy virtual buckets with the live project registry."""
    from .config import project_catalog
    # A user's private registry owns the display label when IDs overlap.
    return {**PROJECT_DISPLAY_NAMES, **project_catalog()}


def _configured_project_rules() -> list[tuple[str, tuple[str, ...], tuple[str, ...]]]:
    from .config import project_match_rules
    return project_match_rules()

WEAK_TITLE_WORDS = (
    "准备", "整理", "配置", "恢复", "维护", "检查", "清理", "统计", "文档", "文件传输",
    "环境排查", "资源管理", "构建与测试", "代码归档", "输出补录",
)

# Only these concrete repository/path markers are trusted when scanning task
# bodies.  Human-readable aliases above are intentionally reserved for task
# and group headings; otherwise a sentence such as "refer to robot_asr_zh"
# incorrectly turns an OCR task into an ASR task.
DETAIL_PROJECT_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("image_identify", ("trufor",)),
    ("router", ("router/", "model router")),
)


def _strong_alias(project_id: str, alias: str) -> bool:
    """Reject short bare IDs such as ASR/OCR as configured proof."""
    value = alias.strip()
    return not (value.casefold() == project_id.casefold() and value.isascii() and len(value) <= 3)


def _task_text(task: dict[str, Any]) -> str:
    """Return the evidence-bearing text used for project classification."""
    return " ".join(
        [str(task.get("title") or ""),
         *(str(value) for key in ("did", "why", "results", "files", "evidence", "conclusions")
           for value in (task.get(key) or []))]
    )


def _reclassify_report(
    report: dict[str, Any], path: Path, *, apply_project_cache: bool = True,
) -> dict[str, Any]:
    """Correct generic ASR assignments without invoking a model on page load.

    Concrete repository/path evidence wins over an old ``asr_other`` label.
    Remaining ambiguous items may use the one-time cached DeepSeek decision
    produced by :mod:`rd_cockpit.project_classifier`.
    """
    try:
        from .project_classifier import cached_classification
    except ImportError:  # pragma: no cover - installation corruption fallback
        cached_classification = None

    all_project_ids: list[str] = []
    for group in report.get("groups") or []:
        task_ids: list[str] = []
        for task in group.get("tasks") or []:
            current = list(task.get("project_ids") or [])
            configured_paths = _configured_path_ids(_task_text(task))
            configured_heading = [
                project_id for project_id, aliases, _ in _configured_project_rules()
                if any(_strong_alias(project_id, alias)
                       and alias.casefold() in str(task.get("title") or "").casefold()
                       for alias in aliases)
            ]
            if len(configured_paths) == 1:
                # This also fixes historical normalized reports created before
                # the repository was added to the live registry.
                current = configured_paths
                task["classification"] = {
                    "source": "configured_path", "confidence": 1.0,
                    "reason": "关键文件或仓库路径命中项目注册表",
                }
            elif current in ([], ["unassigned"], ["asr_other"]) and len(configured_heading) == 1:
                current = configured_heading
                task["classification"] = {
                    "source": "configured_heading", "confidence": 0.95,
                    "reason": "事项标题命中项目注册表中的唯一别名",
                }
            elif "asr_other" in current:
                detailed = _detail_project_ids(_task_text(task))
                if detailed:
                    # A concrete path is stronger than an old broad model label.
                    current = detailed
                    task["classification"] = {
                        "source": "deterministic_path", "confidence": 1.0,
                        "reason": "关键文件或仓库路径命中明确项目",
                    }
                elif apply_project_cache and cached_classification is not None:
                    decision = cached_classification(path, report.get("date"), task)
                    if decision and float(decision.get("confidence", 0)) >= 0.75:
                        project_id = str(decision.get("project_id") or "")
                        if project_id in project_display_names() and project_id != "asr_other":
                            current = [project_id]
                            task["classification"] = {
                                "source": "llm_cache",
                                "model": decision.get("model"),
                                "confidence": decision.get("confidence"),
                                "reason": decision.get("reason"),
                            }
            task["project_ids"] = list(dict.fromkeys(current))
            task["display_title"] = _enhanced_title(task)
            task_ids.extend(task["project_ids"])
        group["project_ids"] = list(dict.fromkeys(task_ids))
        all_project_ids.extend(group["project_ids"])
    report["project_ids"] = list(dict.fromkeys(all_project_ids))
    return report


def report_directory() -> Path:
    return Path(os.environ.get("RD_DAILY_REPORT_DIR", str(DEFAULT_REPORT_DIR))).expanduser()


def report_directories() -> list[Path]:
    """Return report roots in authority order, excluding duplicate paths."""
    configured = os.environ.get("RD_DAILY_REPORT_LEGACY_DIRS")
    legacy = (
        [Path(value).expanduser() for value in configured.split(os.pathsep) if value.strip()]
        if configured is not None else list(DEFAULT_LEGACY_REPORT_DIRS)
    )
    return list(dict.fromkeys([report_directory(), *legacy]))


def _clean(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^[-*]\s+", "", text)
    text = MARKDOWN_LINK.sub(r"\1", text)
    text = text.replace("**", "").replace("`", "")
    return re.sub(r"\s+", " ", text).strip()


def _asr_project_id(normalized: str, *, allow_generic: bool) -> str | None:
    if any(marker.casefold() in normalized for marker in ASR_DIALECT_MARKERS):
        return "asr_dialect"
    if any(marker.casefold() in normalized for marker in ASR_ALIGNMENT_PATH_MARKERS):
        return "asr_alignment"
    if any(marker.casefold() in normalized for marker in ASR_ALIGNMENT_MARKERS):
        return "asr_alignment"
    if any(marker.casefold() in normalized for marker in ASR_EMBODIED_PATH_MARKERS):
        return "asr"
    if any(marker.casefold() in normalized for marker in ASR_MODEL_EVAL_PATH_MARKERS):
        return "asr_model_eval"
    if any(marker.casefold() in normalized for marker in ASR_EMBODIED_MARKERS):
        return "asr"
    if any(marker.casefold() in normalized for marker in ASR_MODEL_EVAL_MARKERS):
        return "asr_model_eval"
    if allow_generic and any(marker.casefold() in normalized for marker in ASR_GENERIC_MARKERS):
        return "asr_other"
    return None


def _project_ids(text: str, *, allow_generic_asr: bool = True) -> list[str]:
    normalized = text.casefold()
    configured = [
        project_id for project_id, aliases, _ in _configured_project_rules()
        if any(_strong_alias(project_id, alias) and alias.casefold() in normalized for alias in aliases)
    ]
    project_ids: list[str] = list(configured)
    if asr_project := _asr_project_id(normalized, allow_generic=allow_generic_asr):
        project_ids.append(asr_project)
    project_ids.extend(
        project_id for project_id, aliases in PROJECT_ALIASES
        if any(alias.casefold() in normalized for alias in aliases)
    )
    return list(dict.fromkeys(project_ids))


def _detail_project_ids(text: str) -> list[str]:
    normalized = text.casefold()
    project_ids: list[str] = []
    if asr_project := _asr_project_id(normalized, allow_generic=False):
        project_ids.append(asr_project)
    project_ids.extend(
        project_id for project_id, markers in DETAIL_PROJECT_MARKERS
        if any(marker.casefold() in normalized for marker in markers)
    )
    project_ids.extend(
        project_id for project_id, _, paths in _configured_project_rules()
        if any(path.casefold() in normalized for path in paths)
    )
    return list(dict.fromkeys(project_ids))


def _configured_path_ids(text: str) -> list[str]:
    """Return projects backed by a configured repository/path marker."""
    normalized = text.casefold()
    return list(dict.fromkeys(
        project_id for project_id, _, paths in _configured_project_rules()
        if any(path.casefold() in normalized for path in paths)
    ))


def _clip_title_detail(text: str, limit: int = 54) -> str:
    value = re.split(r"[。；\n]", _clean(text), maxsplit=1)[0].strip(" ，,;；")
    return value if len(value) <= limit else f"{value[:limit].rstrip()}…"


def _enhanced_title(task: dict[str, Any]) -> str:
    """Build a readable index title without changing the report's own title."""
    original = task["title"] or "未命名事项"
    names = project_display_names()
    labels = [names.get(project_id, project_id) for project_id in task["project_ids"]]
    project_label = " × ".join(labels) if labels else "其他"
    enhanced = f"{project_label}｜{original}"
    compact = re.sub(r"\s+", "", original).casefold()
    needs_detail = compact[:4].isdigit() or (
        len(original) <= 22 and any(word.casefold() in compact for word in WEAK_TITLE_WORDS)
    )
    if needs_detail:
        candidates = [*task["results"], *task["did"]]
        detail = next((_clip_title_detail(value) for value in candidates if _clip_title_detail(value)), "")
        if detail and detail.casefold() not in original.casefold():
            enhanced = f"{enhanced} — {detail}"
    return enhanced


def available_report_dates(directory: Path | None = None) -> list[str]:
    roots = [directory] if directory is not None else report_directories()
    dates: set[str] = set()
    for root in roots:
        if not root.exists():
            continue
        dates.update(
            match.group(1)
            for path in root.iterdir()
            if path.is_file() and (match := REPORT_NAME.match(path.name))
        )
    return sorted(dates)


def _empty_report(report_date: str | None, directory: Path) -> dict[str, Any]:
    return {
        "available": False,
        "date": report_date,
        "generated_at": None,
        "source_path": None,
        "groups": [],
        "token": {"columns": [], "rows": [], "notes": [], "total_tokens": 0},
        "blockers": [],
        "next": [],
        "plan_closure": [],
        "knowledge": [],
        "data_quality": [],
        "push_summary": "",
        "task_count": 0,
        "project_ids": [],
        "project_names": project_display_names(),
        "message": f"{report_date or '所选日期'} 尚未生成正式日报。" if directory.exists()
        else f"日报目录不存在：{directory}",
    }


def load_report(report_date: str | None = None, directory: Path | None = None) -> dict[str, Any]:
    """Load a report by date, or the latest available report when omitted."""
    roots = [directory] if directory is not None else report_directories()
    dates = available_report_dates(directory)
    selected = report_date or (dates[-1] if dates else None)
    if selected is None:
        return _empty_report(None, roots[0])
    for root in roots:
        path = root / f"{selected}.md"
        if path.exists():
            return parse_report(path)
    return _empty_report(selected, roots[0])


def parse_report(path: Path, *, apply_project_cache: bool = True) -> dict[str, Any]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    date_match = REPORT_NAME.match(path.name)
    report_date = date_match.group(1) if date_match else None
    for line in lines[:5]:
        if match := re.match(r"^#\s+日报\s+(\d{4}-\d{2}-\d{2})", line.strip()):
            report_date = match.group(1)
            break

    groups: list[dict[str, Any]] = []
    blockers: list[str] = []
    next_items: list[str] = []
    push_lines: list[str] = []
    token_columns: list[str] = []
    token_rows: list[dict[str, str]] = []
    token_notes: list[str] = []
    plan_closure: list[str] = []
    knowledge: list[str] = []
    data_quality: list[str] = []

    section: str | None = None
    current_group: dict[str, Any] | None = None
    current_task: dict[str, Any] | None = None
    current_field: str | None = None
    in_token_table = False

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.startswith("## ") and not stripped.startswith("### "):
            title = stripped[3:].strip()
            section = {
                "核心进展": "core",
                "Token 消耗": "token",
                "阻塞 / 待解决": "blockers",
                "明日计划": "next",
                "昨日计划闭环": "closure",
                "关键结论与知识": "knowledge",
                "数据完整性": "data_quality",
                "推送摘要": "push",
            }.get(title, "other")
            current_group = None
            current_task = None
            current_field = None
            in_token_table = False
            continue

        if section == "core":
            if stripped.startswith("### ") and not stripped.startswith("#### "):
                title = _clean(stripped[4:])
                current_group = {"title": title, "project_ids": _project_ids(title), "tasks": []}
                groups.append(current_group)
                current_task = None
                current_field = None
                continue
            if stripped.startswith("#### "):
                if current_group is None:
                    current_group = {"title": "其他进展", "project_ids": [], "tasks": []}
                    groups.append(current_group)
                title = _clean(stripped[5:])
                task_project_ids = _project_ids(title) or list(current_group["project_ids"])
                current_task = {
                    "title": title,
                    "project_ids": task_project_ids,
                    "did": [],
                    "why": [],
                    "results": [],
                    "files": [],
                    "evidence": [],
                }
                current_group["tasks"].append(current_task)
                current_field = None
                continue
            if current_task is not None and (match := FIELD.match(stripped)):
                current_field = FIELD_KEYS[match.group(1)]
                value = _clean(match.group(2))
                if value:
                    current_task[current_field].append(value)
                continue
            if current_task is not None and current_field and stripped and not stripped.startswith("---"):
                # Multi-line result/file lists are common in older reports.
                if line.startswith((" ", "\t")) or stripped.startswith(("- ", "* ")):
                    value = _clean(stripped)
                    if value:
                        current_task[current_field].append(value)
            continue

        if section == "token":
            if stripped.startswith("|"):
                cells = [cell.strip() for cell in stripped.strip("|").split("|")]
                if cells and all(re.fullmatch(r"[-:]+", cell) for cell in cells):
                    in_token_table = True
                    continue
                if not token_columns:
                    token_columns = [_clean(cell) for cell in cells]
                    continue
                if in_token_table and len(cells) == len(token_columns):
                    token_rows.append(dict(zip(token_columns, (_clean(cell) for cell in cells))))
                continue
            if stripped.startswith(("- ", "* ")):
                token_notes.append(_clean(stripped))
            continue

        # These sections often contain indented reason/evidence bullets.  The
        # overview counters and cards should represent top-level records only,
        # not count each explanatory child as a separate plan or blocker.
        is_top_level_bullet = line.startswith(("- ", "* "))
        if section == "blockers" and is_top_level_bullet:
            blockers.append(_clean(stripped))
        elif section == "next" and is_top_level_bullet:
            next_items.append(_clean(stripped))
        elif section == "closure" and is_top_level_bullet:
            plan_closure.append(_clean(stripped))
        elif section == "knowledge" and is_top_level_bullet:
            knowledge.append(_clean(stripped))
        elif section == "data_quality" and is_top_level_bullet:
            data_quality.append(_clean(stripped))
        elif section == "push" and stripped:
            push_lines.append(_clean(stripped))

    # Reclassify after the whole task has been read.  Paths and results often
    # carry the only reliable distinction between dialect competition,
    # embodied deployment, general model evaluation and alignment work.
    all_project_ids: list[str] = []
    for group in groups:
        task_ids: list[str] = []
        for task in group["tasks"]:
            task_text = " ".join(
                [task["title"], *task["did"], *task["why"], *task["results"],
                 *task["files"], *task["evidence"]]
            )
            # Bare mentions such as "free GPU for a later ASR run" or
            # "skip disabled ASR websocket" do not make an unrelated task an
            # ASR task.  Generic ASR is accepted only from the task/group
            # heading; specific repositories and research markers may come
            # from any structured field.
            detailed_ids = _detail_project_ids(task_text)
            task_heading_ids = _project_ids(task["title"])
            if task_heading_ids:
                # A concrete ASR repository may refine an otherwise generic
                # "ASR" heading, but unrelated paths mentioned in the body do
                # not add extra projects to a clearly titled task.
                specific_asr = next(
                    (project_id for project_id in detailed_ids
                     if project_id in {"asr", "asr_dialect", "asr_model_eval", "asr_alignment"}),
                    None,
                )
                if "asr_other" in task_heading_ids and specific_asr:
                    task_heading_ids = [specific_asr if value == "asr_other" else value
                                        for value in task_heading_ids]
                task["project_ids"] = list(dict.fromkeys(task_heading_ids))
                task_ids.extend(task["project_ids"])
                continue
            group_ids = _project_ids(group["title"])
            if detailed_ids:
                overlap = [project_id for project_id in detailed_ids if project_id in group_ids]
                task["project_ids"] = overlap or detailed_ids
            else:
                task["project_ids"] = group_ids
            task_ids.extend(task["project_ids"])
        group["project_ids"] = list(dict.fromkeys(task_ids or _project_ids(group["title"])))
        for task in group["tasks"]:
            task["display_title"] = _enhanced_title(task)
        all_project_ids.extend(group["project_ids"])

    total_tokens = 0
    for row in token_rows:
        raw_total = row.get("总量", "0").replace(",", "")
        if raw_total.isdigit():
            total_tokens += int(raw_total)

    generated_at = datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat()
    report = {
        "available": True,
        "date": report_date,
        "generated_at": generated_at,
        "source_path": str(path),
        "groups": groups,
        "token": {"columns": token_columns, "rows": token_rows, "notes": token_notes,
                  "total_tokens": total_tokens},
        "blockers": blockers,
        "next": next_items,
        "plan_closure": plan_closure,
        "knowledge": knowledge,
        "data_quality": data_quality,
        "push_summary": "\n\n".join(push_lines),
        "task_count": sum(len(group["tasks"]) for group in groups),
        "project_ids": list(dict.fromkeys(all_project_ids)),
        "project_names": project_display_names(),
        "message": None,
    }
    # Legacy reports can have a model-normalized sidecar.  The Markdown remains
    # the source of truth; a stale sidecar is ignored automatically by SHA256.
    try:
        from .historical_reports import apply_normalized
        report = apply_normalized(report, path)
    except Exception:
        pass
    return _reclassify_report(report, path, apply_project_cache=apply_project_cache)


def iter_reports(directory: Path | None = None, *, since: str | None = None) -> list[dict[str, Any]]:
    dates = [value for value in available_report_dates(directory) if since is None or value >= since]
    return [load_report(value, directory) for value in dates]
