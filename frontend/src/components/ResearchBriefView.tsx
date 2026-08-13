import { useState } from "react";
import type { ProjectResearchBrief } from "../lib/types";
import { Card } from "./ui";

const TABS = [
  ["models", "模型结构"],
  ["experiments", "实验脉络"],
  ["insights", "研究启发"],
  ["future", "未来方向"],
] as const;

function priorityTone(priority: string) {
  return priority === "P0" ? "border-critical/35 bg-critical/8 text-critical"
    : priority === "P1" ? "border-warning/35 bg-warning/8 text-warning"
      : "border-primary/35 bg-primary/8 text-primary";
}

function Models({ brief }: { brief: ProjectResearchBrief }) {
  const [selected, setSelected] = useState(brief.models[0]?.id ?? "");
  const model = brief.models.find((item) => item.id === selected) ?? brief.models[0];
  if (!model) return null;
  return <div className="space-y-4">
    <div className="flex flex-wrap gap-2">
      {brief.models.map((item) => <button key={item.id} onClick={() => setSelected(item.id)}
        className={`rounded-lg border px-3 py-2 text-left transition ${item.id === model.id ? "border-primary/50 bg-primary/10" : "border-line bg-page/30 hover:border-primary/30"}`}>
        <div className="text-xs font-medium text-ink">{item.name}</div>
        <div className="mt-1 text-[10px] text-ink3">{item.variant}</div>
      </button>)}
    </div>
    <div className="rounded-xl border border-primary/25 bg-gradient-to-r from-primary/10 via-card to-card px-4 py-4">
      <div className="text-[10px] uppercase tracking-[.18em] text-primary">{model.variant}</div>
      <h3 className="mt-1 text-lg font-semibold text-ink">{model.name}</h3>
      <p className="mt-2 text-xs leading-6 text-ink2">{model.summary}</p>
      <p className="mt-2 text-[10px] leading-5 text-ink3">作用：{model.role}</p>
      <div className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
        {model.specs.map((spec) => <div key={spec.label} className="rounded-lg border border-line bg-page/35 px-3 py-2">
          <div className="text-[9px] uppercase tracking-wider text-ink3">{spec.label}</div>
          <div className="mt-1 text-xs font-medium text-ink">{spec.value}</div>
        </div>)}
      </div>
    </div>
    <div className="overflow-x-auto pb-2">
      <div className="flex min-w-max items-stretch gap-2">
        {model.stages.map((stage, index) => <div key={`${model.id}-${stage.name}`} className="flex items-center gap-2">
          <article className="w-56 rounded-xl border border-primary/20 bg-page/30 px-3 py-3">
            <div className="text-[9px] uppercase tracking-wider text-primary">{stage.kind}</div>
            <h4 className="mt-1 text-xs font-medium text-ink">{stage.name}</h4>
            <p className="mt-2 text-[10px] leading-5 text-ink2">{stage.role}</p>
            <p className="mt-2 border-t border-line/60 pt-2 text-[9px] leading-4 text-ink3">{stage.detail}</p>
          </article>
          {index < model.stages.length - 1 && <span className="text-lg text-primary/70">→</span>}
        </div>)}
      </div>
    </div>
    <div className="grid gap-3 lg:grid-cols-3">
      {brief.metric_lanes.map((lane) => <article key={lane.level} className={`rounded-xl border px-3 py-3 ${lane.tone === "good" ? "border-passed/25 bg-passed/5" : lane.tone === "warning" ? "border-warning/25 bg-warning/5" : "border-primary/25 bg-primary/5"}`}>
        <div className="text-[10px] font-medium text-ink">{lane.label}</div>
        <div className="mt-3 space-y-3">{lane.values.map((value) => <div key={value.name}>
          <div className="flex items-baseline justify-between gap-3"><span className="text-[10px] text-ink3">{value.name}</span><strong className="text-sm text-ink">{value.value}</strong></div>
          <p className="mt-1 text-[9px] leading-4 text-ink3">{value.note}</p>
        </div>)}</div>
      </article>)}
    </div>
  </div>;
}

