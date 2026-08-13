// 只读 API 客户端 —— 唯一网络层。前端不发送任何写请求。
//
// BASE 解析规则：
// - import.meta.env.VITE_API_BASE_URL 已定义（包括空串）→ 使用该值
//   - 空串 = 同源（dev 下由 Vite proxy 转发到后端，规避 CORS）
// - 未定义 → 默认 http://127.0.0.1:8787

import type {
  Achievement,
  AgentBlindspot,
  Anomaly,
  AttentionBudget,
  BudgetRoi,
  ChangeImpact,
  ConfidenceItem,
  ContextPackData,
  CountdownItem,
  Coverage,
  DailyCard,
  DailyReport,
  DecisionConflict,
  DecisionGraph,
  DigitalTwin,
  DontItem,
  ExperimentEfficiency,
  Fingerprint,
  FreshnessItem,
  GpuReport,
  HealthInfo,
  HealthOk,
  Hypothesis,
  InfoGainItem,
  KnowledgeCard,
  MapProject,
  MemoryFreshness,
  ParamLineage,
  ProjectBrief,
  ProjectState,
  ReproItem,
  ResearchDebt,
  ResearchWrapped,
  ResourceCostItem,
  Rhythm,
  RiskRadar,
  SemanticFacts,
  SessionEfficiencyItem,
  SessionInfo,
  StatsFacts,
  Suggestion,
  SwitchAnalysis,
  TimelineEvent,
  TodayReplay,
  WhatChanged,
  WhyNotDone,
  Counterfactual,
  HandoffQualityItem,
  SimpleAnalyticsResponse,
  SimpleDailyResponse,
  SimpleKnowledgeResponse,
  SourceDailyReport,
  SourceReportDates,
  ResearchRadarResponse,
  LifeDashboard,
  DevelopmentResponse,
  ProjectIntelligenceResponse,
  AlgorithmArchitectureIndex,
  AlgorithmArchitectureDetail,
  ExperimentIntelligenceResponse,
  ProjectDiscoveryResponse,
} from "./types";

export const API_BASE: string =
  import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8787";

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;
  readonly url: string;

  constructor(status: number, detail: string, url: string) {
    super(`API ${status}: ${detail} (${url})`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.url = url;
  }
}

type Params = Record<string, string | number | boolean | undefined | null>;

function buildUrl(path: string, params?: Params, base: string = API_BASE): string {
  const origin = base === "" ? "http://rd-cockpit.local" : base;
  const url = new URL(path, origin.endsWith("/") ? origin : `${origin}/`);
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value === undefined || value === null || value === "") continue;
      url.searchParams.set(key, String(value));
    }
  }
  if (base === "") {
    // 同源开发：加 /api 前缀（Vite proxy 转发到后端并去掉前缀，避免与 SPA 路由冲突）
    return `/api${url.pathname}${url.search}`;
  }
  return url.toString();
}

async function request<T>(path: string, params?: Params): Promise<T> {
  const url = buildUrl(path, params);
  let response: Response;
  try {
    response = await fetch(url, { headers: { Accept: "application/json" } });
  } catch (err) {
    throw new ApiError(
      0,
      `网络错误：无法连接 ${API_BASE || "同源 API"}（${err instanceof Error ? err.message : String(err)}）`,
      url,
    );
  }
  if (!response.ok) {
    let detail = response.statusText || "未知错误";
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
      else if (body.detail) detail = JSON.stringify(body.detail);
    } catch {
      // 非 JSON 错误体，保留 statusText
    }
    throw new ApiError(response.status, detail, url);
  }
  return (await response.json()) as T;
}

// ---------- 基础状态 ----------

export const getHealth = () => request<HealthOk>("/health");

export const getProjects = () => request<Record<string, ProjectState>>("/projects");

export const getProjectState = (projectId: string, at?: string) =>
  request<ProjectState>(`/projects/${encodeURIComponent(projectId)}/state`, { at });

export const getProjectTimeline = (projectId: string) =>
  request<TimelineEvent[]>(`/projects/${encodeURIComponent(projectId)}/timeline`);

export const getAnomalies = (project?: string) =>
  request<Anomaly[]>("/anomalies", { project });

export const getSessions = (project?: string, active?: boolean) =>
  request<SessionInfo[]>("/sessions", { project, active: active ? true : undefined });

export const getStats = (period: "week" | "month", reportDate?: string) =>
  request<StatsFacts>("/stats", { period, report_date: reportDate });

export const getDailyReport = (date: string) =>
  request<DailyReport>(`/reports/daily/${encodeURIComponent(date)}`);

export const getDailySemantic = (date: string) =>
  request<SemanticFacts>(`/reports/daily/${encodeURIComponent(date)}/semantic`);

// ---------- 面向日常使用的简化视图 ----------

export const getSimpleDaily = (reportDate?: string, project?: string) =>
  request<SimpleDailyResponse>("/simple/daily", { report_date: reportDate, project });

export const getSimpleAnalytics = (days = 30) =>
  request<SimpleAnalyticsResponse>("/simple/analytics", { days });

export const getSimpleKnowledge = (project?: string) =>
  request<SimpleKnowledgeResponse>("/simple/knowledge", { project });

export const getSourceDailyReport = (reportDate?: string) =>
  request<SourceDailyReport>("/simple/report", { report_date: reportDate });

export const getSourceReportDates = () =>
  request<SourceReportDates>("/simple/report-dates");

export const getResearchRadar = (project?: string, refresh = false) =>
  request<ResearchRadarResponse>("/simple/research-radar", { project, refresh: refresh || undefined });

