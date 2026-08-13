import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Chart } from "../components/Chart";
import { Card, EmptyState, PageHeader, QueryBoundary, StatCard } from "../components/ui";
import { getDevelopment, getLifeDashboard } from "../lib/api";
import { fmtTokens } from "../lib/format";
import type { DevelopmentMetric, DevelopmentResponse, DevelopmentTaskNode, LifeDashboard } from "../lib/types";
import { axisBase, baseChartOption, catColor, C, legendBase, tooltipBase } from "../lib/chartTheme";

const PHASES = ["探索", "实现", "执行", "验证", "交付", "运维"];
const PHASE_COLORS = [C.cat[2], C.cat[0], "#38bdf8", C.warning, C.passed, "#fb923c"];
const STATUS_COLOR: Record<string, string> = { working: C.primary, result: C.passed, blocked: C.critical };
const STATUS_LABEL: Record<string, string> = { working: "推进中", result: "有结果", blocked: "有阻塞" };

function esc(value: unknown): string {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char] ?? char);
}

function Guide({ children }: { children: string }) {
  return <div className="mb-3 rounded-md border border-primary/15 bg-primary/5 px-3 py-2 text-xs leading-5 text-ink2"><span className="font-medium text-primary">怎么看：</span>{children}</div>;
}

function compactText(value: string | null | undefined, limit = 170): string {
  const cleaned = String(value ?? "")
    .replace(/\[([^\]]+)\]\([^\)]+\)/g, "$1")
    .replace(/[`*_#>]/g, "")
    .replace(/\s+/g, " ")
    .trim();
  return cleaned.length > limit ? `${cleaned.slice(0, limit - 1)}…` : cleaned;
}

function ProjectMetro({ data, project }: { data: DevelopmentResponse; project: string }) {
  const nodes = (data.storylines[project] ?? []).slice(-14);
  return (
    <Card title="项目地铁图" subtitle="一站是一条正式日报事项，线路只表示先后顺序">
      <Guide>从左向右读。站点颜色表示当日工作类型；红圈只表示那份日报写有未解决内容，不代表今天仍在阻塞，也不表示任务之间存在依赖。</Guide>
      {!nodes.length ? <EmptyState text="还没有能画成线路的日报记录" /> : (
        <div className="overflow-x-auto pb-2">
          <div className="relative flex min-w-max gap-3 px-2 pb-1 pt-7">
            <div className="absolute left-10 right-10 top-[43px] h-1 rounded-full bg-gradient-to-r from-violet-500 via-cyan-400 to-emerald-400 opacity-55" />
            {nodes.map((node, index) => {
              const phaseIndex = Math.max(0, PHASES.indexOf(node.phase));
              const color = node.status === "blocked" ? C.critical : PHASE_COLORS[phaseIndex];
              return (
                <div key={node.id} className="relative w-48 shrink-0 pt-7">
                  <span className="absolute left-1/2 top-0 z-10 flex h-8 w-8 -translate-x-1/2 items-center justify-center rounded-full border-[3px] border-card text-[10px] font-semibold text-page shadow-lg" style={{ backgroundColor: color, boxShadow: `0 0 16px ${color}66` }}>{index + 1}</span>
                  <div className="h-full rounded-lg border border-line bg-page/60 px-3 py-3">
                    <div className="flex items-center justify-between gap-2 text-[10px]"><span className="text-ink3">{node.date.slice(5)}</span><span style={{ color }}>{(node.work_types ?? [node.phase]).join("+")}{node.status === "blocked" ? " · 当时未解决" : ""}</span></div>
                    <div className="mt-2 text-xs font-medium leading-5 text-ink">{compactText(node.title, 58)}</div>
                    <p className="mt-1 text-[11px] leading-4 text-ink3">{compactText(node.results[0] ?? node.did[0] ?? "日报未补充结果", 88)}</p>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </Card>
  );
}

function ResearchStarfield({ data }: { data: DevelopmentResponse }) {
  const projects = [...data.lifecycles].filter((item) => item.project_id !== "unassigned" && data.storylines[item.project_id]?.length)
    .sort((a, b) => b.task_count - a.task_count).slice(0, 10);
  const dates = [...new Set(projects.flatMap((project) => (data.storylines[project.project_id] ?? []).map((node) => node.date)))].sort().slice(-60);
  const dateSet = new Set(dates);
  const points = projects.flatMap((project, projectIndex) => {
    const byDate = new Map<string, DevelopmentTaskNode[]>();
    (data.storylines[project.project_id] ?? []).filter((node) => dateSet.has(node.date)).forEach((node) => byDate.set(node.date, [...(byDate.get(node.date) ?? []), node]));
    return [...byDate.entries()].map(([date, nodes]) => ({
      value: [date.slice(5), project.name, nodes.length],
      nodes,
      symbolSize: 7 + Math.min(17, nodes.length * 3),
      itemStyle: {
        color: catColor(projectIndex),
        borderColor: nodes.some((node) => node.status === "blocked") ? C.critical : C.ink,
        borderWidth: nodes.some((node) => node.status === "blocked") ? 2 : 0.5,
        shadowBlur: 13,
        shadowColor: `${catColor(projectIndex)}99`,
      },
    }));
  });
  return (
    <Card title="研发星空" subtitle="每颗星代表某个项目在某一天留下的正式日报记录">
      <Guide>横轴是日期，纵轴是项目。星越大表示当天日报事项越多；红色外圈表示当日日报写有未解决内容，不代表当前仍阻塞。星空不拿 Token 冒充产出。</Guide>
      {!points.length ? <EmptyState text="还没有足够的项目日期记录" /> : <div className="rounded-lg bg-[#07101d] px-2 py-2"><Chart height={Math.max(330, projects.length * 42)} option={{
        ...baseChartOption(),
        backgroundColor: "transparent",
        grid: { left: 10, right: 16, top: 12, bottom: 18, containLabel: true },
        tooltip: { ...tooltipBase, formatter: (raw: unknown) => {
          const item = raw as { data?: { nodes?: DevelopmentTaskNode[] } };
          const nodes = item.data?.nodes ?? [];
          if (!nodes.length) return "";
          return `<b>${esc(nodes[0].date)} · ${esc(data.project_names[nodes[0].project_id] ?? nodes[0].project_id)}</b><br/>${nodes.length} 条日报事项${nodes.some((node) => node.status === "blocked") ? " · 含阻塞" : ""}<br/>${nodes.slice(0, 3).map((node) => `• ${esc(node.title)}`).join("<br/>")}`;
        } },
        xAxis: { type: "category", data: dates.map((date) => date.slice(5)), ...axisBase(), axisLabel: { color: C.ink3, rotate: dates.length > 25 ? 35 : 0 }, splitLine: { show: false } },
        yAxis: { type: "category", data: projects.map((item) => item.name), ...axisBase(), axisLabel: { color: C.ink2 }, splitLine: { lineStyle: { color: C.line, opacity: .22, type: "dashed" } } },
        series: [{ type: "scatter", data: points, emphasis: { scale: 1.5 } }],
      }} /></div>}
    </Card>
  );
}

function GpuPetZoo({ life }: { life?: LifeDashboard }) {
  const pet = life?.gpu_pet;
  const pets = pet?.pets ?? [];
  return (
    <Card title="GPU 宠物园" subtitle="瞬时状态与连续趋势分开表达，不从一张快照推断资源浪费">
      <Guide>“显存已分配”“此刻忙碌”只描述最新快照；只有最近 3 次且跨度至少 9 分钟都满足条件，才会显示“显存驻留 · 持续低利用率”或“持续奔跑”。前者也不自动等于浪费，系统不会操作进程。</Guide>
      {!pet ? <EmptyState text="GPU 状态还在加载" /> : !pets.length ? <EmptyState text={`${pet.icon} ${pet.state}`} detail={pet.detail} /> : (
        <>
          <div className="mb-3 flex items-center justify-between rounded-lg border border-line bg-page/40 px-4 py-2">
            <span className="text-sm text-ink"><span className="mr-2 text-xl">{pet.icon}</span>{pet.state}</span>
            <span className="text-[10px] text-ink3">{pet.observed_at ? `观测 ${new Date(pet.observed_at).toLocaleString("zh-CN", { hour12: false })}` : "尚无观测"}</span>
          </div>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {pets.map((item) => (
              <article key={item.gpu} className={`rounded-xl border px-3 py-3 text-center ${item.stale ? "border-line bg-page/30 opacity-75" : item.state.includes("显存") || item.state.includes("热") ? "border-warning/30 bg-warning/5" : "border-primary/20 bg-primary/5"}`}>
                <div className="text-[10px] font-medium text-ink3">GPU {item.gpu}</div>
                <div className="my-2 text-4xl drop-shadow-lg">{item.icon}</div>
                <div className="text-xs font-medium text-ink">{item.state}</div>
                <div className="mt-1 text-[10px] leading-4 text-ink3">{item.detail}</div>
              </article>
            ))}
          </div>
        </>
      )}
    </Card>
  );
}

function ProjectOverview({ data, project }: { data: DevelopmentResponse; project: string }) {
  const story = data.storylines[project] ?? [];
  const lifecycle = data.lifecycles.find((item) => item.project_id === project);
  const effort = data.effort_output.find((item) => item.project_id === project);
  const first = story[0];
  const latest = story[story.length - 1];
  const latestSnapshot = [...data.time_travel].reverse()
    .map((snapshot) => snapshot.projects.find((item) => item.project_id === project))
    .find(Boolean);
  const currentBlocker = lifecycle?.status === "blocked"
    ? lifecycle.blockers[lifecycle.blockers.length - 1]
    : undefined;
  const activeDays = new Set(story.map((node) => node.date)).size;
  const total = Math.max(1, story.length);
  const latestResult = latest?.results[0] ?? latest?.conclusions[0] ?? "最近一条日报没有写出明确结果";
  const next = latestSnapshot?.next[0] ?? currentBlocker?.text ?? "日报尚未写明下一步";

  return (
    <Card
      title={data.project_names[project] ?? project}
      subtitle={first && latest ? `从 ${first.date} 记录到 ${latest.date}` : "尚无项目发展记录"}
      right={latest && <span className="rounded-full border px-2.5 py-1 text-xs" style={{ color: STATUS_COLOR[latest.status], borderColor: `${STATUS_COLOR[latest.status]}55`, backgroundColor: `${STATUS_COLOR[latest.status]}10` }}>{STATUS_LABEL[latest.status]}</span>}
    >
      {!latest || !lifecycle ? <EmptyState text="这个项目还没有可读的发展记录" /> : (
        <>
          <div className="grid gap-3 lg:grid-cols-2">
            <div className="rounded-lg border border-primary/20 bg-primary/5 px-4 py-3">
              <div className="text-[11px] text-primary">最近在做</div>
              <div className="mt-1 text-base font-medium leading-6 text-ink">{latest.title}</div>
              <p className="mt-2 text-xs leading-5 text-ink2">{latest.did[0] ?? "日报没有补充具体动作"}</p>
            </div>
            <div className="rounded-lg border border-passed/20 bg-passed/5 px-4 py-3">
              <div className="text-[11px] text-passed">最近得到的结果</div>
              <p className="mt-1 text-sm leading-6 text-ink">{latestResult}</p>
              <div className="mt-2 text-[10px] text-ink3">{latest.date} · {latest.phase}阶段 · 来源 {latest.source}</div>
            </div>
            <div className={`rounded-lg border px-4 py-3 ${currentBlocker ? "border-critical/25 bg-critical/5" : "border-line bg-page/25"}`}>
              <div className={`text-[11px] ${currentBlocker ? "text-critical" : "text-ink3"}`}>当前阻塞</div>
              <p className="mt-1 text-xs leading-5 text-ink2">{currentBlocker ? currentBlocker.text : "最新日报没有把这个项目标为阻塞"}</p>
              {currentBlocker && <div className="mt-2 text-[10px] text-ink3">记录于 {currentBlocker.date}</div>}
            </div>
            <div className="rounded-lg border border-warning/20 bg-warning/5 px-4 py-3">
              <div className="text-[11px] text-warning">接下来</div>
              <p className="mt-1 text-xs leading-5 text-ink2">{next}</p>
            </div>
          </div>

          <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <div><div className="text-[10px] text-ink3">最近主要工作类型</div><div className="mt-1 text-lg font-semibold text-primary">{lifecycle.current_phase}</div></div>
            <div><div className="text-[10px] text-ink3">有记录的日期</div><div className="mt-1 text-lg font-semibold text-ink">{activeDays} 天</div></div>
            <div><div className="text-[10px] text-ink3">带结果的日报事项</div><div className="mt-1 text-lg font-semibold text-passed">{lifecycle.result_count} / {lifecycle.task_count}</div></div>
            <div><div className="text-[10px] text-ink3">日报归属的 Agent Token</div><div className="mt-1 text-lg font-semibold text-ink">{fmtTokens(effort?.tokens ?? 0)}</div></div>
          </div>

          <div className="mt-4 grid gap-2 sm:grid-cols-4">
            {PHASES.map((phase, index) => {
              const count = lifecycle.work_type_counts?.[phase] ?? lifecycle.phase_counts[phase] ?? 0;
              const current = lifecycle.current_phase === phase;
              return (
                <div key={phase} className={`rounded-md border px-3 py-2 ${current ? "border-primary/50 bg-primary/5" : "border-line bg-page/20"}`}>
                  <div className="flex items-center justify-between text-xs"><span style={{ color: PHASE_COLORS[index] }}>{phase}</span><span className="tabular-nums text-ink2">{count} 条</span></div>
                  <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-line/50"><div className="h-full rounded-full" style={{ width: `${(count / total) * 100}%`, backgroundColor: PHASE_COLORS[index] }} /></div>
                </div>
              );
            })}
          </div>
          <p className="mt-2 text-[10px] text-ink3">一个日报事项可以同时属于实现和验证；上面的条数允许重复。主要工作类型优先采用任务标题，不是项目完成百分比。Token 来自日报采集器对 Agent Session 的项目归属统计，包含缓存。</p>
        </>
      )}
    </Card>
  );
}

function ProjectTrajectory({ data, project }: { data: DevelopmentResponse; project: string }) {
  const nodes = (data.storylines[project] ?? []).slice(-36);
  const buckets = new Map<string, DevelopmentTaskNode[]>();
  nodes.forEach((node) => {
    (node.work_types ?? [node.phase]).forEach((workType) => {
      const key = `${node.date}|${workType}`;
      buckets.set(key, [...(buckets.get(key) ?? []), { ...node, phase: workType }]);
    });
  });
  const points = [...buckets.values()].map((items) => ({
    value: [items[0].date.slice(5), items[0].phase, items.length],
    nodes: items,
    symbolSize: 9 + Math.min(12, items.length * 3),
    itemStyle: { color: items.some((node) => node.status === "blocked") ? C.critical : items.some((node) => node.status === "result") ? C.passed : C.primary },
  }));
  return (
    <Card title="工作类型时间分布" subtitle="按日期展示探索、实现、执行、验证、交付和运维；同一事项可以出现在多个类型中">
      <Guide>横轴是时间，纵轴是多标签工作类型。圆点越大表示当天该类型事项越多；红色只表示当日日报中至少一项尚未解决。</Guide>
      {!nodes.length ? <EmptyState text="暂无阶段轨迹" /> : <Chart height={330} option={{
        ...baseChartOption(),
        grid: { left: 12, right: 18, top: 18, bottom: 12, containLabel: true },
        tooltip: { ...tooltipBase, formatter: (raw: unknown) => {
          const item = raw as { data?: { nodes?: DevelopmentTaskNode[] } };
          const values = item.data?.nodes ?? [];
          if (!values.length) return "";
          const titles = values.slice(0, 4).map((node) => `• ${esc(node.title)}`).join("<br/>");
          return `<b>${esc(values[0].date)} · ${esc(values[0].phase)}（${values.length} 条）</b><br/>${titles}${values.length > 4 ? "<br/>…" : ""}`;
        } },
        xAxis: { type: "category", data: [...new Set(nodes.map((node) => node.date.slice(5)))], ...axisBase(), axisLabel: { color: C.ink2, rotate: nodes.length > 18 ? 35 : 0 } },
        yAxis: { type: "category", data: PHASES, ...axisBase(), axisLabel: { color: (value?: string | number) => PHASE_COLORS[Math.max(0, PHASES.indexOf(String(value)))] } },
        series: [{ type: "scatter", data: points, itemStyle: { borderColor: C.card, borderWidth: 2, opacity: .9 }, emphasis: { scale: 1.35 } }],
      }} />}
    </Card>
  );
}

function ProjectActivity({ data, project }: { data: DevelopmentResponse; project: string }) {
  const item = data.activity.projects.find((value) => value.project_id === project);
  const dates = data.activity.dates;
  const activities = item?.activities ?? [];
  const tokens = item?.tokens ?? [];
  const hasTokens = tokens.some((value) => value > 0);
  const start = Math.max(0, dates.length - 45);
  const visibleDates = dates.slice(start).map((value) => value.slice(5));
  const visibleActivities = activities.slice(start);
  const visibleTokens = tokens.slice(start);
  return (
    <Card title="投入节奏" subtitle="柱子是正式日报事项；折线是日报采集器归属到该项目的 Agent Token">
      <Guide>Token 来自当天 Session 用量统计，包含输入、输出和缓存；无法可靠归属的 Session 不会硬塞给项目，因此它用于观察量级，不代表费用、工时或产出质量。</Guide>
      {!item ? <EmptyState text="暂无投入节奏" /> : <Chart height={330} option={{
        ...baseChartOption(),
        tooltip: { ...tooltipBase, trigger: "axis", formatter: (raw: unknown) => {
          const values = raw as Array<{ dataIndex: number }>;
          const index = values[0]?.dataIndex ?? 0;
          return `<b>${esc(visibleDates[index])}</b><br/>日报事项：${visibleActivities[index] ?? 0}${hasTokens ? `<br/>项目 Agent Token：${fmtTokens(visibleTokens[index] ?? 0)}` : "<br/>Token：当日没有可靠项目归属"}`;
        } },
        legend: { ...legendBase() },
        xAxis: { type: "category", data: visibleDates, ...axisBase(), axisLabel: { color: C.ink2, rotate: visibleDates.length > 24 ? 40 : 0 } },
        yAxis: [
          { type: "value", minInterval: 1, ...axisBase(), name: "日报事项", nameTextStyle: { color: C.ink3 } },
          { type: "value", ...axisBase(), name: hasTokens ? "Token" : "", nameTextStyle: { color: C.ink3 }, axisLabel: { color: C.ink2, formatter: (value: number) => fmtTokens(value) } },
        ],
        series: [
          { name: "日报事项", type: "bar", data: visibleActivities, barMaxWidth: 18, itemStyle: { color: C.primary, borderRadius: [3, 3, 0, 0] } },
          ...(hasTokens ? [{ name: "项目 Agent Token", type: "line" as const, yAxisIndex: 1, data: visibleTokens, symbol: "none", lineStyle: { color: C.warning, width: 2 } }] : []),
        ],
      }} />}
    </Card>
  );
}

function MilestoneTimeline({ data, project }: { data: DevelopmentResponse; project: string }) {
  const nodes = (data.storylines[project] ?? []).filter((node) => node.results.length || node.status === "blocked").slice(-12).reverse();
  return (
    <Card title="最近结果与阻塞" subtitle="按时间倒序展示写出明确结果或阻塞的日报事项，不自动认定为项目里程碑">
      {!nodes.length ? <EmptyState text="还没有带结果或阻塞的日报事项" /> : (
        <div className="relative ml-2 border-l border-line pl-5">
          {nodes.map((node) => (
            <div key={node.id} className="relative pb-5 last:pb-0">
              <span className="absolute -left-[27px] top-1.5 h-3 w-3 rounded-full border-2 border-card" style={{ backgroundColor: STATUS_COLOR[node.status] }} />
              <div className="flex flex-wrap items-center gap-2 text-[10px] text-ink3">
                <span>{node.date}</span><span className="rounded-full border border-line px-2 py-0.5" style={{ color: PHASE_COLORS[PHASES.indexOf(node.phase)] }}>{node.phase}</span><span>{STATUS_LABEL[node.status]}</span>
              </div>
              <h4 className="mt-1 text-sm font-medium leading-5 text-ink">{node.title}</h4>
              <p className="mt-1 text-xs leading-5 text-ink2">{node.results[0] ?? node.did[0] ?? "没有可读摘要"}</p>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

function MetricMountain({ metrics, project }: { metrics: DevelopmentMetric[]; project: string }) {
  const keys = useMemo(() => [...new Set(metrics.filter((item) => item.project_id === project).map((item) => `${item.name}|${item.unit}`))], [metrics, project]);
  const [selected, setSelected] = useState("");
  useEffect(() => { if (!keys.includes(selected)) setSelected(keys[0] ?? ""); }, [keys, selected]);
  const [name, unit] = selected.split("|");
  const points = metrics.filter((item) => item.project_id === project && item.name === name && item.unit === (unit ?? ""));
  return (
    <Card title="指标记录点" subtitle="展示日报明确写出的 CER、WER、F1、延迟等数值，但不自动连成趋势">
      <Guide>同名同单位仍可能来自不同数据集、模型或硬件，因此只画独立点。悬停必须结合原始任务和语句判断是否可比。</Guide>
      <div className="mb-3 flex justify-end">
        <select value={selected} onChange={(event) => setSelected(event.target.value)} className="rounded-md border border-line bg-page px-3 py-1.5 text-xs text-ink">
          {keys.length ? keys.map((key) => { const [metric, metricUnit] = key.split("|"); return <option key={key} value={key}>{metric}{metricUnit ? `（${metricUnit}）` : ""}</option>; }) : <option value="">没有指标</option>}
        </select>
      </div>
      {!points.length ? <EmptyState text="这个项目还没有可画的明确指标" detail="日报写出类似 CER = 9.8% 后会自动出现。" /> : (
        <Chart height={300} option={{
          ...baseChartOption(),
          tooltip: { ...tooltipBase, trigger: "item", formatter: (raw: unknown) => {
            const params = raw as { dataIndex?: number };
            const point = points[params.dataIndex ?? 0];
            return `<b>${esc(point.name)} ${point.value}${esc(point.unit)}</b><br/>${point.date} · ${esc(point.task)}<br/>${esc(point.context)}`;
          } },
          xAxis: { type: "category", data: points.map((item) => item.date.slice(5)), ...axisBase() },
          yAxis: { type: "value", ...axisBase(), name: unit || undefined, nameTextStyle: { color: C.ink3 } },
          series: [{ type: "scatter", data: points.map((item) => item.value), symbolSize: 11,
                     itemStyle: { color: C.primary, borderColor: C.card, borderWidth: 2 } }],
        }} />
      )}
    </Card>
  );
}

function LifecycleChart({ data }: { data: DevelopmentResponse }) {
  const values = [...data.lifecycles].filter((item) => item.project_id !== "unassigned")
    .sort((a, b) => b.last_activity.localeCompare(a.last_activity) || b.task_count - a.task_count)
    .slice(0, 12);
  return (
    <Card title="近期项目工作类型" subtitle="统计日报事项涉及的探索、实现、执行、验证、交付和运维；一个事项可以贡献多个类型">
      <Guide>这不是项目进度或完成率，只回答“记录里涉及哪些工作类型”。多标签避免“实现后顺手测试”被整体误写成验证。</Guide>
      <Chart height={Math.max(300, values.length * 38)} option={{
        ...baseChartOption(), tooltip: { ...tooltipBase, trigger: "axis", axisPointer: { type: "shadow" } },
        legend: { ...legendBase() },
        xAxis: { type: "value", minInterval: 1, ...axisBase(), name: "日报事项数", nameTextStyle: { color: C.ink3 } },
        yAxis: { type: "category", inverse: true, data: values.map((item) => item.name), ...axisBase() },
        series: PHASES.map((phase, index) => ({ name: phase, type: "bar", stack: "phase", barMaxWidth: 24,
          data: values.map((item) => item.work_type_counts?.[phase] ?? item.phase_counts[phase] ?? 0), itemStyle: { color: PHASE_COLORS[index] } })),
      }} />
    </Card>
  );
}

function ActivityRiver({ data }: { data: DevelopmentResponse }) {
  const top = [...data.activity.projects].filter((item) => item.project_id !== "unassigned")
    .sort((a, b) => b.activities.reduce((x, y) => x + y, 0) - a.activities.reduce((x, y) => x + y, 0)).slice(0, 6);
  const start = Math.max(0, data.activity.dates.length - 45);
  return (
    <Card title="跨项目日报记录密度" subtitle="仅展示近 45 个有记录日期中，事项数最多的 6 个项目">
      <Guide>颜色面积表示日报记录条数，不表示工时、难度或产出。它只适合观察工作主题何时出现和切换。</Guide>
      <Chart height={340} option={{
        ...baseChartOption(), tooltip: { ...tooltipBase, trigger: "axis" }, legend: { ...legendBase(), type: "scroll" },
        xAxis: { type: "category", boundaryGap: false, data: data.activity.dates.slice(start).map((value) => value.slice(5)), ...axisBase() },
        yAxis: { type: "value", minInterval: 1, ...axisBase(), name: "日报事项", nameTextStyle: { color: C.ink3 } },
        series: top.map((item, index) => ({ name: item.name, type: "line", stack: "activity", smooth: .35, symbol: "none",
          data: item.activities.slice(start), lineStyle: { width: 1, color: catColor(index) }, areaStyle: { opacity: .45, color: catColor(index) }, emphasis: { focus: "series" } })),
      }} />
    </Card>
  );
}

function PlanTrend({ data }: { data: DevelopmentResponse }) {
  const statuses = ["完成", "部分完成", "阻塞", "延后", "无证据", "取消", "未标明"];
  const colors: Record<string, string> = { 完成: C.passed, 部分完成: C.primary, 阻塞: C.critical, 延后: C.warning, 无证据: C.neutral, 取消: C.ink3, 未标明: C.line };
  const daily = data.plans.daily.slice(-30);
  return (
    <Card title="计划闭环记录" subtitle="按日报日期展示计划被标记为完成、部分完成、阻塞、延后或无证据的数量">
      <Guide>这里只复现日报写下的状态，不根据代码或 Token 推测完成。“未标明”是旧日报缺少明确状态，不代表失败。</Guide>
      {!data.plans.total ? <EmptyState text="日报还没有昨日计划闭环数据" /> : (
        <div className="grid gap-4 xl:grid-cols-[1.2fr_.8fr]">
          <Chart height={320} option={{
            ...baseChartOption(), tooltip: { ...tooltipBase, trigger: "axis", axisPointer: { type: "shadow" } }, legend: { ...legendBase(), type: "scroll" },
            xAxis: { type: "category", data: daily.map((item) => item.date.slice(5)), ...axisBase(), axisLabel: { color: C.ink2, rotate: daily.length > 18 ? 35 : 0 } },
            yAxis: { type: "value", minInterval: 1, ...axisBase(), name: "计划条目", nameTextStyle: { color: C.ink3 } },
            series: statuses.map((status) => ({ name: status, type: "bar", stack: "closure", barMaxWidth: 22,
              data: daily.map((item) => item.counts[status] ?? 0), itemStyle: { color: colors[status] } })),
          }} />
          <div className="max-h-80 space-y-2 overflow-y-auto pr-1">
            {data.plans.items.slice(0, 14).map((item, index) => (
              <div key={`${item.date}-${index}`} className="rounded-md border border-line bg-page/25 px-3 py-2">
                <div className="flex justify-between gap-2 text-[10px] text-ink3"><span>{item.date}</span><span>{item.status}</span></div>
                <div className="mt-1 text-xs leading-5 text-ink2">{item.text}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </Card>
  );
}

function TimeTravel({ data }: { data: DevelopmentResponse }) {
  const [index, setIndex] = useState(Math.max(0, data.time_travel.length - 1));
  useEffect(() => setIndex(Math.max(0, data.time_travel.length - 1)), [data.time_travel.length]);
  const snapshot = data.time_travel[index];
  return (
    <Card title="历史日报快照" subtitle="选择一个日期，只查看截至当天日报已经记录的任务、结果和阻塞">
      <Guide>这是日报视角的历史快照，不是当时 Git、机器和实验环境的完整复原，也不会用后来的结果改写过去。</Guide>
      {!snapshot ? <EmptyState text="还没有历史快照" /> : (
        <>
          <div className="mb-4 flex items-center gap-3">
            <span className="w-20 text-sm font-medium text-primary">{snapshot.date}</span>
            <input type="range" min={0} max={Math.max(0, data.time_travel.length - 1)} value={index} onChange={(event) => setIndex(Number(event.target.value))} className="w-full accent-cyan-400" />
          </div>
          <div className="grid gap-3 xl:grid-cols-2">
            {snapshot.projects.filter((item) => item.project_id !== "unassigned").map((item) => (
              <div key={item.project_id} className="rounded-lg border border-line bg-page/25 px-4 py-3">
                <div className="flex items-center justify-between"><h4 className="text-sm font-medium text-ink">{item.name}</h4><span className="text-xs text-primary">{item.phase}</span></div>
                <div className="mt-3 space-y-2 text-xs leading-5">
                  <p><span className="text-ink3">当时最新任务：</span><span className="text-ink2">{item.latest_task}</span></p>
                  <p><span className="text-ink3">当时最新结果：</span><span className="text-passed">{item.latest_result ?? "尚无明确结果"}</span></p>
                  {item.blockers.length > 0 && <p><span className="text-ink3">当时阻塞：</span><span className="text-critical">{item.blockers.join("；")}</span></p>}
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </Card>
  );
}

function DevelopmentContent({ data, project, setProject, life }: {
  data: DevelopmentResponse;
  project: string;
  setProject: (value: string) => void;
  life?: LifeDashboard;
}) {
  const recentProject = [...data.lifecycles]
    .filter((item) => item.project_id !== "unassigned" && data.storylines[item.project_id]?.length)
    .sort((a, b) => b.last_activity.localeCompare(a.last_activity) || b.task_count - a.task_count)[0]?.project_id;
  const active = data.storylines[project] ? project : (recentProject ?? Object.keys(data.storylines).find((id) => id !== "unassigned") ?? "");
  useEffect(() => { if (project !== active && active) setProject(active); }, [active, project, setProject]);
  const totalNodes = Object.values(data.storylines).reduce((sum, nodes) => sum + nodes.length, 0);
  return (
    <div className="space-y-4">
      <ProjectOverview data={data} project={active} />
      <details open className="group rounded-lg border border-primary/25 bg-card">
        <summary className="flex cursor-pointer list-none items-center justify-between px-4 py-3 text-sm font-medium text-ink">
          <span>有趣视图 · 仍然可以追溯</span>
          <span className="text-xs font-normal text-ink3">地铁图 · 研发星空 · GPU 宠物园　<span className="inline-block transition-transform group-open:rotate-180">⌄</span></span>
        </summary>
        <div className="space-y-4 border-t border-line px-4 py-4">
          <ProjectMetro data={data} project={active} />
          <ResearchStarfield data={data} />
          <GpuPetZoo life={life} />
        </div>
      </details>
      <div className="grid gap-4 2xl:grid-cols-2">
        <ProjectTrajectory data={data} project={active} />
        <ProjectActivity data={data} project={active} />
      </div>
      <MilestoneTimeline data={data} project={active} />
      <MetricMountain metrics={data.metrics} project={active} />

      <details className="group rounded-lg border border-line bg-card">
        <summary className="flex cursor-pointer list-none items-center justify-between px-4 py-3 text-sm font-medium text-ink">
          <span>展开全局记录统计</span>
          <span className="text-xs font-normal text-ink3">{data.report_count} 份日报 · {totalNodes} 个节点 · {data.lifecycles.length - Number(Boolean(data.storylines.unassigned))} 个项目　<span className="inline-block transition-transform group-open:rotate-180">⌄</span></span>
        </summary>
        <div className="space-y-4 border-t border-line px-4 py-4">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard label="正式日报" value={data.report_count} hint={`最近 ${data.days} 天`} />
            <StatCard label="全部发展节点" value={totalNodes} hint="来自日报任务，不是内部事件" tone="primary" />
            <StatCard label="明确指标点" value={data.metrics.length} hint="CER / WER / F1 / 延迟等" tone="good" />
            <StatCard label="计划闭环记录" value={data.plans.total} hint="完成、阻塞、延后或未标明" />
          </div>
          <LifecycleChart data={data} />
          <ActivityRiver data={data} />
          <PlanTrend data={data} />
          <TimeTravel data={data} />
        </div>
      </details>
    </div>
  );
}

export function Development() {
  const [days, setDays] = useState(90);
  const [project, setProject] = useState("");
  const query = useQuery({ queryKey: ["development", days], queryFn: () => getDevelopment(days), refetchInterval: 5 * 60_000 });
  const lifeQuery = useQuery({ queryKey: ["development-life"], queryFn: () => getLifeDashboard(), refetchInterval: 5 * 60_000 });
  const projectOptions = useMemo(() => [...(query.data?.lifecycles ?? [])]
    .filter((item) => item.project_id !== "unassigned" && query.data?.storylines[item.project_id]?.length)
    .sort((a, b) => b.last_activity.localeCompare(a.last_activity) || b.task_count - a.task_count), [query.data]);
  return (
    <div className="space-y-4">
      <PageHeader
        title="项目发展"
        description="先看一个项目现在在哪、最近取得什么结果，再展开全局统计"
        right={<div className="flex gap-2">
          <select value={project} onChange={(event) => setProject(event.target.value)} className="rounded-md border border-line bg-card px-3 py-1.5 text-sm text-ink">
            {projectOptions.map((item) => <option key={item.project_id} value={item.project_id}>{item.name} · {item.last_activity.slice(5)} · {item.task_count} 条</option>)}
          </select>
          <select value={days} onChange={(event) => setDays(Number(event.target.value))} className="rounded-md border border-line bg-card px-3 py-1.5 text-sm text-ink">
            <option value={30}>最近 30 天</option><option value={90}>最近 90 天</option><option value={180}>最近 180 天</option><option value={365}>最近一年</option>
          </select>
        </div>}
      />
      <div className="rounded-lg border border-primary/20 bg-primary/5 px-4 py-3 text-xs leading-5 text-ink2">
        <span className="font-medium text-ink">这页回答四个问题：</span>最近在做什么、已经得到什么、卡在哪里、下一步是什么。可读内容来自正式 Markdown 日报；Token 来自日报采集器，GPU 状态来自只读采样。
      </div>
      <QueryBoundary query={query} isEmpty={(data) => data.report_count === 0} emptyText="这段时间没有正式日报">
        {(data) => <DevelopmentContent data={data} project={project} setProject={setProject} life={lifeQuery.data} />}
      </QueryBoundary>
    </div>
  );
}
