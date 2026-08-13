import { useParams, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  getConfidence,
  getConflicts,
  getContextPack,
  getCoverage,
  getEfficiency,
  getFingerprints,
  getFreshness,
  getHealthScore,
  getImpact,
  getLineage,
  getProjectState,
  getProjectTimeline,
  getReproducibility,
  getRisk,
  getWhyNotDone,
} from "../lib/api";
import { buildProjectView, stageRows } from "../lib/adapters";
import { fmtDateTime, fmtPct, shortSha } from "../lib/format";
import { Card, DataTable, EmptyState, KeyValue, PageHeader, QueryBoundary } from "../components/ui";
import { ConfidenceTag, DirtyBadge, EvidenceRef, ProgressBar, StatusBadge } from "../components/badges";
import { Tabs } from "../components/controls";
import { ContextPackView } from "../components/ContextPackView";
import { Chart } from "../components/Chart";
import { baseChartOption, C } from "../lib/chartTheme";
import type { ProjectState } from "../lib/types";

const TABS = [
  { key: "state", label: "Current State" },
  { key: "funnel", label: "Verification Funnel" },
  { key: "timeline", label: "Timeline" },
  { key: "experiments", label: "Experiments" },
  { key: "decisions", label: "Decisions" },
  { key: "parameters", label: "Parameters" },
  { key: "risks", label: "Risks" },
  { key: "context", label: "Context Pack" },
];

export function ProjectDetail() {
  const { projectId } = useParams<{ projectId: string }>();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? "state";
  const pid = projectId ?? "";

  const setTab = (key: string) => {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set("tab", key);
      return next;
    });
  };

  const state = useQuery({
    queryKey: ["project-state", pid],
    queryFn: () => getProjectState(pid),
    enabled: Boolean(pid),
  });

  return (
    <div>
      <PageHeader
        title={state.data ? state.data.name : pid}
        description={`项目 ${pid} · 来源: /projects/${pid}/state 及多个投影端点`}
        right={
          state.data && (
            <div className="flex items-center gap-2 text-xs text-ink3">
              <DirtyBadge dirty={state.data.dirty} />
              <code className="font-mono">{shortSha(state.data.head)}</code>
              <span>{state.data.branch}</span>
            </div>
          )
        }
      />
      <Tabs tabs={TABS} value={tab} onChange={setTab} />
      {!pid ? (
        <EmptyState text="缺少项目 ID" />
      ) : (
        <>
          {tab === "state" && <StateTab pid={pid} />}
          {tab === "funnel" && <FunnelTab pid={pid} />}
          {tab === "timeline" && <TimelineTab pid={pid} />}
          {tab === "experiments" && <ExperimentsTab pid={pid} />}
          {tab === "decisions" && <DecisionsTab pid={pid} />}
          {tab === "parameters" && <ParametersTab pid={pid} />}
          {tab === "risks" && <RisksTab pid={pid} />}
          {tab === "context" && <ContextTab pid={pid} />}
        </>
      )}
    </div>
  );
}

// ---------- Current State ----------

