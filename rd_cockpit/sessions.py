"""Read-only Agent session projections.

Sessions remain useful for duration, project attribution, Token accounting and
daily-report source coverage.  They intentionally contain no intermediate
manually curated summaries.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .ledger import Ledger


SESSION_EVENT_TYPES = {"agent_session_started", "agent_session_completed"}


def _payload(row: Any) -> dict[str, Any]:
    try:
        value = json.loads(row["payload_json"])
        return value if isinstance(value, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _datetime(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return result if result.tzinfo else result.replace(tzinfo=timezone.utc)


def session_views(ledger: Ledger, project_id: str | None = None, *, active: bool = False) -> list[dict[str, Any]]:
    rows = ledger.events(project_id=project_id, event_types=SESSION_EVENT_TYPES)
    grouped: dict[str, list[Any]] = {}
    for row in rows:
        grouped.setdefault(row["session_id"] or "unknown", []).append(row)

    output: list[dict[str, Any]] = []
    for session_id, values in grouped.items():
        starts = [row for row in values if row["event_type"] == "agent_session_started"]
        ends = [row for row in values if row["event_type"] == "agent_session_completed"]
        started = starts[-1] if starts else values[0]
        ended = ends[-1] if ends and _datetime(ends[-1]["occurred_at"]) >= _datetime(started["occurred_at"]) else None
        item = {
            "session_id": session_id,
            "project_id": (ended["project_id"] if ended is not None else None) or started["project_id"],
            "started_at": started["occurred_at"],
            "ended_at": ended["occurred_at"] if ended is not None else None,
            "status": (ended["status"] or "completed") if ended is not None else "active",
            "goal": _payload(started).get("goal"),
            "handoff": _payload(ended) if ended is not None else {},
        }
        if not active or item["status"] == "active":
            output.append(item)
    return sorted(output, key=lambda item: item["started_at"] or "", reverse=True)
