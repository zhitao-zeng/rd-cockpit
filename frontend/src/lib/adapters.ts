// 视图模型适配层 —— 纯函数，把后端 JSON 转成页面视图模型。
// 原则：只做推导/聚合，绝不补造后端没有的数据；缺数据返回 null/空数组。

import type {
  GpuReport,
  MapProject,
  ProjectState,
  ResourceSnapshotPayload,
  StageInfo,
  StatsFacts,
  WhatChanged,
} from "./types";

// ---------- 项目状态推导 ----------

export type ProjectStatus = "done" | "blocked" | "stale" | "active" | "dormant" | "historical";

export const PROJECT_STATUS_LABEL: Record<ProjectStatus, string> = {
  done: "Done",
  blocked: "Blocked",
  stale: "Stale",
  active: "Active",
  dormant: "Dormant",
  historical: "Historical",
};

/** 验证进度 0~1；无阶段配置 → 0 */
export function stageProgress(verification: Record<string, StageInfo>): number {
  const values = Object.values(verification);
  if (values.length === 0) return 0;
  return values.filter((v) => v.status === "passed").length / values.length;
}

/**
 * 状态推导：done(100%) > blocked(有 blocker) > stale(有过期阶段) > active。
 */
export function deriveProjectStatus(state: ProjectState): ProjectStatus {
  if (state.lifecycle_status === "historical") return "historical";
  if (state.lifecycle_status === "dormant") return "dormant";
  const stages = Object.values(state.verification);
  if (stages.length > 0 && stageProgress(state.verification) >= 1) return "done";
  if (state.blockers.length > 0) return "blocked";
  if (stages.some((s) => s.status === "stale")) return "stale";
  return "active";
}

export interface StageRow {
  stage: string;
  status: string;
  reason: string | null;
  staleReason: string | null;
  commit: string | null;
  eventId: string | null;
  verifiedAt: string | null;
}

/** 按 config 顺序展开的验证阶段行（verification 是有序 dict，保持插入序） */
export function stageRows(state: ProjectState): StageRow[] {
  return Object.entries(state.verification).map(([stage, info]) => ({
    stage,
    status: info.status,
    reason: info.reason ?? null,
    staleReason: info.stale_reason ?? null,
    commit: info.commit ?? null,
    eventId: info.event_id ?? null,
    verifiedAt: info.verified_at ?? null,
  }));
}

export interface ProjectView {
  id: string;
  name: string;
  goal: string | null;
  status: ProjectStatus;
  progress: number; // 0~1
  passedStages: number;
  totalStages: number;
  blockerCount: number;
  blockers: string[];
  remaining: string[];
  branch: string | null;
  head: string | null;
  dirty: boolean | null;
  lastActivity: string | null; // ISO
}

export function buildProjectView(state: ProjectState): ProjectView {
  const stages = Object.values(state.verification);
  const last = state.recent_events[state.recent_events.length - 1];
  return {
    id: state.project_id,
    name: state.name,
    goal: state.goal,
    status: deriveProjectStatus(state),
    progress: stageProgress(state.verification),
    passedStages: stages.filter((s) => s.status === "passed").length,
    totalStages: stages.length,
    blockerCount: state.blockers.length,
    blockers: state.blockers,
    remaining: state.remaining,
    branch: state.branch,
    head: state.head,
    dirty: state.dirty,
    lastActivity: last ? last.occurred_at : null,
  };
}

// ---------- 验证漏斗（跨项目聚合） ----------

export interface FunnelStage {
  stage: string;
  passed: number;
  stale: number;
  pending: number;
  total: number;
}

/**
 * 跨项目按阶段聚合；阶段顺序 = 各项目 config 中首次出现的顺序
 * （ProjectState.verification 保持后端 config 的插入序）。
 */
export function buildFunnel(states: ProjectState[]): FunnelStage[] {
  const order: string[] = [];
  const agg = new Map<string, FunnelStage>();
  for (const state of states) {
    for (const [stage, info] of Object.entries(state.verification)) {
      if (!agg.has(stage)) {
        agg.set(stage, { stage, passed: 0, stale: 0, pending: 0, total: 0 });
        order.push(stage);
      }
      const row = agg.get(stage)!;
      row.total += 1;
      if (info.status === "passed") row.passed += 1;
      else if (info.status === "stale") row.stale += 1;
      else row.pending += 1;
    }
  }
  return order.map((stage) => agg.get(stage)!);
}

