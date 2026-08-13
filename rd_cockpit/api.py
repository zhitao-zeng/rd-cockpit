from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .anomalies import find_anomalies
from .config import load_config
from .ledger import Ledger
from .period import build_period_facts
from .state import build_state, state_dict
from .sessions import session_views


def create_app(home: Path) -> Any:
    try:
        from fastapi import FastAPI, HTTPException
        from fastapi.responses import HTMLResponse
    except ImportError as exc:  # pragma: no cover - exercised only without the optional server extra
        raise RuntimeError("read-only API requires: pip install -e '.[server]'") from exc

    app = FastAPI(title="R&D Cockpit", version="0.1.0", docs_url="/docs")

    def with_ledger(fn):
        ledger = Ledger(home / ".rd-cockpit" / "events.sqlite")
        try: return fn(ledger)
        finally: ledger.close()

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "home": str(home), "database": str(home / ".rd-cockpit" / "events.sqlite")}

    @app.get("/projects")
    def projects() -> dict[str, Any]:
        config = load_config(home / "config" / "projects.yaml")
        ledger = Ledger(home / ".rd-cockpit" / "events.sqlite")
        try:
            return {pid: state_dict(build_state(ledger, home, pid)) for pid in config.get("projects", {})}
        finally:
            ledger.close()

    @app.get("/projects/{project_id}/state")
    def project_state(project_id: str, at: str | None = None) -> dict[str, Any]:
        config = load_config(home / "config" / "projects.yaml")
        if project_id not in config.get("projects", {}): raise HTTPException(status_code=404, detail="unknown project")
        if at:
            try: datetime.fromisoformat(at)
            except ValueError as exc: raise HTTPException(status_code=400, detail="at must be an ISO date/time") from exc
        return with_ledger(lambda ledger: state_dict(build_state(ledger, home, project_id, at=at)))

    @app.get("/projects/{project_id}/timeline")
    def project_timeline(project_id: str) -> list[dict[str, Any]]:
        config = load_config(home / "config" / "projects.yaml")
        if project_id not in config.get("projects", {}): raise HTTPException(status_code=404, detail="unknown project")
        def read(ledger: Ledger) -> list[dict[str, Any]]:
            output = []
            for row in ledger.events(project_id=project_id):
                item = {"event_id": row["event_id"], "occurred_at": row["occurred_at"], "type": row["event_type"],
                        "status": row["status"], "source": row["source"], "commit": row["commit_sha"],
                        "provenance": row["provenance"], "payload": json.loads(row["payload_json"])}
                item["evidence"] = [dict(e) for e in ledger.event_evidence(row["event_id"])]
                output.append(item)
            return output
        return with_ledger(read)

    @app.get("/anomalies")
    def anomalies(project: str | None = None) -> list[dict[str, Any]]:
        return with_ledger(lambda ledger: find_anomalies(ledger, home, project_id=project))

    @app.get("/sessions")
    def sessions(project: str | None = None, active: bool = False) -> list[dict[str, Any]]:
        return with_ledger(lambda ledger: session_views(ledger, project, active=active))

    @app.get("/reports/daily/{report_date}")
    def daily_report(report_date: str) -> dict[str, Any]:
        try: target = date.fromisoformat(report_date)
        except ValueError: raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")
        path = home / "reports" / f"{target.isoformat()}.json"
        if not path.exists(): raise HTTPException(status_code=404, detail="report not generated")
        return json.loads(path.read_text(encoding="utf-8"))

    @app.get("/reports/daily/{report_date}/semantic")
    def daily_semantic(report_date: str) -> dict[str, Any]:
        try: target = date.fromisoformat(report_date)
        except ValueError as exc: raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD") from exc
        from .semantic import build_semantic_facts
        return with_ledger(lambda ledger: build_semantic_facts(ledger, home, target))

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard() -> Any:
        from .dashboard import render
        return with_ledger(lambda ledger: render(ledger, home))

    @app.get("/stats")
    def stats(period: str = "week", report_date: str | None = None) -> dict[str, Any]:
        if period not in {"week", "month"}: raise HTTPException(status_code=400, detail="period must be week or month")
        try:
            target = date.fromisoformat(report_date) if report_date else date.today()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD") from exc
        return with_ledger(lambda ledger: build_period_facts(ledger, period, target))

    @app.get("/insights/{kind}")
    def insights(kind: str, project: str | None = None, query: str | None = None) -> Any:
        from . import insights as views
        allowed = {"lineage", "graph", "conflicts", "freshness", "efficiency", "gpu", "coverage",
                   "reproducibility", "impact", "context", "suggest", "counterfactual", "twin", "switches",
                   "sessions", "replay", "wrapped", "resource-cost", "changed"}
        if kind not in allowed: raise HTTPException(status_code=404, detail="unknown insight")
        def read(ledger: Ledger) -> Any:
            if kind == "lineage": return views.parameter_lineage(ledger, project)
            if kind == "graph": return views.decision_graph(ledger, project)
            if kind == "conflicts": return views.decision_conflicts(ledger, project)
            if kind == "freshness": return views.decision_freshness(ledger, project)
            if kind == "efficiency": return views.experiment_efficiency(ledger, project)
            if kind == "gpu": return views.gpu_report(ledger)
            if kind == "coverage": return views.evidence_coverage(ledger, project)
            if kind == "reproducibility": return views.reproducibility(ledger, project)
            if kind == "impact": return views.change_impact(ledger, home, project) if project else {"error": "project required"}
            if kind == "context": return views.context_pack(ledger, home, project) if project else {"error": "project required"}
            if kind == "suggest": return views.suggest_experiments(ledger, project)
            if kind == "counterfactual": return views.counterfactual(ledger, project, query or "") if project else {"error": "project required"}
            if kind == "switches": return views.context_switch_analysis(ledger)
            if kind == "sessions": return views.session_efficiency(ledger, project)
            if kind == "replay": return views.today_replay(ledger, home, date.fromisoformat(query) if query else date.today())
            if kind == "wrapped": return views.research_wrapped(ledger, home, "month", date.fromisoformat(query) if query else date.today())
            if kind == "resource-cost": return views.resource_cost(ledger, project)
            if kind == "changed": return views.what_changed(ledger, query or "today", project)
            return views.digital_twin(ledger, home)
        return with_ledger(read)

    @app.get("/advanced/{kind}")
    def advanced(kind: str, project: str | None = None, query: str | None = None) -> Any:
        from . import advanced as views
        allowed = {"debt", "confidence", "hypotheses", "information-gain", "budget", "metric-lineage", "fingerprints", "health", "risk", "why-not-done", "attention", "rhythm", "handoff-quality", "agent-blindspots", "memory", "refresh", "knowledge", "brief", "context-pack", "achievements", "card", "map", "dont", "countdown"}
        if kind not in allowed: raise HTTPException(status_code=404, detail="unknown advanced projection")
        def read(ledger: Ledger) -> Any:
            if kind == "debt": return views.research_debt(ledger, home, project)
            if kind == "confidence": return views.claim_confidence(ledger, project)
            if kind == "hypotheses": return views.hypotheses(ledger, project)
            if kind == "information-gain": return views.information_gain(ledger, project)
            if kind == "budget": return views.budget_roi(ledger, project)
            if kind == "metric-lineage": return views.metric_lineage(ledger, project)
            if kind == "fingerprints": return views.fingerprints(ledger, project)
            if kind == "health": return views.health(ledger, home, project) if project else {"error": "project required"}
            if kind == "risk": return views.risk_radar(ledger, home, project) if project else {"error": "project required"}
            if kind == "why-not-done": return views.why_not_done(ledger, home, project) if project else {"error": "project required"}
            if kind == "attention": return views.attention_budget(ledger, project)
            if kind == "rhythm": return views.rhythm(ledger, project)
            if kind == "handoff-quality": return views.handoff_quality(ledger, project)
            if kind == "agent-blindspots": return views.agent_blindspots(ledger, project)
            if kind == "memory": return views.memory_freshness(ledger, home, project) if project else {"error": "project required"}
            if kind == "refresh": return views.refresh(ledger, home, project, record=False) if project else {"error": "project required"}
            if kind == "knowledge": return views.knowledge_cards(ledger, project)
            if kind == "brief": return views.project_brief(ledger, home, project) if project else {"error": "project required"}
            if kind == "context-pack":
                from .insights import context_pack
                return context_pack(ledger, home, project) if project else {"error": "project required"}
            if kind == "achievements": return views.achievements(ledger, home, project)
            if kind == "card": return views.daily_card(ledger, home, date.fromisoformat(query) if query else date.today())
            if kind == "map": return views.research_map(ledger, home)
            if kind == "dont": return views.dont(ledger, home, project)
            return views.decision_countdown(ledger, project)
        return with_ledger(read)

    @app.get("/simple/daily")
    def simple_daily(report_date: str | None = None, project: str | None = None) -> dict[str, Any]:
        try: target = date.fromisoformat(report_date) if report_date else date.today()
        except ValueError as exc: raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD") from exc
        from .simple import daily_records
        return with_ledger(lambda ledger: daily_records(ledger, home, target, project))

    @app.get("/simple/report")
    def simple_report(report_date: str | None = None) -> dict[str, Any]:
        """Return the user's existing Markdown daily report in a UI-friendly shape.

        With no date this deliberately returns the latest completed report.  A
        missing current-day report is never filled with raw Agent prompt text.
        """
        if report_date:
            try: date.fromisoformat(report_date)
            except ValueError as exc: raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD") from exc
        from .daily_source import load_report
        from .daily_supplement import load_supplement
        report = load_report(report_date)
        report["supplement"] = load_supplement(report["date"]) if report.get("date") else None
        if report["supplement"] and not report["token"]["total_tokens"]:
            report["token"]["total_tokens"] = report["supplement"]["totals"]["tokens"]
        return report

    @app.get("/simple/report-dates")
    def simple_report_dates() -> dict[str, Any]:
        from .daily_source import available_report_dates, report_directories, report_directory
        dates = available_report_dates()
        return {"dates": list(reversed(dates)), "latest": dates[-1] if dates else None,
                "directory": str(report_directory()),
                "directories": [str(value) for value in report_directories()]}

    @app.get("/simple/analytics")
    def simple_analytics(days: int = 30) -> dict[str, Any]:
        from .simple import analytics
        return with_ledger(lambda ledger: analytics(ledger, home, days=max(1, min(days, 365))))

    @app.get("/simple/knowledge")
    def simple_knowledge(project: str | None = None) -> dict[str, Any]:
        from .simple import knowledge
        return with_ledger(lambda ledger: knowledge(ledger, home, project))

    @app.get("/simple/research-radar")
    def simple_research_radar(project: str | None = None, refresh: bool = False) -> dict[str, Any]:
        """Return recent papers related to configured project research topics.

        Results are cached locally for 24 hours by default. ``refresh`` rebuilds
        the candidate pool and rotates qualified unseen papers; it only reads
        external metadata; it never changes a project repository or research
        conclusion.
        """
        from .research_radar import research_radar
        try:
            return research_radar(home, project=project, refresh=refresh)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown project") from exc

    @app.get("/simple/life")
    def simple_life(target_date: str | None = None) -> dict[str, Any]:
        try:
            target = date.fromisoformat(target_date) if target_date else date.today()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD") from exc
        from .life import life_dashboard
        return with_ledger(lambda ledger: life_dashboard(ledger, home, target))

    @app.get("/simple/project-discovery")
    def simple_project_discovery() -> dict[str, Any]:
        """Read cached Session/Codex project candidates; never scan or call a model."""
        from .project_discovery import read_discovery
        return read_discovery(home)

    @app.get("/simple/development")
    def simple_development(days: int = 90, target_date: str | None = None) -> dict[str, Any]:
        try:
            target = date.fromisoformat(target_date) if target_date else date.today()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD") from exc
        from .development import development_dashboard
        return development_dashboard(home, days=max(7, min(days, 365)), target=target)

    @app.get("/simple/intelligence")
    def simple_intelligence(days: int = 90, baseline: str | None = None,
                            target_date: str | None = None) -> dict[str, Any]:
        try:
            target = date.fromisoformat(target_date) if target_date else date.today()
            baseline_date = date.fromisoformat(baseline) if baseline else None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="dates must be YYYY-MM-DD") from exc
        from .intelligence import project_intelligence
        return project_intelligence(home, days=max(7, min(days, 365)),
                                    baseline=baseline_date, target=target)

    @app.get("/simple/algorithm-architecture")
    def simple_algorithm_architecture() -> dict[str, Any]:
        """List cached snapshots.  This endpoint never invokes an LLM."""
        from .algorithm_architecture import architecture_index
        return architecture_index(home)

    @app.get("/simple/algorithm-architecture/{project_id}")
    def simple_algorithm_architecture_detail(project_id: str) -> dict[str, Any]:
        """Read one current, versioned algorithm snapshot and its short history."""
        from .algorithm_architecture import load_snapshot, snapshot_history
        from .research_brief import load_research_brief
        config = load_config(home / "config" / "projects.yaml")
        if project_id not in config.get("projects", {}):
            raise HTTPException(status_code=404, detail="unknown project")
        snapshot = load_snapshot(home, project_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="algorithm architecture not analyzed")
        # Source roots are useful to the offline collector but add no value to
        # the browser.  Keep the API focused on relative evidence references.
        public_snapshot = dict(snapshot)
        public_snapshot["sources"] = [
            {key: item.get(key) for key in (
                "id", "label", "kind", "scope", "source_type", "retrieved_at", "url", "exists",
            )}
            for item in snapshot.get("sources", []) if isinstance(item, dict)
        ]
        return {
            "snapshot": public_snapshot,
            "history": snapshot_history(home, project_id)[:20],
            "research_brief": load_research_brief(home, project_id),
        }

    @app.get("/simple/experiment-intelligence")
    def simple_experiment_intelligence(days: int = 90, project: str | None = None,
                                       target_date: str | None = None) -> dict[str, Any]:
        """Read accepted Daily-Report experiment sidecars; never call a model."""
        try:
            target = date.fromisoformat(target_date) if target_date else date.today()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="target_date must be YYYY-MM-DD") from exc
        config = load_config(home / "config" / "projects.yaml")
        if project and project not in config.get("projects", {}):
            raise HTTPException(status_code=404, detail="unknown project")
        from .experiment_intelligence import experiment_intelligence
        return experiment_intelligence(home, days=max(7, min(days, 365)), project=project, target=target)

    return app
