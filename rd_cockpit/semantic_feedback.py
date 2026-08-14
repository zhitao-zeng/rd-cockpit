"""Private user feedback for generated semantic summaries.

Feedback is append-only evidence in the local ledger.  Negative feedback is
fed into the next bounded Codex audit for only the cited report dates; it is
never copied into the public repository or used as an unverified fact.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

from .ledger import Ledger
from .project_identity import canonical_project_id, registered_project_names


RATINGS = {"accurate", "noise", "incorrect", "wrong_project", "missing"}
ITEM_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _payload(row: Any) -> dict[str, Any]:
    try:
        value = json.loads(row["payload_json"] or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def record_feedback(home: Path, ledger: Ledger, value: dict[str, Any]) -> dict[str, Any]:
    item_id = str(value.get("item_id") or "").strip()
    view = str(value.get("view") or "").strip()
    rating = str(value.get("rating") or "").strip()
    if not ITEM_ID.fullmatch(item_id) or not ITEM_ID.fullmatch(view):
        raise ValueError("view and item_id must be short stable identifiers")
    if rating not in RATINGS:
        raise ValueError(f"rating must be one of {sorted(RATINGS)}")
    project_id = canonical_project_id(value.get("project_id"), home)
    corrected = str(value.get("corrected_project_id") or "").strip() or None
    if corrected and corrected not in registered_project_names(home):
        raise ValueError("corrected_project_id must be a registered project")
    text = str(value.get("text") or "").strip()[:4000]
    comment = str(value.get("comment") or "").strip()[:2000]
    source_dates = list(dict.fromkeys(
        str(day) for day in value.get("source_dates") or [] if DAY.fullmatch(str(day))
    ))[:31]
    if view == "storyline" and not source_dates:
        raise ValueError("storyline feedback must cite at least one report date")
    payload = {
        "view": view, "item_id": item_id, "rating": rating,
        "text": text, "comment": comment, "source_dates": source_dates,
        "corrected_project_id": corrected,
    }
    event_id = ledger.append(
        event_type="semantic_feedback_recorded", source="web_feedback",
        project_id=None if project_id == "unassigned" else project_id,
        status=rating, provenance="reported", verification="user_confirmed",
        payload=payload,
    )
    return {"event_id": event_id, "project_id": project_id, **payload}


def latest_feedback(
    ledger: Ledger, *, view: str | None = None, project_id: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    rows = ledger.events(
        project_id=project_id, event_types={"semantic_feedback_recorded"}, include_history=True,
    )
    output: dict[tuple[str, str], dict[str, Any]] = {}
    for row in reversed(rows):
        payload = _payload(row)
        if view and payload.get("view") != view:
            continue
        key = (str(payload.get("view") or ""), str(payload.get("item_id") or ""))
        if not all(key) or key in output:
            continue
        output[key] = {
            "event_id": row["event_id"], "occurred_at": row["occurred_at"],
            "project_id": row["project_id"] or "unassigned", **payload,
        }
        if len(output) >= max(1, min(limit, 1000)):
            break
    return list(output.values())


def feedback_for_records(
    ledger: Ledger, records: Iterable[dict[str, Any]], *, limit: int = 100,
) -> list[dict[str, Any]]:
    records = list(records)
    dates = {str(item.get("date") or "") for item in records}
    projects = {str(project) for item in records for project in item.get("project_ids") or []}
    output = []
    for item in latest_feedback(ledger, limit=1000):
        source_dates = set(item.get("source_dates") or [])
        if source_dates and not source_dates.intersection(dates):
            continue
        if not source_dates and item.get("project_id") not in projects:
            continue
        output.append({
            key: item.get(key) for key in (
                "event_id", "project_id", "view", "item_id", "rating", "text", "comment",
                "source_dates", "corrected_project_id",
            )
        })
        if len(output) >= limit:
            break
    return output


def feedback_fingerprint(items: Iterable[dict[str, Any]]) -> str:
    compact = [{key: item.get(key) for key in (
        "event_id", "rating", "project_id", "source_dates", "corrected_project_id",
    )} for item in items]
    encoded = json.dumps(compact, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
