import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Chart } from "../components/ScatterChart";
import { Card, EmptyState, PageHeader, QueryBoundary } from "../components/ui";
import { getProjectIntelligence, getSemanticFeedback, recordSemanticFeedback } from "../lib/api";
import { fmtTokens } from "../lib/format";
import type {
  IntelligenceDeltaItem,
  IntelligencePulse,
  ProjectIntelligenceResponse,
  SemanticFeedbackRating,
} from "../lib/types";
import { axisBase, baseChartOption, C, tooltipBase } from "../lib/chartTheme";

const LAST_SEEN_KEY = "rd-cockpit.intelligence.last-seen-report";
const MODE_LABEL: Record<string, string> = {
  audited: "日报审计", historical_audited: "历史已审计",
  historical_fallback: "历史回退", stale_last_good: "上次可信版本", empty: "无数据",
};
const PRIORITY_LABEL: Record<string, string> = { high: "高", medium: "中", low: "低" };
const PRIORITY_COLOR: Record<string, string> = { high: C.critical, medium: C.warning, low: C.primary };
const QUADRANT_LABEL: Record<string, string> = {
  heavy_wins: "高投入 / 高进展",
  attention_needed: "高投入 / 低进展",
  efficient_wins: "低投入 / 高进展",
  low_activity: "低投入 / 低进展",
};
const LIFECYCLE_LABEL: Record<string, string> = {
  active: "进行中", blocked: "有阻塞", dormant: "已休眠", historical: "历史项目",
};
const LIFECYCLE_COLOR: Record<string, string> = {
  active: "text-passed", blocked: "text-critical", dormant: "text-warning", historical: "text-ink3",
};

function compact(value: string | null | undefined, limit = 145): string {
  const text = String(value ?? "").replace(/\s+/g, " ").trim();
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
}

function SourceTag({ mode }: { mode: string }) {
  const trusted = mode === "audited" || mode === "historical_audited";
  return <span className={`rounded-full border px-2 py-0.5 text-[10px] ${trusted ? "border-passed/30 text-passed" : "border-warning/30 text-warning"}`}>{MODE_LABEL[mode] ?? mode}</span>;
}

const FEEDBACK_LABEL: Record<SemanticFeedbackRating, string> = {
  accurate: "准确", noise: "没意义", incorrect: "内容错误",
  wrong_project: "项目错误", missing: "有遗漏",
};

