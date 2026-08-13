import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Experiments } from "../pages/Experiments";
import { getExperimentIntelligence, getProjects } from "../lib/api";
import type { ExperimentIntelligenceResponse, ProjectState } from "../lib/types";

vi.mock("../components/Chart", () => ({ Chart: () => <div data-testid="metric-story" /> }));
vi.mock("../lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/api")>();
  return { ...original, getExperimentIntelligence: vi.fn(), getProjects: vi.fn() };
});

const token = { total_tokens: 120000, codex_tokens: 100000, claude_tokens: 20000, sessions: 2,
  attribution: "project_day_delta" as const, quality: "estimated" as const, long_sessions: 1,
  counter_regressions: 0, note: "共享池", shared_by_records: 2 };
const response: ExperimentIntelligenceResponse = {
  schema_version: 1, generated_from: "Daily Report", target: "2026-08-11", since: "2026-05-14", project_filter: null,
  counts: { records: 1, projects: 1, metrics: 2, conclusions: 1, analyzed_days: 40, validation_errors: 0 },
  projects: [{ project_id: "ocr", name: "具身智能 OCR", record_count: 1, metric_count: 2,
    latest_date: "2026-08-04", result_status: { improved: 1 }, token_pool_total: 120000, token_pool_days: 1 }],
  records: [{ record_id: "exp:ocr:1", project_id: "ocr", date: "2026-08-04", title: "OCR 双后端延迟评测",
    kind: "benchmark", question: "TensorRT 是否值得采用？", method: "在 Jetson 同一图片集比较 TensorRT 与 MNN。",
    models: [{ name: "TensorRT", role: "candidate" }, { name: "MNN", role: "baseline" }],
    datasets: [{ name: "waic-v2", scope: "54 张图片" }], parameters: [{ name: "rec_min_score", value: "0.9" }],
    metrics: [{ name: "Latency", value: "47", unit: "ms", scope: "Jetson / waic-v2", direction: "lower" },
      { name: "Latency", value: "82", unit: "ms", scope: "Jetson / waic-v2", direction: "lower" }],
    result_status: "improved", result_summary: "TensorRT 延迟 47ms，MNN 为 82ms。",
    conclusion: "TensorRT 在这一口径下更快。", decision_impact: "保留 TensorRT。", verification_scope: "jetson",
    machine: "Jetson", commit_sha: "a410bcd", artifacts: ["benchmark.json"], session_ids: ["codex-1"],
    evidence: ["report:2026-08-04:L25-L30"], confidence: "reported", source_mode: "daily_report_audited", token_context: token }],
  metric_series: [{ project_id: "ocr", name: "Latency", unit: "ms", scope: "Jetson / waic-v2",
    points: [{ date: "2026-08-03", value: 82, display_value: "82", record_id: "old", title: "baseline" },
      { date: "2026-08-04", value: 47, display_value: "47", record_id: "exp:ocr:1", title: "new" }] }],
  token_pools: [{ date: "2026-08-04", project_id: "ocr", ...token }], validation_errors: [], backfill_status: {}, notes: [],
};

describe("实验记录页", () => {
  beforeEach(() => {
    vi.mocked(getExperimentIntelligence).mockReset();
    vi.mocked(getProjects).mockReset();
    vi.mocked(getExperimentIntelligence).mockResolvedValue(response);
    vi.mocked(getProjects).mockResolvedValue({ ocr: { project_id: "ocr", name: "具身智能 OCR" } as ProjectState });
  });

  it("展示可读实验、指标口径、证据和非独占 Token 说明", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<MemoryRouter><QueryClientProvider client={client}><Experiments /></QueryClientProvider></MemoryRouter>);
    expect(await screen.findByText("OCR 双后端延迟评测")).toBeInTheDocument();
    expect(screen.getByText("TensorRT 是否值得采用？")).toBeInTheDocument();
    expect(screen.getByText("在 Jetson 同一图片集比较 TensorRT 与 MNN。")).toBeInTheDocument();
    expect(screen.getByText("TensorRT 在这一口径下更快。")).toBeInTheDocument();
    expect(screen.getByText(/由当日 2 条实验共享，非单实验成本/)).toBeInTheDocument();
    expect(screen.getByText(/日报 2026-08-04/)).toBeInTheDocument();
    expect(screen.getByTestId("metric-story")).toBeInTheDocument();
  });
});
