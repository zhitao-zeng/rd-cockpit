from __future__ import annotations

import html
from datetime import date
from pathlib import Path
from typing import Any

from .anomalies import find_anomalies
from .config import load_config
from .ledger import Ledger
from .state import build_state, state_dict
from .report import build_facts
from .semantic import build_semantic_facts
from .period import build_period_facts


CSS = """
body{font-family:Inter,system-ui,sans-serif;background:#0b1020;color:#dbeafe;margin:0;padding:28px}
h1,h2{color:#67e8f9} .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px}
.card{background:#111a2e;border:1px solid #263858;border-radius:10px;padding:16px}
.muted{color:#8ca3c5;font-size:.9em} table{width:100%;border-collapse:collapse}td,th{padding:7px;border-bottom:1px solid #263858;text-align:left}
.warning{color:#fbbf24}.critical{color:#fb7185}.ok{color:#34d399}.pill{border-radius:999px;padding:2px 8px;background:#203152}
code{color:#a7f3d0}
"""


def render(ledger: Ledger, home: Path) -> str:
    config = load_config(home / "config" / "projects.yaml")
    states = {pid: state_dict(build_state(ledger, home, pid)) for pid in sorted(config.get("projects", {}))}
    anomalies = find_anomalies(ledger, home)
    rows = ledger.events()
    recent = rows[-20:]
    today = build_facts(ledger, date.today())
    semantic = build_semantic_facts(ledger, home, date.today())
    week = build_period_facts(ledger, "week", date.today())
    cards = []
    for pid, state in states.items():
        pending = [f"{stage}: {value['status']}" for stage, value in state["verification"].items() if value["status"] not in {"passed"}]
        cards.append(f"<div class='card'><h2>{html.escape(state['name'])}</h2>"
                     f"<div class='muted'>{html.escape(pid)} · {html.escape(state['branch'] or 'unknown')} · dirty={state['dirty']}</div>"
                     f"<p>HEAD <code>{html.escape((state['head'] or 'unknown')[:12])}</code></p>"
                     f"<p>验证：{html.escape(', '.join(pending) or 'all configured stages passed')}</p>"
                     f"<p>阻塞：{html.escape('; '.join(state['blockers']) or 'none')}</p></div>")
    anomaly_rows = "".join(f"<tr><td class='{html.escape(item['level'])}'>{html.escape(item['level'])}</td>"
                            f"<td><code>{html.escape(item['code'])}</code></td><td>{html.escape(item['message'])}</td></tr>" for item in anomalies)
    timeline_rows = "".join(f"<tr><td>{html.escape(row['occurred_at'])}</td><td>{html.escape(row['project_id'] or 'unassigned')}</td>"
                            f"<td>{html.escape(row['event_type'])}</td><td>{html.escape(row['status'] or '-')}</td></tr>" for row in reversed(recent))
    result_rows = "".join(f"<li><code>{html.escape(item.get('project_id') or 'unassigned')}</code> {html.escape(item['text'])}</li>"
                          for item in semantic.get("today_results", [])) or "<li class='muted'>今天尚无已验证成果</li>"
    next_rows = "".join(f"<li><code>{html.escape(item.get('project_id') or 'unassigned')}</code> {html.escape(item['action'])}</li>"
                        for item in semantic.get("next_actions", [])[:6]) or "<li class='muted'>暂无建议</li>"
    trend_rows = "".join(f"<tr><td>{html.escape(item['date'])}</td><td>{item['events']}</td><td>{len(item['projects'])}</td>"
                         f"<td>{item['tests_passed']} / {item['tests_failed']}</td><td>{item['experiments']}</td></tr>"
                         for item in week.get("trend", [])[-7:])
    return f"""<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>R&D Cockpit</title><style>{CSS}</style>
<body><h1>R&amp;D Cockpit</h1><p class='muted'>事实账本驱动的只读研发状态视图；不会自动修改代码或资源。</p>
<div class='grid'>{''.join(cards)}</div>
<div class='grid'><div class='card'><h2>今日成果</h2><p>事件：{today['summary']['events']} · 测试：{today['summary']['tests']['passed']} passed / {today['summary']['tests']['failed']} failed</p><ul>{result_rows}</ul></div>
<div class='card'><h2>下一步</h2><ul>{next_rows}</ul></div></div>
<h2>本周趋势</h2><div class='card'><table><tr><th>日期</th><th>事件</th><th>项目</th><th>测试通过/失败</th><th>实验</th></tr>{trend_rows or '<tr><td colspan=5 class="muted">暂无数据</td></tr>'}</table></div>
<h2>异常</h2><div class='card'><table><tr><th>级别</th><th>规则</th><th>说明</th></tr>{anomaly_rows or '<tr><td colspan=3 class="ok">无异常</td></tr>'}</table></div>
<h2>最近事件</h2><div class='card'><table><tr><th>时间</th><th>项目</th><th>事件</th><th>状态</th></tr>{timeline_rows}</table></div>
</body></html>"""


def write_dashboard(ledger: Ledger, home: Path) -> str:
    path = home / "reports" / "dashboard.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(ledger, home), encoding="utf-8")
    return str(path)
