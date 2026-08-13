"""Recent-paper radar grounded in configured project research topics.

The radar deliberately reports *relevance*, not support or contradiction.  A
title/metadata search is not enough evidence to claim that a paper confirms a
local result.  That stronger classification can be added only after reading
the paper content.
"""

from __future__ import annotations

import json
import os
import html
import math
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .config import load_config
from .daily_source import load_report

OPENALEX_WORKS_URL = "https://api.openalex.org/works"
SCHEMA_VERSION = 4
DEFAULT_LOOKBACK_DAYS = 180
DEFAULT_CACHE_HOURS = 24
DEFAULT_SUMMARY_TIMEOUT = 180.0
DEFAULT_CANDIDATES_PER_TOPIC = 12
DEFAULT_ITEMS_PER_PROJECT = 5
MINIMUM_RECOMMENDATION_SCORE = 42

EXCLUDED_WORK_TYPES = {
    "book-review", "editorial", "erratum", "letter", "paratext",
    "reference-entry", "retraction",
}
LOW_SIGNAL_SOURCE_PATTERNS = (
    "zenodo", "multidisciplinary", "dmpedia", "book series",
    "research square", "preprints.org",
)
ACTIONABLE_TERMS = (
    "ablation", "benchmark", "code", "dataset", "deployment", "efficient",
    "edge", "latency", "lightweight", "open-source", "quantization",
    "real-time", "robust", "runtime", "throughput",
)

Fetcher = Callable[[str], dict[str, Any]]
SummaryResult = tuple[dict[str, dict[str, Any]], dict[str, Any], list[str]]
Summarizer = Callable[[list[dict[str, Any]]], SummaryResult]


def _iso_now(now: datetime | None = None) -> datetime:
    value = now or datetime.now(timezone.utc)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _fetch_json(url: str) -> dict[str, Any]:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "rd-cockpit/0.1"})
    with urlopen(request, timeout=20) as response:  # noqa: S310 - target URL is fixed to OpenAlex
        value = json.load(response)
    if not isinstance(value, dict):
        raise ValueError("OpenAlex returned a non-object response")
    return value


def _cache_path(home: Path) -> Path:
    return home / ".rd-cockpit" / "research-radar.json"


def _load_cache(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) and value.get("schema_version") == SCHEMA_VERSION else None
    except (OSError, json.JSONDecodeError):
        return None


