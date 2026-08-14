from __future__ import annotations

from pathlib import Path

from rd_cockpit.ledger import Ledger
from rd_cockpit.view_cache import get_or_build, prune_view_cache


def test_materialized_view_reuses_cache_and_invalidates_on_source_change(tmp_path: Path) -> None:
    home = tmp_path / "cockpit"
    config = home / "config" / "projects.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("projects: {}\n", encoding="utf-8")
    Ledger(home / ".rd-cockpit" / "events.sqlite").close()
    calls: list[int] = []

    def build() -> dict:
        calls.append(1)
        return {"value": len(calls)}

    first = get_or_build(home, "fixture", {"days": 30}, build)
    second = get_or_build(home, "fixture", {"days": 30}, build)
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert second.data == {"value": 1}
    assert first.etag == second.etag
    assert first.path.stat().st_mode & 0o777 == 0o600

    config.write_text("projects:\n  demo:\n    name: Demo\n", encoding="utf-8")
    third = get_or_build(home, "fixture", {"days": 30}, build)
    assert third.cache_hit is False
    assert third.data == {"value": 2}
    assert third.etag != first.etag


def test_views_ignore_unrelated_resource_samples_but_analytics_tracks_work(tmp_path: Path) -> None:
    home = tmp_path / "cockpit"
    config = home / "config" / "projects.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("projects: {}\n", encoding="utf-8")
    ledger = Ledger(home / ".rd-cockpit" / "events.sqlite")
    calls: list[int] = []
    first = get_or_build(home, "development", {"days": 90}, lambda: {"n": len(calls.append(1) or calls)})
    ledger.append(event_type="resource_snapshot", source="sampler", payload={"gpus": []})
    second = get_or_build(home, "development", {"days": 90}, lambda: {"n": len(calls.append(1) or calls)})
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert len(calls) == 1
    analytics_calls: list[int] = []
    analytics_builder = lambda: {"n": len(analytics_calls.append(1) or analytics_calls)}
    get_or_build(
        home, "analytics", {"days": 90}, analytics_builder, source_scope="analytics",
    )
    ledger.append(event_type="resource_snapshot", source="sampler", payload={"gpus": []})
    cached = get_or_build(
        home, "analytics", {"days": 90}, analytics_builder, source_scope="analytics",
    )
    assert cached.cache_hit is True
    ledger.append(event_type="agent_session_completed", source="codex", payload={"goal": "test"})
    changed = get_or_build(
        home, "analytics", {"days": 90}, analytics_builder, source_scope="analytics",
    )
    assert changed.cache_hit is False
    assert len(analytics_calls) == 2
    ledger.record_agent_activity(
        source="codex", session_id="session", project_id=None, semantic_kind="tool",
        failed=False, duration_ms=100, occurred_at="2026-08-14T08:00:00+00:00",
        activity_key="activity-one",
    )
    activity_changed = get_or_build(
        home, "analytics", {"days": 90}, analytics_builder, source_scope="analytics",
    )
    assert activity_changed.cache_hit is False
    assert len(analytics_calls) == 3
    ledger.close()


def test_view_cache_prunes_old_variants_and_obsolete_wrappers(tmp_path: Path) -> None:
    home = tmp_path / "cockpit"
    config = home / "config" / "projects.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("projects: {}\n", encoding="utf-8")
    Ledger(home / ".rd-cockpit" / "events.sqlite").close()
    for offset in range(4):
        get_or_build(
            home, "history", {"days": 90, "offset": offset, "limit": 14},
            lambda offset=offset: {"offset": offset},
        )
    root = home / ".rd-cockpit" / "views"
    obsolete = root / "obsolete.json"
    obsolete.write_text('{"schema_version": 1}', encoding="utf-8")

    result = prune_view_cache(home, retention_days=30, max_bytes=10_000_000, keep_per_variant=2)

    assert result["removed"] == 3
    assert result["remaining"] == 2
    assert not obsolete.exists()
