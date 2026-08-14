import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { EChartsOption } from "echarts";
import type { ECElementEvent } from "echarts/core";
import { Chart } from "../components/GraphChart";
import { ResearchBriefView } from "../components/ResearchBriefView";
import { Card, EmptyState, PageHeader, QueryBoundary, StatCard } from "../components/ui";
import { getAlgorithmArchitecture, getAlgorithmArchitectureIndex } from "../lib/api";
import { C, tooltipBase } from "../lib/chartTheme";
import type {
  AlgorithmArchitectureDetail,
  AlgorithmArchitectureIndex,
  AlgorithmGroundedItem,
  AlgorithmModel,
  AlgorithmPipelineNode,
} from "../lib/types";

const CATEGORY: Record<string, { label: string; color: string }> = {
  input: { label: "输入", color: "#64748b" },
  preprocess: { label: "预处理", color: "#38bdf8" },
  router: { label: "路由", color: "#a78bfa" },
  model: { label: "模型", color: "#22d3ee" },
  fusion: { label: "融合", color: "#fb7185" },
  postprocess: { label: "后处理", color: "#f59e0b" },
  decision: { label: "判定", color: "#facc15" },
  output: { label: "输出", color: "#34d399" },
};

const STATUS: Record<string, string> = {
  current: "当前采用", candidate: "候选", optional: "可选", legacy: "旧方案",
  rejected: "已拒绝", unknown: "未确认", adopted: "已采用", conditional: "条件采用",
  superseded: "已替代",
};

const ARCH_STATUS: Record<string, string> = {
  verified: "结构已核验", partial: "部分可解释", opaque: "内部证据不足",
};

const ARCH_BASIS: Record<string, string> = {
  deployment_evidence: "本地部署实证",
  family_reference: "仅官方家族参考",
  mixed: "本地采用 + 官方家族参考",
  undisclosed: "官方未披露内部结构",
};

const EvidenceContext = createContext<{
  catalog: NonNullable<AlgorithmArchitectureDetail["snapshot"]["evidence_catalog"]>;
  select: (ref: string) => void;
} | null>(null);

function compact(value: string | null | undefined, limit = 165) {
  const text = String(value ?? "").replace(/\s+/g, " ").trim();
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
}

function evidenceLabel(ref: string) {
  const external = /^external:([^:]+):F(\d+)$/.exec(ref);
  if (external) return `官方公开参考 · ${external[1]} · #${external[2]}`;
  const report = /^report:(\d{4}-\d{2}-\d{2}):L(\d+)-L(\d+)$/.exec(ref);
  if (report) return `日报 ${report[1]} · L${report[2]}–${report[3]}`;
  const source = /^source:([^:]+):(.+):L(\d+)-L(\d+)$/.exec(ref);
  if (!source) return ref;
  const file = source[2].split("/").pop() ?? source[2];
  return `${source[1] === "repo" ? "当前仓库" : source[1]} · ${file} · L${source[3]}–${source[4]}`;
}

function Evidence({ refs, limit = 3 }: { refs: string[]; limit?: number }) {
  const context = useContext(EvidenceContext);
  if (!refs.length) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {refs.slice(0, limit).map((ref) => (
        <button key={ref} title={ref} disabled={!context?.catalog[ref]} onClick={() => context?.select(ref)}
          className={`max-w-full truncate rounded border px-1.5 py-0.5 font-mono text-[9px] enabled:hover:border-primary/50 enabled:hover:text-primary disabled:cursor-default ${context?.catalog[ref]?.kind === "external" ? "border-sky-400/30 bg-sky-400/5 text-sky-300" : "border-line bg-page/60 text-ink3"}`}>
          {evidenceLabel(ref)}
        </button>
      ))}
      {refs.length > limit && <span className="px-1 py-0.5 text-[9px] text-ink3">+{refs.length - limit} 条</span>}
    </div>
  );
}

function StatusPill({ status }: { status: string }) {
  const tone = status === "current" || status === "adopted" ? "border-passed/30 text-passed"
    : status === "rejected" || status === "legacy" ? "border-critical/30 text-critical"
      : "border-warning/30 text-warning";
  return <span className={`rounded-full border px-2 py-0.5 text-[10px] ${tone}`}>{STATUS[status] ?? status}</span>;
}

