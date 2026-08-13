import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { Chart } from "../components/Chart";
import { ProjectSelect } from "../components/controls";
import { Card, EmptyState, PageHeader, QueryBoundary, StatCard } from "../components/ui";
import { getExperimentIntelligence } from "../lib/api";
import { axisBase, baseChartOption, C, lineSeries, tooltipBase } from "../lib/chartTheme";
import { fmtTokens } from "../lib/format";
import type { ExperimentRecord } from "../lib/types";

const KIND: Record<string, string> = {
  experiment: "实验", benchmark: "基准评测", evaluation: "效果评测", ablation: "消融",
  training: "训练", deployment_validation: "部署验证",
};

const STATUS: Record<string, { label: string; tone: string }> = {
  improved: { label: "提升", tone: "border-passed/30 bg-passed/5 text-passed" },
  validated: { label: "已验证", tone: "border-passed/30 bg-passed/5 text-passed" },
  regressed: { label: "回退", tone: "border-critical/30 bg-critical/5 text-critical" },
  failed: { label: "失败", tone: "border-critical/30 bg-critical/5 text-critical" },
  mixed: { label: "有得有失", tone: "border-warning/30 bg-warning/5 text-warning" },
  inconclusive: { label: "证据不足", tone: "border-warning/30 bg-warning/5 text-warning" },
  observed: { label: "已记录", tone: "border-line bg-page/50 text-ink2" },
};

function Evidence({ refs }: { refs: string[] }) {
  if (!refs.length) return null;
  return <div className="mt-3 flex flex-wrap gap-1.5">{refs.map((ref) => {
    const match = /^report:(\d{4}-\d{2}-\d{2}):L(\d+)-L(\d+)$/.exec(ref);
    const label = match ? `日报 ${match[1]} · L${match[2]}–${match[3]}` : ref;
    return <span key={ref} title={ref} className="rounded border border-line bg-page/40 px-1.5 py-0.5 font-mono text-[9px] text-ink3">{label}</span>;
  })}</div>;
}

function Tags({ record }: { record: ExperimentRecord }) {
  const values = [
    ...record.models.map((item) => ({ label: item.name, kind: item.role || "模型" })),
    ...record.datasets.map((item) => ({ label: item.name, kind: item.scope || "数据" })),
    ...record.parameters.map((item) => ({ label: `${item.name}=${item.value}`, kind: "参数" })),
  ];
  if (!values.length) return null;
  return <div className="mt-3 flex flex-wrap gap-1.5">{values.map((item, index) =>
    <span key={`${item.kind}-${item.label}-${index}`} title={item.kind} className="rounded-md border border-primary/20 bg-primary/5 px-2 py-1 text-[10px] text-primary">{item.label}</span>
  )}</div>;
}

function ExperimentCard({ record, projectName }: { record: ExperimentRecord; projectName: string }) {
  const status = STATUS[record.result_status] ?? STATUS.observed;
  return <article className="rounded-xl border border-line bg-card px-4 py-4" data-testid="experiment-record">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2 text-[10px] text-ink3">
          <span>{record.date}</span><span>·</span><span className="text-primary">{projectName}</span>
          <span>·</span><span>{KIND[record.kind] ?? record.kind}</span>
          {record.verification_scope !== "unknown" && <><span>·</span><span>{record.verification_scope}</span></>}
        </div>
        <h3 className="mt-1.5 text-sm font-semibold text-ink">{record.title}</h3>
      </div>
      <span className={`shrink-0 rounded-full border px-2 py-1 text-[10px] ${status.tone}`}>{status.label}</span>
    </div>

    {record.question && <div className="mt-3 rounded-lg border border-sky-400/20 bg-sky-400/5 px-3 py-2">
      <div className="text-[9px] uppercase tracking-wider text-sky-300">研究问题 / 假设</div>
      <p className="mt-1 text-xs leading-5 text-ink2">{record.question}</p>
    </div>}

    <div className="mt-3 grid gap-3 lg:grid-cols-3">
      <div><div className="text-[9px] uppercase tracking-wider text-ink3">怎么做</div><p className="mt-1 text-xs leading-5 text-ink2">{record.method}</p></div>
      <div><div className="text-[9px] uppercase tracking-wider text-ink3">结果</div><p className="mt-1 text-xs leading-5 text-ink">{record.result_summary}</p></div>
      <div><div className="text-[9px] uppercase tracking-wider text-ink3">结论 / 影响</div><p className="mt-1 text-xs leading-5 text-ink2">{record.conclusion || record.decision_impact || "日报只记录了结果，尚未形成明确结论。"}</p></div>
    </div>

    {record.metrics.length > 0 && <div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">{record.metrics.map((metric, index) =>
      <div key={`${metric.name}-${index}`} className="rounded-lg border border-passed/20 bg-passed/5 px-3 py-2">
        <div className="text-[9px] text-ink3">{metric.name}</div>
        <div className="mt-0.5 text-lg font-semibold text-passed">{metric.value}{metric.unit && !metric.value.includes(metric.unit) ? ` ${metric.unit}` : ""}</div>
        <p className="mt-1 line-clamp-2 text-[9px] leading-4 text-ink3">{metric.scope || "口径未注明"}</p>
      </div>
    )}</div>}

    <Tags record={record} />
    <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-line/60 pt-3 text-[10px] text-ink3">
      <span title={record.token_context.note}>项目当日 Token 增量 · {fmtTokens(record.token_context.total_tokens)}</span>
      <span>{record.token_context.sessions} 个 Agent Session</span>
      {record.token_context.quality === "estimated" && <span className="text-warning">跨日/跨项目 Session，近似归属</span>}
      {record.token_context.shared_by_records > 1 && <span>由当日 {record.token_context.shared_by_records} 条实验共享，非单实验成本</span>}
      {record.commit_sha && <span className="font-mono">commit {record.commit_sha.slice(0, 10)}</span>}
      {record.machine && <span>{record.machine}</span>}
    </div>
    <Evidence refs={record.evidence} />
  </article>;
}

