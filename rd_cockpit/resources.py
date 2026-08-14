from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from typing import Any

from .ledger import Ledger


def _command(args: list[str]) -> tuple[int, str, str]:
    try:
        p = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        return p.returncode, p.stdout, p.stderr
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return 127, "", str(exc)


def sample(ledger: Ledger, *, machine: str = "local") -> dict[str, Any]:
    result: dict[str, Any] = {"machine": machine, "sampled_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    code, out, err = _command(["nvidia-smi", "--query-gpu=index,name,memory.used,memory.total,utilization.gpu,temperature.gpu,power.draw", "--format=csv,noheader,nounits"])
    gpus: list[dict[str, Any]] = []
    if code == 0:
        for line in out.splitlines():
            parts = [part.strip() for part in line.split(",")]
            if len(parts) >= 7:
                keys = ["index", "name", "memory_used_mb", "memory_total_mb", "utilization_pct", "temperature_c", "power_w"]
                item: dict[str, Any] = {k: v for k, v in zip(keys, parts)}
                for key in keys[0:1] + keys[2:]:
                    try: item[key] = float(item[key]) if key != "index" else int(item[key])
                    except (TypeError, ValueError): pass
                gpus.append(item)
    result["gpus"] = gpus
    result["nvidia_smi_error"] = err.strip() if code else None
    code, out, err = _command(["docker", "ps", "--format", "{{json .}}"])
    containers: list[dict[str, Any]] = []
    if code == 0:
        for line in out.splitlines():
            try: containers.append(json.loads(line))
            except json.JSONDecodeError: pass
    result["containers"] = containers
    result["docker_error"] = err.strip() if code else None
    ledger.append(event_type="resource_snapshot", source="resource_sampler", machine=machine,
                  payload=result, dedup_key=f"resource:{machine}:{result['sampled_at']}")
    return result


def _bucket_start(value: str, kind: str) -> str:
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    if kind == "hour":
        stamp = stamp.replace(minute=0, second=0, microsecond=0)
    else:
        stamp = stamp.replace(hour=0, minute=0, second=0, microsecond=0)
    return stamp.isoformat()


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _recent_rollups(ledger: Ledger, since: str, kind: str) -> dict[tuple[str, str, int], dict[str, Any]]:
    groups: dict[tuple[str, str, int], dict[str, Any]] = {}
    rows = ledger.db.execute(
        "SELECT occurred_at,machine,payload_json FROM events "
        "WHERE event_type='resource_snapshot' AND occurred_at>=? ORDER BY occurred_at",
        (since,),
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            continue
        for gpu in payload.get("gpus") or []:
            if not isinstance(gpu, dict):
                continue
            try:
                gpu_index = int(gpu.get("index"))
            except (TypeError, ValueError):
                continue
            try:
                bucket_start = _bucket_start(str(row["occurred_at"]), kind)
            except ValueError:
                continue
            key = (bucket_start, str(row["machine"] or "local"), gpu_index)
            item = groups.setdefault(key, {
                "sample_count": 0, "utilization": [], "memory": [], "temperature": [], "power": [],
            })
            item["sample_count"] += 1
            for field, target in (
                ("utilization_pct", "utilization"), ("memory_used_mb", "memory"),
                ("temperature_c", "temperature"), ("power_w", "power"),
            ):
                if (number := _float(gpu.get(field))) is not None:
                    item[target].append(number)
    points: dict[tuple[str, str, int], dict[str, Any]] = {}
    for (bucket_start, machine, gpu_index), item in groups.items():
        average = lambda values: sum(values) / len(values) if values else None
        points[(bucket_start, machine, gpu_index)] = {
            "bucket_start": bucket_start, "machine": machine, "gpu_index": gpu_index,
            "sample_count": item["sample_count"],
            "avg_utilization_pct": average(item["utilization"]),
            "avg_memory_used_mb": average(item["memory"]),
            "max_memory_used_mb": max(item["memory"]) if item["memory"] else None,
            "avg_temperature_c": average(item["temperature"]),
            "avg_power_w": average(item["power"]),
        }
    return points


def rollup_history(ledger: Ledger, *, days: int = 365, kind: str = "day") -> dict[str, Any]:
    if kind not in {"hour", "day"}:
        raise ValueError("kind must be hour or day")
    since = (datetime.now(timezone.utc) - timedelta(days=max(1, days))).isoformat()
    rows = ledger.db.execute(
        "SELECT * FROM resource_rollups WHERE bucket_kind=? AND bucket_start>=? "
        "ORDER BY bucket_start,machine,gpu_index", (kind, since),
    ).fetchall()
    # Recent samples have not reached the compaction threshold yet. Aggregate
    # them on read so the history endpoint is continuous instead of showing
    # only data older than 30 days. If a crash/no-prune run leaves both forms,
    # the raw bucket wins and is not counted twice.
    recent = _recent_rollups(ledger, since, kind)
    persisted = {
        (str(row["bucket_start"]), str(row["machine"]), int(row["gpu_index"])): {
            "bucket_start": row["bucket_start"], "machine": row["machine"],
            "gpu_index": row["gpu_index"], "sample_count": row["sample_count"],
            "avg_utilization_pct": row["avg_utilization_pct"],
            "avg_memory_used_mb": row["avg_memory_used_mb"],
            "max_memory_used_mb": row["max_memory_used_mb"],
            "avg_temperature_c": row["avg_temperature_c"], "avg_power_w": row["avg_power_w"],
        }
        for row in rows
    }
    persisted.update(recent)
    return {
        "kind": kind, "days": days, "retention_note": "原始采样保留 30 天；更早数据使用小时/日聚合。",
        "points": [persisted[key] for key in sorted(persisted)],
    }