function ProjectRail({ index, project, onProject }: {
  index: AlgorithmArchitectureIndex; project: string; onProject: (id: string) => void;
}) {
  return (
    <Card title="项目算法快照" subtitle={`${index.counts.ready}/${index.counts.total} 个项目已生成`} pad={false}>
      <div className="max-h-[640px] overflow-y-auto p-2">
        {index.projects.map((item) => (
          <button key={item.project_id} onClick={() => onProject(item.project_id)}
            className={`mb-1 w-full rounded-md border px-3 py-2.5 text-left transition-colors ${project === item.project_id ? "border-primary/50 bg-primary/10" : "border-transparent hover:border-line hover:bg-cardhover"}`}>
            <div className="flex items-center justify-between gap-2">
              <span className="truncate text-xs font-medium text-ink">{item.name}</span>
              <span className={`h-2 w-2 shrink-0 rounded-full ${item.status === "ready" ? "bg-passed" : item.status === "analysis_failed" ? "bg-critical" : "bg-ink3"}`} />
            </div>
            <p className="mt-1 truncate text-[10px] text-ink3">
              {item.models.length ? `${item.models.length} 个模型 · ${item.models.map((model) => model.name).join(" / ")}` : item.status === "not_analyzed" ? "等待首次分析" : "工作流 / 证据不足"}
            </p>
          </button>
        ))}
      </div>
    </Card>
  );
}

function PipelineGraph({ detail, selectedNode, onNode }: {
  detail: AlgorithmArchitectureDetail; selectedNode: string; onNode: (id: string) => void;
}) {
  const option = useMemo<EChartsOption>(() => {
    const snapshot = detail.snapshot;
    const categories = Object.entries(CATEGORY).map(([name, item]) => ({ name, itemStyle: { color: item.color } }));
    const nodes = snapshot.pipeline.nodes.map((node) => ({
      id: node.id, name: node.label, category: node.category,
      symbolSize: node.id === selectedNode ? 58 : node.category === "model" ? 48 : 38,
      value: node.summary,
      itemStyle: {
        color: CATEGORY[node.category]?.color ?? C.neutral,
        opacity: node.status === "rejected" || node.status === "legacy" ? 0.38 : node.status === "candidate" ? 0.72 : 1,
        borderColor: node.id === selectedNode ? "#ffffff" : C.card,
        borderWidth: node.id === selectedNode ? 3 : 2,
      },
      label: { show: true, color: C.ink, fontSize: 10, width: 90, overflow: "truncate" as const },
    }));
    return {
      backgroundColor: "transparent",
      tooltip: { ...tooltipBase, formatter: (raw: unknown) => {
        const data = (raw as { data?: { name?: string; value?: string; label?: string; data?: string } }).data;
        if (!data) return "";
        return `<b>${data.name ?? data.label ?? ""}</b><br/>${compact(data.value ?? data.data ?? "", 220)}`;
      } },
      legend: [{ data: categories.map((item) => item.name), bottom: 0, textStyle: { color: C.ink3, fontSize: 9 },
        formatter: (name: string) => CATEGORY[name]?.label ?? name }],
      animationDurationUpdate: 550,
      series: [{
        type: "graph" as const, layout: "force", roam: true, draggable: true, focusNodeAdjacency: true,
        categories, data: nodes,
        links: snapshot.pipeline.edges.map((edge) => ({ source: edge.source, target: edge.target,
          label: { show: true, formatter: edge.label, color: C.ink3, fontSize: 8 },
          lineStyle: { color: C.line, width: 1.5, curveness: 0.08 } })),
        force: { repulsion: 280, gravity: 0.08, edgeLength: [80, 175], layoutAnimation: true },
        edgeSymbol: ["none", "arrow"], edgeSymbolSize: [0, 7],
        edgeLabel: { show: true, color: C.ink3, fontSize: 8 },
        emphasis: { focus: "adjacency", lineStyle: { width: 3, color: C.primary } },
      }],
    };
  }, [detail, selectedNode]);
  const click = (params: ECElementEvent) => {
    const data = params.data as { id?: string } | undefined;
    if (params.dataType === "node" && data?.id) onNode(data.id);
  };
  return <Chart height={520} option={option} onClick={click} />;
}