function StorylineFeedback({ projectId, text, evidence, sourceDates, projects }: {
  projectId: string; text: string; evidence: string[]; sourceDates: string[]; projects: IntelligencePulse[];
}) {
  const itemId = `storyline:${projectId}`;
  const dates = [...new Set([...sourceDates, ...evidence.map((value) => value.match(/(?:report:)?(\d{4}-\d{2}-\d{2})/)?.[1])]
    .filter((value): value is string => Boolean(value)))];
  const [comment, setComment] = useState("");
  const [correctedProject, setCorrectedProject] = useState("");
  const feedback = useQuery({
    queryKey: ["semantic-feedback", "storyline", projectId],
    queryFn: () => getSemanticFeedback("storyline", projectId), staleTime: 30_000,
  });
  const current = feedback.data?.items.find((item) => item.item_id === itemId);
  const mutation = useMutation({
    mutationFn: (rating: SemanticFeedbackRating) => recordSemanticFeedback({
      view: "storyline", item_id: itemId, project_id: projectId, rating, text,
      source_dates: dates, comment: comment.trim() || undefined,
      corrected_project_id: rating === "wrong_project" ? correctedProject : undefined,
    }),
    onSuccess: () => { setComment(""); void feedback.refetch(); },
  });
  return (
    <details className="mt-5 rounded-lg border border-line bg-page/25 px-3 py-3">
      <summary className="cursor-pointer text-xs text-ink3">这段总结对吗？{current ? ` · 已标记“${FEEDBACK_LABEL[current.rating]}”` : ""}</summary>
      <div className="mt-3 flex flex-wrap gap-2">
        {(["accurate", "noise", "incorrect", "missing"] as SemanticFeedbackRating[]).map((rating) => (
          <button key={rating} disabled={mutation.isPending} onClick={() => mutation.mutate(rating)}
            className={`rounded border px-2.5 py-1 text-xs ${current?.rating === rating ? "border-primary bg-primary/10 text-primary" : "border-line text-ink2"}`}>
            {FEEDBACK_LABEL[rating]}
          </button>
        ))}
      </div>
      <textarea value={comment} onChange={(event) => setComment(event.target.value)} rows={2}
        placeholder="可选：指出具体哪句不对或漏了什么。反馈只用于下一次离线审计。"
        className="mt-3 w-full rounded-md border border-line bg-card px-3 py-2 text-xs text-ink outline-none focus:border-primary" />
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <select value={correctedProject} onChange={(event) => setCorrectedProject(event.target.value)}
          className="rounded border border-line bg-card px-2 py-1 text-xs text-ink2">
          <option value="">如归错项目，选择正确项目</option>
          {projects.filter((item) => item.project_id !== projectId).map((item) => <option key={item.project_id} value={item.project_id}>{item.name}</option>)}
        </select>
        <button disabled={!correctedProject || mutation.isPending} onClick={() => mutation.mutate("wrong_project")}
          className="rounded border border-warning/40 px-2.5 py-1 text-xs text-warning disabled:opacity-40">项目错误</button>
        {mutation.isError && <span className="text-xs text-critical">保存失败，请稍后重试</span>}
        {mutation.isSuccess && <span className="text-xs text-passed">已保存，下次审计会只重算相关日报</span>}
      </div>
    </details>
  );
}

function PulseCard({ pulse, selected, onClick }: { pulse: IntelligencePulse; selected: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick} className={`min-w-0 rounded-xl border px-4 py-3 text-left transition-colors ${selected ? "border-primary/60 bg-primary/10" : "border-line bg-card hover:border-primary/30"}`}>
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-sm font-semibold text-ink">{pulse.name}</span>
        <span className={`text-xs ${LIFECYCLE_COLOR[pulse.status] ?? "text-passed"}`}>
          {pulse.status === "active" ? pulse.phase : (LIFECYCLE_LABEL[pulse.status] ?? pulse.status)}
        </span>
      </div>
      <p className="mt-2 min-h-10 text-xs leading-5 text-ink2">{compact(pulse.latest_result ?? "最新日报还没有明确结果", 88)}</p>
      <div className="mt-3 grid grid-cols-3 gap-2 text-[10px] text-ink3">
        <span>未知 <b className="text-ink2">{pulse.open_unknowns}</b></span>
        <span>结果 <b className="text-ink2">{pulse.result_items}</b></span>
        <span>{pulse.last_meaningful.slice(5)}</span>
      </div>
      <div className="mt-2 flex items-center justify-between"><span className="text-[10px] text-ink3">{fmtTokens(pulse.tokens)} Token</span><SourceTag mode={pulse.source_mode} /></div>
    </button>
  );
}

function DeltaColumn({ title, marker, tone, items }: { title: string; marker: string; tone: string; items: IntelligenceDeltaItem[] }) {
  return (
    <div className="rounded-lg border border-line bg-page/25 px-3 py-3">
      <div className="text-xs font-medium" style={{ color: tone }}>{marker} {title} · {items.length}</div>
      {items.length === 0 ? <p className="mt-2 text-[11px] text-ink3">这一期间没有记录</p> : (
        <ul className="mt-2 space-y-2">{items.slice(0, 5).map((item, index) => <li key={`${item.date}-${index}`} className="text-[11px] leading-4 text-ink2"><span className="mr-1 text-ink3">{item.date.slice(5)}</span>{compact(item.text, 105)}</li>)}</ul>
      )}
    </div>
  );
}

