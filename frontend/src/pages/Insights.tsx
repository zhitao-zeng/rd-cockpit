import { useQueries, useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import {
  getAchievements,
  getAgentBlindspots,
  getAttention,
  getHandoffQuality,
  getKnowledge,
  getMemory,
  getProjects,
  getRhythm,
} from "../lib/api";
import { fmtPct } from "../lib/format";
import { Card, DataTable, PageHeader, QueryBoundary, StatCard } from "../components/ui";
import { ConfidenceTag, EvidenceRef, StatusBadge } from "../components/badges";
import { ProjectSelect } from "../components/controls";
import { Chart } from "../components/Chart";
import { axisBase, barSeries, baseChartOption, C, legendBase } from "../lib/chartTheme";
import type { MemoryFreshness } from "../lib/types";

export function Insights() {
  const [params, setParams] = useSearchParams();
  const project = params.get("project") ?? "";
  const setProject = (v: string) =>
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (v) next.set("project", v);
      else next.delete("project");
      return next;
    });

  const attention = useQuery({ queryKey: ["attention", project], queryFn: () => getAttention(project || undefined) });
  const rhythm = useQuery({ queryKey: ["rhythm", project], queryFn: () => getRhythm(project || undefined) });
  const handoff = useQuery({ queryKey: ["handoff", project], queryFn: () => getHandoffQuality(project || undefined) });
  const blindspots = useQuery({ queryKey: ["blindspots", project], queryFn: () => getAgentBlindspots(project || undefined) });
  const knowledge = useQuery({ queryKey: ["knowledge", project], queryFn: () => getKnowledge(project || undefined) });
  const achievements = useQuery({ queryKey: ["achievements", project], queryFn: () => getAchievements(project || undefined) });

  const projects = useQuery({ queryKey: ["projects"], queryFn: getProjects });
  const projectIds = Object.keys(projects.data ?? {}).sort();
  const memoryTargets = project ? [project] : projectIds;
  const memories = useQueries({
    queries: memoryTargets.map((id) => ({ queryKey: ["memory", id], queryFn: () => getMemory(id) })),
  });
  const memoryById: Record<string, MemoryFreshness> = {};
  memoryTargets.forEach((id, i) => {
    const data = memories[i]?.data;
    if (data) memoryById[id] = data;
  });

  return (
    <div className="space-y-4">
      <PageHeader
        title="高级洞察"
        description="来源: /advanced/{attention,rhythm,handoff-quality,agent-blindspots,memory,knowledge,achievements}"
        right={<ProjectSelect value={project} onChange={setProject} />}
      />

      {/* 成就 + 记忆新鲜度 */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatCard label="成就" value={achievements.data?.length ?? "…"} tone="primary" source="来源: /advanced/achievements" />
        {memoryTargets.slice(0, 3).map((id) => {
          const m = memoryById[id];
          return (
            <StatCard
              key={id}
              label={`记忆新鲜度 · ${id}`}
              value={m ? m.score : "…"}
              tone={m && m.score >= 70 ? "good" : m && m.score < 40 ? "warn" : "default"}
              hint={m ? `距今 ${m.age_days ?? "?"} 天${m.stale_stages.length ? ` · stale: ${m.stale_stages.join(",")}` : ""}` : undefined}
              source="来源: /advanced/memory"
            />
          );
        })}
      </div>

      {/* 成就列表 */}
      <QueryBoundary query={achievements}>
        {(d) =>
          d.length === 0 ? null : (
            <Card title="成就" subtitle="来源: /advanced/achievements">
              <div className="flex flex-wrap gap-2">
                {d.map((a, i) => (
                  <span key={i} className="inline-flex items-center gap-1.5 rounded-md border border-passed/40 bg-passed/10 px-3 py-1.5 text-sm text-passed">
                    🏅 {a.achievement} <span className="text-xs text-ink2">{a.project_id}</span>
                  </span>
                ))}
              </div>
            </Card>
          )
        }
      </QueryBoundary>

      <div className="grid gap-4 lg:grid-cols-2">
        {/* 注意力预算 */}
        <Card title="注意力预算" subtitle="来源: /advanced/attention（事件数代理，非键盘追踪）">
          <QueryBoundary query={attention} isEmpty={(d) => Object.keys(d.shares).length === 0} emptyText="无事件数据">
            {(d) => {
              const entries = Object.entries(d.shares).sort((a, b) => b[1] - a[1]);
              return (
                <div>
                  <Chart
                    height={200}
                    option={{
                      ...baseChartOption(),
                      xAxis: { type: "category", data: entries.map(([k]) => k), ...axisBase() },
                      yAxis: { type: "value", max: 1, axisLabel: { color: C.ink2, fontSize: 11, formatter: (v: number) => fmtPct(v) }, ...axisBase() },
                      series: [barSeries("占比", entries.map(([, v]) => v), C.cat[0])],
                    }}
                  />
                  <p className="mt-1 text-[10px] text-ink3">{d.note}</p>
                </div>
              );
            }}
          </QueryBoundary>
        </Card>

        {/* 工作节奏 */}
        <Card title="工作节奏" subtitle="来源: /advanced/rhythm（按小时聚合，Asia/Shanghai）">
          <QueryBoundary query={rhythm} isEmpty={(d) => d.hours.length === 0} emptyText="无事件数据">
            {(d) => (
              <div>
                <Chart
                  height={200}
                  option={{
                    ...baseChartOption(),
                    legend: { ...legendBase() },
                    xAxis: { type: "category", data: d.hours.map((h) => `${h.hour}时`), ...axisBase() },
                    yAxis: { type: "value", minInterval: 1, ...axisBase() },
                    series: [
                      barSeries("成功", d.hours.map((h) => h.success ?? 0), C.passed, { stack: "r" }),
                      barSeries("失败", d.hours.map((h) => h.failure ?? 0), C.critical, { stack: "r" }),
                      barSeries("其他", d.hours.map((h) => h.events - (h.success ?? 0) - (h.failure ?? 0)), C.neutral, { stack: "r" }),
                    ],
                  }}
                />
                <p className="mt-1 text-[10px] text-ink3">{d.note}</p>
              </div>
            )}
          </QueryBoundary>
        </Card>

        {/* Handoff 质量 */}
        <Card title="Handoff 质量" subtitle="来源: /advanced/handoff-quality（summary/remaining/blockers/decisions 四字段完备度）" pad={false}>
          <QueryBoundary query={handoff} isEmpty={(d) => d.length === 0} emptyText="无会话 handoff 记录">
            {(d) => (
              <DataTable
                columns={[
                  { key: "session", label: "Session" },
                  { key: "score", label: "评分", align: "right" },
                  { key: "fields", label: "字段完备" },
                ]}
                rows={d.map((h) => ({
                  session: <code className="font-mono text-[10px] text-ink2">{h.session_id.slice(0, 16)}</code>,
                  score: (
                    <span className={`font-semibold tabular-nums ${h.score >= 75 ? "text-passed" : h.score >= 50 ? "text-warning" : "text-critical"}`}>
                      {h.score}
                    </span>
                  ),
                  fields: (
                    <span className="flex gap-1">
                      {Object.entries(h.fields).map(([k, ok]) => (
                        <span key={k} className={`rounded px-1 text-[10px] ${ok ? "bg-passed/15 text-passed" : "bg-line/50 text-ink3"}`}>
                          {k}
                        </span>
                      ))}
                    </span>
                  ),
                }))}
                keyFn={(_, i) => i}
              />
            )}
          </QueryBoundary>
        </Card>

        {/* Agent 盲区 */}
        <Card title="Agent 盲区" subtitle="来源: /advanced/agent-blindspots（可能遗漏远端验证，置信: heuristic）" pad={false}>
          <QueryBoundary query={blindspots} isEmpty={(d) => d.length === 0} emptyText="无 Agent 会话数据">
            {(d) => (
              <DataTable
                columns={[
                  { key: "agent", label: "Agent" },
                  { key: "sessions", label: "会话数", align: "right" },
                  { key: "omissions", label: "疑似遗漏远端验证", align: "right" },
                  { key: "confidence", label: "置信" },
                ]}
                rows={d.map((b) => ({
                  agent: <code className="text-xs text-primary">{b.agent}</code>,
                  sessions: <span className="tabular-nums">{b.sessions}</span>,
                  omissions: (
                    <span className={`tabular-nums ${b.possible_remote_verification_omissions > 0 ? "text-warning" : ""}`}>
                      {b.possible_remote_verification_omissions}
                    </span>
                  ),
                  confidence: <ConfidenceTag value={b.confidence} />,
                }))}
                keyFn={(_, i) => i}
              />
            )}
          </QueryBoundary>
        </Card>
      </div>

      {/* 知识卡片 */}
      <Card title="知识卡片" subtitle="来源: /advanced/knowledge（从决策事件提炼的经验）">
        <QueryBoundary query={knowledge} isEmpty={(d) => d.length === 0} emptyText="无知识卡片" emptyDetail="账本中没有 decision_* 事件">
          {(d) => (
            <ul className="grid gap-2 md:grid-cols-2">
              {d.map((k, i) => (
                <li key={i} className="rounded-md border border-line/60 px-3 py-2">
                  <div className="flex items-center gap-2">
                    <span className="text-sm text-ink">{k.title}</span>
                    <StatusBadge status={k.status} />
                    <ConfidenceTag value={k.confidence} />
                  </div>
                  {k.experience && <p className="mt-1 text-xs text-ink3">{k.experience}</p>}
                  <EvidenceRef ids={k.source} max={1} label="source" />
                </li>
              ))}
            </ul>
          )}
        </QueryBoundary>
      </Card>
    </div>
  );
}
