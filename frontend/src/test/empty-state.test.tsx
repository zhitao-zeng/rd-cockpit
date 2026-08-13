import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { UseQueryResult } from "@tanstack/react-query";
import { DataTable, EmptyState, QueryBoundary } from "../components/ui";

describe("空数据渲染", () => {
  it("EmptyState 显示文案与详情", () => {
    render(<EmptyState text="暂无数据" detail="后端返回为空" />);
    expect(screen.getByTestId("empty-state")).toBeInTheDocument();
    expect(screen.getByText("暂无数据")).toBeInTheDocument();
    expect(screen.getByText("后端返回为空")).toBeInTheDocument();
  });

  it("DataTable 空行 → empty state，不渲染空表格", () => {
    render(<DataTable columns={[{ key: "a", label: "A" }]} rows={[]} />);
    expect(screen.getByTestId("empty-state")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("DataTable 支持自定义 empty 内容", () => {
    render(<DataTable columns={[{ key: "a", label: "A" }]} rows={[]} empty={<div>自定义空态</div>} />);
    expect(screen.getByText("自定义空态")).toBeInTheDocument();
  });

  it("QueryBoundary 空数据走 empty 分支", () => {
    const fakeQuery = {
      isPending: false,
      isError: false,
      data: [] as string[],
      error: null,
      refetch: vi.fn(),
    } as unknown as UseQueryResult<string[], Error>;
    render(
      <QueryBoundary query={fakeQuery} isEmpty={(d) => d.length === 0} emptyText="列表为空">
        {() => <div>有数据</div>}
      </QueryBoundary>,
    );
    expect(screen.getByText("列表为空")).toBeInTheDocument();
    expect(screen.queryByText("有数据")).not.toBeInTheDocument();
  });

  it("QueryBoundary 有数据渲染 children", () => {
    const fakeQuery = {
      isPending: false,
      isError: false,
      data: ["x"],
      error: null,
      refetch: vi.fn(),
    } as unknown as UseQueryResult<string[], Error>;
    render(
      <QueryBoundary query={fakeQuery} isEmpty={(d) => d.length === 0}>
        {(d) => <div>共 {d.length} 条</div>}
      </QueryBoundary>,
    );
    expect(screen.getByText("共 1 条")).toBeInTheDocument();
  });

  it("QueryBoundary 错误走 error 分支并显示信息", () => {
    const fakeQuery = {
      isPending: false,
      isError: true,
      data: undefined,
      error: new Error("boom"),
      refetch: vi.fn(),
    } as unknown as UseQueryResult<string[], Error>;
    render(
      <QueryBoundary query={fakeQuery}>{() => <div>有数据</div>}</QueryBoundary>,
    );
    expect(screen.getByTestId("error-state")).toBeInTheDocument();
    expect(screen.getByText("boom")).toBeInTheDocument();
  });
});
