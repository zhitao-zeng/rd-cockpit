#!/usr/bin/env python3
"""Deterministic payload/latency smoke gate for Daily Report projections."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rd_cockpit.development import (
    development_dashboard, development_project_view, development_summary_view,
)
from rd_cockpit.report_facts import refresh_report_facts


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="rd-performance-") as directory:
        root = Path(directory)
        home = root / "cockpit"
        reports = root / "reports"
        repo = root / "repo"
        (home / "config").mkdir(parents=True)
        reports.mkdir()
        repo.mkdir()
        config = home / "config" / "projects.yaml"
        config.write_text(
            f"projects:\n  demo:\n    name: Demo\n    repo_path: {repo}\n",
            encoding="utf-8",
        )
        os.environ["RD_DAILY_REPORT_DIR"] = str(reports)
        os.environ["RD_PROJECTS_CONFIG"] = str(config)
        start = date(2026, 1, 1)
        for index in range(120):
            day = start + timedelta(days=index)
            (reports / f"{day.isoformat()}.md").write_text(
                f"""# 日报 {day.isoformat()}

## 核心进展
### Demo
#### 第 {index + 1} 轮验证
- **做了什么**：运行固定回归集。
- **为什么**：检查实现变化。
- **结果**：{10 + index % 5} tests passed，延迟为 {40 + index % 7}ms。

## 昨日计划闭环
- Demo 回归：completed
""",
                encoding="utf-8",
            )

        started = time.perf_counter()
        dashboard = development_dashboard(home, days=180, target=start + timedelta(days=120))
        cold_seconds = time.perf_counter() - started
        summary = development_summary_view(dashboard)
        detail = development_project_view(dashboard, "demo")
        full_bytes = len(json.dumps(dashboard, ensure_ascii=False).encode())
        summary_bytes = len(json.dumps(summary, ensure_ascii=False).encode())
        detail_bytes = len(json.dumps(detail, ensure_ascii=False).encode())

        started = time.perf_counter()
        facts = refresh_report_facts(home)
        warm_seconds = time.perf_counter() - started
        metrics = {
            "reports": dashboard["report_count"],
            "cold_seconds": round(cold_seconds, 4),
            "warm_fact_seconds": round(warm_seconds, 4),
            "full_bytes": full_bytes,
            "summary_bytes": summary_bytes,
            "project_bytes": detail_bytes,
            "facts_reused": facts["refresh"]["reused"],
        }
        print(json.dumps(metrics, ensure_ascii=False, indent=2))
        failures = []
        if cold_seconds > 10:
            failures.append("cold projection exceeded 10 seconds")
        if warm_seconds > 1.5:
            failures.append("warm fact refresh exceeded 1.5 seconds")
        if summary_bytes > 250_000:
            failures.append("summary payload exceeded 250 KB")
        if detail_bytes > 500_000:
            failures.append("project payload exceeded 500 KB")
        if summary_bytes >= full_bytes * 0.5:
            failures.append("summary is not materially smaller than the legacy payload")
        if facts["refresh"]["parsed"] != 0 or facts["refresh"]["reused"] != 120:
            failures.append("warm fact refresh did not reuse all reports")
        if failures:
            print("Performance gate failed: " + "; ".join(failures))
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
