"""Import existing reports without pretending their prose is observed telemetry.

Historical reports are useful context, but their claims were usually generated
before this ledger existed.  They are therefore stored as a single inferred
event with a content hash and the original file as evidence.  Structured JSON
summaries are retained in the payload; Markdown/HTML remains opaque source
material and can be interpreted later without losing provenance.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from .ledger import Ledger, sha256_file


def import_report(ledger: Ledger, path: Path, *, project_id: str | None = None) -> str:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    suffix = path.suffix.lower()
    payload: dict[str, Any] = {"path": str(path), "format": suffix.lstrip(".") or "unknown",
                               "bytes": path.stat().st_size}
    if suffix == ".json":
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            payload["report"] = value
            report_date = value.get("report_date") if isinstance(value, dict) else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            report_date = None
    else:
        report_date = None
    if report_date is None:
        # Daily reports conventionally start with YYYY-MM-DD in their filename.
        try:
            report_date = date.fromisoformat(path.stem).isoformat()
        except ValueError:
            report_date = None
    payload["report_date"] = report_date
    digest = sha256_file(path)
    return ledger.append(
        event_type="historical_report_imported", source="report_import", project_id=project_id,
        status="imported", provenance="inferred", payload=payload,
        evidence=[{"type": "historical_report", "path": str(path), "sha256": digest}],
        dedup_key=f"historical_report:{digest}",
    )