export const getLifeDashboard = (targetDate?: string) =>
  request<LifeDashboard>("/simple/life", { target_date: targetDate });

export const getDevelopment = (days = 90, targetDate?: string) =>
  request<DevelopmentResponse>("/simple/development", { days, target_date: targetDate });

export const getProjectIntelligence = (days = 90, baseline?: string, targetDate?: string) =>
  request<ProjectIntelligenceResponse>("/simple/intelligence", { days, baseline, target_date: targetDate });

export const getAlgorithmArchitectureIndex = () =>
  request<AlgorithmArchitectureIndex>("/simple/algorithm-architecture");

export const getAlgorithmArchitecture = (projectId: string) =>
  request<AlgorithmArchitectureDetail>(`/simple/algorithm-architecture/${encodeURIComponent(projectId)}`);

export const getExperimentIntelligence = (days = 90, project?: string, targetDate?: string) =>
  request<ExperimentIntelligenceResponse>("/simple/experiment-intelligence", {
    days, project, target_date: targetDate,
  });

export const getProjectDiscovery = () =>
  request<ProjectDiscoveryResponse>("/simple/project-discovery");

// ---------- Insights ----------

export const getLineage = (project?: string) =>
  request<ParamLineage[]>("/insights/lineage", { project });

export const getDecisionGraph = (project?: string) =>
  request<DecisionGraph>("/insights/graph", { project });

export const getConflicts = (project?: string) =>
  request<DecisionConflict[]>("/insights/conflicts", { project });

export const getFreshness = (project?: string) =>
  request<FreshnessItem[]>("/insights/freshness", { project });

export const getEfficiency = (project?: string) =>
  request<ExperimentEfficiency>("/insights/efficiency", { project });

export const getGpuReport = () => request<GpuReport>("/insights/gpu");

export const getCoverage = (project?: string) =>
  request<Coverage>("/insights/coverage", { project });

export const getReproducibility = (project?: string) =>
  request<ReproItem[]>("/insights/reproducibility", { project });

export const getImpact = (project: string) =>
  request<ChangeImpact>("/insights/impact", { project });

export const getContextPack = (project: string) =>
  request<ContextPackData>("/insights/context", { project });

export const getSuggest = (project?: string) =>
  request<Suggestion[]>("/insights/suggest", { project });

export const getCounterfactual = (project: string, query: string) =>
  request<Counterfactual>("/insights/counterfactual", { project, query });

export const getTwin = () => request<DigitalTwin>("/insights/twin");

export const getSwitches = () => request<SwitchAnalysis>("/insights/switches");

export const getInsightSessions = (project?: string) =>
  request<SessionEfficiencyItem[]>("/insights/sessions", { project });

export const getReplay = (date: string) =>
  request<TodayReplay>("/insights/replay", { query: date });

export const getWrapped = (date: string) =>
  request<ResearchWrapped>("/insights/wrapped", { query: date });

export const getResourceCost = (project?: string) =>
  request<ResourceCostItem[]>("/insights/resource-cost", { project });

export const getChanged = (query: string, project?: string) =>
  request<WhatChanged>("/insights/changed", { query, project });

// ---------- Advanced ----------

export const getDebt = (project?: string) =>
  request<ResearchDebt>("/advanced/debt", { project });

export const getConfidence = (project?: string) =>
  request<ConfidenceItem[]>("/advanced/confidence", { project });

export const getHypotheses = (project?: string) =>
  request<Hypothesis[]>("/advanced/hypotheses", { project });

export const getInformationGain = (project?: string) =>
  request<InfoGainItem[]>("/advanced/information-gain", { project });

export const getBudget = (project?: string) =>
  request<BudgetRoi>("/advanced/budget", { project });

export const getMetricLineage = (project?: string) =>
  request<DecisionGraph>("/advanced/metric-lineage", { project });

export const getFingerprints = (project?: string) =>
  request<Fingerprint[]>("/advanced/fingerprints", { project });

export const getHealthScore = (project: string) =>
  request<HealthInfo>("/advanced/health", { project });

export const getRisk = (project: string) =>
  request<RiskRadar>("/advanced/risk", { project });

export const getWhyNotDone = (project: string) =>
  request<WhyNotDone>("/advanced/why-not-done", { project });

export const getAttention = (project?: string) =>
  request<AttentionBudget>("/advanced/attention", { project });

export const getRhythm = (project?: string) =>
  request<Rhythm>("/advanced/rhythm", { project });

export const getHandoffQuality = (project?: string) =>
  request<HandoffQualityItem[]>("/advanced/handoff-quality", { project });

export const getAgentBlindspots = (project?: string) =>
  request<AgentBlindspot[]>("/advanced/agent-blindspots", { project });

export const getMemory = (project: string) =>
  request<MemoryFreshness>("/advanced/memory", { project });

export const getKnowledge = (project?: string) =>
  request<KnowledgeCard[]>("/advanced/knowledge", { project });

export const getBrief = (project: string) =>
  request<ProjectBrief>("/advanced/brief", { project });

export const getAchievements = (project?: string) =>
  request<Achievement[]>("/advanced/achievements", { project });

export const getCard = (date: string) =>
  request<DailyCard>("/advanced/card", { query: date });

export const getResearchMap = () => request<MapProject[]>("/advanced/map");

export const getDont = (project?: string) =>
  request<DontItem[]>("/advanced/dont", { project });

export const getCountdown = (project?: string) =>
  request<CountdownItem[]>("/advanced/countdown", { project });

// 测试辅助：允许注入覆盖 BASE（避免在测试里改 import.meta.env）
export const __testables = { buildUrl };
