import { describe, expect, it } from "vitest";
import {
  buildFunnel,
  buildGpuSeries,
  buildGpuSummary,
  buildProjectActivity,
  buildRiskDistribution,
  buildTrendRows,
  averageRiskScore,
  phaseFromProgress,
} from "./adapters";
import type { GpuReport, MapProject, ProjectState, StatsFacts, WhatChanged } from "./types";

function makeState(projectId: string, verification: ProjectState["verification"]): ProjectState {
  return {
    project_id: projectId,
    name: projectId,
    goal: null,
    repo_path: "/x",
    branch: null,
    head: null,
    dirty: null,
    verification,
    blockers: [],
    remaining: [],
    recent_events: [],
  };
}

function makeStats(overrides: Partial<StatsFacts> = {}): StatsFacts {
  return {
    schema_version: 1,
    period: "week",
    label: "2026-W31",
    generated_at: "2026-08-02T00:00:00+00:00",
    time: {
      human_active_hours: 0,
      agent_hours: 0,
      command_hours: 0,
      context_switches: 0,
      active_span_hours: 168,
    },
    outputs: {
      events: 0,
      commits: 0,
      tests: { passed: 0, failed: 0 },
      experiments: 0,
      decisions: 0,
      completed_milestones: 0,
    },
    projects: {},
    trend: [],
    unfinished: [],
    events: [],
    ...overrides,
  };
}

describe("buildTrendRows（Overview 本周趋势）", () => {
  it("映射 trend 字段并保留日期", () => {
    const stats = makeStats({
      trend: [
        { date: "2026-08-01", events: 5, projects: ["ocr"], tests_passed: 2, tests_failed: 1, experiments: 1, decisions: 0 },
      ],
    });
    expect(buildTrendRows(stats)).toEqual([
      { date: "2026-08-01", events: 5, testsPassed: 2, testsFailed: 1, experiments: 1, decisions: 0, projectCount: 1 },
    ]);
  });

  it("空趋势 → 空数组，不编造数据点", () => {
    expect(buildTrendRows(makeStats())).toEqual([]);
    expect(buildTrendRows(null)).toEqual([]);
    expect(buildTrendRows(undefined)).toEqual([]);
  });
});

describe("buildFunnel（Overview 验证漏斗）", () => {
  it("跨项目聚合且按 config 首次出现顺序排列", () => {
    const a = makeState("a", {
      implementation: { status: "passed" },
      unit_test: { status: "stale" },
      jetson: { status: "pending" },
    });
    const b = makeState("b", {
      local_eval: { status: "passed" },
      unit_test: { status: "passed" },
    });
    const funnel = buildFunnel([a, b]);
    expect(funnel.map((f) => f.stage)).toEqual(["implementation", "unit_test", "jetson", "local_eval"]);
    expect(funnel[1]).toEqual({ stage: "unit_test", passed: 1, stale: 1, pending: 0, total: 2 });
  });

  it("空项目列表 → 空数组", () => {
    expect(buildFunnel([])).toEqual([]);
  });
});

describe("buildProjectActivity（Overview 项目活动柱状图）", () => {
  it("按 project_id 排序输出事件数与 commit 数", () => {
    const stats = makeStats({
      projects: {
        ocr: { events: 3, types: {}, commits: ["a", "b"] },
        obstacle: { events: 8, types: {}, commits: ["c"] },
      },
    });
    expect(buildProjectActivity(stats)).toEqual([
      { projectId: "obstacle", events: 8, commits: 1 },
      { projectId: "ocr", events: 3, commits: 2 },
    ]);
  });
});

