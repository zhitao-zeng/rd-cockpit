#!/usr/bin/env python3
"""Fail-open executable used by Codex and Claude Code user hooks."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


CODE_ROOT = Path(__file__).resolve().parents[1]
COCKPIT_HOME = Path(os.environ.get("RD_COCKPIT_HOME", CODE_ROOT)).resolve()
sys.path.insert(0, str(CODE_ROOT))

from rd_cockpit.agent_hooks import handle_agent_hook  # noqa: E402
from rd_cockpit.hook_queue import enqueue_hook  # noqa: E402
from rd_cockpit.ledger import Ledger  # noqa: E402


def _log_error(message: str) -> None:
    root = COCKPIT_HOME / ".rd-cockpit"
    root.mkdir(parents=True, exist_ok=True)
    with (root / "hook-errors.log").open("a", encoding="utf-8") as handle:
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        handle.write(f"{stamp} {message[:2000]}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=["codex", "claude-code"], required=True)
    args = parser.parse_args()
    incoming: dict[str, object] = {}
    try:
        loaded = json.load(sys.stdin)
        incoming = loaded
        if not isinstance(incoming, dict):
            raise ValueError("hook input must be a JSON object")
        # Older Agent processes may have loaded the retired summary callbacks
        # before their config was migrated. Acknowledge them without opening
        # the ledger or collecting anything.
        if incoming.get("hook_event_name") in {"Stop", "PostCompact"}:
            print("{}")
            return 0
        # Hooks are on the interactive Agent path. They must fail fast when a
        # collector owns SQLite rather than inheriting the ledger's 30-second
        # batch-writer timeout.
        ledger = Ledger(
            COCKPIT_HOME / ".rd-cockpit" / "events.sqlite",
            timeout_seconds=0.2,
            max_retries=1,
        )
        try:
            handle_agent_hook(COCKPIT_HOME, ledger, args.source, incoming)
        finally:
            ledger.close()
    except sqlite3.OperationalError as exc:
        try:
            queued = enqueue_hook(COCKPIT_HOME, args.source, incoming, str(exc))
            _log_error(
                f"{args.source}: queued {queued.name} after SQLite contention: {exc}"
            )
        except Exception as queue_exc:
            _log_error(
                f"{args.source}: SQLite contention ({exc}); queue failed: "
                f"{type(queue_exc).__name__}: {queue_exc}"
            )
    except Exception as exc:  # Hooks must never block or break an Agent turn.
        _log_error(f"{args.source}: {type(exc).__name__}: {exc}")
    # Agent hooks accept only their documented response schema. Internal
    # ingestion/queue details stay in the local log.
    print("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