function EffortProgress({ data, selected }: { data: ProjectIntelligenceResponse; selected: string }) {
  const points = data.effort_progress.filter((item) => item.project_id !== "unassigned").map((item) => ({
    value: [Math.max(1, item.tokens), item.progress_items], item,
    symbolSize: item.project_id === selected ? 18 : 11,
    itemStyle: { color: item.quadrant === "attention_needed" ? C.warning : item.project_id === selected ? C.primary : C.passed,
                 borderColor: C.card, borderWidth: 2 },
  }));
  return (
    <Card title="投入 vs 有效进展" subtitle="横轴是项目归属 Agent Token（含缓存、对数刻度）；纵轴是有明确日报依据的进展条数">
      {!points.length ? <EmptyState text="还没有可比较的项目投入" /> : <Chart height={390} option={{
        ...baseChartOption(),
        grid: { left: 15, right: 24, top: 18, bottom: 12, containLabel: true },
        tooltip: { ...tooltipBase, formatter: (raw: unknown) => {
          const item = (raw as { data?: { item?: typeof data.effort_progress[number] } }).data?.item;
          return item ? `<b>${item.name}</b><br/>Token：${fmtTokens(item.tokens)}<br/>有效进展：${item.progress_items}<br/>结果事项：${item.result_items}<br/>计划闭环：${item.completed_plans}<br/>关键转折：${item.breakthroughs}<br/>${QUADRANT_LABEL[item.quadrant]}` : "";
        } },
        xAxis: { type: "log", min: 1, ...axisBase(), name: "Agent Token（含缓存）", nameTextStyle: { color: C.ink3 }, axisLabel: { color: C.ink2, formatter: (value: number) => fmtTokens(value) } },
        yAxis: { type: "value", minInterval: 1, ...axisBase(), name: "有效进展条数", nameTextStyle: { color: C.ink3 } },
        series: [{ type: "scatter", data: points, label: { show: true, position: "top", color: C.ink2, fontSize: 10,
          formatter: (raw: unknown) => (raw as { data?: { item?: { name?: string } } }).data?.item?.name ?? "" } }],
      }} />}
      <p className="mt-2 text-[10px] leading-4 text-ink3">有效进展＝带结果事项＋完成的计划闭环＋关键转折＋关闭的未知问题。它是日报记录密度，不是绩效评分。</p>
    </Card>
  );
}

