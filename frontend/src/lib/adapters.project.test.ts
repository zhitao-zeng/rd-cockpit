import { describe, expect, it } from "vitest";
import { buildProjectView, deriveProjectStatus, stageProgress, stageRows } from "./adapters";
import type { ProjectState } from "./types";

function makeState(overrides: Partial<ProjectState> = {}): ProjectState {
  return {
    project_id: "ocr",
    name: "Embodied AI / OCR",
    goal: "提升 OCR 精度",
    repo_path: "/repo",
    branch: "main",
    head: "abc123def456",
    dirty: false,
    verification: {
      implementation: { status: "passed", event_id: "e1", commit: "abc" },
      local_eval: { status: "pending" },
    },
    blockers: [],
    remaining: ["补充分布式评测"],
    recent_events: [
      { event_id: "e1", occurred_at: "2026-08-01T01:00:00+00:00", type: "git_snapshot", status: "clean" },
      { event_id: "e2", occurred_at: "2026-08-02T01:00:00+00:00", type: "test_completed", status: "passed" },
    ],
    ...overrides,
  };
}

describe("stageProgress", () => {
  it("部分通过", () => {
    expect(stageProgress({ a: { status: "passed" }, b: { status: "pending" } })).toBe(0.5);
  });

  it("无阶段配置 → 0", () => {
    expect(stageProgress({})).toBe(0);
  });

  it("stale 不算通过", () => {
    expect(stageProgress({ a: { status: "stale" } })).toBe(0);
  });
});

describe("deriveProjectStatus 四分支", () => {
  it("历史和休眠状态优先于验证进度", () => {
    expect(deriveProjectStatus(makeState({ lifecycle_status: "historical" }))).toBe("historical");
    expect(deriveProjectStatus(makeState({ lifecycle_status: "dormant" }))).toBe("dormant");
  });

  it("全部通过 → done", () => {
    const s = makeState({ verification: { a: { status: "passed" }, b: { status: "passed" } } });
    expect(deriveProjectStatus(s)).toBe("done");
  });

  it("有 blocker → blocked（优先级高于 stale）", () => {
    const s = makeState({
      blockers: ["等数据"],
      verification: { a: { status: "stale" }, b: { status: "pending" } },
    });
    expect(deriveProjectStatus(s)).toBe("blocked");
  });

  it("有过期阶段且无 blocker → stale", () => {
    const s = makeState({ verification: { a: { status: "passed" }, b: { status: "stale" } } });
    expect(deriveProjectStatus(s)).toBe("stale");
  });

  it("其余 → active", () => {
    const s = makeState({ verification: { a: { status: "passed" }, b: { status: "pending" } } });
    expect(deriveProjectStatus(s)).toBe("active");
  });
});

describe("buildProjectView", () => {
  it("推导进度/状态/最近活动", () => {
    const view = buildProjectView(makeState());
    expect(view.id).toBe("ocr");
    expect(view.status).toBe("active");
    expect(view.progress).toBe(0.5);
    expect(view.passedStages).toBe(1);
    expect(view.totalStages).toBe(2);
    expect(view.blockerCount).toBe(0);
    expect(view.lastActivity).toBe("2026-08-02T01:00:00+00:00");
    expect(view.remaining).toEqual(["补充分布式评测"]);
  });

  it("无事件时 lastActivity 为 null 而不是编造时间", () => {
    const view = buildProjectView(makeState({ recent_events: [] }));
    expect(view.lastActivity).toBeNull();
  });
});

describe("stageRows", () => {
  it("展开 stale 原因与证据", () => {
    const s = makeState({
      verification: {
        local_eval: {
          status: "stale",
          event_id: "e9",
          commit: "deadbeef",
          stale_reason: "working tree or commit changed after verification",
          verified_at: "2026-08-01T00:00:00+00:00",
        },
      },
    });
    expect(stageRows(s)).toEqual([
      {
        stage: "local_eval",
        status: "stale",
        reason: null,
        staleReason: "working tree or commit changed after verification",
        commit: "deadbeef",
        eventId: "e9",
        verifiedAt: "2026-08-01T00:00:00+00:00",
      },
    ]);
  });
});
