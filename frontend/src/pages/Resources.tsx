import { useQuery } from "@tanstack/react-query";
import { getAnomalies, getBudget, getChanged, getGpuReport, getResourceCost } from "../lib/api";
import { buildGpuSeries, buildGpuSummary } from "../lib/adapters";
import { daysAgoLocal, fmtMb, fmtPct100 } from "../lib/format";
import { Card, DataTable, EmptyState, PageHeader, QueryBoundary, StatCard } from "../components/ui";
import { ConfidenceTag, EvidenceRef, StatusBadge } from "../components/badges";
import { Chart } from "../components/Chart";
import { axisBase, barSeries, baseChartOption, C, catColor, legendBase, lineSeries } from "../lib/chartTheme";
import type { GpuSamplePoint } from "../lib/adapters";

export function Resources() {
  const gpu = useQuery({ queryKey: ["gpu"], queryFn: getGpuReport });
  // GPU 时序：/insights/changed 返回 resource_snapshot 的完整 payload（只读端点，真实账本数据）
  const changed = useQuery({ queryKey: ["changed", "gpu-7d"], queryFn: () => getChanged(daysAgoLocal(7)) });
  const cost = useQuery({ queryKey: ["resource-cost"], queryFn: () => getResourceCost() });
  const budget = useQuery({ queryKey: ["budget"], queryFn: () => getBudget() });
  const anomalies = useQuery({ queryKey: ["anomalies"], queryFn: () => getAnomalies() });

  const gpuAnomalies = (anomalies.data ?? []).filter((a) => a.code === "gpu_idle_allocated");

  return (
    <div className="space-y-4">
      <PageHeader
        title="资源"
        description="来源: /insights/gpu + /insights/changed（resource_snapshot payload）+ /insights/resource-cost + /advanced/budget"
      />

      {/* GPU 摘要 */}
      <QueryBoundary
        query={gpu}
        isEmpty={(d) => d.gpus.length === 0}
        emptyText="暂无 GPU 采样数据"
        emptyDetail="需要 rd resources --watch 持续记录 resource_snapshot 事件"
      >
        {(d) => {
          const summary = buildGpuSummary(d)!;
          return (
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
          );
        }}
      </QueryBoundary>

      {/* GPU 时序图 */}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="GPU 利用率（近 7 天采样）" subtitle="来源: /insights/changed → resource_snapshot（>4 张卡时显示平均值）">
          <QueryBoundary
            query={changed}
            isEmpty={(d) => buildGpuSeries(d).utilization.length === 0}
            emptyText="近 7 天无 GPU 采样"
            emptyDetail="resource_snapshot 事件为空"
          >
            {(d) => {
              const series = buildGpuSeries(d);
              return <GpuLineChart points={series.utilization} gpuKeys={series.gpuKeys} valueFmt="pct" />;
            }}
          </QueryBoundary>
        </Card>
        <Card title="显存使用（近 7 天采样）" subtitle="来源: /insights/changed → resource_snapshot（MB）">
          <QueryBoundary
            query={changed}
            isEmpty={(d) => buildGpuSeries(d).memory.length === 0}
            emptyText="近 7 天无显存采样"
          >
            {(d) => {
              const series = buildGpuSeries(d);
              return <GpuLineChart points={series.memory} gpuKeys={series.gpuKeys} valueFmt="mb" />;
            }}
          </QueryBoundary>
        </Card>
      </div>

      {/* GPU 对比 + 列表 */}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="GPU 对比" subtitle="来源: /insights/gpu（平均利用率 vs 峰值显存）">
          <QueryBoundary query={gpu} isEmpty={(d) => d.gpus.length === 0} emptyText="无 GPU 数据">
            {(d) => (
              <Chart
                height={220}
                option={{
                  ...baseChartOption(),
                  legend: { ...legendBase() },
                  xAxis: { type: "category", data: d.gpus.map((g) => `GPU ${g.gpu}`), ...axisBase() },
                  yAxis: { type: "value", ...axisBase() },
                  series: [
                    barSeries("平均利用率 %", d.gpus.map((g) => g.avg_utilization_pct), C.cat[0]),
                    barSeries("峰值显存 GB", d.gpus.map((g) => Math.round(g.peak_memory_mb / 102.4) / 10), C.cat[2]),
                  ],
                }}
              />
            )}
          </QueryBoundary>
          <p className="mt-1 text-[10px] text-ink3">注：峰值显存以 GB 显示（MB/1024），与利用率不同量纲仅作形状对比</p>
        </Card>

        <Card title="GPU 列表" subtitle="来源: /insights/gpu" pad={false}>
          <QueryBoundary query={gpu} isEmpty={(d) => d.gpus.length === 0} emptyText="无 GPU 数据">
            {(d) => (
              <DataTable
                columns={[
                  { key: "gpu", label: "GPU" },
                  { key: "samples", label: "采样数", align: "right" },
                  { key: "util", label: "平均利用率", align: "right" },
                  { key: "peak", label: "峰值显存", align: "right" },
                  { key: "idle", label: "空闲占用", align: "right" },
                  { key: "evidence", label: "证据" },
                ]}
                rows={d.gpus.map((g) => ({
                  gpu: <span className="text-primary">GPU {g.gpu}</span>,
                  samples: <span className="tabular-nums">{g.samples}</span>,
                  util: <span className="tabular-nums">{fmtPct100(g.avg_utilization_pct)}</span>,
                  peak: <span className="tabular-nums">{fmtMb(g.peak_memory_mb)}</span>,
                  idle: (
                    <span className={`tabular-nums ${g.idle_allocated_samples > 0 ? "text-warning" : ""}`}>
                      {g.idle_allocated_samples}
                    </span>
                  ),
                  evidence: <EvidenceRef ids={g.evidence} max={1} />,
                }))}
                keyFn={(r) => `gpu-${(r.gpu as React.ReactElement).props.children[1]}`}
              />
            )}
          </QueryBoundary>
        </Card>
      </div>

      {/* Docker 容器 + 资源异常 */}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Docker 容器" subtitle="来源: 最近一次 resource_snapshot → containers（docker ps）" pad={false}>
          <QueryBoundary
            query={changed}
            isEmpty={(d) => buildGpuSeries(d).containers.length === 0}
            emptyText="最近快照中没有运行中的容器"
            emptyDetail="resource_snapshot payload.containers 为空"
          >
            {(d) => {
              const series = buildGpuSeries(d);
              return (
                <DataTable
                  columns={[
                    { key: "name", label: "名称" },
                    { key: "image", label: "镜像" },
                    { key: "status", label: "状态" },
                  ]}
                  rows={series.containers.map((c) => ({
                    name: <span className="text-xs text-primary">{String(c.Names ?? c.Name ?? "—")}</span>,
                    image: <span className="line-clamp-1 max-w-[240px] text-xs text-ink2">{String(c.Image ?? "—")}</span>,
                    status: <span className="text-xs text-ink2">{String(c.Status ?? c.State ?? "—")}</span>,
                  }))}
                  keyFn={(_, i) => i}
                />
              );
            }}
          </QueryBoundary>
          <p className="px-4 pb-2 text-[10px] text-ink3">快照时间: {changed.data ? buildGpuSeries(changed.data).sampledAt ?? "—" : "—"}</p>
        </Card>

        <Card title="资源异常" subtitle="来源: /anomalies → code=gpu_idle_allocated">
          <QueryBoundary query={anomalies} isEmpty={() => gpuAnomalies.length === 0} emptyText="无 GPU 资源异常 🎉">
            {() => (
              <ul className="space-y-2">
                {gpuAnomalies.map((a, i) => (
                  <li key={i} className="rounded-md border border-warning/40 px-3 py-2">
                    <div className="flex items-center gap-2">
                      <StatusBadge status={a.level} />
                      <code className="text-[10px] text-ink3">{a.code}</code>
                    </div>
                    <p className="mt-1 text-sm text-ink2">{a.message}</p>
                    <EvidenceRef ids={a.evidence} max={2} />
                  </li>
                ))}
              </ul>
            )}
          </QueryBoundary>
        </Card>
      </div>

      {/* 资源成本 */}
      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="实验预算 ROI" subtitle="来源: /advanced/budget">
          <QueryBoundary query={budget}>
            {(d) => (
              <div className="space-y-2 text-sm">
                <div className="flex justify-between"><span className="text-ink3">实验数</span><span className="tabular-nums">{d.experiments}</span></div>
                <div className="flex justify-between"><span className="text-ink3">有效实验</span><span className="tabular-nums">{d.useful_experiments}</span></div>
                <div className="flex justify-between"><span className="text-ink3">GPU 观测数</span><span className="tabular-nums">{d.gpu_observations}</span></div>
                <div className="flex items-center justify-between">
                  <span className="text-ink3">GPU-hours</span>
                  {d.gpu_hours === null ? (
                    <span className="text-xs text-ink3">无精确数据（需 GPU 生命周期事件）</span>
                  ) : (
                    <span className="tabular-nums">{d.gpu_hours}</span>
                  )}
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-ink3">单位成本</span>
                  <span className="text-xs text-ink3">{d.unit_cost ?? "—"}</span>
                </div>
                <div className="pt-1"><ConfidenceTag value={d.confidence} /></div>
              </div>
            )}
          </QueryBoundary>
        </Card>

        <Card title="决策资源成本" subtitle="来源: /insights/resource-cost（成本为近似值）" pad={false} className="lg:col-span-2">
          <QueryBoundary query={cost} isEmpty={(d) => d.length === 0} emptyText="无决策资源成本数据">
            {(d) => (
              <DataTable
                columns={[
                  { key: "decision", label: "决策" },
                  { key: "project", label: "项目" },
                  { key: "samples", label: "邻近采样", align: "right" },
                  { key: "gpus", label: "观测到的 GPU" },
                  { key: "approx", label: "成本性质" },
                  { key: "evidence", label: "证据" },
                ]}
                rows={d.map((c) => ({
                  decision: <code className="font-mono text-[10px] text-ink2">{c.decision_id.slice(0, 18)}</code>,
                  project: <span className="text-xs text-primary">{c.project_id ?? "—"}</span>,
                  samples: <span className="tabular-nums">{c.resource_samples}</span>,
                  gpus: <span className="text-xs text-ink2">{c.gpu_observed.join(", ") || "—"}</span>,
                  approx: c.cost_is_approximate ? <ConfidenceTag value="approximate" /> : <ConfidenceTag value="observed" />,
                  evidence: <EvidenceRef ids={c.evidence} max={1} />,
                }))}
                keyFn={(_, i) => i}
              />
            )}
          </QueryBoundary>
        </Card>
      </div>
    </div>
  );
}

