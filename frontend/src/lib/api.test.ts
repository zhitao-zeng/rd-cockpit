import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, __testables, getProjectIntelligence, getStats, getProjectState } from "./api";

const { buildUrl } = __testables;

describe("buildUrl", () => {
  it("默认 base 拼接绝对 URL", () => {
    expect(buildUrl("/projects", undefined, "http://127.0.0.1:8787")).toBe(
      "http://127.0.0.1:8787/projects",
    );
  });

  it("拼接 query 参数", () => {
    expect(buildUrl("/stats", { period: "week" }, "http://127.0.0.1:8787")).toBe(
      "http://127.0.0.1:8787/stats?period=week",
    );
  });

  it("跳过 undefined / null / 空串参数", () => {
    const url = buildUrl(
      "/insights/lineage",
      { project: undefined, query: null, other: "" },
      "http://127.0.0.1:8787",
    );
    expect(url).toBe("http://127.0.0.1:8787/insights/lineage");
  });

  it("base 带尾斜杠时不会双斜杠", () => {
    expect(buildUrl("/health", undefined, "http://127.0.0.1:8787/")).toBe(
      "http://127.0.0.1:8787/health",
    );
  });

  it("空 base → 同源 /api 前缀相对路径（保留 query）", () => {
    expect(buildUrl("/insights/replay", { query: "2026-08-02" }, "")).toBe(
      "/api/insights/replay?query=2026-08-02",
    );
  });

  it("路径参数中的特殊字符被编码", () => {
    expect(buildUrl(`/projects/${encodeURIComponent("a b/c")}/state`, undefined, "http://x")).toBe(
      "http://x/projects/a%20b%2Fc/state",
    );
  });
});

describe("request 错误处理", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("HTTP 404 + JSON detail → ApiError 带 status 与 detail", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ detail: "report not generated" }), {
          status: 404,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    const err = await getProjectState("nope").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(404);
    expect((err as ApiError).detail).toBe("report not generated");
  });

  it("HTTP 500 非 JSON 错误体 → 保留 statusText", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("boom", { status: 500, statusText: "Internal Server Error" })),
    );
    const err = await getStats("week").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(500);
    expect((err as ApiError).detail).toContain("Internal Server Error");
  });

  it("网络拒绝 → ApiError status 0", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("fetch failed");
      }),
    );
    const err = await getStats("week").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(0);
  });

  it("正常返回解析 JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        expect(String(input)).toContain("/stats?period=month");
        return new Response(JSON.stringify({ period: "month" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );
    const data = await getStats("month");
    expect(data).toEqual({ period: "month" });
  });

  it("项目情报传递观察窗口与上次访问日期", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        expect(String(input)).toContain("/simple/intelligence?days=90&baseline=2026-08-01");
        return new Response(JSON.stringify({ pulses: [] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );
    const data = await getProjectIntelligence(90, "2026-08-01");
    expect(data.pulses).toEqual([]);
  });
});
