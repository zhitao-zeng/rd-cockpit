import { Link } from "react-router-dom";
import { useQueries, useQuery } from "@tanstack/react-query";
import {
  getAnomalies,
  getDailySemantic,
  getGpuReport,
  getHealthScore,
  getProjects,
  getResearchMap,
  getStats,
} from "../lib/api";
import {
  anomalyAdvice,
  buildFunnel,
  buildGpuSummary,
  buildProjectActivity,
  buildProjectView,
  buildRiskDistribution,
  buildTrendRows,
} from "../lib/adapters";
import { fmtDateTime, fmtMb, fmtPct100, fmtRelative, todayLocal } from "../lib/format";
import { Card, EmptyState, PageHeader, QueryBoundary, StatCard } from "../components/ui";
import { ConfidenceTag, DirtyBadge, EvidenceRef, ProgressBar, StatusBadge } from "../components/badges";
import { Chart } from "../components/Chart";
import { axisBase, barSeries, baseChartOption, C, legendBase, lineSeries, tooltipBase } from "../lib/chartTheme";
import type { HealthInfo, ProjectState } from "../lib/types";

function usePageData() {
  const today = todayLocal();
  const semantic = useQuery({ queryKey: ["semantic", today], queryFn: () => getDailySemantic(today) });
  const stats = useQuery({ queryKey: ["stats", "week"], queryFn: () => getStats("week") });
  const projects = useQuery({ queryKey: ["projects"], queryFn: getProjects });
  const anomalies = useQuery({ queryKey: ["anomalies"], queryFn: () => getAnomalies() });
  const gpu = useQuery({ queryKey: ["gpu"], queryFn: getGpuReport });
  const map = useQuery({ queryKey: ["map"], queryFn: getResearchMap });
  const projectIds = Object.keys(projects.data ?? {}).sort();
  const health = useQueries({
    queries: projectIds.map((id) => ({
      queryKey: ["health", id],
      queryFn: () => getHealthScore(id),
    })),
  });
  const healthById: Record<string, HealthInfo> = {};
  projectIds.forEach((id, i) => {
    const data = health[i]?.data;
    if (data) healthById[id] = data;
  });
  return { semantic, stats, projects, anomalies, gpu, map, healthById };
}

function SemanticListCard({
  title,
  subtitle,
  query,
  children,
  isEmpty,
}: {
  title: string;
  subtitle: string;
  query: ReturnType<typeof usePageData>["semantic"];
  children: (data: NonNullable<(typeof query)["data"]>) => React.ReactNode;
  isEmpty: (data: NonNullable<(typeof query)["data"]>) => boolean;
}) {
  return (
    <Card title={title} subtitle={subtitle} className="min-h-[180px]">
      <QueryBoundary query={query} isEmpty={isEmpty} emptyText="暂无记录" emptyDetail="后端语义投影在该日期范围内没有匹配事件">
        {children}
      </QueryBoundary>
    </Card>
  );
}

