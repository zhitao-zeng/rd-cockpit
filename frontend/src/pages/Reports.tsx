import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  getCard,
  getDailyReport,
  getDailySemantic,
  getReplay,
  getStats,
  getWrapped,
} from "../lib/api";
import { buildTrendRows } from "../lib/adapters";
import { fmtDateTime, fmtHours, fmtInt, todayLocal } from "../lib/format";
import { Card, DataTable, EmptyState, PageHeader, QueryBoundary, StatCard } from "../components/ui";
import { ConfidenceTag, EvidenceRef, StatusBadge } from "../components/badges";
import { Tabs } from "../components/controls";
import { Chart } from "../components/Chart";
import { axisBase, barSeries, baseChartOption, C, legendBase, lineSeries } from "../lib/chartTheme";
import type { SemanticFacts, StatsFacts } from "../lib/types";

const TABS = [
  { key: "daily", label: "日报" },
  { key: "weekly", label: "周报" },
  { key: "monthly", label: "月报" },
  { key: "replay", label: "Today Replay" },
  { key: "wrapped", label: "Research Wrapped" },
  { key: "card", label: "Daily Card" },
];

export function Reports() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? "daily";
  const date = params.get("date") ?? todayLocal();

  const setParam = (key: string, value: string) => {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (value) next.set(key, value);
      else next.delete(key);
      return next;
    });
  };

  return (
    <div>
      <PageHeader
        title="报告"
        description="日报/周报/月报/Replay/Wrapped/Card · 日期与 Tab 保存在 URL"
        right={
          <input
            type="date"
            value={date}
            onChange={(e) => setParam("date", e.target.value)}
            className="rounded-md border border-line bg-card px-2.5 py-1.5 text-sm text-ink outline-none"
          />
        }
      />
      <Tabs tabs={TABS} value={tab} onChange={(k) => setParam("tab", k)} />
      {tab === "daily" && <DailyTab date={date} />}
      {tab === "weekly" && <PeriodTab period="week" />}
      {tab === "monthly" && <PeriodTab period="month" />}
      {tab === "replay" && <ReplayTab date={date} />}
      {tab === "wrapped" && <WrappedTab date={date} />}
      {tab === "card" && <CardTab date={date} />}
    </div>
  );
}

// ---------- 日报 ----------

