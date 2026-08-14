import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ProjectIntelligence } from "../pages/ProjectIntelligence";
import { getProjectIntelligence } from "../lib/api";
import type { ProjectIntelligenceResponse } from "../lib/types";

vi.mock("../components/Chart", () => ({ Chart: () => <div data-testid="chart" /> }));
vi.mock("../lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/api")>();
  return { ...original, getProjectIntelligence: vi.fn() };
});

const response: ProjectIntelligenceResponse = {
  generated_for: "2026-08-10", days: 90, latest_report_date: "2026-08-09",
  baseline_date: "2026-08-01", available_dates: ["2026-08-09", "2026-08-01"],
  pulses: [{ project_id: "asr", name: "具身智能 ASR", phase: "验证", status: "active",
    latest_result: "Jetson 验证通过", current_blocker: null, next_action: "运行 Judge",
    open_unknowns: 1, last_meaningful: "2026-08-09", tokens: 1200, result_items: 3,
    source_mode: "audited" }],
  effort_progress: [{ project_id: "asr", name: "具身智能 ASR", tokens: 1200,
    agent_minutes: 20, progress_items: 4, result_items: 3, completed_plans: 0,
    breakthroughs: 1, resolved_unknowns: 0, resolved_blockers: 0, quadrant: "heavy_wins" }],
  project_details: { asr: {
    delta: { from: "2026-08-01", to: "2026-08-09", change_count: 1,
      results: [{ date: "2026-08-09", text: "Jetson 验证通过", source: "2026-08-09.md" }],
      knowledge: [], blockers: [], plan_closure: [], unknowns_opened: [], unknowns_resolved: [],
      blockers_opened: [], blockers_resolved: [] },
    stale_unknown_count: 0, hidden_unknown_count: 0, stale_blocker_count: 0,
    unknowns: [{ unknown_id: "u1", project_id: "asr", question: "Judge 是否保持收益？",
      priority: "high", missing_evidence: "Judge submission", first_seen: "2026-08-09",
      last_seen: "2026-08-09", evidence: ["session:s1"], confidence: "reported", source_mode: "audited" }],
    breakthroughs: [{ project_id: "asr", date: "2026-08-09", title: "远端验证",
      change: "Jetson 验证通过", significance: "支持采用", evidence: ["session:s1"],
      confidence: "reported", source_mode: "audited" }],
    storyline: { project_id: "asr", summary: "本地方案已推进到 Jetson，下一步验证 Judge。",
      source_mode: "audited", evidence: ["session:s1"], source_dates: ["2026-08-09"] },
  } },
  audit_coverage: { report_count: 9, audited_count: 8, stale_last_good_count: 0, fallback_count: 1,
    failed_dates: ["2026-08-01"], last_audited_date: "2026-08-09" },
  data_quality: [], explanation: "日报审计",
};

describe("项目情报页", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.mocked(getProjectIntelligence).mockReset();
    vi.mocked(getProjectIntelligence).mockResolvedValue(response);
  });

  it("使用上次浏览日期并展示六类情报", async () => {
    window.localStorage.setItem("rd-cockpit.intelligence.last-seen-report", "2026-08-01");
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><ProjectIntelligence /></QueryClientProvider>);

    expect(await screen.findByText("Project Pulse")).toBeInTheDocument();
    expect(screen.getByText("Since Last Visit")).toBeInTheDocument();
    expect(screen.getByText("Open Unknowns")).toBeInTheDocument();
    expect(screen.getByText("Judge 是否保持收益？")).toBeInTheDocument();
    expect(screen.getByText("Breakthrough Timeline")).toBeInTheDocument();
    expect(screen.getByText("Project Storyline")).toBeInTheDocument();
    expect(screen.getByText("本地方案已推进到 Jetson，下一步验证 Judge。")).toBeInTheDocument();
    expect(getProjectIntelligence).toHaveBeenCalledWith(90, "2026-08-01");
    await waitFor(() => expect(window.localStorage.getItem("rd-cockpit.intelligence.last-seen-report")).toBe("2026-08-09"));
  });
});
