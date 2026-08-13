import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ProjectDiscoveryPanel } from "../components/ProjectDiscoveryPanel";
import { getProjectDiscovery } from "../lib/api";
import type { ProjectDiscoveryResponse } from "../lib/types";

vi.mock("../lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/api")>();
  return { ...original, getProjectDiscovery: vi.fn() };
});

const response: ProjectDiscoveryResponse = {
  updated_at: "2026-08-11T10:00:00Z", scan_days: 30,
  counts: { candidates: 1, total_discovered: 1, new_projects: 1, existing_projects: 0,
    temporary_or_reference: 0, insufficient_evidence: 0, pending_review: 0 },
  candidates: [{
    candidate_id: "project:123", repo_path: "/workspace/speech-research",
    repo_name: "asr-translation", agents: ["codex"], session_ids: ["s1"], session_count: 1,
    topics: ["实现语音翻译"], observed_paths: ["src/evaluate.py"], write_paths: ["src/evaluate.py"],
    write_evidence_count: 1, first_seen: "2026-08-11T09:00:00Z", last_seen: "2026-08-11T10:00:00Z",
    evidence_strength: "strong", git: { branch: "main", last_commit: "abc init", tracked_files: 8 },
    review: { decision: "new_project", suggested_project_id: "asr_translation",
      suggested_name: "ASR 语音翻译", summary: "构建并评测语音翻译链路。", existing_project_id: "",
      confidence: 0.92, reason: "独立仓库内存在明确写入。" },
    review_model: "codex:gpt-5.6-sol@medium",
    accept_command: "cd /workspace/rd-cockpit && .venv/bin/python -m rd_cockpit.cli project accept project:123",
  }],
  model_policy: { reviewer: "codex:gpt-5.6-sol@medium", fallback: null,
    registry_write: "explicit_confirmation_only" },
};

describe("新项目发现", () => {
  beforeEach(() => vi.mocked(getProjectDiscovery).mockResolvedValue(response));

  it("展示 Codex 判断、Session 证据和显式确认命令", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><ProjectDiscoveryPanel /></QueryClientProvider>);
    expect(await screen.findByText("ASR 语音翻译")).toBeInTheDocument();
    expect(screen.getByText("Codex 建议登记")).toBeInTheDocument();
    expect(screen.getByText("构建并评测语音翻译链路。")).toBeInTheDocument();
    expect(screen.getByText(/project accept project:123/)).toBeInTheDocument();
    expect(screen.getByText(/页面不会自动登记项目/)).toBeInTheDocument();
  });
});