def _write_cache(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _radar_topics(config: dict[str, Any], project: str | None = None) -> list[dict[str, Any]]:
    topics: list[dict[str, Any]] = []
    for project_id, project_config in config.get("projects", {}).items():
        if project and project_id != project:
            continue
        for raw in project_config.get("research_radar", []):
            if not isinstance(raw, dict) or not raw.get("query"):
                continue
            topics.append({
                "project_id": project_id,
                "project_name": str(project_config.get("name") or project_id),
                "label": str(raw.get("label") or raw["query"]),
                "query": str(raw["query"]),
                "why": str(raw.get("why") or "与该项目当前研究方向相关"),
                "title_keywords": [str(value).casefold() for value in raw.get("title_keywords", []) if value],
                "preferred_venues": [
                    str(value).casefold() for value in
                    (raw.get("preferred_venues") or project_config.get("preferred_venues") or []) if value
                ],
                "practical_keywords": [
                    str(value).casefold() for value in
                    (raw.get("practical_keywords") or project_config.get("practical_keywords") or []) if value
                ],
            })
    return topics


def _local_context(project_id: str) -> list[str]:
    """Return readable anchors from the latest human-facing daily report."""
    report = load_report()
    anchors: list[str] = []
    for group in report.get("groups", []):
        for task in group.get("tasks", []):
            if project_id not in task.get("project_ids", []):
                continue
            results = task.get("results") or task.get("did") or []
            text = str(results[0]) if results else str(task.get("title") or "")
            if text and text not in anchors:
                anchors.append(text)
            if len(anchors) == 2:
                return anchors
    return anchors


def _authors(raw: Any) -> list[str]:
    output: list[str] = []
    for authorship in raw if isinstance(raw, list) else []:
        if not isinstance(authorship, dict):
            continue
        author = authorship.get("author")
        name = author.get("display_name") if isinstance(author, dict) else None
        if name:
            output.append(str(name))
        if len(output) == 4:
            break
    return output


def _work_url(work: dict[str, Any]) -> tuple[str, str | None]:
    location = work.get("primary_location") if isinstance(work.get("primary_location"), dict) else {}
    landing = location.get("landing_page_url")
    pdf = location.get("pdf_url")
    return str(landing or work.get("doi") or work.get("id") or ""), str(pdf) if pdf else None


def _source_name(work: dict[str, Any]) -> str | None:
    location = work.get("primary_location") if isinstance(work.get("primary_location"), dict) else {}
    source = location.get("source") if isinstance(location.get("source"), dict) else {}
    name = source.get("display_name") or location.get("raw_source_name")
    return str(name) if name else None


def _abstract_text(work: dict[str, Any]) -> str | None:
    index = work.get("abstract_inverted_index")
    if not isinstance(index, dict) or not index:
        return None
    positioned: list[tuple[int, str]] = []
    for word, raw_positions in index.items():
        if not isinstance(raw_positions, list):
            continue
        for position in raw_positions:
            if isinstance(position, int):
                positioned.append((position, str(word)))
    if not positioned:
        return None
    positioned.sort(key=lambda item: item[0])
    return " ".join(word for _, word in positioned)


def _query_terms(query: str) -> list[str]:
    stop = {"automatic", "computer", "large", "model", "models", "system", "using", "with"}
    return list(dict.fromkeys(
        value for value in re.findall(r"[a-z][a-z0-9+-]{2,}", query.casefold()) if value not in stop
    ))


def _score_work(
    work: dict[str, Any], topic: dict[str, Any], *, rank: int, result_count: int,
    title: str, abstract: str | None, venue: str | None, pdf_url: str | None,
) -> dict[str, Any]:
    """Score relevance, research quality and practical value separately.

    The score is intentionally based only on metadata and abstract text.  It is
    a reading-priority signal, not a claim that the paper's conclusions are
    correct.
    """
    title_folded = title.casefold()
    abstract_folded = (abstract or "").casefold()
    combined = f"{title_folded} {abstract_folded}"
    reasons: list[str] = []
    risks: list[str] = []

    # Relevance: OpenAlex rank plus explicit title/abstract matches.  Rank is
    # normalized inside each configured topic so unrelated popularity cannot
    # dominate project fit.
    rank_score = 18.0 * max(0.0, 1.0 - rank / max(1, result_count))
    keywords = topic.get("title_keywords") or _query_terms(topic["query"])
    title_hits = sum(1 for value in keywords if value in title_folded)
    abstract_hits = sum(1 for value in keywords if value in abstract_folded)
    relevance = rank_score + min(14.0, title_hits * 7.0) + min(8.0, abstract_hits * 2.0)
    relevance = round(min(40.0, relevance), 1)
    if title_hits:
        reasons.append("标题直接命中项目研究主题")

    # Quality: use venue, publication metadata, reproducibility access and
    # age-tolerant impact signals.  Zero citations on a very recent paper is a
    # risk note, not an automatic rejection.
    quality = 0.0
    venue_folded = (venue or "").casefold()
    preferred = next((value for value in topic.get("preferred_venues", []) if value in venue_folded), None)
    if preferred:
        quality += 14
        reasons.append("来自该方向优先会议或期刊")
    elif venue:
        quality += 4
    else:
        risks.append("OpenAlex 未提供明确发表 venue")
    weak_source = next((value for value in LOW_SIGNAL_SOURCE_PATTERNS if value in venue_folded), None)
    if weak_source:
        quality -= 8
        risks.append("来源主要是通用仓储或弱发表信号，需额外核验")
    work_type = str(work.get("type") or "unknown")
    if work_type in {"article", "conference-paper"}:
        quality += 4
    elif work_type == "preprint":
        quality += 1
        risks.append("当前记录是预印本，尚未体现同行评审状态")
    if abstract:
        quality += 4
    else:
        risks.append("缺少摘要，当前只能根据标题和元数据判断")
    if work.get("doi"):
        quality += 2
    if pdf_url or work.get("has_fulltext"):
        quality += 3
    if int(work.get("institutions_distinct_count") or 0) > 0:
        quality += 1
    cited = int(work.get("cited_by_count") or 0)
    if cited:
        quality += min(5.0, math.log2(cited + 1) * 1.6)
        reasons.append("已有早期引用信号")
    else:
        risks.append("论文较新或尚无引用，影响力信号不足")
    fwci = float(work.get("fwci") or 0.0)
    if fwci > 1:
        quality += min(2.0, math.log2(fwci + 1))
    quality = round(max(0.0, min(35.0, quality)), 1)

    # Practical value: prefer work that exposes evaluable or deployable
    # details and matches project-specific implementation concerns.
    actionable_hits = [value for value in ACTIONABLE_TERMS if value in combined]
    practical_hits = [value for value in topic.get("practical_keywords", []) if value in combined]
    practical = min(12.0, len(actionable_hits) * 2.0) + min(10.0, len(practical_hits) * 3.0)
    if pdf_url or work.get("has_fulltext"):
        practical += 3
    practical = round(min(25.0, practical), 1)
    if actionable_hits or practical_hits:
        reasons.append("摘要包含可评测、可部署或项目特定信息")
    else:
        risks.append("摘要中暂未看到对当前实现可直接落地的信号")

    total = round(relevance + quality + practical, 1)
    # A high keyword match cannot compensate for weak publication evidence.
    # Quality floors prevent highly relevant but poorly supported uploads from
    # being presented as must-read papers.
    tier = (
        "A" if total >= 75 and quality >= 20
        else "B" if total >= 58 and quality >= 15
        else "C" if total >= MINIMUM_RECOMMENDATION_SCORE and quality >= 8
        else "D"
    )
    return {
        "relevance_score": relevance,
        "quality_score": quality,
        "practical_score": practical,
        "total_score": total,
        "quality_tier": tier,
        "quality_reasons": list(dict.fromkeys(reasons))[:4],
        "quality_risks": list(dict.fromkeys(risks))[:4],
        "preferred_venue": bool(preferred),
    }


def _normalize_work(
    work: dict[str, Any], topic: dict[str, Any], context: list[str], *, rank: int, result_count: int,
) -> dict[str, Any] | None:
    title = work.get("display_name") or work.get("title")
    if not title or not work.get("id"):
        return None
    if work.get("is_retracted") or work.get("is_paratext") or str(work.get("type") or "") in EXCLUDED_WORK_TYPES:
        return None
    title_text = html.unescape(str(title))
    title_keywords = topic.get("title_keywords") or []
    if title_keywords and not any(keyword in title_text.casefold() for keyword in title_keywords):
        return None
    url, pdf_url = _work_url(work)
    abstract = _abstract_text(work)
    venue = _source_name(work)
    score = _score_work(
        work, topic, rank=rank, result_count=result_count, title=title_text,
        abstract=abstract, venue=venue, pdf_url=pdf_url,
    )
    return {
        "id": str(work["id"]),
        "project_id": topic["project_id"],
        "project_name": topic["project_name"],
        "focus": topic["label"],
        "title": title_text,
        "publication_date": work.get("publication_date"),
        "authors": _authors(work.get("authorships")),
        "venue": venue,
        "cited_by_count": int(work.get("cited_by_count") or 0),
        "fwci": float(work.get("fwci") or 0.0),
        "work_type": str(work.get("type") or "unknown"),
        "has_fulltext": bool(work.get("has_fulltext") or pdf_url),
        "url": url,
        "pdf_url": pdf_url,
        "doi": work.get("doi"),
        "why_relevant": topic["why"],
        "local_context": context,
        "relationship": "可能相关，待阅读确认",
        "abstract": abstract[:2400] if abstract else None,
        "title_zh": None,
        "summary_zh": None,
        "key_points_zh": [],
        "read_value_zh": None,
        "summary_basis": "abstract" if abstract else "title_metadata",
        "summary_model": None,
        **score,
    }


def _json_object(text: str) -> dict[str, Any]:
    value = text.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value, flags=re.IGNORECASE | re.DOTALL).strip()
    try:
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for index, character in enumerate(value):
        if character != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(value[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("summary model output does not contain a JSON object")


def _request_chinese_summaries(
    papers: list[dict[str, Any]], model: str, *, timeout: float = DEFAULT_SUMMARY_TIMEOUT,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    endpoint = os.environ.get("RD_RADAR_LLM_URL", "http://127.0.0.1:4000/v1/messages")
    system = (
        "你是个人科研雷达的中文论文编辑。只根据给定标题、摘要和项目上下文生成中文速读，"
        "禁止补充输入中没有的方法、指标或结论。输出 JSON，不要 Markdown。专业术语可保留英文括注。"
    )
    compact = [{
        "id": item["id"],
        "title": item["title"],
        "abstract": (item.get("abstract") or "")[:1800],
        "project": item["project_name"],
        "focus": item["focus"],
        "why_relevant": item["why_relevant"],
        "recent_work": item.get("local_context", [])[:2],
        "quality_tier": item.get("quality_tier"),
        "score_breakdown": {
            "relevance": item.get("relevance_score"),
            "quality": item.get("quality_score"),
            "practical": item.get("practical_score"),
        },
        "quality_reasons": item.get("quality_reasons", []),
        "quality_risks": item.get("quality_risks", []),
    } for item in papers]
    instruction = {
        "task": "为每篇论文生成降低阅读负担的中文导读",
        "output_schema": {
            "papers": [{
                "id": "原样复制输入 id",
                "title_zh": "准确、自然的中文标题",
                "summary_zh": "2到3句、80到160字；说明研究问题、方法方向和可能贡献。没有摘要时明确说仅据标题判断",
                "key_points_zh": ["2到3条，每条不超过45字"],
                "read_value_zh": "1句话说明它对当前项目可能有什么阅读价值，不得声称已支持或推翻本地结论",
            }],
        },
        "papers": compact,
    }
    payload = {
        "model": model,
        "max_tokens": min(12000, max(1600, len(papers) * 650)),
        "temperature": 0.1,
        "stream": False,
        "system": system,
        "messages": [{"role": "user", "content": json.dumps(instruction, ensure_ascii=False)}],
    }
    request = Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-api-key": "local-router",
                 "anthropic-version": "2023-06-01"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - configurable localhost router
            outer = json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc
    content = outer.get("content") if isinstance(outer, dict) else None
    text = "".join(
        str(block.get("text") or "") for block in content or []
        if isinstance(block, dict) and block.get("type") == "text"
    )
    parsed = _json_object(text)
    expected = {str(item["id"]) for item in papers}
    results: dict[str, dict[str, Any]] = {}
    for raw in parsed.get("papers") or []:
        if not isinstance(raw, dict) or str(raw.get("id")) not in expected:
            continue
        title_zh = str(raw.get("title_zh") or "").strip()
        summary_zh = str(raw.get("summary_zh") or "").strip()
        points = [str(value).strip() for value in raw.get("key_points_zh") or [] if str(value).strip()]
        read_value = str(raw.get("read_value_zh") or "").strip()
        if not title_zh or not summary_zh:
            continue
        results[str(raw["id"])] = {
            "title_zh": title_zh[:300],
            "summary_zh": summary_zh[:800],
            "key_points_zh": points[:3],
            "read_value_zh": read_value[:400] or None,
            "summary_model": model,
        }
    if not results:
        raise ValueError("summary model returned no usable paper summaries")
    usage = outer.get("usage") if isinstance(outer, dict) and isinstance(outer.get("usage"), dict) else {}
    return results, {"model": model, "usage": usage}


def _summarize_papers(papers: list[dict[str, Any]]) -> SummaryResult:
    primary = os.environ.get("RD_RADAR_SUMMARY_MODEL", "deepseek-local").strip()
    fallback = os.environ.get("RD_RADAR_SUMMARY_FALLBACK_MODEL", "deepseek").strip()
    models = list(dict.fromkeys(value for value in (primary, fallback) if value))
    remaining = list(papers)
    summaries: dict[str, dict[str, Any]] = {}
    attempts: list[dict[str, Any]] = []
    warnings: list[str] = []
    for model in models:
        if not remaining:
            break
        try:
            values, metadata = _request_chinese_summaries(remaining, model)
        except Exception as exc:
            attempts.append({"model": model, "status": "failed", "error": f"{type(exc).__name__}: {exc}"})
            continue
        summaries.update(values)
        attempts.append({"model": model, "status": "ok", "generated": len(values),
                         "usage": metadata.get("usage", {})})
        remaining = [item for item in remaining if item["id"] not in summaries]
    if remaining:
        warnings.append(f"{len(remaining)} 篇论文暂未生成中文摘要，可展开查看英文原文。")
    return summaries, {
        "primary_model": primary,
        "fallback_model": fallback or None,
        "attempts": attempts,
        "generated_count": len(summaries),
        "missing_count": len(remaining),
        "fallback_used": any(item.get("model") == fallback and item.get("status") == "ok" for item in attempts),
    }, warnings


def _query_url(query: str, start: date, end: date, per_page: int) -> str:
    filters = ",".join((
        f"from_publication_date:{start.isoformat()}",
        f"to_publication_date:{end.isoformat()}",
        f"title_and_abstract.search:{query}",
    ))
    params = {
        "filter": filters,
        "sort": "relevance_score:desc",
        "per-page": per_page,
        "select": (
            "id,doi,title,display_name,abstract_inverted_index,publication_date,cited_by_count,"
            "authorships,primary_location,type,is_retracted,is_paratext,fwci,has_fulltext,"
            "institutions_distinct_count,primary_topic,relevance_score"
        ),
    }
    api_key = os.environ.get("OPENALEX_API_KEY")
    if api_key:
        params["api_key"] = api_key
    return f"{OPENALEX_WORKS_URL}?{urlencode(params)}"


def _select_recommendations(
    candidates: list[dict[str, Any]], previously_seen: set[str], *, per_project: int,
    generated_at: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Keep quality anchors while rotating never-shown eligible papers.

    Refreshing should not be random: the best papers remain visible, and the
    discovery slots move through the qualified backlog.  Once that backlog is
    exhausted, returning the same anchors is more honest than injecting weak
    papers merely to make the page look fresh.
    """
    eligible = [
        item for item in candidates
        if item.get("quality_tier") != "D" and float(item.get("total_score") or 0) >= MINIMUM_RECOMMENDATION_SCORE
    ]
    by_project: dict[str, list[dict[str, Any]]] = {}
    for item in eligible:
        by_project.setdefault(str(item["project_id"]), []).append(item)

    selected: list[dict[str, Any]] = []
    new_count = 0
    retained_anchors = 0
    for project_id in sorted(by_project):
        ranked = sorted(
            by_project[project_id],
            key=lambda item: (
                float(item.get("total_score") or 0),
                str(item.get("publication_date") or ""),
            ),
            reverse=True,
        )
        project_items: list[dict[str, Any]] = []
        # Two stable A/B anchors keep genuinely strong papers from vanishing on
        # every refresh.
        for item in [value for value in ranked if value.get("quality_tier") in {"A", "B"}][:2]:
            if len(project_items) >= per_project:
                break
            project_items.append(item)
            if item["id"] in previously_seen:
                retained_anchors += 1

        # Discovery positions prefer papers that have never appeared before.
        for item in ranked:
            if len(project_items) >= per_project:
                break
            if item in project_items or item["id"] in previously_seen:
                continue
            project_items.append(item)

        # If the qualified backlog is exhausted, fill with the best previous
        # candidates rather than lowering the quality threshold.
        for item in ranked:
            if len(project_items) >= per_project:
                break
            if item not in project_items:
                project_items.append(item)

        for item in project_items:
            item["is_new"] = item["id"] not in previously_seen
            item["first_seen_at"] = generated_at if item["is_new"] else None
            new_count += int(item["is_new"])
            selected.append(item)

    selected.sort(key=lambda item: (
        str(item.get("project_id") or ""),
        -float(item.get("total_score") or 0),
        str(item.get("publication_date") or ""),
    ))
    return selected, {
        "candidate_count": len(candidates),
        "eligible_count": len(eligible),
        "excluded_count": len(candidates) - len(eligible),
        "new_item_count": new_count,
        "retained_anchor_count": retained_anchors,
        "per_project": per_project,
        "minimum_score": MINIMUM_RECOMMENDATION_SCORE,
        "method": "每个项目保留最多 2 篇 A/B 高质量锚点，其余位置优先展示从未出现过的合格论文；不以随机水文制造更新感。",
    }


def _filtered(value: dict[str, Any], project: str | None) -> dict[str, Any]:
    output = dict(value)
    # These fields are persisted only to support rotation and summary reuse;
    # duplicating them in every browser response would make the API grow with
    # the entire reading history.
    output.pop("seen_work_ids", None)
    output.pop("summary_store", None)
    if not project:
        return output
    output["items"] = [item for item in value.get("items", []) if item.get("project_id") == project]
    output["projects"] = {key: item for key, item in value.get("projects", {}).items() if key == project}
    output["item_count"] = len(output["items"])
    return output


def research_radar(
    home: Path,
    project: str | None = None,
    *,
    refresh: bool = False,
    now: datetime | None = None,
    fetcher: Fetcher | None = None,
    summarizer: Summarizer | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    per_topic: int = DEFAULT_CANDIDATES_PER_TOPIC,
    per_project: int = DEFAULT_ITEMS_PER_PROJECT,
) -> dict[str, Any]:
    """Build or read the cached project research radar."""
    current = _iso_now(now)
    cache_file = _cache_path(home)
    cached = _load_cache(cache_file)
    if cached and not refresh:
        try:
            expires = datetime.fromisoformat(str(cached["expires_at"]))
            if expires > current:
                result = dict(cached)
                result["cached"] = True
                return _filtered(result, project)
        except (KeyError, ValueError, TypeError):
            pass

    config = load_config(home / "config" / "projects.yaml")
    if project and project not in config.get("projects", {}):
        raise KeyError(project)
    topics = _radar_topics(config)
    client = fetcher or _fetch_json
    start = current.date() - timedelta(days=max(1, lookback_days))
    end = current.date()
    items: list[dict[str, Any]] = []
    warnings: list[str] = []
    project_status: dict[str, dict[str, Any]] = {}

    for topic in topics:
        project_status.setdefault(topic["project_id"], {
            "name": topic["project_name"], "topics": [], "result_count": 0,
        })["topics"].append(topic["label"])
        try:
            payload = client(_query_url(topic["query"], start, end, per_topic))
            context = _local_context(topic["project_id"])
            raw_results = [value for value in payload.get("results", []) if isinstance(value, dict)]
            for rank, raw in enumerate(raw_results):
                if item := _normalize_work(raw, topic, context, rank=rank, result_count=len(raw_results)):
                    items.append(item)
        except Exception as exc:  # keep independent topics usable when one query fails
            warnings.append(f"{topic['project_name']} / {topic['label']}：{type(exc).__name__}: {exc}")

    # The same work can match multiple configured phrases. Keep the strongest
    # project-specific match rather than whichever query happened to run first.
    best_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        normalized_title = re.sub(r"\W+", "", item["title"].casefold())
        key = (item["project_id"], normalized_title or str(item.get("doi") or item["id"]))
        previous = best_by_key.get(key)
        if previous is None or float(item.get("total_score") or 0) > float(previous.get("total_score") or 0):
            best_by_key[key] = item
    candidates = list(best_by_key.values())

    seen_work_ids = [str(value) for value in (cached or {}).get("seen_work_ids", []) if value]
    seen_work_ids.extend(
        str(item.get("id")) for item in (cached or {}).get("items", []) if item.get("id")
    )
    seen_work_ids = list(dict.fromkeys(seen_work_ids))
    previously_seen = set(seen_work_ids)
    deduplicated, selection = _select_recommendations(
        candidates, previously_seen, per_project=max(1, per_project), generated_at=current.isoformat(),
    )
    for value in project_status.values():
        value["result_count"] = 0
    for item in deduplicated:
        project_status[item["project_id"]]["result_count"] += 1

    cached_items = {
        str(key): value for key, value in (cached or {}).get("summary_store", {}).items()
        if isinstance(value, dict)
    }
    cached_items.update({
        str(item.get("id")): item for item in (cached or {}).get("items", []) if item.get("id")
    })
    pending_summaries: list[dict[str, Any]] = []
    reused_summaries = 0
    summary_fields = ("title_zh", "summary_zh", "key_points_zh", "read_value_zh", "summary_model")
    for item in deduplicated:
        previous = cached_items.get(item["id"])
        if previous and previous.get("title") == item.get("title") and previous.get("summary_zh"):
            for field in summary_fields:
                item[field] = previous.get(field)
            reused_summaries += 1
        else:
            pending_summaries.append(item)

    summary_generation: dict[str, Any] = {
        "generated_count": 0, "reused_count": reused_summaries,
        "missing_count": len(pending_summaries), "attempts": [], "fallback_used": False,
    }
    if pending_summaries:
        try:
            values, summary_generation, summary_warnings = (summarizer or _summarize_papers)(pending_summaries)
            summary_generation["reused_count"] = reused_summaries
            warnings.extend(summary_warnings)
            for item in pending_summaries:
                if item["id"] in values:
                    item.update(values[item["id"]])
        except Exception as exc:
            warnings.append(f"中文摘要生成失败：{type(exc).__name__}: {exc}")

    summary_store = dict(cached_items)
    summary_fields = ("title", "title_zh", "summary_zh", "key_points_zh", "read_value_zh", "summary_model")
    for item in deduplicated:
        if item.get("summary_zh"):
            summary_store[item["id"]] = {field: item.get(field) for field in summary_fields}
    if len(summary_store) > 500:
        summary_store = dict(list(summary_store.items())[-500:])

    if not candidates and cached:
        result = dict(cached)
        result["cached"] = True
        result["stale"] = True
        result["warnings"] = [*warnings, "本次更新失败，正在显示上一次缓存。"]
        return _filtered(result, project)

    try:
        cache_hours = max(1, int(os.environ.get("RD_RADAR_CACHE_HOURS", DEFAULT_CACHE_HOURS)))
    except ValueError:
        cache_hours = DEFAULT_CACHE_HOURS
    expires_at = current + timedelta(hours=cache_hours)
    result = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": current.isoformat(),
        "expires_at": expires_at.isoformat(),
        "source": "OpenAlex",
        "source_url": "https://openalex.org/",
        "lookback_days": max(1, lookback_days),
        "cache_hours": cache_hours,
        "cached": False,
        "stale": False,
        "projects": project_status,
        "items": deduplicated,
        "item_count": len(deduplicated),
        "selection": selection,
        "seen_work_ids": list(dict.fromkeys([*seen_work_ids, *(item["id"] for item in deduplicated)]))[-2000:],
        "summary_store": summary_store,
        "warnings": warnings,
        "summary_generation": summary_generation,
        "explanation": (
            "先从近期候选中分别评估项目相关度、研究质量和实际价值，再保留高质量锚点并轮换未展示过的合格论文。"
            "评分只用于阅读优先级，不自动声称论文支持或推翻本地结论。"
        ),
    }
    _write_cache(cache_file, result)
    return _filtered(result, project)
