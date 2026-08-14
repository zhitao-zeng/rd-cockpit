"""Small, privacy-safe accounting records for background model calls.

Only operational metadata is stored: stage, project, model, token counters,
duration and cache/fallback outcome. Prompts, source excerpts and model output
never enter this table.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from .ledger import Ledger


def _home(context: Mapping[str, Any] | None) -> Path | None:
    value = (context or {}).get("home") or os.environ.get("RD_COCKPIT_HOME")
    return Path(str(value)).expanduser().resolve() if value else None


def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def usage_totals(usage: Mapping[str, Any] | None) -> dict[str, int]:
    value = usage or {}
    input_tokens = _int(
        value.get("input_tokens") or value.get("input_token_count") or value.get("prompt_tokens")
    )
    output_tokens = _int(
        value.get("output_tokens") or value.get("output_token_count")
        or value.get("completion_tokens")
    )
    cached_tokens = _int(
        value.get("cached_input_tokens") or value.get("cache_read_input_tokens")
        or value.get("cached_tokens")
    )
    total_tokens = _int(value.get("total_tokens"))
    if not total_tokens:
        total_tokens = input_tokens + output_tokens + _int(value.get("reasoning_tokens"))
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_tokens": cached_tokens,
        "total_tokens": total_tokens,
    }


def record_model_run(
    context: Mapping[str, Any] | None,
    *,
    requested_model: str | None,
    metadata: Mapping[str, Any] | None = None,
    status: str,
    started_at: datetime,
    finished_at: datetime | None = None,
    error: str | None = None,
) -> str | None:
    home = _home(context)
    if home is None:
        return None
    context = context or {}
    metadata = metadata or {}
    finished = finished_at or datetime.now(timezone.utc)
    usage = usage_totals(metadata.get("usage") if isinstance(metadata.get("usage"), Mapping) else {})
    run_id = f"model_{uuid.uuid4().hex}"
    ledger: Ledger | None = None
    try:
        ledger = Ledger(home / ".rd-cockpit" / "events.sqlite")
        ledger.db.execute(
            """INSERT INTO model_runs
            (run_id,stage,project_id,source_hash,requested_model,selected_model,provider,
             fallback_used,cache_hit,status,started_at,finished_at,duration_ms,input_tokens,
             output_tokens,cached_tokens,total_tokens,reason,error,metadata_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run_id, str(context.get("stage") or "unspecified"), context.get("project_id"),
                context.get("source_hash"), requested_model, metadata.get("model") or requested_model,
                metadata.get("provider"), int(bool(context.get("fallback_used"))),
                int(bool(context.get("cache_hit"))), status,
                started_at.isoformat(timespec="milliseconds"), finished.isoformat(timespec="milliseconds"),
                max(0, int((finished - started_at).total_seconds() * 1000)),
                usage["input_tokens"], usage["output_tokens"], usage["cached_tokens"],
                usage["total_tokens"], str(context.get("reason") or "")[:500],
                str(error or "")[:2_000] or None,
                json.dumps({
                    "reasoning_effort": metadata.get("reasoning_effort"),
                }, ensure_ascii=False, sort_keys=True),
            ),
        )
        ledger.db.commit()
    except Exception:
        # Accounting must never turn an otherwise valid model result into a
        # failed research refresh. The task's primary cache/log still records
        # its outcome and the next run can resume normally.
        if ledger is not None:
            ledger.db.rollback()
        return None
    finally:
        if ledger is not None:
            ledger.close()
    return run_id


def record_cache_decision(
    home: Path,
    *,
    stage: str,
    project_id: str | None,
    source_hash: str | None,
    model: str | None,
    status: str,
    reason: str,
) -> str | None:
    now = datetime.now(timezone.utc)
    return record_model_run(
        {"home": home, "stage": stage, "project_id": project_id,
         "source_hash": source_hash, "cache_hit": status == "cached", "reason": reason},
        requested_model=model, metadata={"model": model, "provider": "cache", "usage": {}},
        status=status, started_at=now, finished_at=now,
    )


def model_run_summary(home: Path, *, days: int = 30, limit: int = 100) -> dict[str, Any]:
    ledger = Ledger(home / ".rd-cockpit" / "events.sqlite")
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=max(1, days))).isoformat()
        totals = ledger.db.execute(
            """SELECT
            COUNT(*) AS records,
            SUM(CASE WHEN COALESCE(provider,'')!='cache' THEN 1 ELSE 0 END) AS model_calls,
            SUM(CASE WHEN status='cached' THEN 1 ELSE 0 END) AS cache_hits,
            SUM(CASE WHEN status='deferred' THEN 1 ELSE 0 END) AS deferred,
            SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed,
            SUM(CASE WHEN COALESCE(provider,'')!='cache' AND fallback_used=1 THEN 1 ELSE 0 END) AS fallbacks,
            SUM(CASE WHEN COALESCE(provider,'')!='cache' THEN input_tokens ELSE 0 END) AS input_tokens,
            SUM(CASE WHEN COALESCE(provider,'')!='cache' THEN output_tokens ELSE 0 END) AS output_tokens,
            SUM(CASE WHEN COALESCE(provider,'')!='cache' THEN cached_tokens ELSE 0 END) AS cached_tokens,
            SUM(CASE WHEN COALESCE(provider,'')!='cache' THEN total_tokens ELSE 0 END) AS total_tokens,
            SUM(CASE WHEN COALESCE(provider,'')!='cache' THEN duration_ms ELSE 0 END) AS duration_ms
            FROM model_runs WHERE started_at>=?""",
            (since,),
        ).fetchone()
        rows = ledger.db.execute(
            "SELECT * FROM model_runs WHERE started_at>=? ORDER BY started_at DESC LIMIT ?",
            (since, max(1, min(limit, 500))),
        ).fetchall()
    finally:
        ledger.close()
    items = [{
        "run_id": row["run_id"], "stage": row["stage"], "project_id": row["project_id"],
        "source_hash": row["source_hash"], "requested_model": row["requested_model"],
        "selected_model": row["selected_model"], "provider": row["provider"],
        "fallback_used": bool(row["fallback_used"]), "cache_hit": bool(row["cache_hit"]),
        "status": row["status"], "started_at": row["started_at"],
        "finished_at": row["finished_at"], "duration_ms": row["duration_ms"],
        "input_tokens": row["input_tokens"], "output_tokens": row["output_tokens"],
        "cached_tokens": row["cached_tokens"], "total_tokens": row["total_tokens"],
        "reason": row["reason"], "error": row["error"],
    } for row in rows]
    return {
        "days": days,
        "counts": {
            "records": _int(totals["records"]), "model_calls": _int(totals["model_calls"]),
            "cache_hits": _int(totals["cache_hits"]), "deferred": _int(totals["deferred"]),
            "failed": _int(totals["failed"]), "fallbacks": _int(totals["fallbacks"]),
        },
        "tokens": {
            "input": _int(totals["input_tokens"]), "output": _int(totals["output_tokens"]),
            "cached": _int(totals["cached_tokens"]), "total": _int(totals["total_tokens"]),
        },
        "duration_ms": _int(totals["duration_ms"]),
        "runs": items,
    }