export function Overview() {
  const { semantic, stats, projects, anomalies, gpu, map, healthById } = usePageData();
  const today = todayLocal();

  return (
    <div className="space-y-4">
      <PageHeader
        title="总览"
        description={`数据日期 ${today}（Asia/Shanghai）· 统计周期：本周 · 全部数据来自只读事件账本`}
      />

      {/* 今日成果 / 当前阻塞 / 下一步建议 */}
      <div className="grid gap-4 lg:grid-cols-3">
        <SemanticListCard
          title="今日成果"
          subtitle="来源: /reports/daily/{date}/semantic → today_results"
          query={semantic}
          isEmpty={(d) => d.today_results.length === 0}
        >
          {(d) => (
            <ul className="space-y-2">
              {d.today_results.map((item, i) => (
                <li key={i} className="text-sm">
                  <div className="flex items-center gap-2">
                    <StatusBadge status={item.status} />
                    <ConfidenceTag value={item.confidence} />
                    {item.project_id && <span className="text-xs text-ink3">{item.project_id}</span>}
                  </div>
                  <p className="mt-1 line-clamp-2 text-ink2">{item.text}</p>
                  <EvidenceRef ids={item.evidence} max={2} />
                </li>
              ))}
            </ul>
          )}
        </SemanticListCard>

        <SemanticListCard
          title="当前阻塞"
          subtitle="来源: /reports/daily/{date}/semantic → current_blockers"
          query={semantic}
          isEmpty={(d) => d.current_blockers.length === 0}
        >
          {(d) => (
            <ul className="space-y-2">
              {d.current_blockers.map((item, i) => (
                <li key={i} className="text-sm">
                  <div className="flex items-center gap-2">
                    <ConfidenceTag value={item.confidence} />
                    {item.project_id && <span className="text-xs text-ink3">{item.project_id}</span>}
                  </div>
                  <p className="mt-1 line-clamp-2 text-ink2">{item.text}</p>
                  {item.evidence && <EvidenceRef ids={item.evidence} max={2} />}
                </li>
              ))}
            </ul>
          )}
        </SemanticListCard>

        <SemanticListCard
          title="下一步建议"
          subtitle="来源: /reports/daily/{date}/semantic → next_actions"
          query={semantic}
          isEmpty={(d) => d.next_actions.length === 0}
        >
          {(d) => (
            <ul className="space-y-2">
              {d.next_actions.map((item, i) => (
                <li key={i} className="text-sm">
                  <p className="text-ink">
                    {item.project_id && <span className="mr-1.5 text-xs text-primary">{item.project_id}</span>}
                    {item.action}
                  </p>
                  <p className="mt-0.5 line-clamp-2 text-xs text-ink3">{item.reason}</p>
                </li>
              ))}
            </ul>
          )}
        </SemanticListCard>
      </div>

      {/* 项目健康度 + 验证进度 */}
      <Card title="项目健康度与验证进度" subtitle="来源: /projects + /advanced/health?project=（点击行进入项目详情）">
        <QueryBoundary
          query={projects}
          isEmpty={(d) => Object.keys(d).length === 0}
          emptyText="没有配置项目"
          emptyDetail="config/projects.yaml 中没有项目"
        >
          {(data) => (
            <div className="divide-y divide-line/50">
              {Object.values(data)
                .sort((a, b) => a.project_id.localeCompare(b.project_id))
                .map((state: ProjectState) => {
                  const view = buildProjectView(state);
                  const health = healthById[state.project_id];
                  return (
                    <Link
                      key={view.id}
                      to={`/projects/${view.id}`}
                      className="grid grid-cols-2 items-center gap-x-4 gap-y-1.5 py-2.5 hover:bg-cardhover/40 md:grid-cols-[1fr_auto_auto_2fr_auto] md:px-2"
                    >
                      <div className="min-w-0">
                        <div className="truncate text-sm text-ink">{view.name}</div>
                        <div className="truncate text-xs text-ink3">{view.goal ?? "（无当前目标）"}</div>
                      </div>
                      <StatusBadge status={view.status} className="justify-self-start" />
                      <div className="text-right md:text-left">
                        {health ? (
                          <span
                            className={`text-lg font-semibold tabular-nums ${
                              health.score >= 70 ? "text-passed" : health.score >= 40 ? "text-warning" : "text-critical"
                            }`}
                          >
                            {health.score}
                            <span className="text-xs font-normal text-ink3">/100</span>
                          </span>
                        ) : (
                          <span className="text-xs text-ink3">评分加载中…</span>
                        )}
                      </div>
                      <div>
                        <div className="mb-1 flex justify-between text-[10px] text-ink3">
                          <span>
                            验证 {view.passedStages}/{view.totalStages}
                          </span>
                          <span>blocker ×{view.blockerCount}</span>
                        </div>
                        <ProgressBar ratio={view.progress} tone={view.progress >= 1 ? "good" : "primary"} />
                      </div>
                      <div className="col-span-2 flex items-center gap-2 md:col-span-1">
                        <DirtyBadge dirty={view.dirty} />
                        <span className="text-[10px] text-ink3" title={view.lastActivity ?? undefined}>
                          {view.lastActivity ? fmtRelative(view.lastActivity) : "无活动"}
                        </span>
                      </div>
                    </Link>
                  );
                })}
            </div>
          )}
        </QueryBoundary>
      </Card>

      {/* 图表区 */}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="本周每日事件趋势" subtitle="来源: /stats?period=week → trend">
          <QueryBoundary query={stats} isEmpty={(d) => d.trend.length === 0} emptyText="本周无事件趋势数据">
            {(d) => {
              const rows = buildTrendRows(d);
              return (
                <Chart
                  height={220}
                  option={{
                    ...baseChartOption(),
                    xAxis: { type: "category", data: rows.map((r) => r.date.slice(5)), ...axisBase() },
                    yAxis: { type: "value", minInterval: 1, ...axisBase() },
                    series: [lineSeries("事件数", rows.map((r) => r.events), C.primary)],
                  }}
                />
              );
            }}
          </QueryBoundary>
        </Card>

        <Card title="测试通过/失败趋势" subtitle="来源: /stats?period=week → trend">
          <QueryBoundary query={stats} isEmpty={(d) => d.trend.length === 0} emptyText="本周无测试数据">
            {(d) => {
              const rows = buildTrendRows(d);
              return (
                <Chart
                  height={220}
                  option={{
                    ...baseChartOption(),
                    legend: { ...legendBase() },
                    xAxis: { type: "category", data: rows.map((r) => r.date.slice(5)), ...axisBase() },
                    yAxis: { type: "value", minInterval: 1, ...axisBase() },
                    series: [
                      lineSeries("通过", rows.map((r) => r.testsPassed), C.passed),
                      lineSeries("失败", rows.map((r) => r.testsFailed), C.critical),
                    ],
                  }}
                />
              );
            }}
          </QueryBoundary>
        </Card>

        <Card title="项目活动" subtitle="来源: /stats?period=week → projects（本周事件数）">
          <QueryBoundary query={stats} isEmpty={(d) => Object.keys(d.projects).length === 0} emptyText="本周无项目活动">
            {(d) => {
              const rows = buildProjectActivity(d);
              return (
                <Chart
                  height={220}
                  option={{
                    ...baseChartOption(),
                    xAxis: { type: "category", data: rows.map((r) => r.projectId), ...axisBase() },
                    yAxis: { type: "value", minInterval: 1, ...axisBase() },
                    series: [barSeries("事件数", rows.map((r) => r.events), C.cat[0])],
                  }}
                />
              );
            }}
          </QueryBoundary>
        </Card>

        <Card title="验证漏斗" subtitle="来源: /projects → verification（跨项目聚合，值为 passed 项目数）">
          <QueryBoundary query={projects} isEmpty={(d) => Object.keys(d).length === 0} emptyText="无验证阶段数据">
            {(d) => {
              const funnel = buildFunnel(Object.values(d));
              if (funnel.length === 0) return <EmptyState text="无验证阶段数据" />;
              const maxTotal = Math.max(...funnel.map((f) => f.total));
              return (
                <Chart
                  height={Math.max(220, funnel.length * 44)}
                  option={{
                    tooltip: {
                      ...tooltipBase,
                      formatter: (p: unknown) => {
                        const item = funnel[(p as { dataIndex: number }).dataIndex];
                        return `${item.stage}<br/>passed ${item.passed} / stale ${item.stale} / pending ${item.pending}（共 ${item.total} 个项目）`;
                      },
                    },
                    series: [
                      {
                        type: "funnel",
                        left: "8%",
                        width: "84%",
                        top: 8,
                        bottom: 8,
                        min: 0,
                        max: maxTotal,
                        sort: "none",
                        gap: 2,
                        label: { show: true, position: "inside", formatter: "{b}: {c}", color: "#0a1420", fontSize: 11, fontWeight: 600 },
                        itemStyle: { borderColor: "#0a1420", borderWidth: 2 },
                        data: funnel.map((f, i) => ({
                          name: f.stage,
                          value: f.passed,
                          itemStyle: { color: `rgba(34, 211, 238, ${0.92 - (i / Math.max(1, funnel.length - 1)) * 0.5})` },
                        })),
                      },
                    ],
                  }}
                />
              );
            }}
          </QueryBoundary>
        </Card>

        <Card title="项目风险分布" subtitle="来源: /advanced/map → risk（四维风险等级计数）">
          <QueryBoundary query={map} isEmpty={(d) => d.length === 0} emptyText="无风险数据">
            {(d) => {
              const rows = buildRiskDistribution(d);
              return (
                <Chart
                  height={220}
                  option={{
                    ...baseChartOption(),
                    legend: { ...legendBase() },
                    xAxis: { type: "category", data: rows.map((r) => r.projectId), ...axisBase() },
                    yAxis: { type: "value", minInterval: 1, max: 4, ...axisBase() },
                    series: [
                      barSeries("high", rows.map((r) => r.high), C.critical, { stack: "risk" }),
                      barSeries("medium", rows.map((r) => r.medium), C.warning, { stack: "risk" }),
                      barSeries("low", rows.map((r) => r.low), C.passed, { stack: "risk" }),
                      barSeries("unknown", rows.map((r) => r.unknown), C.neutral, { stack: "risk" }),
                    ],
                  }}
                />
              );
            }}
          </QueryBoundary>
        </Card>

        <Card title="GPU 状态摘要" subtitle="来源: /insights/gpu（采样快照，非连续监控）">
          <QueryBoundary
            query={gpu}
            isEmpty={(d) => d.gpus.length === 0}
            emptyText="暂无 GPU 采样数据"
            emptyDetail="需要 rd resources --watch 记录的 resource_snapshot 事件"
          >
            {(d) => {
              const summary = buildGpuSummary(d)!;
              return (
                <div>
                  <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                    <StatCard label="GPU 数量" value={summary.gpuCount} source={`samples: ${summary.samples}`} />
                    <StatCard label="平均利用率" value={fmtPct100(summary.avgUtilization)} source="来源: /insights/gpu" />
                    <StatCard label="峰值显存" value={fmtMb(summary.peakMemoryMb)} source="来源: /insights/gpu" />
                    <StatCard
                      label="空闲占用采样"
                      value={summary.idleAllocated}
                      tone={summary.idleAllocated > 0 ? "warn" : "default"}
                      hint="util=0 且显存>1GB"
                      source="来源: /insights/gpu"
                    />
                  </div>
                  <p className="mt-3 text-[10px] text-ink3">{d.note}</p>
                </div>
              );
            }}
          </QueryBoundary>
        </Card>
      </div>

      {/* 最近事件 + 当前异常 */}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="最近事件" subtitle="来源: /stats?period=week → events（最新 15 条）" pad={false}>
          <QueryBoundary query={stats} isEmpty={(d) => d.events.length === 0} emptyText="本周无事件">
            {(d) => (
              <ul className="max-h-[380px] divide-y divide-line/50 overflow-y-auto">
                {[...d.events]
                  .sort((a, b) => b.occurred_at.localeCompare(a.occurred_at))
                  .slice(0, 15)
                  .map((e) => (
                    <li key={e.event_id} className="flex items-center gap-3 px-4 py-2 text-sm">
                      <span className="w-20 shrink-0 font-mono text-[10px] text-ink3">{fmtDateTime(e.occurred_at)}</span>
                      <code className="shrink-0 text-xs text-primary">{e.type}</code>
                      <span className="min-w-0 flex-1 truncate text-xs text-ink2">{e.project_id ?? "unassigned"}</span>
                      <StatusBadge status={e.status} />
                    </li>
                  ))}
              </ul>
            )}
          </QueryBoundary>
        </Card>

        <Card title="当前异常" subtitle="来源: /anomalies（含建议动作）">
          <QueryBoundary query={anomalies} isEmpty={(d) => d.length === 0} emptyText="当前无异常 🎉">
            {(d) => (
              <ul className="max-h-[380px] space-y-2.5 overflow-y-auto">
                {d.slice(0, 8).map((a, i) => (
                  <li key={i} className="rounded-md border border-line/60 px-3 py-2">
                    <div className="flex items-center gap-2">
                      <StatusBadge status={a.level} />
                      <code className="text-[10px] text-ink3">{a.code}</code>
                      {a.project_id && <span className="text-xs text-primary">{a.project_id}</span>}
                    </div>
                    <p className="mt-1 text-sm text-ink2">{a.message}</p>
                    <p className="mt-1 text-xs text-ink3">建议：{anomalyAdvice(a.code)}</p>
                    <EvidenceRef ids={a.evidence} max={2} />
                  </li>
                ))}
              </ul>
            )}
          </QueryBoundary>
        </Card>
      </div>

    </div>
  );
}
