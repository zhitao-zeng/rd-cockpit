import { useQueries, useQuery } from "@tanstack/react-query";
import {
  getAnomalies,
  getDebt,
  getFreshness,
  getProjects,
  getRisk,
} from "../lib/api";
import { anomalyAdvice } from "../lib/adapters";
import { Card, DataTable, EmptyState, PageHeader, QueryBoundary, StatCard } from "../components/ui";
import { ConfidenceTag, EvidenceRef, StatusBadge } from "../components/badges";
import { Chart } from "../components/Chart";
import { baseChartOption, C } from "../lib/chartTheme";
import type { RiskRadar } from "../lib/types";

export function Anomalies() {
  const anomalies = useQuery({ queryKey: ["anomalies"], queryFn: () => getAnomalies() });
  const freshness = useQuery({ queryKey: ["freshness"], queryFn: () => getFreshness() });
  const debt = useQuery({ queryKey: ["debt"], queryFn: () => getDebt() });
  const projects = useQuery({ queryKey: ["projects"], queryFn: getProjects });
  const projectIds = Object.keys(projects.data ?? {}).sort();
  const risks = useQueries({
    queries: projectIds.map((id) => ({ queryKey: ["risk", id], queryFn: () => getRisk(id) })),
  });
  const riskById: Record<string, RiskRadar> = {};
  projectIds.forEach((id, i) => {
    const data = risks[i]?.data;
    if (data) riskById[id] = data;
  });

  return (
    <div className="space-y-4">
      <PageHeader
        title="异常 / 风险"
        description="来源: /anomalies + /advanced/{debt,risk} + /insights/freshness"
      />

      {/* 统计行 */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
        <StatCard
          label="当前异常"
          value={anomalies.data?.length ?? "…"}
          tone={(anomalies.data?.length ?? 0) > 0 ? "warn" : "good"}
          source="来源: /anomalies"
        />
        <StatCard
          label="研发债务（高危）"
          value={debt.data ? `${debt.data.total}（${debt.data.high_risk}）` : "…"}
          tone={(debt.data?.high_risk ?? 0) > 0 ? "bad" : "default"}
          source="来源: /advanced/debt"
        />
        <StatCard label="过期决策" value={freshness.data?.length ?? "…"} source="来源: /insights/freshness" />
      </div>

      {/* 风险雷达（每项目） */}
      <Card title="项目风险雷达" subtitle="来源: /advanced/risk?project=（0=未知 1=低 2=中 3=高）">
        {projectIds.length === 0 ? (
          <EmptyState text="项目加载中或无项目" />
        ) : (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {projectIds.map((pid) => {
              const risk = riskById[pid];
              if (!risk) return <EmptyState key={pid} text={`${pid} 风险加载中…`} />;
              const dims = Object.keys(risk.risks);
              const score = (level: string) => ({ high: 3, medium: 2, low: 1 } as Record<string, number>)[level] ?? 0;
              return (
                <div key={pid} className="rounded-md border border-line/60 p-2">
                  <div className="mb-1 flex items-center justify-between px-1">
                    <span className="text-sm text-primary">{pid}</span>
                    <ConfidenceTag value={risk.confidence} />
                  </div>
                  <Chart
                    height={200}
                    option={{
                      ...baseChartOption(),
                      radar: {
                        indicator: dims.map((k) => ({ name: k, max: 3 })),
                        radius: "62%",
                        axisName: { color: C.ink2, fontSize: 10 },
                        splitLine: { lineStyle: { color: C.line } },
                        splitArea: { show: false },
                        axisLine: { lineStyle: { color: C.line } },
                      },
                      series: [
                        {
                          type: "radar",
                          data: [
                            {
                              name: pid,
                              value: dims.map((k) => score(risk.risks[k])),
                              lineStyle: { color: C.warning, width: 2 },
                              itemStyle: { color: C.warning },
                              areaStyle: { color: "rgba(250, 204, 21, 0.10)" },
                            },
                          ],
                        },
                      ],
                    }}
                  />
                  <div className="flex flex-wrap gap-1.5 px-1 pb-1">
                    {Object.entries(risk.risks).map(([k, level]) => (
                      <span key={k} className="text-[10px] text-ink3">
                        {k} <StatusBadge status={level} />
                      </span>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </Card>

      {/* 异常列表 */}
      <Card title="异常列表" subtitle="来源: /anomalies（每条含等级/原因/项目/证据/建议动作）">
        <QueryBoundary query={anomalies} isEmpty={(d) => d.length === 0} emptyText="当前无异常 🎉">
          {(d) => (
            <ul className="space-y-2.5">
              {d.map((a, i) => (
                <li key={i} className="rounded-md border border-line/60 px-3 py-2.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <StatusBadge status={a.level} />
                    <code className="text-[10px] text-ink3">{a.code}</code>
                    {a.project_id && <span className="text-xs text-primary">{a.project_id}</span>}
                    <span className="ml-auto"><EvidenceRef ids={a.evidence} max={3} /></span>
                  </div>
                  <p className="mt-1 text-sm text-ink">{a.message}</p>
                  <p className="mt-1 text-xs text-ink3">建议动作：{anomalyAdvice(a.code)}</p>
                </li>
              ))}
            </ul>
          )}
        </QueryBoundary>
      </Card>

      <Card title="过期决策" subtitle="来源: /insights/freshness（决策后代码/数据已变化）">
          <QueryBoundary query={freshness} isEmpty={(d) => d.length === 0} emptyText="无过期决策">
            {(d) => (
              <ul className="space-y-2">
                {d.map((f) => (
                  <li key={f.event_id} className="rounded-md border border-line px-3 py-2">
                    <div className="flex items-center gap-2">
                      <StatusBadge status="stale" />
                      <span className="text-xs text-primary">{f.project_id}</span>
                    </div>
                    <p className="mt-1 text-sm text-ink2">{f.text ?? f.event_id}</p>
                    <p className="mt-0.5 text-xs text-warning">{f.reasons.join("；")}</p>
                    <EvidenceRef ids={f.evidence} max={2} />
                  </li>
                ))}
              </ul>
            )}
          </QueryBoundary>
      </Card>

      {/* 研发债务 */}
      <Card title="研发债务" subtitle="来源: /advanced/debt（未验证阶段 / 无证据实验 / 无证据决策）" pad={false}>
        <QueryBoundary query={debt} isEmpty={(d) => d.items.length === 0} emptyText="无研发债务 🎉">
          {(d) => (
            <div>
              <div className="flex flex-wrap gap-3 px-4 pt-3 text-sm">
                {Object.entries(d.by_category).map(([k, v]) => (
                  <span key={k} className="text-ink2">
                    <code className="mr-1 text-xs text-ink3">{k}</code>×{v}
                  </span>
                ))}
              </div>
              <DataTable
                columns={[
                  { key: "project", label: "项目" },
                  { key: "category", label: "类别" },
                  { key: "severity", label: "严重度" },
                  { key: "text", label: "内容" },
                  { key: "evidence", label: "证据" },
                ]}
                rows={d.items.map((item) => ({
                  project: <span className="text-xs text-primary">{item.project_id}</span>,
                  category: <code className="text-[10px] text-ink2">{item.category}</code>,
                  severity: <StatusBadge status={item.severity === "high" ? "high" : "medium"} />,
                  text: <span className="line-clamp-1 max-w-[280px] text-xs text-ink2">{item.text ?? "—"}</span>,
                  evidence: <EvidenceRef ids={item.evidence} max={1} />,
                }))}
                keyFn={(_, i) => i}
              />
            </div>
          )}
        </QueryBoundary>
      </Card>

    </div>
  );
}
