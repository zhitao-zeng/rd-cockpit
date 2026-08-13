from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import rd_cockpit.research_radar as radar


def _home(tmp_path: Path) -> Path:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "projects.yaml").write_text(
        """projects:
  asr:
    name: ASR
    repo_path: /tmp/asr
    research_radar:
      - label: Streaming ASR
        query: '\"streaming automatic speech recognition\"'
        why: Follow low-latency ASR
    verification_stages: []
  ocr:
    name: OCR
    repo_path: /tmp/ocr
    research_radar:
      - label: Scene text
        query: '\"scene text recognition\"'
        why: Follow scene text recognition
    verification_stages: []
""",
        encoding="utf-8",
    )
    return tmp_path


def _openalex_result(title: str, work_id: str) -> dict:
    return {
        "id": f"https://openalex.org/{work_id}",
        "doi": f"https://doi.org/10.1/{work_id.lower()}",
        "title": title,
        "display_name": title,
        "abstract_inverted_index": {"This": [0], "paper": [1], "tests": [2], "a": [3], "method": [4]},
        "publication_date": "2026-07-20",
        "cited_by_count": 3,
        "authorships": [{"author": {"display_name": "Ada Researcher"}}],
        "primary_location": {
            "landing_page_url": f"https://example.test/{work_id}",
            "pdf_url": f"https://example.test/{work_id}.pdf",
            "source": {"display_name": "Example Journal"},
        },
    }


def _summarizer(papers: list[dict]) -> radar.SummaryResult:
    return ({
        paper["id"]: {
            "title_zh": f"中文：{paper['title']}",
            "summary_zh": "这是一段基于论文摘要生成的中文速读，用于测试缓存和页面展示。",
            "key_points_zh": ["研究问题", "方法方向"],
            "read_value_zh": "可用于判断是否值得进一步阅读全文。",
            "summary_model": "test-model",
        } for paper in papers
    }, {"generated_count": len(papers), "missing_count": 0, "attempts": [], "fallback_used": False}, [])


def test_research_radar_fetches_topics_and_uses_cache(tmp_path: Path, monkeypatch) -> None:
    home = _home(tmp_path)
    monkeypatch.setattr(radar, "load_report", lambda: {
        "groups": [{"tasks": [{"project_ids": ["asr"], "title": "Streaming", "results": ["RTF improved"]}]}],
    })
    calls: list[str] = []

    def fetcher(url: str) -> dict:
        calls.append(url)
        query = parse_qs(urlparse(url).query)["filter"][0]
        if "streaming" in query:
            return {"results": [_openalex_result("A streaming ASR paper", "W1")]}
        return {"results": [_openalex_result("A scene text paper", "W2")]}

    now = datetime(2026, 8, 3, 8, tzinfo=timezone.utc)
    first = radar.research_radar(home, now=now, fetcher=fetcher, summarizer=_summarizer)
    assert first["item_count"] == 2
    assert first["items"][0]["relationship"] == "可能相关，待阅读确认"
    asr = next(item for item in first["items"] if item["project_id"] == "asr")
    assert asr["local_context"] == ["RTF improved"]
    assert asr["abstract"] == "This paper tests a method"
    assert asr["title_zh"].startswith("中文：")
    assert asr["summary_zh"].startswith("这是一段")
    assert first["summary_generation"]["generated_count"] == 2
    assert len(calls) == 2

    second = radar.research_radar(home, project="asr", now=now, fetcher=lambda _: (_ for _ in ()).throw(AssertionError()))
    assert second["cached"] is True
    assert second["item_count"] == 1
    assert second["items"][0]["project_id"] == "asr"


def test_research_radar_falls_back_to_stale_cache_on_network_error(tmp_path: Path, monkeypatch) -> None:
    home = _home(tmp_path)
    monkeypatch.setattr(radar, "load_report", lambda: {"groups": []})
    now = datetime(2026, 8, 3, 8, tzinfo=timezone.utc)
    def fetcher(url: str) -> dict:
        query = parse_qs(urlparse(url).query)["filter"][0]
        title = "A streaming speech recognition paper" if "streaming" in query else "A scene text recognition paper"
        return {"results": [_openalex_result(title, "W1" if "streaming" in query else "W2")]}

    radar.research_radar(
        home,
        now=now,
        fetcher=fetcher,
        summarizer=_summarizer,
    )

    result = radar.research_radar(
        home,
        refresh=True,
        now=now,
        fetcher=lambda url: (_ for _ in ()).throw(OSError("offline")),
    )
    assert result["stale"] is True
    assert result["item_count"] == 2  # one cached result for each configured topic
    assert "显示上一次缓存" in result["warnings"][-1]


def test_selection_retains_quality_anchors_and_rotates_unseen_candidates() -> None:
    def paper(work_id: str, score: float, tier: str) -> dict:
        return {
            "id": work_id, "project_id": "asr", "publication_date": "2026-08-01",
            "total_score": score, "quality_tier": tier,
        }

    candidates = [
        paper("anchor-a", 82, "A"), paper("anchor-b", 65, "B"),
        paper("new-c", 54, "C"), paper("new-d", 51, "C"), paper("old-c", 49, "C"),
    ]
    selected, metadata = radar._select_recommendations(
        candidates, {"anchor-a", "anchor-b", "old-c"}, per_project=4,
        generated_at="2026-08-04T00:00:00+00:00",
    )

    assert [item["id"] for item in selected] == ["anchor-a", "anchor-b", "new-c", "new-d"]
    assert metadata["new_item_count"] == 2
    assert metadata["retained_anchor_count"] == 2


def test_selection_excludes_d_tier_instead_of_filling_with_weak_papers() -> None:
    selected, metadata = radar._select_recommendations(
        [
            {"id": "good", "project_id": "ocr", "publication_date": "2026-08-01", "total_score": 60, "quality_tier": "B"},
            {"id": "weak", "project_id": "ocr", "publication_date": "2026-08-02", "total_score": 30, "quality_tier": "D"},
        ],
        set(), per_project=5, generated_at="2026-08-04T00:00:00+00:00",
    )

    assert [item["id"] for item in selected] == ["good"]
    assert metadata["excluded_count"] == 1


def test_preferred_venue_outranks_generic_repository_with_same_content() -> None:
    topic = {
        "query": '"streaming automatic speech recognition"',
        "title_keywords": ["speech recognition"],
        "preferred_venues": ["icassp"],
        "practical_keywords": ["streaming", "latency"],
    }
    work = {
        "type": "conference-paper", "doi": "https://doi.org/10.1/demo",
        "has_fulltext": True, "institutions_distinct_count": 2,
        "cited_by_count": 0, "fwci": 0,
    }
    title = "Efficient streaming speech recognition with low latency"
    abstract = "We benchmark a real-time streaming speech recognition system with latency measurements."

    strong = radar._score_work(
        work, topic, rank=0, result_count=10, title=title, abstract=abstract,
        venue="ICASSP 2026", pdf_url="https://example.test/paper.pdf",
    )
    weak = radar._score_work(
        work, topic, rank=0, result_count=10, title=title, abstract=abstract,
        venue="Zenodo", pdf_url="https://example.test/paper.pdf",
    )

    assert strong["quality_score"] > weak["quality_score"]
    assert strong["quality_tier"] in {"A", "B"}
    assert weak["quality_tier"] in {"C", "D"}
    assert weak["quality_risks"]