/** GPU 折线图：≤4 张卡时每卡一条线（categorical 固定序）；>4 张卡时画平均线（不循环用色） */
function GpuLineChart({
  points,
  gpuKeys,
  valueFmt,
}: {
  points: GpuSamplePoint[];
  gpuKeys: string[];
  valueFmt: "pct" | "mb";
}) {
  if (points.length === 0) return <EmptyState text="无采样数据" />;
  const labels = points.map((p) => p.at.slice(5, 16).replace("T", " "));
  const yAxis = {
    type: "value" as const,
    ...axisBase(),
    axisLabel: {
      color: C.ink2,
      fontSize: 11,
      formatter: (v: number) => (valueFmt === "pct" ? `${v}%` : v >= 1024 ? `${(v / 1024).toFixed(0)}G` : `${v}M`),
    },
  };
  if (gpuKeys.length > 4) {
    const avg = points.map((p) => {
      const values = gpuKeys.map((k) => p[k]).filter((v): v is number => typeof v === "number");
      return values.length ? Math.round((values.reduce((a, b) => a + b, 0) / values.length) * 10) / 10 : null;
    });
    return (
      <div>
        <Chart
          height={220}
          option={{
            ...baseChartOption(),
            xAxis: { type: "category", data: labels, ...axisBase() },
            yAxis,
            series: [lineSeries(`${gpuKeys.length} 张 GPU 平均`, avg, C.primary)],
          }}
        />
        <p className="mt-1 text-[10px] text-ink3">共 {gpuKeys.length} 张 GPU，显示平均值（避免色彩循环导致误读）</p>
      </div>
    );
  }
  return (
    <Chart
      height={220}
      option={{
        ...baseChartOption(),
        legend: { ...legendBase() },
        xAxis: { type: "category", data: labels, ...axisBase() },
        yAxis,
        series: gpuKeys.map((key, i) =>
          lineSeries(
            `GPU ${key}`,
            points.map((p) => (typeof p[key] === "number" ? (p[key] as number) : null)),
            catColor(i),
          ),
        ),
      }}
    />
  );
}