describe("buildRiskDistribution（Overview 风险分布）", () => {
  it("按等级计数风险维度", () => {
    const map: MapProject[] = [
      { project_id: "ocr", progress: 0.5, risk: { correctness: "high", progress: "medium", reproducibility: "low", resource: "unknown" }, status: "active", bubble: 10 },
    ];
    expect(buildRiskDistribution(map)).toEqual([
      { projectId: "ocr", high: 1, medium: 1, low: 1, unknown: 1 },
    ]);
  });

  it("空 map → 空数组", () => {
    expect(buildRiskDistribution([])).toEqual([]);
    expect(buildRiskDistribution(null)).toEqual([]);
  });
});

describe("buildGpuSummary（Overview GPU 摘要）", () => {
  it("聚合 GPU 报告", () => {
    const report: GpuReport = {
      samples: 4,
      note: "",
      gpus: [
        { gpu: "0", samples: 2, avg_utilization_pct: 50, peak_memory_mb: 20000, idle_allocated_samples: 0, evidence: [] },
        { gpu: "1", samples: 2, avg_utilization_pct: 10, peak_memory_mb: 40000, idle_allocated_samples: 2, evidence: [] },
      ],
    };
    expect(buildGpuSummary(report)).toEqual({
      gpuCount: 2,
      avgUtilization: 30,
      peakMemoryMb: 40000,
      idleAllocated: 2,
      samples: 4,
    });
  });

  it("无 GPU 数据 → null（页面显示 empty state 而非 0）", () => {
    expect(buildGpuSummary({ samples: 0, gpus: [], note: "" })).toBeNull();
    expect(buildGpuSummary(null)).toBeNull();
  });
});

describe("buildGpuSeries（Resources GPU 时序）", () => {
  it("从 resource_snapshot payload 构建时序，缺失 GPU 补 null", () => {
    const changed: WhatChanged = {
      query: "2026-08-01",
      counts: {},
      events: [
        {
          event_id: "e1",
          occurred_at: "2026-08-01T00:00:00+00:00",
          project_id: null,
          type: "resource_snapshot",
          status: null,
          commit: null,
          evidence: [],
          payload: { sampled_at: "2026-08-01T00:00:00+00:00", gpus: [{ index: 0, utilization_pct: 80, memory_used_mb: 1000 }], containers: [] },
        },
        {
          event_id: "e2",
          occurred_at: "2026-08-01T01:00:00+00:00",
          project_id: null,
          type: "resource_snapshot",
          status: null,
          commit: null,
          evidence: [],
          payload: { sampled_at: "2026-08-01T01:00:00+00:00", gpus: [{ index: 0, utilization_pct: 0, memory_used_mb: 2000 }, { index: 1, utilization_pct: 50, memory_used_mb: 3000 }], containers: [{ Names: "ocr" }] },
        },
      ],
    };
    const view = buildGpuSeries(changed);
    expect(view.gpuKeys).toEqual(["0", "1"]);
    expect(view.utilization).toHaveLength(2);
    expect(view.utilization[0]).toEqual({ at: "2026-08-01T00:00:00+00:00", "0": 80, "1": null });
    expect(view.utilization[1]["1"]).toBe(50);
    expect(view.containers).toEqual([{ Names: "ocr" }]);
  });

  it("无快照 → 全空", () => {
    const view = buildGpuSeries({ query: "x", counts: {}, events: [] });
    expect(view.gpuKeys).toEqual([]);
    expect(view.utilization).toEqual([]);
    expect(view.sampledAt).toBeNull();
  });
});

describe("Research Map 坐标推导", () => {
  it("riskScore 均值", () => {
    expect(averageRiskScore({ a: "high", b: "low" })).toBe(2);
    expect(averageRiskScore({})).toBe(0);
  });

  it("阶段推导边界", () => {
    expect(phaseFromProgress(0)).toBe("探索");
    expect(phaseFromProgress(0.24)).toBe("探索");
    expect(phaseFromProgress(0.25)).toBe("实现");
    expect(phaseFromProgress(0.5)).toBe("验证");
    expect(phaseFromProgress(0.75)).toBe("交付");
    expect(phaseFromProgress(1)).toBe("交付");
  });
});