// ---------- 趋势图 ----------

export interface TrendRow {
  date: string;
  events: number;
  testsPassed: number;
  testsFailed: number;
  experiments: number;
  decisions: number;
  projectCount: number;
}

/** stats.trend → 图表行；空趋势 → 空数组（页面显示 empty state） */
export function buildTrendRows(stats: StatsFacts | null | undefined): TrendRow[] {
  if (!stats) return [];
  return stats.trend.map((t) => ({
    date: t.date,
    events: t.events,
    testsPassed: t.tests_passed,
    testsFailed: t.tests_failed,
    experiments: t.experiments,
    decisions: t.decisions,
    projectCount: t.projects.length,
  }));
}

export interface ProjectActivityBar {
  projectId: string;
  events: number;
  commits: number;
}

/** stats.projects → 项目活动柱状图数据（按 project_id 排序，保持色彩稳定） */
export function buildProjectActivity(stats: StatsFacts | null | undefined): ProjectActivityBar[] {
  if (!stats) return [];
  return Object.entries(stats.projects)
    .map(([projectId, v]) => ({ projectId, events: v.events, commits: v.commits.length }))
    .sort((a, b) => a.projectId.localeCompare(b.projectId));
}

// ---------- 风险分布 ----------

export const RISK_LEVEL_ORDER = ["high", "medium", "low", "unknown"] as const;
export type RiskLevel = (typeof RISK_LEVEL_ORDER)[number];

export interface ProjectRiskRow {
  projectId: string;
  high: number;
  medium: number;
  low: number;
  unknown: number;
}

/** /advanced/map → 每项目各等级风险维度计数（用于堆叠条形图） */
export function buildRiskDistribution(map: MapProject[] | null | undefined): ProjectRiskRow[] {
  if (!map) return [];
  return map.map((p) => {
    const row: ProjectRiskRow = { projectId: p.project_id, high: 0, medium: 0, low: 0, unknown: 0 };
    for (const level of Object.values(p.risk)) {
      if (level === "high" || level === "medium" || level === "low" || level === "unknown") {
        row[level] += 1;
      } else {
        row.unknown += 1;
      }
    }
    return row;
  });
}

/** 单项目 risk dict → 雷达图维度（high=3, medium=2, low=1, unknown=0） */
export function riskScore(level: string): number {
  switch (level) {
    case "high":
      return 3;
    case "medium":
      return 2;
    case "low":
      return 1;
    default:
      return 0;
  }
}

/** Research Map 纵轴：风险维度均分 0~3 */
export function averageRiskScore(risk: Record<string, string>): number {
  const values = Object.values(risk);
  if (values.length === 0) return 0;
  return values.reduce((sum, level) => sum + riskScore(level), 0) / values.length;
}

/** Research Map 横轴阶段：按验证进度推导（后端无现成字段，规则见设计文档） */
export type MapPhase = "探索" | "实现" | "验证" | "交付";

export function phaseFromProgress(progress: number): MapPhase {
  if (progress < 0.25) return "探索";
  if (progress < 0.5) return "实现";
  if (progress < 0.75) return "验证";
  return "交付";
}

export const MAP_PHASE_ORDER: MapPhase[] = ["探索", "实现", "验证", "交付"];

// ---------- GPU 摘要 ----------

export interface GpuSummaryView {
  gpuCount: number;
  avgUtilization: number | null; // 各 GPU 平均利用率的均值（%）
  peakMemoryMb: number | null;
  idleAllocated: number;
  samples: number;
}

export function buildGpuSummary(report: GpuReport | null | undefined): GpuSummaryView | null {
  if (!report || report.gpus.length === 0) return null;
  const utils = report.gpus.map((g) => g.avg_utilization_pct);
  return {
    gpuCount: report.gpus.length,
    avgUtilization: utils.reduce((a, b) => a + b, 0) / utils.length,
    peakMemoryMb: Math.max(...report.gpus.map((g) => g.peak_memory_mb)),
    idleAllocated: report.gpus.reduce((sum, g) => sum + g.idle_allocated_samples, 0),
    samples: report.samples,
  };
}

// ---------- GPU 时序（来自 /insights/changed 的 resource_snapshot payload） ----------

export interface GpuSamplePoint {
  at: string; // ISO
  [gpuKey: string]: number | string | null;
}

