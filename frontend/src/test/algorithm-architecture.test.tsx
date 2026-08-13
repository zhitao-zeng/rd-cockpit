import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AlgorithmArchitecture } from "../pages/AlgorithmArchitecture";
import { getAlgorithmArchitecture, getAlgorithmArchitectureIndex } from "../lib/api";
import type { AlgorithmArchitectureDetail, AlgorithmArchitectureIndex } from "../lib/types";

vi.mock("../components/Chart", () => ({ Chart: () => <div data-testid="architecture-graph" /> }));
vi.mock("../lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/api")>();
  return { ...original, getAlgorithmArchitectureIndex: vi.fn(), getAlgorithmArchitecture: vi.fn() };
});

const ref = "source:repo:model.py:L1-L20";
const externalRef = "external:zipdepth_official:F1";
const index: AlgorithmArchitectureIndex = {
  schema_version: 2, generated_at: "2026-08-11T02:00:00Z",
  counts: { total: 1, ready: 1, not_analyzed: 0, insufficient: 0, failed: 0 },
  projects: [{ project_id: "obstacle", name: "Obstacle", priority: "P0", status: "ready",
    summary: "深度与分割融合", algorithm_type: "hybrid_system", generated_at: "2026-08-11T02:00:00Z",
    head: "abc", dirty: false, evidence_summary: { bundled: 3, cited: 2, models: 1, explained_models: 1, metrics: 1 },
    models: [{ id: "depth", name: "ZipDepth", variant: "Base INT8", status: "current", architecture_status: "partial", architecture_basis: "mixed" }] }],
};

const detail: AlgorithmArchitectureDetail = {
  history: [],
  snapshot: {
    schema_version: 2, snapshot_id: "snapshot-1", project_id: "obstacle", project_name: "Obstacle",
    status: "ready", algorithm_type: "hybrid_system", objective: "预测近障碍距离。",
    summary: "室内使用 ZipDepth，车辆分支融合深度与实例分割。",
    pipeline: {
      nodes: [{ id: "image", label: "图像输入", category: "input", summary: "接收 RGB 图像", status: "current", evidence: [ref] },
        { id: "depth", label: "ZipDepth", category: "model", summary: "预测逆深度", status: "current", evidence: [ref] }],
      edges: [{ source: "image", target: "depth", label: "推理", data: "RGB", evidence: [ref] }],
    },
    models: [{ id: "depth", node_id: "depth", name: "ZipDepth", variant: "Base INT8", role: "预测室内深度",
      status: "current", architecture_status: "partial", architecture_basis: "mixed", architecture_summary: "四级编码器连接 FPN 解码器。",
      input: "1×3×384×512", output: "逆深度图", quantization: "INT8", parameters: "", artifact_size: "",
      design_rationale: ["兼顾精度与部署成本"], limitations: ["输出不是绝对米制深度"], evidence: [ref, externalRef],
      blocks: [{ id: "encoder", name: "Four-stage encoder", type: "backbone", role: "提取多尺度特征", details: "48/96/192/384 通道", evidence: [externalRef] }],
      metrics: [{ name: "accuracy", value: "0.91", unit: "", scope: "local", verification: "observed", evidence: [ref] }] }],
    design_decisions: [{ title: "采用 ZipDepth", rationale: "室内验证更稳", status: "adopted", evidence: [ref] }],
    alternatives: [], algorithm_diff: [], open_questions: [], warnings: [],
    source_state: { head: "abcdef", branch: "main", dirty: false, source_hash: "hash" },
    generated_at: "2026-08-11T02:00:00Z", model_run: { model: "codex:gpt-5.6-sol@medium", provider: "codex-cli", usage: { input_tokens: 1000, output_tokens: 200 } },
    validation_errors: [], evidence_summary: { bundled: 3, cited: 2, models: 1, explained_models: 1, metrics: 1 },
    evidence_catalog: { [ref]: { kind: "source", source_id: "repo", path: "model.py", line_start: 1, line_end: 20,
      sha256: "1234567890abcdef1234567890abcdef", text: "class FourStageEncoder: pass" },
      [externalRef]: { kind: "external", source_id: "zipdepth_official", path: "ZipDepth official", line_start: null, line_end: null,
        sha256: "abcdef1234567890abcdef1234567890", text: "ZipDepth family uses a four-stage encoder.", scope: "family_reference",
        source_type: "official_repository", url: "https://example.org/zipdepth", retrieved_at: "2026-08-11" } },
  },
};