function NodeInspector({ node, model }: { node?: AlgorithmPipelineNode; model?: AlgorithmModel }) {
  if (!node) return <EmptyState text="点击图中的节点查看解释" />;
  return (
    <div data-testid="node-inspector">
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded px-2 py-1 text-[10px] text-white" style={{ background: CATEGORY[node.category]?.color }}>{CATEGORY[node.category]?.label ?? node.category}</span>
        <StatusPill status={node.status} />
      </div>
      <h3 className="mt-3 text-base font-semibold text-ink">{node.label}</h3>
      <p className="mt-2 text-xs leading-6 text-ink2">{node.summary}</p>
      {model && <div className="mt-4 rounded-lg border border-primary/20 bg-primary/5 px-3 py-3">
        <div className="text-[10px] uppercase tracking-wider text-primary">模型结构</div>
        <div className="mt-1 text-sm font-medium text-ink">{model.name}</div>
        <p className="mt-1 text-[11px] leading-5 text-ink2">{model.variant}</p>
        <p className="mt-2 text-[10px] text-ink3">{ARCH_STATUS[model.architecture_status]} · {ARCH_BASIS[model.architecture_basis ?? "deployment_evidence"]}</p>
      </div>}
      <Evidence refs={node.evidence} />
    </div>
  );
}

function ModelAnatomy({ model }: { model: AlgorithmModel }) {
  return (
    <Card title={`${model.name} · 模型内部结构`} subtitle={`${ARCH_STATUS[model.architecture_status]} · ${ARCH_BASIS[model.architecture_basis ?? "deployment_evidence"]} · ${model.variant || "变体未记录"}`}
      right={<StatusPill status={model.status} />}>
      <p className="text-xs leading-6 text-ink2">{model.architecture_summary || "当前证据只能确认模型用途，内部结构尚未核验。"}</p>
      {(model.architecture_basis === "mixed" || model.architecture_basis === "family_reference") && <div className="mt-3 rounded-lg border border-sky-400/20 bg-sky-400/5 px-3 py-2 text-[10px] leading-5 text-ink3">蓝色引用来自官方公开资料，只解释模型家族骨架；本地 checkpoint、量化方式和实际效果仍以项目证据为准。</div>}
      {model.architecture_basis === "undisclosed" && <div className="mt-3 rounded-lg border border-warning/20 bg-warning/5 px-3 py-2 text-[10px] leading-5 text-ink3">官方只披露了接口或能力，没有公开足够的内部网络结构，系统不会自行补画。</div>}
      <div className="mt-4 grid gap-2 sm:grid-cols-[1fr_auto_1fr]">
        <div className="rounded-lg border border-line bg-page/30 px-3 py-2"><div className="text-[9px] uppercase text-ink3">Input</div><p className="mt-1 text-[11px] leading-5 text-ink2">{model.input || "未记录"}</p></div>
        <div className="hidden items-center text-primary sm:flex">→</div>
        <div className="rounded-lg border border-line bg-page/30 px-3 py-2"><div className="text-[9px] uppercase text-ink3">Output</div><p className="mt-1 text-[11px] leading-5 text-ink2">{model.output || "未记录"}</p></div>
      </div>
      {model.blocks.length > 0 ? <div className="mt-5 overflow-x-auto pb-2">
        <div className="flex min-w-max items-stretch gap-2">
          {model.blocks.map((block, index) => <div key={block.id} className="flex items-center gap-2">
            <article className="w-52 rounded-xl border border-primary/25 bg-gradient-to-b from-primary/10 to-page/20 px-3 py-3">
              <div className="text-[9px] uppercase tracking-wider text-primary">{block.type || `Block ${index + 1}`}</div>
              <h4 className="mt-1 text-xs font-medium text-ink">{block.name}</h4>
              <p className="mt-2 text-[10px] leading-4 text-ink2">{block.role}</p>
              {block.details && <p className="mt-2 border-t border-line/60 pt-2 text-[9px] leading-4 text-ink3">{block.details}</p>}
              <Evidence refs={block.evidence} limit={1} />
            </article>
            {index < model.blocks.length - 1 && <span className="text-primary/70">→</span>}
          </div>)}
        </div>
      </div> : <EmptyState text="当前证据不足以展开模型内部模块" detail="系统不会用模型常识补写缺失结构。" />}
      <div className="mt-4 flex flex-wrap gap-2 text-[10px]">
        {model.quantization && <span className="rounded border border-line px-2 py-1 text-ink2">精度 · {model.quantization}</span>}
        {model.parameters && <span className="rounded border border-line px-2 py-1 text-ink2">参数 · {model.parameters}</span>}
        {model.artifact_size && <span className="rounded border border-line px-2 py-1 text-ink2">制品 · {model.artifact_size}</span>}
      </div>
      {model.metrics.length > 0 && <div className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">{model.metrics.map((metric, index) => <div key={`${metric.name}-${index}`} className="rounded-lg border border-passed/20 bg-passed/5 px-3 py-2">
        <div className="text-[9px] text-ink3">{metric.name}</div><div className="mt-1 text-lg font-semibold text-passed">{metric.value}{metric.unit && !metric.value.includes(metric.unit) ? ` ${metric.unit}` : ""}</div><p className="mt-1 text-[9px] leading-4 text-ink3">{metric.scope}</p>
      </div>)}</div>}
      {(model.design_rationale.length > 0 || model.limitations.length > 0) && <div className="mt-4 grid gap-3 md:grid-cols-2">
        <div className="rounded-lg border border-passed/20 bg-passed/5 px-3 py-3"><div className="text-[10px] font-medium text-passed">为什么这样设计</div><ul className="mt-2 space-y-1 text-[11px] leading-5 text-ink2">{model.design_rationale.map((item) => <li key={item}>+ {item}</li>)}</ul></div>
        <div className="rounded-lg border border-warning/20 bg-warning/5 px-3 py-3"><div className="text-[10px] font-medium text-warning">已知边界</div><ul className="mt-2 space-y-1 text-[11px] leading-5 text-ink2">{model.limitations.map((item) => <li key={item}>△ {item}</li>)}</ul></div>
      </div>}
      <Evidence refs={model.evidence} />
    </Card>
  );
}