function IntelligenceContent({ data, project, setProject, baseline, setBaseline }: {
  data: ProjectIntelligenceResponse; project: string; setProject: (value: string) => void;
  baseline: string; setBaseline: (value: string) => void;
}) {
  const active = data.project_details[project] ? project : (data.pulses[0]?.project_id ?? "");
  useEffect(() => { if (active && active !== project) setProject(active); }, [active, project, setProject]);
  const detail = data.project_details[active];
  const pulse = data.pulses.find((item) => item.project_id === active);
  if (!detail || !pulse) return <EmptyState text="暂无项目情报" />;
  return (
    <div className="space-y-4">
      <Card title="Project Pulse" subtitle="最近活跃的六个项目；卡片只压缩正式日报中的结果、未知和投入">
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">{data.pulses.slice(0, 6).map((item) => <PulseCard key={item.project_id} pulse={item} selected={item.project_id === active} onClick={() => setProject(item.project_id)} />)}</div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-[1.25fr_.75fr]">
        <Card title="Since Last Visit" subtitle={`${detail.delta.from || "—"} → ${detail.delta.to || "—"} · 只展示这段时间新增的正式日报内容`}>
          <div className="mb-3 flex justify-end">
            <select value={baseline || data.baseline_date || ""} onChange={(event) => setBaseline(event.target.value)} className="rounded-md border border-line bg-page px-3 py-1.5 text-xs text-ink">
              {data.available_dates.map((value) => <option key={value} value={value}>从 {value} 开始{value === data.latest_report_date ? "（当前无新增）" : ""}</option>)}
            </select>
          </div>
          {detail.delta.change_count === 0 ? <EmptyState text="上次访问后没有新的项目日报变化" detail="可切换更早的对比日期。" /> : <div className="grid gap-3 sm:grid-cols-2">
            <DeltaColumn title="新增结果" marker="+" tone={C.passed} items={detail.delta.results} />
            <DeltaColumn title="新增知识" marker="+" tone={C.primary} items={detail.delta.knowledge} />
            <DeltaColumn title="新增阻塞记录" marker="!" tone={C.critical} items={detail.delta.blockers} />
            <DeltaColumn title="计划闭环" marker="✓" tone={C.warning} items={detail.delta.plan_closure} />
            <DeltaColumn title="新增未知" marker="?" tone={C.warning} items={detail.delta.unknowns_opened} />
            <DeltaColumn title="关闭未知" marker="✓" tone={C.passed} items={detail.delta.unknowns_resolved} />
            <DeltaColumn title="阻塞开启" marker="!" tone={C.critical} items={detail.delta.blockers_opened} />
            <DeltaColumn title="阻塞解除" marker="✓" tone={C.passed} items={detail.delta.blockers_resolved} />
          </div>}
        </Card>

        <Card title="Open Unknowns" subtitle={`按优先级展示最多 8 条当前问题${detail.hidden_unknown_count ? ` · 另有 ${detail.hidden_unknown_count} 条当前问题` : ""}${detail.stale_unknown_count ? ` · ${detail.stale_unknown_count} 条已陈旧` : ""}`}>
          {!detail.unknowns.length ? <EmptyState text="最新日报没有保留开放未知" /> : <div className="space-y-3">{detail.unknowns.map((item) => <article key={item.unknown_id} className="rounded-lg border border-line bg-page/25 px-3 py-3">
            <div className="flex items-center justify-between"><span className="text-[10px] font-medium" style={{ color: PRIORITY_COLOR[item.priority] }}>{PRIORITY_LABEL[item.priority] ?? item.priority}优先级</span><SourceTag mode={item.source_mode} /></div>
            <p className="mt-2 text-xs leading-5 text-ink">{item.question}</p>
            <p className="mt-2 text-[10px] leading-4 text-ink3">缺失证据：{item.missing_evidence || "日报未明确说明"}</p>
          </article>)}</div>}
        </Card>
      </div>

      <EffortProgress data={data} selected={active} />

      <div className="grid gap-4 xl:grid-cols-[.9fr_1.1fr]">
        <Card title="Breakthrough Timeline" subtitle="只保留指标变化、验证推进、结论修正和方向转折">
          {!detail.breakthroughs.length ? <EmptyState text="暂时没有达到门槛的关键转折" /> : <div className="relative ml-2 border-l border-line pl-5">{[...detail.breakthroughs].reverse().map((item, index) => <article key={`${item.date}-${index}`} className="relative pb-5 last:pb-0">
            <span className="absolute -left-[27px] top-1 h-3 w-3 rounded-full border-2 border-card bg-primary" />
            <div className="flex items-center justify-between gap-2"><span className="text-[10px] text-ink3">{item.date}</span><SourceTag mode={item.source_mode} /></div>
            <h4 className="mt-1 text-xs font-medium text-ink">{item.title}</h4>
            <p className="mt-1 text-xs leading-5 text-ink2">{item.change}</p>
            <p className="mt-1 text-[10px] text-ink3">{item.significance}</p>
          </article>)}</div>}
        </Card>

        <Card title="Project Storyline" subtitle="如果三个月没看这个项目，用一分钟恢复问题、转折、当前结果和未知">
          <div className="flex items-center justify-between"><h3 className="text-base font-semibold text-ink">{pulse.name}</h3><SourceTag mode={detail.storyline.source_mode} /></div>
          <p className="mt-4 whitespace-pre-line text-sm leading-7 text-ink2">{detail.storyline.summary}</p>
          <div className="mt-5 grid gap-3 sm:grid-cols-2">
            <div className="rounded-lg border border-critical/20 bg-critical/5 px-3 py-3"><div className="text-[10px] text-critical">当前阻塞</div><p className="mt-1 text-xs leading-5 text-ink2">{pulse.current_blocker ?? "最新相关日报没有明确阻塞"}</p></div>
            <div className="rounded-lg border border-warning/20 bg-warning/5 px-3 py-3"><div className="text-[10px] text-warning">下一证据</div><p className="mt-1 text-xs leading-5 text-ink2">{pulse.next_action ?? detail.unknowns[0]?.missing_evidence ?? "最新相关日报没有明确下一步"}</p></div>
          </div>
          <StorylineFeedback projectId={active} text={detail.storyline.summary}
            evidence={detail.storyline.evidence} sourceDates={detail.storyline.source_dates ?? []}
            projects={data.pulses} />
        </Card>
      </div>

      {data.data_quality.length > 0 && <div className="rounded-lg border border-warning/20 bg-warning/5 px-4 py-3 text-xs leading-5 text-ink2"><span className="font-medium text-warning">数据说明：</span>{data.data_quality.join("；")}</div>}
    </div>
  );
}

