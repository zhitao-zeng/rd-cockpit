from __future__ import annotations

import json
import os
import secrets
from datetime import date, datetime
from pathlib import Path
from typing import Any

try:
    from starlette.requests import Request
    from starlette.responses import Response
except ImportError:  # pragma: no cover - the server extra is optional
    Request = Response = Any  # type: ignore[misc,assignment]

from .anomalies import find_anomalies
from .config import load_config
from .ledger import Ledger
from .period import build_period_facts
from .state import build_state, state_dict
from .sessions import session_views


def create_app(
    home: Path, *, safe_mode: bool = False, api_token: str | None = None,
) -> Any:
    try:
        from fastapi import FastAPI, HTTPException
        from fastapi.responses import HTMLResponse, JSONResponse
    except ImportError as exc:  # pragma: no cover - exercised only without the optional server extra
        raise RuntimeError("read-only API requires: pip install -e '.[server]'") from exc

    app = FastAPI(
        title="R&D Cockpit", version="0.1.0",
        docs_url=None if safe_mode else "/docs",
        redoc_url=None if safe_mode else "/redoc",
        openapi_url=None if safe_mode else "/openapi.json",
    )
    configured_token = api_token if api_token is not None else os.environ.get("RD_API_TOKEN", "")

    if safe_mode:
        from .privacy import safe_value

        @app.middleware("http")
        async def safe_api_boundary(request: Request, call_next):
            path = request.url.path
            root_path = str(request.scope.get("root_path") or "")
            if root_path and path.startswith(root_path):
                path = path[len(root_path):] or "/"
            allowed = path in {"/health", "/auth/status"} or path.startswith("/simple/")
            if not allowed:
                return JSONResponse({"detail": "not found"}, status_code=404)
            authorization = request.headers.get("authorization", "")
            supplied = authorization[7:] if authorization.casefold().startswith("bearer ") else ""
            authenticated = not configured_token or secrets.compare_digest(supplied, configured_token)
            if path != "/auth/status" and not authenticated:
                return JSONResponse(
                    {"detail": "authentication required"}, status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
            response = await call_next(request)
            if response.headers.get("X-RD-Privacy-Safe") == "1":
                response.headers.setdefault("Cache-Control", "private, max-age=30")
                response.headers.setdefault("X-Content-Type-Options", "nosniff")
                return response
            content_type = response.headers.get("content-type", "")
            if "application/json" not in content_type:
                return response
            body = b"".join([chunk async for chunk in response.body_iterator])
            try:
                value = json.loads(body)
            except (UnicodeDecodeError, json.JSONDecodeError):
                return Response(
                    body, status_code=response.status_code, headers=dict(response.headers),
                    media_type=content_type,
                )
            sanitized = safe_value(value, cockpit_home=home)
            headers = dict(response.headers)
            headers.pop("content-length", None)
            headers.setdefault("Cache-Control", "private, max-age=30")
            headers.setdefault("X-Content-Type-Options", "nosniff")
            return JSONResponse(sanitized, status_code=response.status_code, headers=headers)

    def with_ledger(fn):
        ledger = Ledger(home / ".rd-cockpit" / "events.sqlite", readonly=True)
        try: return fn(ledger)
        finally: ledger.close()

    def deliver_cached(
        request: Request, response: Response, cached, *, privacy_safe: bool = False,
    ) -> Any:
        etag = f'"{cached.etag}"'
        headers = {
            "ETag": etag,
            "Cache-Control": "private, max-age=30, must-revalidate",
            "X-RD-Cache": "hit" if cached.cache_hit else "miss",
            "X-RD-Generated-At": cached.generated_at,
        }
        if privacy_safe:
            headers["X-RD-Privacy-Safe"] = "1"
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        for key, value in headers.items():
            response.headers[key] = value
        return cached.data

    def safe_projection(value: dict[str, Any]) -> dict[str, Any]:
        if not safe_mode:
            return value
        from .privacy import safe_value

        sanitized = safe_value(value, cockpit_home=home)
        return sanitized if isinstance(sanitized, dict) else {}

    def projection_parameters(value: dict[str, Any]) -> dict[str, Any]:
        return {**value, "privacy": "safe" if safe_mode else "local"}

    def development_core(days: int, target: date):
        from .development import development_dashboard
        from .view_cache import get_or_build

        parameters = {"days": days, "target_date": target.isoformat()}
        return get_or_build(
            home, "development-core", parameters,
            lambda: development_dashboard(home, days=days, target=target),
        )

    @app.get("/health")
    def health() -> dict[str, Any]:
        if safe_mode:
            return {"ok": True}
        return {"ok": True, "home": str(home), "database": str(home / ".rd-cockpit" / "events.sqlite")}

    @app.get("/auth/status")
    def auth_status(request: Request) -> dict[str, Any]:
        authorization = request.headers.get("authorization", "")
        supplied = authorization[7:] if authorization.casefold().startswith("bearer ") else ""
        return {
            "required": bool(configured_token),
            "authenticated": not configured_token or secrets.compare_digest(supplied, configured_token),
        }

    @app.get("/simple/projects")
    def simple_projects() -> dict[str, Any]:
        config = load_config(home / "config" / "projects.yaml")
        return {
            project_id: {
                "project_id": project_id,
                "name": str(value.get("name") or project_id),
                "lifecycle_status": str(value.get("lifecycle_status") or "active"),
            }
            for project_id, value in config.get("projects", {}).items()
        }

    @app.get("/projects")
    def projects() -> dict[str, Any]:
        config = load_config(home / "config" / "projects.yaml")
        ledger = Ledger(home / ".rd-cockpit" / "events.sqlite", readonly=True)
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
        from .project_identity import canonicalize_report, canonicalize_supplement
        report = canonicalize_report(load_report(report_date), home)
        report["supplement"] = canonicalize_supplement(
            load_supplement(report["date"]) if report.get("date") else None, home,
        )
        if report["supplement"] and not report["token"]["total_tokens"]:
            report["token"]["total_tokens"] = report["supplement"]["totals"]["tokens"]
        return report

    @app.get("/simple/report-dates")
    def simple_report_dates() -> dict[str, Any]:
        from .daily_source import available_report_dates, report_directories, report_directory
        dates = available_report_dates()
        return {"dates": list(reversed(dates)), "latest": dates[-1] if dates else None,
                "directory": "" if safe_mode else str(report_directory()),
                "directories": [] if safe_mode else [str(value) for value in report_directories()]}

    @app.get("/simple/analytics")
    def simple_analytics(request: Request, response: Response, days: int = 30) -> Any:
        from .simple import analytics
        from .view_cache import get_or_build
        bounded = max(1, min(days, 365))

        def build() -> dict[str, Any]:
            return with_ledger(lambda ledger: analytics(ledger, home, days=bounded))

        cached = get_or_build(
            home, "analytics", {"days": bounded}, build, source_scope="analytics",
        )
        return deliver_cached(request, response, cached)

    @app.get("/simple/knowledge")
    def simple_knowledge(project: str | None = None) -> dict[str, Any]:
        from .simple import knowledge
        return with_ledger(lambda ledger: knowledge(ledger, home, project))

    @app.get("/simple/semantic-feedback")
    def simple_semantic_feedback(
        view: str | None = None, project: str | None = None, limit: int = 200,
    ) -> dict[str, Any]:
        """Return the latest private rating for each generated semantic item."""
        from .semantic_feedback import latest_feedback
        from .project_identity import canonical_project_id

        project_id = canonical_project_id(project, home) if project else None
        items = with_ledger(lambda ledger: latest_feedback(
            ledger, view=view, project_id=None if project_id == "unassigned" else project_id,
            limit=max(1, min(limit, 1000)),
        ))
        return {"items": items, "count": len(items)}

    @app.post("/simple/semantic-feedback")
    def simple_record_semantic_feedback(value: dict[str, Any]) -> dict[str, Any]:
        """Append user feedback; it is never treated as an observed research fact."""
        from .semantic_feedback import record_feedback

        ledger = Ledger(home / ".rd-cockpit" / "events.sqlite")
        try:
            try:
                item = record_feedback(home, ledger, value)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            ledger.close()
        return {"ok": True, "item": item}

    @app.get("/simple/research-radar")
    def simple_research_radar(project: str | None = None) -> dict[str, Any]:
        """Return recent papers related to configured project research topics.

        This endpoint is cache-only: it never performs network or model work.
        The scheduled ``radar-refresh`` command updates the snapshot.
        """
        from .research_radar import read_research_radar
        try:
            return read_research_radar(home, project=project)
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

    @app.get("/simple/task-status")
    def simple_task_status() -> dict[str, Any]:
        """Read the last scheduled refresh result; this endpoint starts no work."""
        from .task_status import read_status
        return read_status(home)

    @app.get("/simple/model-runs")
    def simple_model_runs(days: int = 30, limit: int = 100) -> dict[str, Any]:
        """Return privacy-safe model cost/cache metadata; never prompts or outputs."""
        from .model_runs import model_run_summary
        return model_run_summary(home, days=max(1, min(days, 365)), limit=max(1, min(limit, 500)))

    @app.get("/simple/resource-history")
    def simple_resource_history(days: int = 365, kind: str = "day") -> dict[str, Any]:
        from .resources import rollup_history
        if kind not in {"hour", "day"}:
            raise HTTPException(status_code=400, detail="kind must be hour or day")
        return with_ledger(lambda ledger: rollup_history(
            ledger, days=max(1, min(days, 3650)), kind=kind,
        ))

    @app.get("/simple/development")
    def simple_development(
        request: Request, response: Response, days: int = 90, target_date: str | None = None,
    ) -> Any:
        try:
            target = date.fromisoformat(target_date) if target_date else date.today()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD") from exc
        if safe_mode:
            raise HTTPException(status_code=404, detail="use the compact development endpoints")
        from .development import development_dashboard
        from .view_cache import get_or_build
        bounded = max(7, min(days, 365))
        parameters = {"days": bounded, "target_date": target.isoformat()}
        cached = get_or_build(
            home, "development", parameters,
            lambda: development_dashboard(home, days=bounded, target=target),
        )
        return deliver_cached(request, response, cached)

    @app.get("/simple/development-summary")
    def simple_development_summary(
        request: Request, response: Response, days: int = 90, target_date: str | None = None,
    ) -> Any:
        try:
            target = date.fromisoformat(target_date) if target_date else date.today()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD") from exc
        from .development import development_summary_view
        from .view_cache import get_or_build
        bounded = max(7, min(days, 365))
        parameters = {"days": bounded, "target_date": target.isoformat()}
        core = development_core(bounded, target)
        cached = get_or_build(
            home, "development-summary", projection_parameters(parameters),
            lambda: safe_projection(development_summary_view(core.data)),
        )
        return deliver_cached(request, response, cached, privacy_safe=safe_mode)

    @app.get("/simple/development-project/{project_id}")
    def simple_development_project(
        project_id: str, request: Request, response: Response, days: int = 90,
        target_date: str | None = None, timeline_limit: int = 120,
    ) -> Any:
        try:
            target = date.fromisoformat(target_date) if target_date else date.today()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD") from exc
        config = load_config(home / "config" / "projects.yaml")
        bounded = max(7, min(days, 365))
        core = development_core(bounded, target)
        known = set(config.get("projects", {})) | set((core.data.get("storylines") or {}).keys())
        if project_id not in known:
            raise HTTPException(status_code=404, detail="unknown project")
        from .development import development_project_view
        from .view_cache import get_or_build
        limit = max(12, min(timeline_limit, 500))
        parameters = {
            "days": bounded, "target_date": target.isoformat(),
            "project_id": project_id, "timeline_limit": limit,
        }
        cached = get_or_build(
            home, "development-project", projection_parameters(parameters),
            lambda: safe_projection(development_project_view(
                core.data, project_id, timeline_limit=limit,
            )),
        )
        return deliver_cached(request, response, cached, privacy_safe=safe_mode)

    @app.get("/simple/development-global")
    def simple_development_global(
        request: Request, response: Response, days: int = 90, target_date: str | None = None,
    ) -> Any:
        try:
            target = date.fromisoformat(target_date) if target_date else date.today()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD") from exc
        from .development import development_global_view
        from .view_cache import get_or_build
        bounded = max(7, min(days, 365))
        parameters = {"days": bounded, "target_date": target.isoformat()}
        core = development_core(bounded, target)
        cached = get_or_build(
            home, "development-global", projection_parameters(parameters),
            lambda: safe_projection(development_global_view(core.data)),
        )
        return deliver_cached(request, response, cached, privacy_safe=safe_mode)

    @app.get("/simple/development-timeline")
    def simple_development_timeline(
        request: Request, response: Response, days: int = 90, project: str | None = None,
        target_date: str | None = None, offset: int = 0, limit: int = 50,
    ) -> Any:
        try:
            target = date.fromisoformat(target_date) if target_date else date.today()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD") from exc
        from .development import development_timeline_view
        from .view_cache import get_or_build
        bounded = max(7, min(days, 365))
        start, size = max(0, offset), max(1, min(limit, 200))
        core = development_core(bounded, target)
        if project and project not in (core.data.get("storylines") or {}):
            raise HTTPException(status_code=404, detail="unknown project")
        parameters = {
            "days": bounded, "target_date": target.isoformat(), "project": project,
            "offset": start, "limit": size,
        }
        cached = get_or_build(
            home, "development-timeline", projection_parameters(parameters),
            lambda: safe_projection(development_timeline_view(
                core.data, project_id=project, offset=start, limit=size,
            )),
        )
        return deliver_cached(request, response, cached, privacy_safe=safe_mode)

    @app.get("/simple/development-history")
    def simple_development_history(
        request: Request, response: Response, days: int = 90,
        target_date: str | None = None, offset: int = 0, limit: int = 10,
    ) -> Any:
        try:
            target = date.fromisoformat(target_date) if target_date else date.today()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD") from exc
        from .development import development_history_view
        from .view_cache import get_or_build
        bounded = max(7, min(days, 365))
        start, size = max(0, offset), max(1, min(limit, 31))
        core = development_core(bounded, target)
        parameters = {
            "days": bounded, "target_date": target.isoformat(),
            "offset": start, "limit": size,
        }
        cached = get_or_build(
            home, "development-history", projection_parameters(parameters),
            lambda: safe_projection(development_history_view(
                core.data, offset=start, limit=size,
            )),
        )
        return deliver_cached(request, response, cached, privacy_safe=safe_mode)

    @app.get("/simple/intelligence")
    def simple_intelligence(
        request: Request, response: Response, days: int = 90, baseline: str | None = None,
        target_date: str | None = None,
    ) -> Any:
        try:
            target = date.fromisoformat(target_date) if target_date else date.today()
            baseline_date = date.fromisoformat(baseline) if baseline else None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="dates must be YYYY-MM-DD") from exc
        from .intelligence import project_intelligence
        from .view_cache import get_or_build
        bounded = max(7, min(days, 365))
        parameters = {
            "days": bounded, "target_date": target.isoformat(),
            "baseline": baseline_date.isoformat() if baseline_date else None,
        }
        cached = get_or_build(
            home, "intelligence", parameters,
            lambda: project_intelligence(home, days=bounded, baseline=baseline_date, target=target),
        )
        return deliver_cached(request, response, cached)

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