function Experiments({ brief }: { brief: ProjectResearchBrief }) {
  return <div className="relative space-y-3 before:absolute before:bottom-4 before:left-[7px] before:top-4 before:w-px before:bg-primary/25">
    {brief.experiment_phases.map((phase, index) => <article key={phase.period} className="relative pl-7">
      <span className="absolute left-0 top-4 z-10 h-[15px] w-[15px] rounded-full border-4 border-card bg-primary" />
      <div className="rounded-xl border border-line bg-page/25 px-4 py-3">
        <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-sm font-medium text-ink">{index + 1}. {phase.title}</h3><span className="font-mono text-[10px] text-primary">{phase.period}</span></div>
        <p className="mt-2 text-[11px] text-ink2">研究问题：{phase.question}</p>
        <ul className="mt-3 space-y-1 text-[10px] leading-5 text-ink3">{phase.experiments.map((item) => <li key={item}>· {item}</li>)}</ul>
        <div className="mt-3 rounded-lg border border-passed/20 bg-passed/5 px-3 py-2 text-[10px] leading-5 text-ink2"><span className="font-medium text-passed">阶段结论：</span>{phase.takeaway}</div>
      </div>
    </article>)}
  </div>;
}

function Insights({ brief }: { brief: ProjectResearchBrief }) {
  return <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">{brief.insights.map((item, index) => <article key={item.title} className="rounded-xl border border-line bg-page/25 px-4 py-4">
    <div className="text-[9px] font-mono text-primary">INSIGHT {String(index + 1).padStart(2, "0")}</div>
    <h3 className="mt-2 text-sm font-medium text-ink">{item.title}</h3>
    <p className="mt-3 text-[10px] leading-5 text-ink3">观察：{item.observation}</p>
    <p className="mt-2 rounded-lg border border-primary/15 bg-primary/5 px-3 py-2 text-[10px] leading-5 text-ink2">启发：{item.implication}</p>
  </article>)}</div>;
}

function Future({ brief }: { brief: ProjectResearchBrief }) {
  return <div className="space-y-3">{brief.future_directions.map((item, index) => <article key={item.title} className="rounded-xl border border-line bg-page/25 px-4 py-4">
    <div className="flex items-start gap-3">
      <span className={`rounded border px-2 py-1 font-mono text-[10px] ${priorityTone(item.priority)}`}>{item.priority}</span>
      <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-sm font-medium text-ink">{index + 1}. {item.title}</h3></div>
        <p className="mt-2 text-[11px] leading-5 text-ink2">假设：{item.hypothesis}</p>
        <div className="mt-3 grid gap-2 lg:grid-cols-2">
          <div className="rounded-lg border border-line bg-card/50 px-3 py-2"><div className="text-[9px] uppercase tracking-wider text-ink3">最小实验</div><p className="mt-1 text-[10px] leading-5 text-ink2">{item.smallest_experiment}</p></div>
          <div className="rounded-lg border border-passed/20 bg-passed/5 px-3 py-2"><div className="text-[9px] uppercase tracking-wider text-passed">晋级条件</div><p className="mt-1 text-[10px] leading-5 text-ink2">{item.promotion_gate}</p></div>
        </div>
      </div>
    </div>
  </article>)}</div>;
}

export function ResearchBriefView({ brief }: { brief: ProjectResearchBrief }) {
  const [tab, setTab] = useState<(typeof TABS)[number][0]>("models");
  return <Card title={brief.title} subtitle={`人工复核研究视图 · ${brief.reviewed_at}`}>
    <p className="text-xs leading-6 text-ink2">{brief.overview}</p>
    <p className="mt-2 text-[10px] leading-5 text-ink3">证据说明：{brief.evidence_note}</p>
    <div className="my-4 flex flex-wrap gap-2 border-b border-line pb-3">
      {TABS.map(([key, label]) => <button key={key} onClick={() => setTab(key)} className={`rounded-lg border px-3 py-2 text-xs transition ${tab === key ? "border-primary/50 bg-primary/10 text-primary" : "border-line bg-page/30 text-ink3 hover:text-ink"}`}>{label}</button>)}
    </div>
    {tab === "models" && <Models brief={brief} />}
    {tab === "experiments" && <Experiments brief={brief} />}
    {tab === "insights" && <Insights brief={brief} />}
    {tab === "future" && <Future brief={brief} />}
  </Card>;
}