export function Experiments() {
  const [params, setParams] = useSearchParams();
  const project = params.get("project") ?? "";
  const setProject = (value: string) => setParams((previous) => {
    const next = new URLSearchParams(previous);
    if (value) next.set("project", value); else next.delete("project");
    return next;
  });
  const query = useQuery({
    queryKey: ["experiment-intelligence", project],
    queryFn: () => getExperimentIntelligence(90, project || undefined),
  });
  const names = useMemo(() => Object.fromEntries((query.data?.projects ?? []).map((item) => [item.project_id, item.name])), [query.data]);
  const trend = (query.data?.metric_series ?? []).find((item) => item.points.length >= 2) ?? query.data?.metric_series[0];

  return <div className="space-y-4">
    <PageHeader title="实验记录" description="从 Daily Report 提炼：做了什么、怎么验证、结果、结论与证据。Agent / Token 只作补充。"
      right={<ProjectSelect value={project} onChange={setProject} />} />
    <QueryBoundary query={query} isEmpty={(data) => data.counts.analyzed_days === 0}
      emptyText="尚未生成实验情报缓存" emptyDetail="运行 rd experiment-backfill 后，这里才会出现日报实验记录。">
      {(data) => <>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
          <StatCard label="可读实验记录" value={data.counts.records} tone="primary" source="日报证据审计" />
          <StatCard label="覆盖项目" value={data.counts.projects} source={`${data.counts.analyzed_days} 个已分析日报日`} />
          <StatCard label="明确指标" value={data.counts.metrics} tone="good" source="数字已通过引用校验" />
          <StatCard label="形成结论" value={data.counts.conclusions} source="结论非空的记录" />
          <StatCard label="校验告警" value={data.counts.validation_errors} tone={data.counts.validation_errors ? "warn" : "default"} source="已拒绝的内容或引用" />
        </div>

        <div className="grid gap-4 xl:grid-cols-[1fr_1.4fr]">
          <Card title="项目实验覆盖" subtitle="记录数来自语义实验，不是原始 experiment_* 事件">
            <div className="space-y-2">{data.projects.map((item) => <button key={item.project_id} onClick={() => setProject(item.project_id)}
              className="grid w-full grid-cols-[1fr_auto_auto] items-center gap-3 rounded-lg border border-line bg-page/20 px-3 py-2 text-left hover:border-primary/40">
              <div><div className="text-xs font-medium text-ink">{item.name}</div><div className="mt-0.5 text-[9px] text-ink3">最近 {item.latest_date} · {item.metric_count} 个指标</div></div>
              <div className="text-right"><div className="text-lg font-semibold text-primary">{item.record_count}</div><div className="text-[9px] text-ink3">条记录</div></div>
              <div className="w-20 text-right"><div className="text-xs text-ink2">{fmtTokens(item.token_pool_total)}</div><div className="text-[9px] text-ink3">Token 日增量</div></div>
            </button>)}</div>
          </Card>

          <Card title="同口径指标故事" subtitle={trend ? `${names[trend.project_id] ?? trend.project_id} · ${trend.name} · ${trend.scope}` : "仅同项目、同指标、同单位、同口径连线"}>
            {!trend ? <EmptyState text="暂无可连线指标" detail="实验记录仍可阅读；没有统一口径时系统不会硬画趋势。" /> : <Chart height={280} option={{
              ...baseChartOption(), tooltip: { ...tooltipBase, trigger: "axis" },
              xAxis: { type: "category", data: trend.points.map((item) => item.date.slice(5)), ...axisBase() },
              yAxis: { type: "value", name: trend.unit, nameTextStyle: { color: C.ink3 }, ...axisBase(), scale: true },
              series: [lineSeries(trend.name, trend.points.map((item) => item.value), C.primary, { areaStyle: { color: "rgba(34,211,238,.08)" } })],
            }} />}
          </Card>
        </div>

        <Card title="为什么 Token 不是单实验成本" subtitle="跨日 Session 与并行 Agent 使精确拆账不可靠">
          <p className="text-xs leading-6 text-ink2">页面先对每个 Agent Session 的累计计数器做逐日差分，再放进“项目 + 日期”的共享池；它包含缓存输入，跨日或跨项目长 Session 仍只能近似归属。系统不会把同一天的总 Token 平均分给实验，再假装这是精确成本。</p>
        </Card>

        <div className="space-y-3">
          {data.records.length === 0 ? <EmptyState text="该范围没有合格实验记录" detail="普通代码修改、Git 提交和仅有单元测试的工作不会被包装成实验。" /> : data.records.map((record) =>
            <ExperimentCard key={record.record_id} record={record} projectName={names[record.project_id] ?? record.project_id} />
          )}
        </div>
      </>}
    </QueryBoundary>
  </div>;
}
