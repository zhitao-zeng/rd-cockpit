import type { ContextPackData } from "../lib/types";
import { fmtDateTime, shortSha } from "../lib/format";
import { stageRows } from "../lib/adapters";
import { Card, DataTable, EmptyState, KeyValue } from "./ui";
import { DirtyBadge, EvidenceRef, ProgressBar, StatusBadge } from "./badges";

/**
 * Agent 接管视图：当前目标 / HEAD / branch / dirty / 验证漏斗 /
 * 最近事件 / 参数族谱 / 决策 / reproducibility / remaining。
 * ProjectDetail 的 Context Pack tab 与独立 Context Pack 页共用。
 */
export function ContextPackView({ data }: { data: ContextPackData }) {
  const { project } = data;
  const stages = stageRows(project);
  const passed = stages.filter((s) => s.status === "passed").length;
  return (
    <div className="space-y-4">
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="当前状态" subtitle="来源: /insights/context → project">
          <KeyValue k="项目" v={project.name} />
          <KeyValue k="当前目标" v={project.goal ?? "（无）"} />
          <KeyValue k="HEAD" v={shortSha(project.head)} mono />
          <KeyValue k="branch" v={project.branch ?? "—"} mono />
          <div className="flex items-center justify-between py-1">
            <span className="text-xs text-ink3">dirty</span>
            <DirtyBadge dirty={project.dirty} />
          </div>
          <KeyValue k="repo" v={project.repo_path} mono />
        </Card>

        <Card title={`验证漏斗（${passed}/${stages.length}）`} subtitle="stale 阶段显示原因">
          {stages.length === 0 ? (
            <EmptyState text="无验证阶段" />
          ) : (
            <div className="space-y-2">
              <ProgressBar ratio={stages.length ? passed / stages.length : 0} tone={passed === stages.length ? "good" : "primary"} />
              {stages.map((s) => (
                <div key={s.stage} className="flex items-center gap-2 text-sm">
                  <span className="w-36 shrink-0 truncate text-ink2">{s.stage}</span>
                  <StatusBadge status={s.status} />
                  {s.staleReason && <span className="truncate text-xs text-warning" title={s.staleReason}>{s.staleReason}</span>}
                  {s.eventId && <EvidenceRef ids={[s.eventId]} max={1} label="" />}
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      <Card title={`Remaining（${project.remaining.length}）`}>
        {project.remaining.length === 0 ? (
          <EmptyState text="无 remaining" />
        ) : (
          <ul className="list-disc space-y-1 pl-5 text-sm text-ink2">
            {project.remaining.map((r, i) => <li key={i}>{r}</li>)}
          </ul>
        )}
      </Card>

      <Card title={`最近事件（${data.recent_events.length}）`} subtitle="来源: context → recent_events" pad={false}>
        {data.recent_events.length === 0 ? (
          <EmptyState text="无最近事件" />
        ) : (
          <ul className="max-h-64 divide-y divide-line/50 overflow-y-auto">
            {[...data.recent_events].reverse().map((e) => (
              <li key={e.event_id} className="flex items-center gap-3 px-4 py-1.5 text-sm">
                <span className="w-20 shrink-0 font-mono text-[10px] text-ink3">{fmtDateTime(e.occurred_at)}</span>
                <code className="text-xs text-primary">{e.type}</code>
                <StatusBadge status={e.status} />
                {e.commit && <code className="font-mono text-[10px] text-ink3">{shortSha(e.commit)}</code>}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title={`决策（${data.decisions.length}）`} subtitle="来源: context → decisions" pad={false}>
          {data.decisions.length === 0 ? (
            <EmptyState text="无决策记录" />
          ) : (
            <ul className="max-h-64 space-y-2 overflow-y-auto px-4 py-3">
              {data.decisions.map((d) => (
                <li key={d.event_id} className="rounded border border-line px-3 py-2">
                  <code className="text-xs text-primary">{d.type}</code>
                  <p className="mt-0.5 text-sm text-ink2">{String(d.payload.text ?? d.payload.name ?? "—")}</p>
                  <EvidenceRef ids={[d.event_id]} max={1} />
                </li>
              ))}
            </ul>
          )}
      </Card>

      <Card title={`参数族谱（${data.parameter_lineage.length}）`} subtitle="来源: context → parameter_lineage" pad={false}>
        <DataTable
          columns={[
            { key: "parameter", label: "参数" },
            { key: "current", label: "当前值" },
            { key: "changed", label: "曾变更" },
            { key: "history", label: "历史次数", align: "right" },
          ]}
          rows={data.parameter_lineage.map((p) => ({
            parameter: <code className="text-xs text-primary">{p.parameter}</code>,
            current: <code className="text-xs text-ink2">{JSON.stringify(p.current)}</code>,
            changed: p.changed ? <StatusBadge status="dirty" className="!text-warning" /> : <span className="text-xs text-ink3">否</span>,
            history: <span className="tabular-nums">{p.history.length}</span>,
          }))}
          keyFn={(r) => (r.parameter as React.ReactElement).props.children as string}
        />
      </Card>

      <Card title={`Reproducibility（${data.reproducibility.length}）`} subtitle="来源: context → reproducibility" pad={false}>
        <DataTable
          columns={[
            { key: "event", label: "事件" },
            { key: "score", label: "评分", align: "right" },
            { key: "missing", label: "缺失项" },
          ]}
          rows={data.reproducibility.map((r) => ({
            event: <EvidenceRef ids={[r.event_id]} max={1} label="" />,
            score: (
              <span className={`font-semibold tabular-nums ${r.score >= 80 ? "text-passed" : r.score >= 50 ? "text-warning" : "text-critical"}`}>
                {r.score}
              </span>
            ),
            missing: r.missing.length > 0 ? <span className="text-xs text-ink2">{r.missing.join(", ")}</span> : <span className="text-xs text-passed">完整</span>,
          }))}
          keyFn={(_, i) => i}
        />
      </Card>

      <p className="text-[10px] text-ink3">
        数据置信说明：以上全部来自事件账本投影（/insights/context），评分规则见后端 reproducibility 投影。
      </p>
    </div>
  );
}
