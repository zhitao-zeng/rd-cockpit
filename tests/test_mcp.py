from __future__ import annotations

import json
from pathlib import Path

from rd_cockpit.mcp_stdio import handle_message


def test_mcp_initialize_tools_and_call(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "projects.yaml").write_text(
        "projects:\n  demo:\n    name: Demo\n    repo_path: /tmp\n    verification_stages: [implementation]\n", encoding="utf-8"
    )
    initialized = handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, tmp_path)
    assert initialized and initialized["result"]["serverInfo"]["name"] == "rd-cockpit"
    listed = handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, tmp_path)
    assert listed and {tool["name"] for tool in listed["result"]["tools"]} >= {"rd_resume", "rd_anomalies", "rd_daily"}
    called = handle_message({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                             "params": {"name": "rd_resume", "arguments": {"project": "demo"}}}, tmp_path)
    assert called and called["result"]["isError"] is False
    assert called["result"]["structuredContent"]["result"]["project_id"] == "demo"
    next_call = handle_message({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                "params": {"name": "rd_next", "arguments": {"project": "demo"}}}, tmp_path)
    assert next_call and next_call["result"]["isError"] is False
    search_call = handle_message({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                                  "params": {"name": "rd_search", "arguments": {"query": "missing"}}}, tmp_path)
    assert search_call and search_call["result"]["isError"] is False
    insight_call = handle_message({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                                   "params": {"name": "rd_insights", "arguments": {"kind": "twin"}}}, tmp_path)
    assert insight_call and insight_call["result"]["isError"] is False