describe("算法架构页", () => {
  beforeEach(() => {
    vi.mocked(getAlgorithmArchitectureIndex).mockReset();
    vi.mocked(getAlgorithmArchitecture).mockReset();
    vi.mocked(getAlgorithmArchitectureIndex).mockResolvedValue(index);
    vi.mocked(getAlgorithmArchitecture).mockResolvedValue(detail);
  });

  it("展示交互式流水线、模型剖面和证据约束", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><AlgorithmArchitecture /></QueryClientProvider>);

    expect(await screen.findByText("预测近障碍距离。")).toBeInTheDocument();
    expect(screen.getByTestId("architecture-graph")).toBeInTheDocument();
    expect(screen.getAllByText("ZipDepth").length).toBeGreaterThan(0);
    expect(screen.getByText("Four-stage encoder")).toBeInTheDocument();
    expect(screen.getByText("为什么这样设计")).toBeInTheDocument();
    expect(screen.getByText(/输出不是绝对米制深度/)).toBeInTheDocument();
    expect(screen.getAllByText(/model.py/).length).toBeGreaterThan(0);
    fireEvent.click(screen.getAllByText(/model.py/)[0]);
    expect(screen.getByTestId("evidence-drawer")).toHaveTextContent("class FourStageEncoder: pass");
    fireEvent.click(screen.getAllByText(/官方公开参考/)[0]);
    expect(screen.getByTestId("evidence-drawer")).toHaveTextContent("仅说明公开家族结构");
    expect(screen.getByRole("link", { name: /打开官方来源/ })).toHaveAttribute("href", "https://example.org/zipdepth");
    expect(getAlgorithmArchitecture).toHaveBeenCalledWith("obstacle");
  });

  it("展示人工复核的模型精读、实验脉络、研究启发和未来路线", async () => {
    vi.mocked(getAlgorithmArchitecture).mockResolvedValue({
      ...detail,
      research_brief: {
        schema_version: 1, project_id: "asr_dialect", title: "方言 ASR / LID 研究复盘",
        reviewed_at: "2026-08-13", overview: "ASR回答说了什么，LID回答是哪种方言。", evidence_note: "已经复核。",
        models: [{ id: "asr", name: "ExampleConformer", variant: "baseline", role: "方言转写", summary: "Conformer接Transformer Decoder。",
          specs: [{ label: "Encoder", value: "16层" }], stages: [{ name: "Conformer Encoder ×16", kind: "声学骨干", role: "提取声学表示", detail: "同时看局部与全局。" }] }],
        metric_lanes: [{ level: "platform", label: "真实平台", tone: "good", values: [{ name: "CER", value: "13.49%", note: "最终记录" }] }],
        experiment_phases: [{ period: "07-01", title: "解冻声学编码器", question: "瓶颈在哪", experiments: ["冻结与解冻对比"], takeaway: "声学适配是主线" }],
        insights: [{ title: "数据规范优先", observation: "外部转写不一致", implication: "先统一正字法" }],
        future_directions: [{ priority: "P0", title: "固定可信评测", hypothesis: "口径稳定才能比较", smallest_experiment: "speaker-disjoint", promotion_gate: "逐方言均通过" }],
      },
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><AlgorithmArchitecture /></QueryClientProvider>);

    expect(await screen.findByText("方言 ASR / LID 研究复盘")).toBeInTheDocument();
    expect(screen.getByText("Conformer Encoder ×16")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "实验脉络" }));
    expect(screen.getByText(/解冻声学编码器/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "研究启发" }));
    expect(screen.getByText("数据规范优先")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "未来方向" }));
    expect(screen.getByText(/固定可信评测/)).toBeInTheDocument();
  });
});
