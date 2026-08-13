import { useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { useQueries, useQuery } from "@tanstack/react-query";
import { getAnomalies, getInsightSessions, getProjects, getProjectTimeline, getSessions, getStats, getSwitches } from "../lib/api";
import { eventCategory, mergeEvents, type MergedEvent } from "../lib/adapters";
import { fmtDateTime, fmtHours, fmtRelative } from "../lib/format";
import { Card, DataTable, PageHeader, QueryBoundary, StatCard } from "../components/ui";
import { ConfidenceTag, EvidenceRef, StatusBadge } from "../components/badges";
import { ProjectSelect } from "../components/controls";
import type { TimelineEvent } from "../lib/types";

const CATEGORY_OPTIONS = [
  "决策",
  "实验",
  "测试/Benchmark",
  "Agent 会话",
  "Git 快照",
  "GPU 采样",
  "Blocker",
  "工作区快照",
  "验证",
  "计划",
  "命令",
  "其他",
];

const QUICK_FILTERS = [
  { key: "", label: "全部" },
  { key: "decisions", label: "只看决策" },
  { key: "experiments", label: "只看实验" },
  { key: "anomalies", label: "只看异常" },
];

export function Timeline() {
  const [params, setParams] = useSearchParams();
  const period = params.get("period") === "month" ? "month" : "week";
  const project = params.get("project") ?? "";
  const category = params.get("category") ?? "";
  const status = params.get("status") ?? "";
  const quick = params.get("quick") ?? "";
  const from = params.get("from") ?? "";
  const to = params.get("to") ?? "";

  const setParam = (key: string, value: string) => {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (value) next.set(key, value);
      else next.delete(key);
      return next;
    });
  };

  const stats = useQuery({ queryKey: ["stats", period], queryFn: () => getStats(period) });
  const projects = useQuery({ queryKey: ["projects"], queryFn: getProjects });
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: () => getSessions() });
  const switches = useQuery({ queryKey: ["switches"], queryFn: getSwitches });
  const sessionEff = useQuery({ queryKey: ["insight-sessions"], queryFn: () => getInsightSessions() });
  const anomalies = useQuery({ queryKey: ["anomalies"], queryFn: () => getAnomalies() });

  const projectIds = Object.keys(projects.data ?? {}).sort();
  const timelines = useQueries({
    queries: projectIds.map((id) => ({
      queryKey: ["project-timeline", id],
      queryFn: () => getProjectTimeline(id),
    })),
  });
  const timelinesLoaded = timelines.filter((t) => t.data).length;

  const merged = useMemo(() => {
    if (!stats.data) return [];
    const timelinePairs = projectIds
      .map((id, i) => ({ projectId: id, events: timelines[i]?.data as TimelineEvent[] | undefined }))
      .filter((p): p is { projectId: string; events: TimelineEvent[] } => Array.isArray(p.events));
    return mergeEvents(stats.data.events, timelinePairs);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stats.data, projectIds.join(","), timelinesLoaded]);

  const anomalyEventIds = useMemo(() => {
    const ids = new Set<string>();
    for (const a of anomalies.data ?? []) {
      for (const e of a.evidence) if (e) ids.add(e);
    }
    return ids;
  }, [anomalies.data]);

  const filtered = useMemo(() => {
    return merged.filter((e) => {
      if (project && e.projectId !== project) return false;
      if (category && eventCategory(e.type) !== category) return false;
      if (status === "success" && e.status !== "passed" && e.status !== "completed" && e.status !== "clean") return false;
      if (status === "failed" && e.status !== "failed") return false;
      if (quick === "decisions" && !e.type.startsWith("decision_")) return false;
      if (quick === "experiments" && !e.type.startsWith("experiment_")) return false;
      if (quick === "anomalies" && e.status !== "failed" && !anomalyEventIds.has(e.eventId)) return false;
      if (from && e.occurredAt.slice(0, 10) < from) return false;
      if (to && e.occurredAt.slice(0, 10) > to) return false;
      return true;
    });
  }, [merged, project, category, status, quick, from, to, anomalyEventIds]);

  const activeSessions = (sessions.data ?? []).filter((s) => s.status === "active").length;

  return (
    <div className="space-y-4">
      <PageHeader
        title="时间线"
        description="来源: /stats?period= + /projects/{id}/timeline（payload 按 event_id 合并）· 过滤器保存在 URL"
      />

      {/* 过滤栏 */}
      <Card pad={false}>
        <div className="flex flex-wrap items-center gap-2 px-4 py-3">
          <div className="flex rounded-md border border-line">
            {(["week", "month"] as const).map((p) => (
              <button
                key={p}
                onClick={() => setParam("period", p === "week" ? "" : p)}
                className={`px-3 py-1.5 text-xs ${period === p ? "bg-primary/10 text-primary" : "text-ink2"}`}
              >
                {p === "week" ? "本周" : "本月"}
              </button>
            ))}
          </div>
          <ProjectSelect value={project} onChange={(v) => setParam("project", v)} />
          <select
            value={category}
            onChange={(e) => setParam("category", e.target.value)}
            className="rounded-md border border-line bg-card px-2.5 py-1.5 text-sm text-ink outline-none focus:border-primary"
          >
            <option value="">全部类型</option>
            {CATEGORY_OPTIONS.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
          <select
            value={status}
            onChange={(e) => setParam("status", e.target.value)}
            className="rounded-md border border-line bg-card px-2.5 py-1.5 text-sm text-ink outline-none focus:border-primary"
          >
            <option value="">成功/失败</option>
            <option value="success">成功</option>
            <option value="failed">失败</option>
          </select>
          <div className="flex items-center gap-1 text-xs text-ink3">
            <input
              type="date"
              value={from}
              onChange={(e) => setParam("from", e.target.value)}
              className="rounded-md border border-line bg-card px-2 py-1.5 text-xs text-ink outline-none"
            />
            <span>→</span>
            <input
              type="date"
              value={to}
              onChange={(e) => setParam("to", e.target.value)}
              className="rounded-md border border-line bg-card px-2 py-1.5 text-xs text-ink outline-none"
            />
          </div>
          <div className="flex gap-1">
            {QUICK_FILTERS.map((f) => (
              <button
                key={f.key}
                onClick={() => setParam("quick", f.key)}
                className={`rounded-md border px-2.5 py-1.5 text-xs ${
                  quick === f.key ? "border-primary bg-primary/10 text-primary" : "border-line text-ink2 hover:text-ink"
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>
      </Card>

      {/* 统计行 */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatCard label={`筛选结果（${period === "week" ? "本周" : "本月"}）`} value={filtered.length} source="来源: /stats events" />
        <StatCard
          label="上下文切换"
          value={switches.data?.switches ?? "…"}
          hint={switches.data ? `序列: ${switches.data.sequence.slice(-6).join(" → ")}` : undefined}
          source="来源: /insights/switches"
        />
        <StatCard
          label="会话（活跃）"
          value={sessions.data ? `${sessions.data.length}（${activeSessions}）` : "…"}
          source="来源: /sessions"
        />
        <StatCard
          label="异常事件"
          value={merged.filter((e) => e.status === "failed" || anomalyEventIds.has(e.eventId)).length}
          source="status=failed ∪ /anomalies evidence"
        />
      </div>

      {/* 事件列表 */}
      <Card title="事件流" subtitle="点击行展开 payload（有 payload 的事件来自项目 timeline 合并）" pad={false}>
        <QueryBoundary query={stats} isEmpty={() => filtered.length === 0} emptyText="当前过滤条件下无事件">
          {() => (
            <ul className="max-h-[560px] divide-y divide-line/50 overflow-y-auto">
              {filtered.slice(0, 300).map((e) => (
                <TimelineRow key={e.eventId} event={e} flagged={anomalyEventIds.has(e.eventId)} />
              ))}
              {filtered.length > 300 && (
                <li className="px-4 py-2 text-center text-xs text-ink3">仅显示前 300 条（共 {filtered.length} 条，请收紧过滤条件）</li>
              )}
            </ul>
          )}
        </QueryBoundary>
      </Card>

      {/* 会话效率 */}
      <Card title="Agent 会话效率" subtitle="来源: /insights/sessions" pad={false}>
        <QueryBoundary query={sessionEff} isEmpty={(d) => d.length === 0} emptyText="无会话记录">
          {(d) => (
            <DataTable
              columns={[
                { key: "session", label: "Session" },
                { key: "project", label: "项目" },
                { key: "duration", label: "时长", align: "right" },
                { key: "events", label: "事件", align: "right" },
                { key: "tests", label: "测试/实验", align: "right" },
                { key: "failures", label: "失败", align: "right" },
                { key: "status", label: "状态" },
              ]}
              rows={[...d]
                .sort((a, b) => (b.started_at ?? "").localeCompare(a.started_at ?? ""))
                .slice(0, 20)
                .map((s) => ({
                  session: <code className="font-mono text-[10px] text-ink2">{s.session_id.slice(0, 16)}</code>,
                  project: <span className="text-xs text-primary">{s.project_id ?? "—"}</span>,
                  duration: <span className="tabular-nums">{fmtHours(s.duration_hours)}</span>,
                  events: <span className="tabular-nums">{s.events}</span>,
                  tests: <span className="tabular-nums">{s.tests}</span>,
                  failures: (
                    <span className={`tabular-nums ${s.failures > 0 ? "text-critical" : ""}`}>{s.failures}</span>
                  ),
                  status: <StatusBadge status={s.status} />,
                }))}
              keyFn={(_, i) => i}
            />
          )}
        </QueryBoundary>
      </Card>
    </div>
  );
}

function TimelineRow({ event, flagged }: { event: MergedEvent; flagged: boolean }) {
  const hasPayload = Boolean(event.payload && Object.keys(event.payload).length > 0);
  const inner = (
    <>
      <span className="w-20 shrink-0 font-mono text-[10px] text-ink3" title={event.occurredAt}>
        {fmtDateTime(event.occurredAt)}
      </span>
      <span className="w-24 shrink-0 truncate text-[10px] text-ink3">{eventCategory(event.type)}</span>
      <code className="shrink-0 text-xs text-primary">{event.type}</code>
      <span className="min-w-0 flex-1 truncate text-xs text-ink2">
        {event.projectId ?? "unassigned"}
        {event.payload && (
          <span className="ml-2 text-ink3">
            {String(event.payload.text ?? event.payload.name ?? event.payload.command ?? event.payload.stage ?? "").slice(0, 80)}
          </span>
        )}
      </span>
      {flagged && <StatusBadge status="warning" />}
      <StatusBadge status={event.status} />
      <ConfidenceTag value={event.provenance} />
    </>
  );
  return (
    <li>
      {hasPayload ? (
        <details>
          <summary className="flex cursor-pointer list-none items-center gap-3 px-4 py-2 text-sm hover:bg-cardhover/40">
            {inner}
          </summary>
          <div className="space-y-2 px-4 pb-3 pl-32">
            <pre className="max-h-48 overflow-auto rounded bg-page/60 p-2 text-[11px] text-ink2">
              {JSON.stringify(event.payload, null, 2)}
            </pre>
            <div className="flex items-center gap-3">
              <EvidenceRef ids={[event.eventId, ...event.evidenceIds]} max={4} label="event/evidence" />
              {event.commit && <code className="font-mono text-[10px] text-ink3">{event.commit.slice(0, 8)}</code>}
              <span className="text-[10px] text-ink3">{fmtRelative(event.occurredAt)}</span>
            </div>
          </div>
        </details>
      ) : (
        <div className="flex items-center gap-3 px-4 py-2 text-sm">{inner}</div>
      )}
    </li>
  );
}