export interface GpuSeriesView {
  gpuKeys: string[]; // 例如 ["0","1",...]
  utilization: GpuSamplePoint[]; // 每个采样点一行：{at, "0": 12, "1": 0}
  memory: GpuSamplePoint[];
  containers: Array<Record<string, unknown>>; // 最近一次快照的 Docker 容器
  sampledAt: string | null;
}

function toNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

/**
 * changed.events 中过滤 resource_snapshot，构建每 GPU 利用率/显存时序。
 * 无快照 → 三个数组均为空。
 */
export function buildGpuSeries(changed: WhatChanged | null | undefined): GpuSeriesView {
  const empty: GpuSeriesView = { gpuKeys: [], utilization: [], memory: [], containers: [], sampledAt: null };
  if (!changed) return empty;
  const snapshots = changed.events.filter((e) => e.type === "resource_snapshot");
  if (snapshots.length === 0) return empty;

  const gpuKeys = new Set<string>();
  const parsed = snapshots.map((e) => {
    const payload = e.payload as ResourceSnapshotPayload;
    const at = payload.sampled_at ?? e.occurred_at;
    return { at, payload };
  });
  parsed.sort((a, b) => a.at.localeCompare(b.at));
  for (const { payload } of parsed) {
    for (const gpu of payload.gpus ?? []) gpuKeys.add(String(gpu.index));
  }
  const keys = [...gpuKeys].sort((a, b) => Number(a) - Number(b));

  const utilization: GpuSamplePoint[] = [];
  const memory: GpuSamplePoint[] = [];
  for (const { at, payload } of parsed) {
    const utilRow: GpuSamplePoint = { at };
    const memRow: GpuSamplePoint = { at };
    const byIndex = new Map((payload.gpus ?? []).map((g) => [String(g.index), g]));
    for (const key of keys) {
      const gpu = byIndex.get(key);
      utilRow[key] = gpu ? toNumber(gpu.utilization_pct) : null;
      memRow[key] = gpu ? toNumber(gpu.memory_used_mb) : null;
    }
    utilization.push(utilRow);
    memory.push(memRow);
  }
  const last = parsed[parsed.length - 1];
  return {
    gpuKeys: keys,
    utilization,
    memory,
    containers: last.payload.containers ?? [],
    sampledAt: last.at,
  };
}

// ---------- 事件合并（Timeline 页） ----------

export interface MergedEvent {
  eventId: string;
  occurredAt: string;
  type: string;
  projectId: string | null;
  status: string | null;
  commit?: string | null;
  provenance?: string | null;
  payload?: Record<string, unknown>;
  evidenceIds: string[];
}

/**
 * stats.events（全集但无 payload）与项目 timeline（有 payload/evidence）按 event_id 合并；
 * 时间倒序。payload 缺失时保持 undefined，不伪造。
 */
export function mergeEvents(
  statsEvents: StatsFacts["events"],
  timelines: Array<{ projectId: string; events: import("./types").TimelineEvent[] }>,
): MergedEvent[] {
  const detail = new Map<string, import("./types").TimelineEvent>();
  for (const { events } of timelines) {
    for (const e of events) detail.set(e.event_id, e);
  }
  const merged: MergedEvent[] = statsEvents.map((e) => {
    const d = detail.get(e.event_id);
    return {
      eventId: e.event_id,
      occurredAt: e.occurred_at,
      type: e.type,
      projectId: e.project_id ?? null,
      status: e.status ?? d?.status ?? null,
      commit: d?.commit ?? null,
      provenance: d?.provenance ?? null,
      payload: d?.payload,
      evidenceIds: d ? d.evidence.map((ev) => String(ev.path ?? ev.type ?? "")).filter(Boolean) : [],
    };
  });
  merged.sort((a, b) => b.occurredAt.localeCompare(a.occurredAt));
  return merged;
}

/** 事件大类（过滤器用） */
export function eventCategory(type: string): string {
  if (type.startsWith("decision_")) return "决策";
  if (type.startsWith("experiment_")) return "实验";
  if (type.startsWith("test_") || type === "benchmark_completed") return "测试/Benchmark";
  if (type.startsWith("agent_")) return "Agent 会话";
  if (type === "git_snapshot") return "Git 快照";
  if (type === "resource_snapshot") return "GPU 采样";
  if (type.startsWith("blocker_")) return "Blocker";
  if (type === "workspace_snapshot") return "工作区快照";
  if (type.startsWith("verification_")) return "验证";
  if (type.startsWith("plan_")) return "计划";
  if (type.startsWith("command_")) return "命令";
  return "其他";
}