function GroundedList({ title, subtitle, items, tone = C.primary }: {
  title: string; subtitle: string; items: AlgorithmGroundedItem[]; tone?: string;
}) {
  return <Card title={title} subtitle={subtitle}>{items.length === 0 ? <EmptyState text="当前没有可靠条目" /> : <div className="space-y-3">{items.map((item, index) => <article key={`${item.title ?? item.name ?? item.question}-${index}`} className="rounded-lg border border-line bg-page/25 px-3 py-3">
    <div className="flex items-start justify-between gap-2">
      <h4 className="text-xs font-medium text-ink">{item.title ?? item.name ?? item.question ?? item.after}</h4>
      {item.status && <StatusPill status={item.status} />}
      {item.priority && <span className="text-[10px]" style={{ color: item.priority === "high" ? C.critical : item.priority === "medium" ? C.warning : C.ink3 }}>{item.priority.toUpperCase()}</span>}
    </div>
    {item.before && item.after && <div className="mt-2 grid gap-1 text-[11px] sm:grid-cols-[1fr_auto_1fr]"><span className="rounded bg-critical/5 px-2 py-1.5 text-ink3">{item.before}</span><span className="self-center text-ink3">→</span><span className="rounded bg-passed/5 px-2 py-1.5 text-ink2">{item.after}</span></div>}
    <p className="mt-2 text-[11px] leading-5 text-ink2">{item.rationale ?? item.reason ?? item.detail ?? item.missing_evidence}</p>
    <Evidence refs={item.evidence} limit={2} />
    <span className="mt-2 block h-px w-8" style={{ backgroundColor: tone }} />
  </article>)}</div>}</Card>;
}