export function ProjectIntelligence() {
  const [days, setDays] = useState(90);
  const [project, setProject] = useState("");
  const [baseline, setBaseline] = useState(() => window.localStorage.getItem(LAST_SEEN_KEY) ?? "");
  const query = useQuery({ queryKey: ["project-intelligence", days, baseline], queryFn: () => getProjectIntelligence(days, baseline || undefined), refetchInterval: 5 * 60_000 });
  useEffect(() => {
    if (query.data?.latest_report_date) window.localStorage.setItem(LAST_SEEN_KEY, query.data.latest_report_date);
  }, [query.data?.latest_report_date]);
  const options = useMemo(() => query.data?.pulses ?? [], [query.data?.pulses]);
  return (
    <div className="space-y-4">
      <PageHeader title="项目情报" description="30 秒回答：项目怎么了、哪里变了、当前不知道什么、投入是否换来了可见进展" right={<div className="flex gap-2">
        <select value={project} onChange={(event) => setProject(event.target.value)} className="rounded-md border border-line bg-card px-3 py-1.5 text-sm text-ink">{options.map((item) => <option key={item.project_id} value={item.project_id}>{item.name} · {item.last_meaningful.slice(5)}</option>)}</select>
        <select value={days} onChange={(event) => setDays(Number(event.target.value))} className="rounded-md border border-line bg-card px-3 py-1.5 text-sm text-ink"><option value={30}>30 天</option><option value={90}>90 天</option><option value={180}>180 天</option><option value={365}>一年</option></select>
      </div>} />
      <div className="rounded-lg border border-primary/20 bg-primary/5 px-4 py-3 text-xs leading-5 text-ink2">自然语言只来自正式日报及其审计结果；Token 只用于投入量级。切换项目不会调用模型。</div>
      {query.data?.audit_coverage && <div className="rounded-lg border border-line bg-card px-4 py-3 text-xs text-ink2">
        审计覆盖：<b className="text-ink">{query.data.audit_coverage.audited_count}/{query.data.audit_coverage.report_count}</b> 份日报
        {query.data.audit_coverage.fallback_count > 0 && <span className="ml-2 text-warning">· {query.data.audit_coverage.fallback_count} 份仍为保守回退</span>}
        {query.data.audit_coverage.stale_last_good_count > 0 && <span className="ml-2 text-warning">· {query.data.audit_coverage.stale_last_good_count} 份保留上次可信版本</span>}
        {query.data.audit_coverage.last_audited_date && <span className="ml-2 text-ink3">· 最近审计 {query.data.audit_coverage.last_audited_date}</span>}
      </div>}
      <QueryBoundary query={query} isEmpty={(data) => data.pulses.length === 0} emptyText="这段时间还没有可生成情报的正式日报">
        {(data) => <IntelligenceContent data={data} project={project} setProject={setProject} baseline={baseline} setBaseline={setBaseline} />}
      </QueryBoundary>
    </div>
  );
}