function DailyTab({ date }: { date: string }) {
  const report = useQuery({ queryKey: ["daily-report", date], queryFn: () => getDailyReport(date), retry: 0 });
  const semantic = useQuery({ queryKey: ["semantic", date], queryFn: () => getDailySemantic(date) });
  return (
    <div className="space-y-4">
      <Card title="语义摘要" subtitle={`来源: /reports/daily/${date}/semantic（实时计算，不依赖已生成日报）`}>
        <QueryBoundary query={semantic}>
          {(d) => <SemanticSections data={d} />}
        </QueryBoundary>
      </Card>
      <Card title="日报事实" subtitle={`来源: /reports/daily/${date}（需要 rd daily 已生成该日报告）`}>
        <QueryBoundary query={report}>
          {(d) => (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <StatCard label="事件" value={fmtInt(d.summary.events)} source="report.summary" />
                <StatCard
                  label="测试 通过/失败"
                  value={`${d.summary.tests.passed}/${d.summary.tests.failed}`}
                  tone={d.summary.tests.failed > 0 ? "warn" : "good"}
                  source="report.summary.tests"
                />
                <StatCard label="人工活跃" value={fmtHours(d.summary.time.human_active_hours as number)} source="report.summary.time" />
                <StatCard label="Agent 时长" value={fmtHours(d.summary.time.agent_hours as number)} source="report.summary.time" />
              </div>
              {Array.isArray(d.summary.highlights) && d.summary.highlights.length > 0 && (
                <div>
                  <h4 className="mb-1.5 text-xs font-medium text-ink2">Highlights</h4>
                  <ul className="space-y-1">
                    {d.summary.highlights.map((h, i) => (
                      <li key={i} className="text-sm text-ink2">
                        <code className="mr-2 text-xs text-primary">{String(h.type ?? "")}</code>
                        {String(h.detail ?? h.status ?? "")}
                        <span className="ml-2 text-[10px] text-ink3">{String(h.project_id ?? "")}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              <DataTable
                columns={[
                  { key: "project", label: "项目" },
                  { key: "events", label: "事件", align: "right" },
                  { key: "commits", label: "Commits", align: "right" },
                  { key: "types", label: "事件类型分布" },
                ]}
                rows={Object.entries(d.summary.projects).map(([pid, v]) => ({
                  project: <span className="text-primary">{pid}</span>,
                  events: <span className="tabular-nums">{v.events}</span>,
                  commits: <span className="tabular-nums">{v.commits.length}</span>,
                  types: (
                    <span className="text-xs text-ink3">
                      {Object.entries(v.types).map(([t, n]) => `${t}=${n}`).join(" · ")}
                    </span>
                  ),
                }))}
                keyFn={(r) => (r.project as React.ReactElement).props.children as string}
              />
            </div>
          )}
        </QueryBoundary>
      </Card>
    </div>
  );
}

function SemanticSections({ data }: { data: SemanticFacts }) {
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <div>
        <h4 className="mb-1.5 text-xs font-medium text-ink2">今日成果（{data.today_results.length}）</h4>
        {data.today_results.length === 0 ? (
          <EmptyState text="今日无成果记录" />
        ) : (
          <ul className="space-y-1.5">
            {data.today_results.map((r, i) => (
              <li key={i} className="text-sm text-ink2">
                <StatusBadge status={r.status} className="mr-1.5" />
                {r.text}
                <span className="ml-1.5"><EvidenceRef ids={r.evidence} max={1} /></span>
              </li>
            ))}
          </ul>
        )}
        <h4 className="mb-1.5 mt-4 text-xs font-medium text-ink2">昨日计划闭环（{data.yesterday_plan_closure.length}）</h4>
        {data.yesterday_plan_closure.length === 0 ? (
          <EmptyState text="昨日无计划闭环记录" />
        ) : (
          <ul className="space-y-1.5">
            {data.yesterday_plan_closure.map((p, i) => (
              <li key={i} className="text-sm text-ink2">
                <StatusBadge status={String(p.status ?? "open")} className="mr-1.5" />
                {String(p.plan ?? p.text ?? "")}
                {Boolean(p.reason) && <span className="ml-1 text-xs text-ink3">{String(p.reason)}</span>}
              </li>
            ))}
          </ul>
        )}
      </div>
      <div>
        <h4 className="mb-1.5 text-xs font-medium text-ink2">当前阻塞（{data.current_blockers.length}）</h4>
        {data.current_blockers.length === 0 ? (
          <EmptyState text="无阻塞" />
        ) : (
          <ul className="space-y-1.5">
            {data.current_blockers.map((b, i) => (
              <li key={i} className="text-sm text-ink2">
                <ConfidenceTag value={b.confidence} className="mr-1.5" />
                {b.text}
                <span className="ml-1.5 text-[10px] text-ink3">{b.project_id}</span>
              </li>
            ))}
          </ul>
        )}
        <h4 className="mb-1.5 mt-4 text-xs font-medium text-ink2">下一步（{data.next_actions.length}）</h4>
        {data.next_actions.length === 0 ? (
          <EmptyState text="无下一步建议" />
        ) : (
          <ul className="space-y-1.5">
            {data.next_actions.map((a, i) => (
              <li key={i} className="text-sm text-ink2">
                <span className="mr-1.5 text-xs text-primary">{a.project_id}</span>
                {a.action}
                <span className="ml-1.5 text-xs text-ink3">{a.reason}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

// ---------- 周报 / 月报 ----------

function PeriodTab({ period }: { period: "week" | "month" }) {
  const stats = useQuery({ queryKey: ["stats", period], queryFn: () => getStats(period) });
  const label = period === "week" ? "周报" : "月报";
  return (
    <QueryBoundary query={stats}>
      {(d: StatsFacts) => {
        const rows = buildTrendRows(d);
        return (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <StatCard label={`${label}周期`} value={d.label} source="来源: /stats" />
              <StatCard label="事件 / Commits" value={`${d.outputs.events} / ${d.outputs.commits}`} source="stats.outputs" />
              <StatCard
                label="测试 通过/失败"
                value={`${d.outputs.tests.passed}/${d.outputs.tests.failed}`}
                tone={d.outputs.tests.failed > 0 ? "warn" : "good"}
                source="stats.outputs.tests"
              />
              <StatCard label="实验 / 决策" value={`${d.outputs.experiments} / ${d.outputs.decisions}`} source="stats.outputs" />
              <StatCard label="人工活跃" value={fmtHours(d.time.human_active_hours)} source="stats.time" />
              <StatCard label="Agent 时长" value={fmtHours(d.time.agent_hours)} source="stats.time" />
              <StatCard label="命令时长" value={fmtHours(d.time.command_hours)} source="stats.time" />
              <StatCard label="上下文切换" value={d.time.context_switches} source="stats.time" />
            </div>

            <Card title="每日趋势" subtitle="来源: stats.trend">
              {rows.length === 0 ? (
                <EmptyState text="该周期无趋势数据" />
              ) : (
                <Chart
                  height={240}
                  option={{
                    ...baseChartOption(),
                    legend: { ...legendBase() },
                    xAxis: { type: "category", data: rows.map((r) => r.date.slice(5)), ...axisBase() },
                    yAxis: { type: "value", minInterval: 1, ...axisBase() },
                    series: [
                      lineSeries("事件", rows.map((r) => r.events), C.primary),
                      barSeries("实验", rows.map((r) => r.experiments), C.cat[0]),
                      barSeries("决策", rows.map((r) => r.decisions), C.cat[2]),
                    ],
                  }}
                />
              )}
            </Card>

            <div className="grid gap-4 lg:grid-cols-2">
              <Card title="项目分布" subtitle="来源: stats.projects" pad={false}>
                <DataTable
                  columns={[
                    { key: "project", label: "项目" },
                    { key: "events", label: "事件", align: "right" },
                    { key: "commits", label: "Commits", align: "right" },
                  ]}
                  rows={Object.entries(d.projects).map(([pid, v]) => ({
                    project: <span className="text-primary">{pid}</span>,
                    events: <span className="tabular-nums">{v.events}</span>,
                    commits: <span className="tabular-nums">{v.commits.length}</span>,
                  }))}
                  keyFn={(r) => (r.project as React.ReactElement).props.children as string}
                />
              </Card>
              <Card title={`未完成计划（${d.unfinished.length}）`} subtitle="来源: stats.unfinished">
                {d.unfinished.length === 0 ? (
                  <EmptyState text="无未完成计划 🎉" />
                ) : (
                  <ul className="space-y-1.5">
                    {d.unfinished.map((u, i) => (
                      <li key={i} className="text-sm text-ink2">
                        <span className="mr-1.5 text-xs text-primary">{u.project_id}</span>
                        {u.text}
                        <StatusBadge status={u.status} className="ml-1.5" />
                      </li>
                    ))}
                  </ul>
                )}
              </Card>
            </div>
          </div>
        );
      }}
    </QueryBoundary>
  );
}

// ---------- Today Replay ----------

function ReplayTab({ date }: { date: string }) {
  const replay = useQuery({ queryKey: ["replay", date], queryFn: () => getReplay(date) });
  return (
    <Card title={`${date} 回放`} subtitle="来源: /insights/replay?query=（当日事件时间线 + 语义摘要）" pad={false}>
      <QueryBoundary query={replay} isEmpty={(d) => d.timeline.length === 0} emptyText="该日期无事件回放">
        {(d) => (
          <ul className="max-h-[600px] divide-y divide-line/50 overflow-y-auto">
            {d.timeline.map((t, i) => (
              <li key={i} className="flex items-center gap-3 px-4 py-2 text-sm">
                <span className="w-16 shrink-0 font-mono text-[10px] text-ink3">{fmtDateTime(t.at)}</span>
                <code className="shrink-0 text-xs text-primary">{t.type}</code>
                <span className="w-28 shrink-0 truncate text-xs text-ink3">{t.project_id ?? "unassigned"}</span>
                <span className="min-w-0 flex-1 truncate text-xs text-ink2">{t.detail}</span>
                <StatusBadge status={t.status} />
                <EvidenceRef ids={t.evidence} max={1} />
              </li>
            ))}
          </ul>
        )}
      </QueryBoundary>
    </Card>
  );
}

// ---------- Research Wrapped ----------

function WrappedTab({ date }: { date: string }) {
  const wrapped = useQuery({ queryKey: ["wrapped", date], queryFn: () => getWrapped(date) });
  return (
    <QueryBoundary query={wrapped}>
      {(d) => {
        const rows = buildTrendRows({ trend: d.trend } as unknown as StatsFacts);
        return (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <StatCard label="周期" value={d.period} source="来源: /insights/wrapped" />
              <StatCard label="最活跃项目" value={d.most_active_project ?? "—"} tone="primary" source="wrapped" />
              <StatCard label="事件 / Commits" value={`${d.outputs.events} / ${d.outputs.commits}`} source="wrapped.outputs" />
              <StatCard
                label="测试 通过/失败"
                value={`${d.outputs.tests.passed}/${d.outputs.tests.failed}`}
                source="wrapped.outputs"
              />
              <StatCard label="实验" value={d.outputs.experiments} source="wrapped.outputs" />
              <StatCard label="决策" value={d.outputs.decisions} source="wrapped.outputs" />
              <StatCard label="失败事件" value={d.failed_events} tone={d.failed_events > 0 ? "warn" : "default"} source="wrapped" />
              <StatCard
                label="被否/被替代决策"
                value={d.rejected_or_superseded_decisions}
                hint="研究中的正常淘汰"
                source="wrapped"
              />
            </div>
            <Card title="趋势" subtitle="wrapped.trend">
              {rows.length === 0 ? (
                <EmptyState text="该周期无趋势数据" />
              ) : (
                <Chart
                  height={240}
                  option={{
                    ...baseChartOption(),
                    xAxis: { type: "category", data: rows.map((r) => r.date.slice(5)), ...axisBase() },
                    yAxis: { type: "value", minInterval: 1, ...axisBase() },
                    series: [lineSeries("事件", rows.map((r) => r.events), C.primary)],
                  }}
                />
              )}
            </Card>
            <p className="text-[10px] text-ink3">basis evidence: {d.basis.length} 条事件</p>
          </div>
        );
      }}
    </QueryBoundary>
  );
}

// ---------- Daily Research Card ----------

function CardTab({ date }: { date: string }) {
  const card = useQuery({ queryKey: ["daily-card", date], queryFn: () => getCard(date) });
  return (
    <QueryBoundary query={card}>
      {(d) => (
        <div className="mx-auto max-w-2xl">
          <Card title={`研究卡片 · ${d.date}`} subtitle="来源: /advanced/card?query=">
            <div className="space-y-4">
              <div>
                <h4 className="mb-1 text-xs font-medium text-primary">主线</h4>
                {d.mainline.length === 0 ? (
                  <p className="text-sm text-ink3">（当日无主线成果）</p>
                ) : (
                  <p className="text-base text-ink">{d.mainline[0].text}</p>
                )}
              </div>
              <div>
                <h4 className="mb-1 text-xs font-medium text-ink2">成果（{d.results.length}）</h4>
                <ul className="space-y-1">
                  {d.results.map((r, i) => (
                    <li key={i} className="text-sm text-ink2">
                      <StatusBadge status={r.status} className="mr-1.5" />
                      {r.text}
                    </li>
                  ))}
                  {d.results.length === 0 && <li className="text-sm text-ink3">（无）</li>}
                </ul>
              </div>
              <div>
                <h4 className="mb-1 text-xs font-medium text-ink2">阻塞（{d.blockers.length}）</h4>
                <ul className="space-y-1">
                  {d.blockers.map((b, i) => (
                    <li key={i} className="text-sm text-warning/90">{b.text}</li>
                  ))}
                  {d.blockers.length === 0 && <li className="text-sm text-ink3">（无）</li>}
                </ul>
              </div>
              <div>
                <h4 className="mb-1 text-xs font-medium text-ink2">下一步（{d.next.length}）</h4>
                <ul className="space-y-1">
                  {d.next.map((a, i) => (
                    <li key={i} className="text-sm text-ink2">{a.action}</li>
                  ))}
                  {d.next.length === 0 && <li className="text-sm text-ink3">（无）</li>}
                </ul>
              </div>
              <EvidenceRef ids={d.evidence} max={5} />
            </div>
          </Card>
        </div>
      )}
    </QueryBoundary>
  );
}
