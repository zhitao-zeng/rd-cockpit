import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQueries, useQuery } from "@tanstack/react-query";
import {
  getConfidence,
  getConflicts,
  getCounterfactual,
  getCountdown,
  getDecisionGraph,
  getFreshness,
  getLineage,
  getMetricLineage,
  getProjects,
  getProjectTimeline,
  getSuggest,
} from "../lib/api";
import { buildRelationGraph, numericParamSeries } from "../lib/adapters";
import { fmtDateTime } from "../lib/format";
import { Card, DataTable, EmptyState, PageHeader, QueryBoundary, StatCard } from "../components/ui";
import { ConfidenceTag, EvidenceRef, StatusBadge } from "../components/badges";
import { ProjectSelect } from "../components/controls";
import { Chart } from "../components/Chart";
import { axisBase, baseChartOption, C, legendBase, lineSeries, tooltipBase } from "../lib/chartTheme";
import type { TimelineEvent } from "../lib/types";

const NODE_COLORS: Record<string, string> = {
  decision: C.primary,
  experiment: C.cat[0],
  metric: C.cat[2],
  artifact: C.cat[3],
  dataset: C.neutral,
  model: C.neutral,
  commit_sha: C.neutral,
  tree_hash: C.neutral,
  other: C.neutral,
};

