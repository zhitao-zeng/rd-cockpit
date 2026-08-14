// API 客户端 —— 唯一网络层。除本地语义纠错外，页面只读。
//
// BASE 解析规则：
// - import.meta.env.VITE_API_BASE_URL 已定义（包括空串）→ 使用该值
//   - 空串 = 同源（dev 下由 Vite proxy 转发到后端，规避 CORS）
// - 未定义 → 默认 http://127.0.0.1:8787

import type {
  HealthOk,
  ProjectState,
  StatsFacts,
  SimpleAnalyticsResponse,
  SimpleKnowledgeResponse,
  SourceDailyReport,
  SourceReportDates,
  ResearchRadarResponse,
  LifeDashboard,
  DevelopmentResponse,
  DevelopmentSummaryResponse,
  DevelopmentProjectResponse,
  DevelopmentGlobalResponse,
  DevelopmentTimelineResponse,
  DevelopmentHistoryResponse,
  ProjectIntelligenceResponse,
  AlgorithmArchitectureIndex,
  AlgorithmArchitectureDetail,
  ExperimentIntelligenceResponse,
  ProjectDiscoveryResponse,
  BackgroundTaskStatus,
  ProjectSummary,
  SemanticFeedback,
  SemanticFeedbackInput,
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
const API_TOKEN_KEY = "rd-cockpit.api-token";

export function getApiToken(): string {
  return typeof window === "undefined" ? "" : (window.localStorage.getItem(API_TOKEN_KEY) ?? "");
}

export function setApiToken(value: string): void {
  if (typeof window === "undefined") return;
  const token = value.trim();
  if (token) window.localStorage.setItem(API_TOKEN_KEY, token);
  else window.localStorage.removeItem(API_TOKEN_KEY);
}

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

async function request<T>(path: string, params?: Params, init?: RequestInit): Promise<T> {
  const url = buildUrl(path, params);
  let response: Response;
  try {
    const token = getApiToken();
    response = await fetch(url, {
      ...init,
      headers: {
        Accept: "application/json",
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(init?.headers ?? {}),
      },
    });
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

export const getAuthStatus = () =>
  request<{ required: boolean; authenticated: boolean }>("/auth/status");

export const getProjects = () => request<Record<string, ProjectSummary>>("/simple/projects");

export const getProjectState = (projectId: string, at?: string) =>
  request<ProjectState>(`/projects/${encodeURIComponent(projectId)}/state`, { at });

export const getStats = (period: "week" | "month", reportDate?: string) =>
  request<StatsFacts>("/stats", { period, report_date: reportDate });

// ---------- 面向日常使用的简化视图 ----------

export const getSimpleAnalytics = (days = 30) =>
  request<SimpleAnalyticsResponse>("/simple/analytics", { days });

export const getSimpleKnowledge = (project?: string) =>
  request<SimpleKnowledgeResponse>("/simple/knowledge", { project });

export const getSourceDailyReport = (reportDate?: string) =>
  request<SourceDailyReport>("/simple/report", { report_date: reportDate });

export const getSourceReportDates = () =>
  request<SourceReportDates>("/simple/report-dates");

export const getResearchRadar = (project?: string) =>
  request<ResearchRadarResponse>("/simple/research-radar", { project });

export const getLifeDashboard = (targetDate?: string) =>
  request<LifeDashboard>("/simple/life", { target_date: targetDate });

export const getDevelopment = (days = 90, targetDate?: string) =>
  request<DevelopmentResponse>("/simple/development", { days, target_date: targetDate });

export const getDevelopmentSummary = (days = 90, targetDate?: string) =>
  request<DevelopmentSummaryResponse>("/simple/development-summary", { days, target_date: targetDate });

export const getDevelopmentProject = (
  projectId: string, days = 90, targetDate?: string, timelineLimit = 120,
) => request<DevelopmentProjectResponse>(
  `/simple/development-project/${encodeURIComponent(projectId)}`,
  { days, target_date: targetDate, timeline_limit: timelineLimit },
);

export const getDevelopmentGlobal = (days = 90, targetDate?: string) =>
  request<DevelopmentGlobalResponse>("/simple/development-global", { days, target_date: targetDate });

export const getDevelopmentTimeline = (
  days = 90, project?: string, offset = 0, limit = 50, targetDate?: string,
) => request<DevelopmentTimelineResponse>("/simple/development-timeline", {
  days, project, offset, limit, target_date: targetDate,
});

export const getDevelopmentHistory = (
  days = 90, offset = 0, limit = 10, targetDate?: string,
) => request<DevelopmentHistoryResponse>("/simple/development-history", {
  days, offset, limit, target_date: targetDate,
});

export const getProjectIntelligence = (days = 90, baseline?: string, targetDate?: string) =>
  request<ProjectIntelligenceResponse>("/simple/intelligence", { days, baseline, target_date: targetDate });

export const getSemanticFeedback = (view?: string, project?: string) =>
  request<{ items: SemanticFeedback[]; count: number }>("/simple/semantic-feedback", { view, project });

export const recordSemanticFeedback = (value: SemanticFeedbackInput) =>
  request<{ ok: boolean; item: SemanticFeedback }>("/simple/semantic-feedback", undefined, {
    method: "POST", body: JSON.stringify(value),
  });

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

export const getBackgroundTaskStatus = () =>
  request<BackgroundTaskStatus>("/simple/task-status");

// 测试辅助：允许注入覆盖 BASE（避免在测试里改 import.meta.env）
export const __testables = { buildUrl };