function StateTab({ pid }: { pid: string }) {
  const state = useQuery({ queryKey: ["project-state", pid], queryFn: () => getProjectState(pid) });
  return (
    <QueryBoundary query={state}>
      {(d: ProjectState) => {
        const view = buildProjectView(d);
        return (
          <div className="grid gap-4 lg:grid-cols-2">
            <Card title="当前状态" subtitle="来源: /projects/{id}/state">
              <KeyValue k="当前目标" v={d.goal ?? "（无）"} />
              <KeyValue k="HEAD" v={shortSha(d.head)} mono />
              <KeyValue k="branch" v={d.branch ?? "—"} mono />
              <div className="flex items-center justify-between py-1">
                <span className="text-xs text-ink3">dirty</span>
                <DirtyBadge dirty={d.dirty} />
              </div>
              <KeyValue k="repo" v={d.repo_path} mono />
              <KeyValue k="验证进度" v={`${view.passedStages}/${view.totalStages}（${fmtPct(view.progress)}）`} />
              <div className="pt-1">
                <ProgressBar ratio={view.progress} />
              </div>
            </Card>
            <div className="space-y-4">
              <Card title={`Blockers（${d.blockers.length}）`}>
                {d.blockers.length === 0 ? (
                  <EmptyState text="无 blocker" />
                ) : (
                  <ul className="list-disc space-y-1 pl-5 text-sm text-critical/90">
                    {d.blockers.map((b, i) => <li key={i}>{b}</li>)}
                  </ul>
                )}
              </Card>
              <Card title={`Remaining（${d.remaining.length}）`}>
                {d.remaining.length === 0 ? (
                  <EmptyState text="无 remaining" />
                ) : (
                  <ul className="list-disc space-y-1 pl-5 text-sm text-ink2">
                    {d.remaining.map((b, i) => <li key={i}>{b}</li>)}
                  </ul>
                )}
              </Card>
            </div>
            <Card title="最近事件" subtitle="state → recent_events" pad={false} className="lg:col-span-2">
              {d.recent_events.length === 0 ? (
                <EmptyState text="无最近事件" />
              ) : (
                <ul className="divide-y divide-line/50">
                  {[...d.recent_events].reverse().map((e) => (
                    <li key={e.event_id} className="flex items-center gap-3 px-4 py-1.5 text-sm">
                      <span className="w-20 shrink-0 font-mono text-[10px] text-ink3">{fmtDateTime(e.occurred_at)}</span>
                      <code className="text-xs text-primary">{e.type}</code>
                      <StatusBadge status={e.status} />
                      {e.commit && <code className="font-mono text-[10px] text-ink3">{shortSha(e.commit)}</code>}
                      <EvidenceRef ids={[e.event_id]} max={1} />
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>
        );
      }}
    </QueryBoundary>
  );
}

// ---------- Verification Funnel ----------

function FunnelTab({ pid }: { pid: string }) {
  const state = useQuery({ queryKey: ["project-state", pid], queryFn: () => getProjectState(pid) });
  const impact = useQuery({ queryKey: ["impact", pid], queryFn: () => getImpact(pid) });
  return (
    <div className="space-y-4">
      <Card title="验证漏斗" subtitle="来源: /projects/{id}/state → verification（stale 阶段显示原因与证据）">
        <QueryBoundary query={state}>
          {(d) => {
            const rows = stageRows(d);
            if (rows.length === 0) return <EmptyState text="该项目未配置验证阶段" />;
            return (
              <div className="space-y-2">
                {rows.map((s) => (
                  <div key={s.stage} className="flex flex-wrap items-center gap-2 rounded-md border border-line/60 px-3 py-2">
                    <span className="w-40 shrink-0 text-sm text-ink">{s.stage}</span>
                    <StatusBadge status={s.status} />
                    {s.staleReason && (
                      <span className="text-xs text-warning" title={s.staleReason}>
                        {s.staleReason}
                      </span>
                    )}
                    {s.reason && !s.staleReason && <span className="text-xs text-ink3">{s.reason}</span>}
                    <span className="ml-auto flex items-center gap-2">
                      {s.commit && <code className="font-mono text-[10px] text-ink3">{shortSha(s.commit)}</code>}
                      {s.verifiedAt && <span className="text-[10px] text-ink3">{fmtDateTime(s.verifiedAt)}</span>}
                      <EvidenceRef ids={[s.eventId]} max={1} />
                    </span>
                  </div>
                ))}
              </div>
            );
          }}
        </QueryBoundary>
      </Card>
      <Card title="变更影响" subtitle="来源: /insights/impact?project=">
        <QueryBoundary query={impact}>
          {(d) => (
            <div>
              <p className="text-sm text-ink2">{d.recommendation}</p>
              <div className="mt-2 flex flex-wrap gap-2">
                {d.stages.map((s) => (
                  <span key={s.stage} className="inline-flex items-center gap-1.5 rounded border border-line px-2 py-1 text-xs">
                    {s.stage} <StatusBadge status={s.status} />
                  </span>
                ))}
              </div>
            </div>
          )}
        </QueryBoundary>
      </Card>
    </div>
  );
}

// ---------- Timeline ----------

function TimelineTab({ pid }: { pid: string }) {
  const timeline = useQuery({ queryKey: ["project-timeline", pid], queryFn: () => getProjectTimeline(pid) });
  return (
    <Card title="事件时间线" subtitle="来源: /projects/{id}/timeline（点击行展开 payload 与 evidence）" pad={false}>
      <QueryBoundary query={timeline} isEmpty={(d) => d.length === 0} emptyText="无事件">
        {(d) => (
          <ul className="max-h-[600px] divide-y divide-line/50 overflow-y-auto">
            {[...d].reverse().map((e) => (
              <li key={e.event_id}>
                <details className="group px-4 py-2">
                  <summary className="flex cursor-pointer list-none items-center gap-3 text-sm">
                    <span className="w-24 shrink-0 font-mono text-[10px] text-ink3">{fmtDateTime(e.occurred_at)}</span>
                    <code className="shrink-0 text-xs text-primary">{e.type}</code>
                    <StatusBadge status={e.status} />
                    <span className="min-w-0 flex-1 truncate text-xs text-ink2">
                      {String(e.payload.text ?? e.payload.name ?? e.payload.command ?? e.payload.stage ?? "")}
                    </span>
                    <ConfidenceTag value={e.provenance} />
                  </summary>
                  <div className="mt-2 space-y-2 pl-28">
                    <pre className="max-h-52 overflow-auto rounded bg-page/60 p-2 text-[11px] text-ink2">
                      {JSON.stringify(e.payload, null, 2)}
                    </pre>
                    <EvidenceRef
                      ids={[e.event_id, ...e.evidence.map((ev) => String(ev.path ?? "")).filter(Boolean)]}
                      max={4}
                      label="event/evidence"
                    />
                  </div>
                </details>
              </li>
            ))}
          </ul>
        )}
      </QueryBoundary>
    </Card>
  );
}

// ---------- Experiments ----------

function ExperimentsTab({ pid }: { pid: string }) {
  const efficiency = useQuery({ queryKey: ["efficiency", pid], queryFn: () => getEfficiency(pid) });
  const repro = useQuery({ queryKey: ["reproducibility", pid], queryFn: () => getReproducibility(pid) });
  const fingerprints = useQuery({ queryKey: ["fingerprints", pid], queryFn: () => getFingerprints(pid) });
  return (
    <div className="space-y-4">
      <Card title="实验效率" subtitle="来源: /insights/efficiency?project=">
        <QueryBoundary query={efficiency} isEmpty={(d) => d.total === 0} emptyText="无实验记录">
          {(d) => (
            <div>
              <div className="mb-3 flex flex-wrap gap-4 text-sm">
                <span className="text-ink2">总数 <b className="text-ink">{d.total}</b></span>
                <span className="text-ink2">有效率 <b className="text-primary">{fmtPct(d.effective_rate)}</b></span>
                {Object.entries(d.counts).map(([k, v]) => (
                  <span key={k} className="text-ink2">
                    <StatusBadge status={k} className="mr-1" />{v}
                  </span>
                ))}
              </div>
              <DataTable
                columns={[
                  { key: "name", label: "实验" },
                  { key: "classification", label: "分类" },
                  { key: "status", label: "状态" },
                  { key: "evidence", label: "证据" },
                ]}
                rows={d.items.map((item) => ({
                  name: <span className="text-xs text-ink2">{item.name ?? item.event_id}</span>,
                  classification: <StatusBadge status={item.classification} />,
                  status: <StatusBadge status={item.status} />,
                  evidence: <EvidenceRef ids={item.evidence} max={1} />,
                }))}
                keyFn={(_, i) => i}
              />
            </div>
          )}
        </QueryBoundary>
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="可复现度" subtitle="来源: /insights/reproducibility?project=" pad={false}>
          <QueryBoundary query={repro} isEmpty={(d) => d.length === 0} emptyText="无可复现性记录">
            {(d) => (
              <DataTable
                columns={[
                  { key: "event", label: "事件" },
                  { key: "score", label: "评分", align: "right" },
                  { key: "missing", label: "缺失项" },
                ]}
                rows={d.map((r) => ({
                  event: <EvidenceRef ids={[r.event_id]} max={1} label="" />,
                  score: <span className={`tabular-nums ${r.score >= 80 ? "text-passed" : r.score >= 50 ? "text-warning" : "text-critical"}`}>{r.score}</span>,
                  missing: <span className="text-xs text-ink2">{r.missing.join(", ") || "完整"}</span>,
                }))}
                keyFn={(_, i) => i}
              />
            )}
          </QueryBoundary>
        </Card>
        <Card title="配置 Fingerprint" subtitle="来源: /advanced/fingerprints?project=（重复 = 相同配置多次运行）" pad={false}>
          <QueryBoundary query={fingerprints} isEmpty={(d) => d.length === 0} emptyText="无 fingerprint">
            {(d) => (
              <DataTable
                columns={[
                  { key: "fp", label: "Fingerprint" },
                  { key: "count", label: "次数", align: "right" },
                  { key: "dup", label: "重复" },
                ]}
                rows={d.map((f) => ({
                  fp: <code className="font-mono text-[10px] text-ink2">{f.fingerprint.slice(0, 12)}…</code>,
                  count: <span className="tabular-nums">{f.count}</span>,
                  dup: f.duplicate ? <StatusBadge status="warning" /> : <span className="text-xs text-ink3">否</span>,
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

// ---------- Decisions ----------

function DecisionsTab({ pid }: { pid: string }) {
  const confidence = useQuery({ queryKey: ["confidence", pid], queryFn: () => getConfidence(pid) });
  const conflicts = useQuery({ queryKey: ["conflicts", pid], queryFn: () => getConflicts(pid) });
  const freshness = useQuery({ queryKey: ["freshness", pid], queryFn: () => getFreshness(pid) });
  const staleIds = new Set((freshness.data ?? []).map((f) => f.event_id));
  return (
    <div className="space-y-4">
      <Card title="决策可信度" subtitle="来源: /advanced/confidence?project=（过期标记来自 /insights/freshness）" pad={false}>
        <QueryBoundary query={confidence} isEmpty={(d) => d.length === 0} emptyText="无决策记录">
          {(d) => (
            <DataTable
              columns={[
                { key: "claim", label: "决策/结论" },
                { key: "score", label: "可信度", align: "right" },
                { key: "confidence", label: "置信" },
                { key: "stale", label: "过期" },
                { key: "evidence", label: "证据" },
              ]}
              rows={d.map((c) => ({
                claim: (
                  <div className="max-w-[360px]">
                    <span className="line-clamp-2 text-xs text-ink2">{c.claim ?? "（无文本）"}</span>
                    <span className="text-[10px] text-ink3">{c.reasons.join(" · ")}</span>
                  </div>
                ),
                score: (
                  <span className={`font-semibold tabular-nums ${c.score >= 70 ? "text-passed" : c.score >= 40 ? "text-warning" : "text-critical"}`}>
                    {c.score}
                  </span>
                ),
                confidence: <ConfidenceTag value={c.confidence} />,
                stale: staleIds.has(c.event_id) ? <StatusBadge status="stale" /> : <span className="text-xs text-ink3">—</span>,
                evidence: <EvidenceRef ids={[c.event_id]} max={1} />,
              }))}
              keyFn={(_, i) => i}
            />
          )}
        </QueryBoundary>
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="决策冲突" subtitle="来源: /insights/conflicts?project=">
          <QueryBoundary query={conflicts} isEmpty={(d) => d.length === 0} emptyText="无冲突 🎉">
            {(d) => (
              <ul className="space-y-3">
                {d.map((c, i) => (
                  <li key={i} className="rounded-md border border-warning/40 px-3 py-2">
                    <div className="flex items-center gap-2">
                      <code className="text-xs text-warning">{c.decision_key}</code>
                      {c.different_scope && <StatusBadge status="warning" />}
                    </div>
                    <ul className="mt-1 space-y-1">
                      {c.decisions.map((dec) => (
                        <li key={dec.event_id} className="text-xs text-ink2">
                          <span className="font-mono text-[10px] text-ink3">{fmtDateTime(dec.occurred_at)}</span>{" "}
                          <StatusBadge status={dec.status} /> {String(dec.payload.text ?? "")}
                        </li>
                      ))}
                    </ul>
                    <p className="mt-1 text-xs text-ink3">建议：{c.recommendation}</p>
                  </li>
                ))}
              </ul>
            )}
          </QueryBoundary>
        </Card>
        <Card title="过期决策候选" subtitle="来源: /insights/freshness?project=">
          <QueryBoundary query={freshness} isEmpty={(d) => d.length === 0} emptyText="无过期决策">
            {(d) => (
              <ul className="space-y-2">
                {d.map((f) => (
                  <li key={f.event_id} className="rounded-md border border-line px-3 py-2">
                    <p className="text-sm text-ink2">{f.text ?? f.event_id}</p>
                    <p className="mt-0.5 text-xs text-warning">{f.reasons.join("；")}</p>
                    <EvidenceRef ids={f.evidence} max={2} />
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

// ---------- Parameters ----------

function ParametersTab({ pid }: { pid: string }) {
  const lineage = useQuery({ queryKey: ["lineage", pid], queryFn: () => getLineage(pid) });
  return (
    <Card title="参数族谱" subtitle="来源: /insights/lineage?project=（点击行展开历史）" pad={false}>
      <QueryBoundary query={lineage} isEmpty={(d) => d.length === 0} emptyText="无参数记录" emptyDetail="事件 payload 中没有 parameters/params 字段">
        {(d) => (
          <ul className="divide-y divide-line/50">
            {d.map((p) => (
              <li key={p.parameter}>
                <details className="group px-4 py-2">
                  <summary className="flex cursor-pointer list-none items-center gap-3">
                    <code className="w-40 shrink-0 truncate text-xs text-primary">{p.parameter}</code>
                    <code className="min-w-0 flex-1 truncate text-xs text-ink2">{JSON.stringify(p.current)}</code>
                    {p.changed ? <StatusBadge status="dirty" /> : <span className="text-[10px] text-ink3">未变更</span>}
                    <span className="text-[10px] text-ink3">{p.history.length} 次</span>
                  </summary>
                  <div className="mt-2 pl-4">
                    <DataTable
                      columns={[
                        { key: "at", label: "时间" },
                        { key: "value", label: "值" },
                        { key: "type", label: "事件类型" },
                        { key: "status", label: "状态" },
                        { key: "commit", label: "Commit" },
                        { key: "reason", label: "原因/假设" },
                      ]}
                      rows={p.history.map((h) => ({
                        at: <span className="font-mono text-[10px] text-ink3">{fmtDateTime(h.occurred_at)}</span>,
                        value: <code className="text-xs text-ink">{JSON.stringify(h.value)}</code>,
                        type: <code className="text-[10px] text-ink2">{h.type}</code>,
                        status: <StatusBadge status={h.status} />,
                        commit: <code className="font-mono text-[10px] text-ink3">{shortSha(h.commit)}</code>,
                        reason: <span className="line-clamp-1 max-w-[220px] text-xs text-ink3">{h.reason ?? "—"}</span>,
                      }))}
                      keyFn={(_, i) => i}
                    />
                  </div>
                </details>
              </li>
            ))}
          </ul>
        )}
      </QueryBoundary>
    </Card>
  );
}

// ---------- Risks ----------

function RisksTab({ pid }: { pid: string }) {
  const risk = useQuery({ queryKey: ["risk", pid], queryFn: () => getRisk(pid) });
  const health = useQuery({ queryKey: ["health", pid], queryFn: () => getHealthScore(pid) });
  const coverage = useQuery({ queryKey: ["coverage", pid], queryFn: () => getCoverage(pid) });
  const whyNotDone = useQuery({ queryKey: ["why-not-done", pid], queryFn: () => getWhyNotDone(pid) });
  const RISK_LABEL: Record<string, string> = { high: "高", medium: "中", low: "低", unknown: "未知" };
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="风险雷达" subtitle="来源: /advanced/risk?project=（0=未知 1=低 2=中 3=高）">
        <QueryBoundary query={risk}>
          {(d) => {
            const dims = Object.keys(d.risks);
            if (dims.length === 0) return <EmptyState text="无风险数据" />;
            const score = (level: string) => ({ high: 3, medium: 2, low: 1 } as Record<string, number>)[level] ?? 0;
            return (
              <div>
                <Chart
                  height={240}
                  option={{
                    ...baseChartOption(),
                    radar: {
                      indicator: dims.map((k) => ({ name: k, max: 3 })),
                      radius: "65%",
                      axisName: { color: C.ink2, fontSize: 11 },
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
                            value: dims.map((k) => score(d.risks[k])),
                            lineStyle: { color: C.warning, width: 2 },
                            itemStyle: { color: C.warning },
                            areaStyle: { color: "rgba(250, 204, 21, 0.12)" },
                          },
                        ],
                      },
                    ],
                  }}
                />
                <div className="flex flex-wrap gap-2">
                  {Object.entries(d.risks).map(([k, level]) => (
                    <span key={k} className="text-xs text-ink2">
                      {k}: <StatusBadge status={level} /> {RISK_LABEL[level] ?? level}
                    </span>
                  ))}
                </div>
                <p className="mt-2 text-[10px] text-ink3">置信: {d.confidence}</p>
              </div>
            );
          }}
        </QueryBoundary>
      </Card>
      <div className="space-y-4">
        <Card title="健康评分" subtitle="来源: /advanced/health?project=">
          <QueryBoundary query={health}>
            {(d) => (
              <div>
                <div className="flex items-baseline gap-2">
                  <span className={`text-3xl font-semibold tabular-nums ${d.score >= 70 ? "text-passed" : d.score >= 40 ? "text-warning" : "text-critical"}`}>
                    {d.score}
                  </span>
                  <span className="text-xs text-ink3">/100</span>
                </div>
                <div className="mt-2 grid grid-cols-2 gap-2 text-xs text-ink2">
                  <span>证据覆盖 {fmtPct(d.dimensions.evidence)}</span>
                  <span>可复现 {fmtPct(d.dimensions.reproducibility)}</span>
                  <span>验证 {fmtPct(d.dimensions.verification)}</span>
                  <span>blocker ×{d.dimensions.blockers}</span>
                </div>
              </div>
            )}
          </QueryBoundary>
        </Card>
        <Card title="证据覆盖率" subtitle="来源: /insights/coverage?project=">
          <QueryBoundary query={coverage}>
            {(d) => (
              <div>
                <div className="mb-1 text-sm text-ink2">
                  {d.covered_claims}/{d.total_claims} 条结论有证据（{fmtPct(d.coverage)}）
                </div>
                <ProgressBar ratio={d.coverage} tone={d.coverage >= 0.8 ? "good" : d.coverage >= 0.5 ? "warn" : "bad"} />
                {d.claims_without_evidence.length > 0 && (
                  <div className="mt-2">
                    <p className="mb-1 text-xs text-ink3">缺证据的结论：</p>
                    <EvidenceRef ids={d.claims_without_evidence} max={6} />
                  </div>
                )}
              </div>
            )}
          </QueryBoundary>
        </Card>
        <Card title="为什么还没完成" subtitle="来源: /advanced/why-not-done?project=">
          <QueryBoundary query={whyNotDone} isEmpty={(d) => d.primary_reasons.length === 0} emptyText="没有未完成原因（可能已全部完成）">
            {(d) => (
              <ul className="space-y-1.5">
                {d.primary_reasons.map((r, i) => (
                  <li key={i} className="flex items-center gap-2 text-sm text-ink2">
                    <span className={`rounded px-1 text-[10px] ${r.priority === 1 ? "bg-critical/20 text-critical" : "bg-line/60 text-ink3"}`}>
                      P{r.priority}
                    </span>
                    {r.reason}
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

// ---------- Context Pack ----------

function ContextTab({ pid }: { pid: string }) {
  const context = useQuery({ queryKey: ["context", pid], queryFn: () => getContextPack(pid) });
  return (
    <QueryBoundary query={context}>
      {(d) => <ContextPackView data={d} />}
    </QueryBoundary>
  );
}