export function Decisions() {
  const [params, setParams] = useSearchParams();
  const project = params.get("project") ?? "";
  const setProject = (v: string) =>
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (v) next.set("project", v);
      else next.delete("project");
      return next;
    });

  const confidence = useQuery({ queryKey: ["confidence", project], queryFn: () => getConfidence(project || undefined) });
  const freshness = useQuery({ queryKey: ["freshness", project], queryFn: () => getFreshness(project || undefined) });
  const conflicts = useQuery({ queryKey: ["conflicts", project], queryFn: () => getConflicts(project || undefined) });
  const graph = useQuery({ queryKey: ["graph", project], queryFn: () => getDecisionGraph(project || undefined) });
  const metricLineage = useQuery({ queryKey: ["metric-lineage", project], queryFn: () => getMetricLineage(project || undefined) });
  const lineage = useQuery({ queryKey: ["lineage", project], queryFn: () => getLineage(project || undefined) });
  const suggest = useQuery({ queryKey: ["suggest", project], queryFn: () => getSuggest(project || undefined) });
  const countdown = useQuery({ queryKey: ["countdown", project], queryFn: () => getCountdown(project || undefined) });

  // 决策时间线：decision_* 事件（带日期）来自项目 timeline 合并
  const projects = useQuery({ queryKey: ["projects"], queryFn: getProjects });
  const projectIds = Object.keys(projects.data ?? {}).sort();
  const timelineTargets = project ? [project] : projectIds;
  const timelines = useQueries({
    queries: timelineTargets.map((id) => ({ queryKey: ["project-timeline", id], queryFn: () => getProjectTimeline(id) })),
  });
  const timelinesLoaded = timelines.filter((t) => t.data).length;
  const decisionEvents = useMemo(() => {
    const all: TimelineEvent[] = [];
    for (const t of timelines) {
      for (const e of t.data ?? []) {
        if (e.type.startsWith("decision_")) all.push(e);
      }
    }
    return all.sort((a, b) => a.occurred_at.localeCompare(b.occurred_at));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [timelinesLoaded, project]);

  const staleIds = new Set((freshness.data ?? []).map((f) => f.event_id));
  const conflictDecisionIds = new Set(
    (conflicts.data ?? []).flatMap((c) => c.decisions.map((d) => d.event_id)),
  );
  const avgScore =
    confidence.data && confidence.data.length > 0
      ? Math.round(confidence.data.reduce((s, c) => s + c.score, 0) / confidence.data.length)
      : null;

  return (
    <div className="space-y-4">
      <PageHeader
        title="决策"
        description="来源: /advanced/confidence + /insights/{graph,freshness,conflicts,lineage,suggest,counterfactual} + /advanced/{metric-lineage,countdown}"
        right={<ProjectSelect value={project} onChange={setProject} />}
      />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatCard label="决策/结论总数" value={confidence.data?.length ?? "…"} source="来源: /advanced/confidence" />
        <StatCard
          label="平均可信度"
          value={avgScore ?? "—"}
          tone={avgScore !== null && avgScore >= 70 ? "good" : avgScore !== null && avgScore < 40 ? "warn" : "default"}
          source="confidence.score 均值"
        />
        <StatCard
          label="过期候选"
          value={freshness.data?.length ?? "…"}
          tone={(freshness.data?.length ?? 0) > 0 ? "warn" : "default"}
          source="来源: /insights/freshness"
        />
        <StatCard
          label="冲突"
          value={conflicts.data?.length ?? "…"}
          tone={(conflicts.data?.length ?? 0) > 0 ? "bad" : "default"}
          source="来源: /insights/conflicts"
        />
      </div>

      {/* 关系图 */}
      <Card
        title="Decision → Experiment → Metric → Commit 关系图"
        subtitle="来源: /insights/graph + /advanced/metric-lineage（合并去重，可拖拽缩放）"
      >
        <QueryBoundary
          query={graph}
          isEmpty={(d) => d.nodes.length === 0 && (metricLineage.data?.nodes.length ?? 0) === 0}
          emptyText="无决策关系数据"
          emptyDetail="账本中没有 decision_*/experiment/metric 事件"
        >
          {(d) => {
            const relation = buildRelationGraph(d, metricLineage.data ?? null);
            if (relation.nodes.length === 0) return <EmptyState text="无决策关系数据" />;
            return (
              <Chart
                height={420}
                option={{
                  tooltip: {
                    ...tooltipBase,
                    formatter: (p: unknown) => {
                      const pp = p as { dataType: string; data: { id?: string; name?: string; relation?: string } };
                      if (pp.dataType === "edge") return `${pp.data.relation ?? ""}`;
                      return `${pp.data.id ?? pp.data.name ?? ""}`;
                    },
                  },
                  legend: { ...legendBase(), data: relation.categories },
                  series: [
                    {
                      type: "graph",
                      layout: "force",
                      roam: true,
                      categories: relation.categories.map((c) => ({
                        name: c,
                        itemStyle: { color: NODE_COLORS[c] ?? C.neutral },
                      })),
                      data: relation.nodes.map((n) => ({
                        id: n.id,
                        name: n.name.length > 28 ? `${n.name.slice(0, 28)}…` : n.name,
                        category: n.category,
                        symbolSize: n.category === "decision" ? 26 : 18,
                        label: { show: true, fontSize: 9, color: C.ink2 },
                      })),
                      links: relation.links.map((l) => ({
                        source: l.source,
                        target: l.target,
                        relation: l.relation,
                        lineStyle: { color: C.line, width: 1.5 },
                      })),
                      force: { repulsion: 180, edgeLength: [60, 120], gravity: 0.08 },
                      emphasis: { focus: "adjacency" },
                    },
                  ],
                }}
              />
            );
          }}
        </QueryBoundary>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        {/* 决策时间线 */}
        <Card title="决策时间线" subtitle="来源: /projects/{id}/timeline → decision_* 事件">
          {decisionEvents.length === 0 ? (
            <EmptyState text="无决策事件" />
          ) : (
            <Chart
              height={260}
              option={{
                ...baseChartOption(),
                xAxis: { type: "category", data: decisionEvents.map((e) => fmtDateTime(e.occurred_at)), ...axisBase(), axisLabel: { color: C.ink3, fontSize: 9 } },
                yAxis: { type: "category", data: [...new Set(decisionEvents.map((e) => e.payload.project_id as string ?? "unknown"))], ...axisBase() },
                series: [
                  {
                    type: "scatter",
                    symbolSize: 10,
                    data: decisionEvents.map((e) => ({
                      value: [fmtDateTime(e.occurred_at), String(e.payload.project_id ?? "unknown")],
                      itemStyle: {
                        color:
                          e.status === "adopted" || e.status === "confirmed"
                            ? C.passed
                            : e.status === "rejected" || e.status === "superseded"
                              ? C.critical
                              : staleIds.has(e.event_id)
                                ? C.warning
                                : C.primary,
                      },
                    })),
                  },
                ],
                tooltip: {
                  ...tooltipBase,
                  formatter: (p: unknown) => {
                    const idx = (p as { dataIndex: number }).dataIndex;
                    const e = decisionEvents[idx];
                    return `${e.type} · ${e.status ?? "—"}<br/>${String(e.payload.text ?? "").slice(0, 120)}`;
                  },
                },
              }}
            />
          )}
          <div className="mt-1 flex gap-3 text-[10px] text-ink3">
            <span className="text-passed">● adopted/confirmed</span>
            <span className="text-critical">● rejected/superseded</span>
            <span className="text-warning">● stale 候选</span>
            <span className="text-primary">● 其他</span>
          </div>
        </Card>

        {/* 参数演化 */}
        <ParamEvolution lineage={lineage.data ?? []} isPending={lineage.isPending} isError={lineage.isError} error={lineage.error} />
      </div>

      {/* 决策列表 */}
      <Card title="决策列表" subtitle="可信度 + 过期 + 冲突联合标注" pad={false}>
        <QueryBoundary query={confidence} isEmpty={(d) => d.length === 0} emptyText="无决策记录">
          {(d) => (
            <DataTable
              columns={[
                { key: "claim", label: "决策/结论" },
                { key: "project", label: "项目" },
                { key: "score", label: "可信度", align: "right" },
                { key: "flags", label: "标记" },
                { key: "confidence", label: "置信" },
                { key: "evidence", label: "证据" },
              ]}
              rows={d.map((c) => ({
                claim: (
                  <div className="max-w-[340px]">
                    <span className="line-clamp-2 text-xs text-ink2">{c.claim ?? "（无文本）"}</span>
                    <span className="text-[10px] text-ink3">{c.reasons.join(" · ")}</span>
                  </div>
                ),
                project: <span className="text-xs text-primary">{c.project_id ?? "—"}</span>,
                score: (
                  <span className={`font-semibold tabular-nums ${c.score >= 70 ? "text-passed" : c.score >= 40 ? "text-warning" : "text-critical"}`}>
                    {c.score}
                  </span>
                ),
                flags: (
                  <span className="flex gap-1">
                    {staleIds.has(c.event_id) && <StatusBadge status="stale" />}
                    {conflictDecisionIds.has(c.event_id) && <StatusBadge status="critical" />}
                  </span>
                ),
                confidence: <ConfidenceTag value={c.confidence} />,
                evidence: <EvidenceRef ids={[c.event_id]} max={1} />,
              }))}
              keyFn={(_, i) => i}
            />
          )}
        </QueryBoundary>
      </Card>

      <div className="grid gap-4 lg:grid-cols-3">
        {/* 建议 */}
        <Card title="实验建议" subtitle="来源: /insights/suggest">
          <QueryBoundary query={suggest} isEmpty={(d) => d.length === 0} emptyText="当前无建议">
            {(d) => (
              <ul className="space-y-2">
                {d.map((s, i) => (
                  <li key={i} className="rounded-md border border-line px-3 py-2">
                    <p className="text-sm text-ink">{s.suggestion}</p>
                    <p className="mt-0.5 text-xs text-ink3">{s.reason}</p>
                    <div className="mt-1 flex items-center gap-2">
                      <StatusBadge status={s.kind === "stale_decision" ? "stale" : "warning"} />
                      <span className="text-[10px] text-ink3">{s.kind}</span>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </QueryBoundary>
        </Card>

        {/* 决策倒计时 */}
        <Card title="决策倒计时" subtitle="来源: /advanced/countdown（≥3 天未推进的决策）">
          <QueryBoundary query={countdown} isEmpty={(d) => d.length === 0} emptyText="无等待中的决策">
            {(d) => (
              <ul className="space-y-2">
                {d.map((c) => (
                  <li key={c.decision_id} className="rounded-md border border-warning/40 px-3 py-2">
                    <div className="flex items-center justify-between">
                      <code className="font-mono text-[10px] text-ink2">{c.decision_id.slice(0, 20)}</code>
                      <span className="text-sm font-semibold text-warning tabular-nums">{c.waiting_days} 天</span>
                    </div>
                    <p className="mt-0.5 text-xs text-ink3">
                      后续关联事件 {c.dependent_events} 条 · 成本: {c.cost}<ConfidenceTag value="approximate" className="ml-1" />
                    </p>
                    <EvidenceRef ids={c.evidence} max={1} />
                  </li>
                ))}
              </ul>
            )}
          </QueryBoundary>
        </Card>

        {/* 反事实查询 */}
        <CounterfactualBox project={project} projectIds={projectIds} />
      </div>
    </div>
  );
}

// ---------- 参数演化（数值参数画折线，非数值降级表格） ----------

function ParamEvolution({
  lineage,
  isPending,
  isError,
  error,
}: {
  lineage: Awaited<ReturnType<typeof getLineage>>;
  isPending: boolean;
  isError: boolean;
  error: Error | null;
}) {
  const numericParams = lineage.filter((p) => p.changed && numericParamSeries(p.history) !== null);
  const [selected, setSelected] = useState<string>("");
  const current = numericParams.find((p) => p.parameter === selected) ?? numericParams[0];

  return (
    <Card
      title="参数演化"
      subtitle="来源: /insights/lineage（仅展示有变更的数值型参数）"
      right={
        numericParams.length > 1 && (
          <select
            value={current?.parameter ?? ""}
            onChange={(e) => setSelected(e.target.value)}
            className="rounded-md border border-line bg-card px-2 py-1 text-xs text-ink outline-none focus:border-primary"
          >
            {numericParams.map((p) => (
              <option key={p.parameter} value={p.parameter}>{p.parameter}</option>
            ))}
          </select>
        )
      }
    >
      {isPending ? (
        <EmptyState text="加载中…" />
      ) : isError ? (
        <EmptyState text="加载失败" detail={error?.message} />
      ) : !current ? (
        <EmptyState text="无有变更的数值型参数" detail="参数历史为空或全部为非数值/未变更" />
      ) : (
        <Chart
          height={260}
          option={{
            ...baseChartOption(),
            xAxis: { type: "category", data: current.history.map((h) => fmtDateTime(h.occurred_at)), ...axisBase() },
            yAxis: { type: "value", scale: true, ...axisBase() },
            series: [
              lineSeries(
                current.parameter,
                current.history.map((h) => Number(h.value)),
                C.primary,
                { step: "end" },
              ),
            ],
          }}
        />
      )}
    </Card>
  );
}

// ---------- 反事实查询（GET /insights/counterfactual） ----------

function CounterfactualBox({ project, projectIds }: { project: string; projectIds: string[] }) {
  const [localProject, setLocalProject] = useState(project || projectIds[0] || "");
  const [queryText, setQueryText] = useState("");
  const [submitted, setSubmitted] = useState<{ project: string; query: string } | null>(null);
  const result = useQuery({
    queryKey: ["counterfactual", submitted?.project, submitted?.query],
    queryFn: () => getCounterfactual(submitted!.project, submitted!.query),
    enabled: Boolean(submitted),
  });

  return (
    <Card title="反事实查询" subtitle="来源: /insights/counterfactual（只读查询，答案含置信标签）">
      <div className="space-y-2">
        <select
          value={localProject}
          onChange={(e) => setLocalProject(e.target.value)}
          className="w-full rounded-md border border-line bg-card px-2.5 py-1.5 text-sm text-ink outline-none focus:border-primary"
        >
          {projectIds.map((id) => (
            <option key={id} value={id}>{id}</option>
          ))}
        </select>
        <div className="flex gap-2">
          <input
            value={queryText}
            onChange={(e) => setQueryText(e.target.value)}
            placeholder="例如：TensorRT"
            className="min-w-0 flex-1 rounded-md border border-line bg-card px-2.5 py-1.5 text-sm text-ink outline-none placeholder:text-ink3 focus:border-primary"
            onKeyDown={(e) => {
              if (e.key === "Enter" && queryText.trim() && localProject) {
                setSubmitted({ project: localProject, query: queryText.trim() });
              }
            }}
          />
          <button
            onClick={() => queryText.trim() && localProject && setSubmitted({ project: localProject, query: queryText.trim() })}
            className="shrink-0 rounded-md border border-primary/50 px-3 py-1.5 text-sm text-primary hover:bg-primary/10"
          >
            查询
          </button>
        </div>
        {submitted && (
          <QueryBoundary query={result}>
            {(d) => (
              <div className="rounded-md border border-line/60 p-2.5">
                <p className="text-sm text-ink2">{d.answer}</p>
                <div className="mt-1.5 flex items-center gap-2">
                  <ConfidenceTag value={d.confidence} />
                  <EvidenceRef ids={d.evidence} max={2} />
                </div>
                {d.observed_decision && (
                  <pre className="mt-2 max-h-32 overflow-auto rounded bg-page/60 p-2 text-[10px] text-ink3">
                    {JSON.stringify(d.observed_decision, null, 1).slice(0, 400)}
                  </pre>
                )}
              </div>
            )}
          </QueryBoundary>
        )}
      </div>
    </Card>
  );
}
