import { useQuery } from "@tanstack/react-query";
import { getProjects, getResearchMap } from "../lib/api";
import {
  averageRiskScore,
  buildProjectView,
  MAP_PHASE_ORDER,
  phaseFromProgress,
  type ProjectStatus,
} from "../lib/adapters";
import { fmtPct } from "../lib/format";
import { Card, PageHeader, QueryBoundary } from "../components/ui";
import { StatusBadge } from "../components/badges";
import { Chart } from "../components/Chart";
import { baseChartOption, C, tooltipBase } from "../lib/chartTheme";
import type { ECElementEvent } from "echarts";
import { useNavigate } from "react-router-dom";

const STATUS_COLORS: Record<ProjectStatus, string> = {
  active: C.primary,
  blocked: C.critical,
  stale: C.warning,
  done: C.passed,
  dormant: C.ink3,
  historical: C.ink3,
};

export function ResearchMap() {
  const map = useQuery({ queryKey: ["map"], queryFn: getResearchMap });
  const projects = useQuery({ queryKey: ["projects"], queryFn: getProjects });
  const navigate = useNavigate();

  return (
    <div className="space-y-4">
      <PageHeader
        title="研究地图"
        description="横轴: 探索→实现→验证→交付（按验证进度推导）· 纵轴: 风险均分 · 气泡: 事件数 · 颜色: 状态 · 来源: /advanced/map + /projects"
      />
      <Card title="项目二维地图" subtitle="点击气泡进入项目详情">
        <QueryBoundary query={map} isEmpty={(d) => d.length === 0} emptyText="无项目数据">
          {(d) => {
            const statusById: Record<string, ProjectStatus> = {};
            for (const state of Object.values(projects.data ?? {})) {
              statusById[state.project_id] = buildProjectView(state).status;
            }
            const points = d.map((p) => {
              const status: ProjectStatus =
                statusById[p.project_id] ?? (p.status === "blocked" ? "blocked" : p.status === "done" ? "done" : "active");
              const phase = phaseFromProgress(p.progress);
              return {
                projectId: p.project_id,
                x: MAP_PHASE_ORDER.indexOf(phase),
                y: Math.round(averageRiskScore(p.risk) * 100) / 100,
                size: p.bubble,
                status,
                progress: p.progress,
                risk: p.risk,
              };
            });
            // 同位置点确定性错位（仅展示层偏移，tooltip 仍显示真实值）：
            // 按 (阶段, 四舍五入风险) 分组，组内按 project_id 排序后扇形展开
            const groups = new Map<string, typeof points>();
            for (const p of points) {
              const key = `${p.x}:${p.y.toFixed(2)}`;
              groups.set(key, [...(groups.get(key) ?? []), p]);
            }
            const offsetOf = new Map<string, { dx: number; dy: number }>();
            for (const group of groups.values()) {
              const sorted = [...group].sort((a, b) => a.projectId.localeCompare(b.projectId));
              sorted.forEach((p, i) => {
                const center = (sorted.length - 1) / 2;
                offsetOf.set(p.projectId, { dx: (i - center) * 0.34, dy: (i - center) * 0.22 });
              });
            }
            const placed = points.map((p) => ({
              ...p,
              px: p.x + (offsetOf.get(p.projectId)?.dx ?? 0),
              py: Math.max(0, Math.min(3, p.y + (offsetOf.get(p.projectId)?.dy ?? 0))),
            }));
            const maxBubble = Math.max(1, ...points.map((p) => p.size));
            return (
              <div>
                <Chart
                  height={440}
                  onClick={(params: ECElementEvent) => {
                    const point = placed[params.dataIndex];
                    if (point) navigate(`/projects/${encodeURIComponent(point.projectId)}`);
                  }}
                  option={{
                    ...baseChartOption(),
                    tooltip: {
                      ...tooltipBase,
                      formatter: (p: unknown) => {
                        const point = placed[(p as { dataIndex: number }).dataIndex];
                        const risks = Object.entries(point.risk)
                          .map(([k, v]) => `${k}=${v}`)
                          .join(" · ");
                        return [
                          `<b>${point.projectId}</b>`,
                          `阶段: ${MAP_PHASE_ORDER[point.x]}（进度 ${fmtPct(point.progress)}）`,
                          `风险均分: ${point.y.toFixed(2)} / 3`,
                          `事件数: ${point.size}`,
                          `状态: ${point.status}`,
                          risks,
                        ].join("<br/>");
                      },
                    },
                    xAxis: {
                      type: "value",
                      min: -1,
                      max: 4,
                      interval: 1,
                      axisLine: { lineStyle: { color: C.line } },
                      axisTick: { show: false },
                      axisLabel: {
                        color: C.ink,
                        fontSize: 12,
                        formatter: (v: number) => (Number.isInteger(v) && v >= 0 && v <= 3 ? MAP_PHASE_ORDER[v] : ""),
                      },
                      splitLine: { show: true, lineStyle: { color: C.line, opacity: 0.4, type: "dashed" } },
                    },
                    yAxis: {
                      type: "value",
                      min: 0,
                      max: 3,
                      interval: 1,
                      name: "风险",
                      nameTextStyle: { color: C.ink3 },
                      axisLine: { show: false },
                      axisTick: { show: false },
                      axisLabel: {
                        color: C.ink2,
                        fontSize: 11,
                        formatter: (v: number) => ["低", "中低", "中高", "高"][v] ?? v,
                      },
                      splitLine: { lineStyle: { color: C.line, opacity: 0.5, type: "dashed" } },
                    },
                    series: [
                      {
                        type: "scatter",
                        data: placed.map((p) => ({
                          value: [p.px, p.py],
                          itemStyle: {
                            color: STATUS_COLORS[p.status],
                            opacity: 0.85,
                            borderColor: "#0a1420",
                            borderWidth: 2,
                          },
                          symbolSize: 22 + (p.size / maxBubble) * 46,
                          label: {
                            show: true,
                            position: "top",
                            formatter: p.projectId,
                            color: C.ink,
                            fontSize: 11,
                            distance: 6,
                          },
                        })),
                      },
                    ],
                  }}
                />
                <div className="mt-2 flex flex-wrap gap-4 text-[11px] text-ink2">
                  {(Object.keys(STATUS_COLORS) as ProjectStatus[]).map((s) => (
                    <span key={s} className="inline-flex items-center gap-1.5">
                      <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: STATUS_COLORS[s] }} />
                      {s} <StatusBadge status={s} />
                    </span>
                  ))}
                  <span className="text-ink3">气泡大小 = 账本事件数</span>
                </div>
              </div>
            );
          }}
        </QueryBoundary>
      </Card>
    </div>
  );
}