function DetailContent({ detail }: { detail: AlgorithmArchitectureDetail }) {
  const snapshot = detail.snapshot;
  const [selectedNode, setSelectedNode] = useState(snapshot.pipeline.nodes[0]?.id ?? "");
  const [selectedModel, setSelectedModel] = useState(snapshot.models[0]?.id ?? "");
  const [selectedEvidence, setSelectedEvidence] = useState("");
  useEffect(() => {
    setSelectedNode(snapshot.models[0]?.node_id ?? snapshot.pipeline.nodes[0]?.id ?? "");
    setSelectedModel(snapshot.models[0]?.id ?? "");
  }, [snapshot.snapshot_id]);
  const node = snapshot.pipeline.nodes.find((item) => item.id === selectedNode);
  const nodeModel = snapshot.models.find((item) => item.node_id === selectedNode);
  const activeModel = snapshot.models.find((item) => item.id === selectedModel) ?? nodeModel ?? snapshot.models[0];
  const chooseNode = (id: string) => {
    setSelectedNode(id);
    const model = snapshot.models.find((item) => item.node_id === id);
    if (model) setSelectedModel(model.id);
  };
  const usage = snapshot.model_run.usage ?? {};
  const evidence = selectedEvidence ? snapshot.evidence_catalog?.[selectedEvidence] : undefined;
  return <EvidenceContext.Provider value={{ catalog: snapshot.evidence_catalog ?? {}, select: setSelectedEvidence }}><div className="space-y-4">
    <div className="rounded-xl border border-primary/25 bg-gradient-to-r from-primary/10 via-card to-card px-5 py-4">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="max-w-4xl"><div className="text-[10px] uppercase tracking-[.2em] text-primary">当前算法 · 证据约束</div><h2 className="mt-2 text-xl font-semibold text-ink">{snapshot.objective || snapshot.project_name}</h2><p className="mt-3 text-sm leading-7 text-ink2">{snapshot.summary}</p></div>
        <div className="text-right text-[10px] leading-5 text-ink3"><div>{new Date(snapshot.generated_at).toLocaleString("zh-CN", { hour12: false })}</div><div>{snapshot.model_run.model ?? "无模型"}</div><div>HEAD {snapshot.source_state.head?.slice(0, 8) ?? "无 Git"}{snapshot.source_state.dirty ? " · dirty" : ""}</div></div>
      </div>
    </div>
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
      <StatCard label="流水线阶段" value={snapshot.pipeline.nodes.length} hint={`${snapshot.pipeline.edges.length} 条数据流`} />
      <StatCard label="模型" value={snapshot.models.length} hint={`${snapshot.evidence_summary.explained_models} 个已展开内部结构`} tone="primary" />
      <StatCard label="已核指标" value={snapshot.evidence_summary.metrics} hint="数字必须出现在引用证据中" tone="good" />
      <StatCard label="证据覆盖" value={`${snapshot.evidence_summary.cited}/${snapshot.evidence_summary.bundled}`} hint="已引用 / 候选片段" />
      <StatCard label="本次分析 Token" value={usage.input_tokens ? `${Math.round((usage.input_tokens + (usage.output_tokens ?? 0)) / 1000)}K` : "—"} hint="只在证据变化后后台刷新" />
    </div>
    {detail.research_brief && <ResearchBriefView brief={detail.research_brief} />}
    <div className="grid gap-4 xl:grid-cols-[1.45fr_.55fr]">
      <Card title="算法数据流" subtitle="拖拽、缩放或点击节点；虚线/半透明表示候选或旧方案"><PipelineGraph detail={detail} selectedNode={selectedNode} onNode={chooseNode} /></Card>
      <Card title="节点解释" subtitle="这个阶段吃什么、做什么、为什么存在"><NodeInspector node={node} model={nodeModel} /></Card>
    </div>
    {snapshot.models.length > 0 && <Card title="模型选择" subtitle="切换查看每个模型的内部结构，而不是只看一个名称"><div className="flex flex-wrap gap-2">{snapshot.models.map((model) => <button key={model.id} onClick={() => { setSelectedModel(model.id); setSelectedNode(model.node_id); }} className={`rounded-lg border px-3 py-2 text-left ${activeModel?.id === model.id ? "border-primary/50 bg-primary/10" : "border-line bg-page/30 hover:border-primary/30"}`}><div className="text-xs font-medium text-ink">{model.name}</div><div className="mt-1 text-[9px] text-ink3">{ARCH_STATUS[model.architecture_status]}</div></button>)}</div></Card>}
    {activeModel && <ModelAnatomy model={activeModel} />}
    <div className="grid gap-4 xl:grid-cols-2">
      <GroundedList title="设计决策" subtitle="当前为什么这样做；状态与依据分开" items={snapshot.design_decisions} tone={C.passed} />
      <GroundedList title="备选与淘汰方案" subtitle="哪些方案仍可选，哪些已经不应默认重做" items={snapshot.alternatives} tone={C.warning} />
      <GroundedList title="Algorithm Diff" subtitle="相较历史认知或旧方案，算法本身发生了什么变化" items={snapshot.algorithm_diff} />
      <GroundedList title="待补证据" subtitle="尚不能确定的问题，以及关闭它需要什么" items={snapshot.open_questions} tone={C.warning} />
    </div>
    {snapshot.warnings.length > 0 && <GroundedList title="口径冲突与风险" subtitle="配置、代码、日报或评测证据彼此不一致；系统保留冲突，不擅自裁决" items={snapshot.warnings} tone={C.critical} />}
    {detail.history.length > 1 && <Card title="架构版本" subtitle="每次证据变化形成新快照；旧版本只用于回看，不会覆盖"><div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">{detail.history.slice(0, 6).map((item, index) => <article key={item.snapshot_id} className={`rounded-lg border px-3 py-2 ${index === 0 ? "border-primary/30 bg-primary/5" : "border-line bg-page/25"}`}><div className="flex items-center justify-between text-[9px] text-ink3"><span>{new Date(item.generated_at).toLocaleString("zh-CN", { hour12: false })}</span><span>{item.head?.slice(0, 8) ?? "无 Git"}</span></div><p className="mt-2 text-[10px] leading-4 text-ink2">{compact(item.summary, 105)}</p></article>)}</div></Card>}
    {snapshot.validation_errors.length > 0 && <div className="rounded-lg border border-warning/20 bg-warning/5 px-4 py-3 text-[10px] leading-5 text-ink3">校验器已丢弃 {snapshot.validation_errors.length} 条不满足引用或数值约束的模型输出。它们不会进入上方架构结论。</div>}
    {evidence && <aside className="fixed bottom-4 right-4 z-40 max-h-[70vh] w-[min(560px,calc(100vw-2rem))] overflow-hidden rounded-xl border border-primary/35 bg-card shadow-2xl shadow-black/40" data-testid="evidence-drawer">
      <header className="flex items-start justify-between gap-3 border-b border-line px-4 py-3"><div><div className="text-[10px] uppercase tracking-wider text-primary">{evidence.kind === "external" ? "官方公开参考" : "项目原始证据"}</div><div className="mt-1 font-mono text-[10px] text-ink3">{evidenceLabel(selectedEvidence)}</div>{evidence.kind === "external" && <div className="mt-1 text-[9px] text-sky-300">仅说明公开家族结构，不代表本地部署已核验</div>}</div><button onClick={() => setSelectedEvidence("")} className="rounded border border-line px-2 py-1 text-xs text-ink2 hover:text-ink">关闭</button></header>
      <pre className="max-h-[55vh] overflow-auto whitespace-pre-wrap break-words px-4 py-3 font-mono text-[10px] leading-5 text-ink2">{evidence.text}</pre>
      <footer className="flex flex-wrap items-center justify-between gap-2 border-t border-line px-4 py-2 font-mono text-[9px] text-ink3"><span>SHA256 {evidence.sha256.slice(0, 16)}… · {evidence.kind === "external" ? `审阅于 ${evidence.retrieved_at ?? "未知日期"}` : "快照生成时已脱敏保存"}</span>{evidence.url && <a href={evidence.url} target="_blank" rel="noreferrer" className="text-sky-300 hover:text-sky-200">打开官方来源 ↗</a>}</footer>
    </aside>}
  </div></EvidenceContext.Provider>;
}

export function AlgorithmArchitecture() {
  const indexQuery = useQuery({ queryKey: ["algorithm-architecture-index"], queryFn: getAlgorithmArchitectureIndex, refetchInterval: 5 * 60_000 });
  const [project, setProject] = useState("");
  const firstReady = indexQuery.data?.projects.find((item) => item.status === "ready")?.project_id ?? indexQuery.data?.projects[0]?.project_id ?? "";
  const active = project || firstReady;
  const activeStatus = indexQuery.data?.projects.find((item) => item.project_id === active)?.status;
  useEffect(() => { if (!project && firstReady) setProject(firstReady); }, [firstReady, project]);
  const detailQuery = useQuery({ queryKey: ["algorithm-architecture", active], queryFn: () => getAlgorithmArchitecture(active), enabled: Boolean(active) && activeStatus !== "not_analyzed", retry: false });
  return <div className="space-y-4">
    <PageHeader title="算法架构" description="从源码、配置、评测、正式日报和经审阅的官方资料提炼：当前怎么设计、模型家族内部是什么、哪些仍未披露" />
    <div className="rounded-lg border border-primary/20 bg-primary/5 px-4 py-3 text-xs leading-5 text-ink2">页面本身不调用大模型。后台只在证据变化后用 Codex 生成候选架构，程序再校验项目归属、引用和指标数字。蓝色引用是官方家族参考，不会冒充你的部署实证；闭源模型没披露就保持“不透明”。</div>
    <QueryBoundary query={indexQuery} isEmpty={(data) => data.projects.length === 0} emptyText="还没有登记项目">
      {(index) => <div className="grid gap-4 lg:grid-cols-[230px_minmax(0,1fr)]"><ProjectRail index={index} project={active} onProject={setProject} /><div className="min-w-0">{activeStatus === "not_analyzed" ? <Card><EmptyState text="该项目等待首次架构分析" detail="夜间增量刷新会自动处理；页面不会为了展示而临时调用模型。" /></Card> : <QueryBoundary query={detailQuery} emptyText="该项目尚未生成算法架构快照">{(detail) => <DetailContent detail={detail} />}</QueryBoundary>}</div></div>}
    </QueryBoundary>
  </div>;
}
