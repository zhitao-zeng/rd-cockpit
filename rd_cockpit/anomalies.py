from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import load_config
from .ledger import Ledger
from .state import build_state


def _payload(row: Any) -> dict[str, Any]:
    try: return json.loads(row["payload_json"])
    except (TypeError, json.JSONDecodeError): return {}


def find_anomalies(ledger: Ledger, home: Path, *, project_id: str | None = None,
                   stale_days: int = 2) -> list[dict[str, Any]]:
    config = load_config(home / "config" / "projects.yaml")
    projects = [project_id] if project_id else sorted(config.get("projects", {}))
    output: list[dict[str, Any]] = []
    for pid in projects:
        state = build_state(ledger, home, pid)
        events = ledger.events(project_id=pid)
        for stage, value in state.stages.items():
            if value.get("status") == "stale":
                output.append({"level": "warning", "code": "stale_verification", "project_id": pid,
                               "message": f"{pid} 的 {stage} 验证已过期：{value.get('stale_reason', '依赖发生变化')}",
                               "evidence": [value.get("event_id"), value.get("commit")]})
        latest_snap = next((e for e in reversed(events) if e["event_type"] == "git_snapshot"), None)
        if latest_snap and latest_snap["dirty"]:
            latest_test = next((e for e in reversed(events) if e["event_type"] in {"test_completed", "benchmark_completed", "experiment_completed"}), None)
            snap_tree = _payload(latest_snap).get("tree_hash")
            test_tree = _payload(latest_test).get("tree_hash") if latest_test else None
            same_tree_was_tested = bool(snap_tree and test_tree and snap_tree == test_tree and latest_test["status"] == "passed")
            if not same_tree_was_tested and (not latest_test or latest_test["occurred_at"] < latest_snap["occurred_at"]):
                output.append({"level": "warning", "code": "unverified_code_change", "project_id": pid,
                               "message": f"{pid} 存在工作树修改，但最近没有绑定在修改之后的测试或实验结果",
                               "evidence": [latest_snap["event_id"]]})
        local_times = [value.get("verified_at") for stage, value in state.stages.items()
                       if stage in {"local_model", "local_eval", "sample_resume"} and value.get("status") == "passed" and value.get("verified_at")]
        if local_times:
            try: old = datetime.fromisoformat(local_times[-1])
            except ValueError: old = None
            if old and datetime.now(timezone.utc) - old > timedelta(days=stale_days):
                remote_pending = [stage for stage in state.stages if stage in {"docker", "jetson", "judge", "platform_submission"}
                                  and state.stages[stage].get("status") in {"pending", "stale"}]
                if remote_pending:
                    output.append({"level": "info", "code": "remote_verification_pending", "project_id": pid,
                                   "message": f"{pid} 本地验证已超过 {stale_days} 天，远端阶段仍未完成：{', '.join(remote_pending)}",
                                   "evidence": local_times})
    snapshots = ledger.events(event_types={"resource_snapshot"})
    if len(snapshots) >= 2:
        current = _payload(snapshots[-1]); previous = _payload(snapshots[-2])
        try:
            current_at = datetime.fromisoformat(current["sampled_at"])
            previous_at = datetime.fromisoformat(previous["sampled_at"])
            sample_gap = current_at - previous_at
        except (KeyError, TypeError, ValueError):
            sample_gap = timedelta(0)
        previous_by_gpu = {str(g.get("index")): g for g in previous.get("gpus", [])}
        for gpu in current.get("gpus", []):
            old = previous_by_gpu.get(str(gpu.get("index")))
            if not old: continue
            try:
                idle = float(gpu.get("utilization_pct", 0)) == 0 and float(gpu.get("memory_used_mb", 0)) > 1024
                old_idle = float(old.get("utilization_pct", 0)) == 0 and float(old.get("memory_used_mb", 0)) > 1024
            except (TypeError, ValueError):
                continue
            # Two adjacent samples are not enough to call a resource orphaned.
            # A normal sampler may briefly observe a model between requests.
            if idle and old_idle and sample_gap >= timedelta(minutes=15):
                output.append({"level": "warning", "code": "gpu_idle_allocated", "project_id": None,
                               "message": f"GPU {gpu.get('index')} 连续采样处于低利用率但仍占用显存",
                               "evidence": [snapshots[-2]["event_id"], snapshots[-1]["event_id"]]})
    return output