/** 实验结果趋势（按本地日期聚合 experiment_completed/failed） */
export interface ExperimentTrendRow {
  date: string;
  completed: number;
  failed: number;
}

export function buildExperimentTrend(
  timelines: Array<{ events: import("./types").TimelineEvent[] }>,
): ExperimentTrendRow[] {
  const byDay = new Map<string, { completed: number; failed: number }>();
  for (const { events } of timelines) {
    for (const e of events) {
      if (e.type !== "experiment_completed" && e.type !== "experiment_failed") continue;
      const d = new Date(e.occurred_at);
      if (Number.isNaN(d.getTime())) continue;
      const day = e.occurred_at.slice(0, 10);
      const row = byDay.get(day) ?? { completed: 0, failed: 0 };
      if (e.status === "failed" || e.type === "experiment_failed") row.failed += 1;
      else row.completed += 1;
      byDay.set(day, row);
    }
  }
  return [...byDay.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, row]) => ({ date, ...row }));
}

/** 异常建议动作（按 anomaly code 的静态映射，属展示层文案而非数据） */
export const ANOMALY_ADVICE: Record<string, string> = {
  stale_verification: "重新运行该验证阶段并记录新的验证事件",
  unverified_code_change: "提交前运行测试/benchmark，确保结果绑定在当前工作树之后",
  remote_verification_pending: "推进远端验证（docker/jetson/judge），避免本地结论过期",
  gpu_idle_allocated: "确认显存占用进程：释放空闲显存或安排新任务",
};

export function anomalyAdvice(code: string): string {
  return ANOMALY_ADVICE[code] ?? "查看证据事件，确认根因后处理";
}

// ---------- 决策关系图（/insights/graph + /advanced/metric-lineage 合并） ----------

export interface RelationNode {
  id: string;
  name: string;
  category: string; // decision | experiment | metric | artifact | dataset | model | commit_sha | tree_hash | other
  status?: string | null;
}

export interface RelationLink {
  source: string;
  target: string;
  relation: string;
}

export interface RelationGraph {
  nodes: RelationNode[];
  links: RelationLink[];
  categories: string[];
}

function nodeCategory(rawType: string, id: string): string {
  if (rawType) return rawType;
  const prefix = id.split(":")[0];
  return prefix || "other";
}

/** 合并两个图投影：节点按 id 去重，边保留 relation；缺失端点自动补节点（类型取 id 前缀） */
export function buildRelationGraph(
  graph: import("./types").DecisionGraph | null | undefined,
  metricLineage: import("./types").DecisionGraph | null | undefined,
): RelationGraph {
  const nodes = new Map<string, RelationNode>();
  const links: RelationLink[] = [];
  const addNode = (id: string, name: string, category: string, status?: string | null) => {
    if (!nodes.has(id)) nodes.set(id, { id, name, category, status });
  };
  const addLink = (source: string, target: string, relation: string) => {
    addNode(source, source, nodeCategory("", source));
    addNode(target, target, nodeCategory("", target));
    links.push({ source, target, relation });
  };

  for (const n of graph?.nodes ?? []) addNode(n.id, n.label || n.id, nodeCategory(n.type, n.id), n.status);
  for (const e of graph?.edges ?? []) addLink(e.from, e.to, e.relation);
  for (const n of metricLineage?.nodes ?? []) addNode(n.id, String(n.name ?? n.id), nodeCategory(n.type, n.id), n.status);
  for (const e of metricLineage?.edges ?? []) addLink(e.from, e.to, e.relation);

  const categories = [...new Set([...nodes.values()].map((n) => n.category))];
  return { nodes: [...nodes.values()], links, categories };
}

/** lineage 参数历史 → 数值序列（非数值返回 null，页面降级为表格） */
export function numericParamSeries(
  history: Array<{ value: unknown; occurred_at: string }>,
): Array<{ at: string; value: number }> | null {
  const points: Array<{ at: string; value: number }> = [];
  for (const h of history) {
    const n = typeof h.value === "number" ? h.value : Number(h.value);
    if (!Number.isFinite(n)) return null;
    points.push({ at: h.occurred_at, value: n });
  }
  return points;
}
