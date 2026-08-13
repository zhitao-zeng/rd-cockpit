import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PageHeader } from "../components/ui";
import { FEATURE_GUIDES } from "../lib/featureGuides";

describe("功能说明", () => {
  it("为当前十个入口提供完整说明", () => {
    expect(FEATURE_GUIDES).toHaveLength(10);
    for (const item of FEATURE_GUIDES) {
      expect(item.purpose.length).toBeGreaterThan(10);
      expect(item.source.length).toBeGreaterThan(10);
      expect(item.reading.length).toBeGreaterThan(10);
      expect(item.caution.length).toBeGreaterThan(10);
    }
  });

  it("在页头直接展示用途、来源、看法和口径", () => {
    render(<PageHeader title="实验记录" />);
    expect(screen.getByRole("region", { name: "实验记录功能说明" })).toBeInTheDocument();
    expect(screen.getByText("怎么用这一页")).toBeInTheDocument();
    expect(screen.getByText("数据来源：")).toBeInTheDocument();
    expect(screen.getByText("建议看法：")).toBeInTheDocument();
    expect(screen.getByText("注意口径：")).toBeInTheDocument();
  });
});
