from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
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
