import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getSimpleAnalytics, getSimpleKnowledge } from "../lib/api";
import { Card, PageHeader, QueryBoundary } from "../components/ui";

function scopeText(scope: unknown): string | null {
  if (!scope) return null;
  if (typeof scope === "string") return scope;
  if (typeof scope === "object") {
    return Object.entries(scope as Record<string, unknown>)
      .map(([key, value]) => `${key}: ${String(value)}`)
      .join(" · ");
  }
  return String(scope);
}

export function SimpleKnowledge() {
  const [project, setProject] = useState("");
  const [kind, setKind] = useState("");
  const [search, setSearch] = useState("");
  const knowledge = useQuery({
    queryKey: ["simple-knowledge", project],
    queryFn: () => getSimpleKnowledge(project || undefined),
  });
  const analytics = useQuery({ queryKey: ["simple-analytics", 30], queryFn: () => getSimpleAnalytics(30) });
  const projectNames = analytics.data?.project_names ?? {};

  return (
    <div className="space-y-4">
      <PageHeader
        title="结论与知识"
        description="把散落在日报、实验和 Agent 交接里的可复用结论集中起来"
        right={(
          <select
            value={project}
            onChange={(event) => setProject(event.target.value)}
            className="rounded-md border border-line bg-card px-3 py-1.5 text-sm text-ink outline-none focus:border-primary"
          >
            <option value="">全部项目</option>
            {Object.entries(projectNames).filter(([id]) => id !== "unassigned").map(([id, name]) => (
              <option key={id} value={id}>{name}</option>
            ))}
          </select>
        )}
      />

      <div className="rounded-lg border border-primary/20 bg-primary/5 px-4 py-3 text-xs leading-5 text-ink2">
        <span className="font-medium text-ink">什么会出现在这里？</span>
        只显示日报明确写出的研究结论、研究决策和待验证假设。构建成功、提交代码、上传文件、测试通过等过程结果仍在每日研究记录里，不再冒充知识。
      </div>

      <QueryBoundary
        query={knowledge}
        isEmpty={(data) => data.items.length === 0}
        emptyText="还没有形成可复用的结论"
        emptyDetail="当日报或 Agent 交接写入明确的 decision / hypothesis 后，这里会按项目自动汇总。"
      >
        {(data) => {
          const needle = search.trim().toLocaleLowerCase();
          const items = data.items.filter((item) => {
            if (kind && item.kind !== kind) return false;
            if (!needle) return true;
            return `${item.title} ${item.detail ?? ""} ${scopeText(item.scope) ?? ""}`.toLocaleLowerCase().includes(needle);
          });
          return (
            <div className="space-y-4">
              <div className="flex flex-col gap-3 rounded-lg border border-line bg-card px-4 py-3 sm:flex-row sm:items-center">
                <select
                  value={kind}
                  onChange={(event) => setKind(event.target.value)}
                  className="rounded-md border border-line bg-canvas px-3 py-1.5 text-sm text-ink outline-none focus:border-primary"
                >
                  <option value="">全部类型</option>
                  <option value="研究结论">研究结论</option>
                  <option value="研究决策">研究决策</option>
                  <option value="假设">待验证假设</option>
                </select>
                <input
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder="搜索结论、参数或模型"
                  className="min-w-0 flex-1 rounded-md border border-line bg-canvas px-3 py-1.5 text-sm text-ink outline-none placeholder:text-ink3 focus:border-primary"
                />
                <span className="shrink-0 text-xs text-ink3">
                  显示 {items.length} / {data.summary?.shown ?? data.items.length} 条
                </span>
              </div>
              {data.summary && (
                <p className="text-xs text-ink3">
                  已从知识页移走 {data.summary.hidden_task_results.toLocaleString()} 条普通过程结果，并合并 {data.summary.deduplicated.toLocaleString()} 条重复结论；这些内容没有删除，仍可在日报中查看。
                </p>
              )}
              {items.length === 0 ? (
                <div className="rounded-lg border border-dashed border-line px-4 py-10 text-center text-sm text-ink3">当前筛选条件下没有结论</div>
              ) : (
                <div className="grid gap-4 lg:grid-cols-2">
                  {items.map((item, index) => (
                    <Card
                      key={`${item.project_id}-${item.date}-${index}`}
                      title={item.title}
                      subtitle={`${projectNames[item.project_id ?? ""] ?? item.project_id ?? "未归类"} · ${item.date}`}
                      right={<span className={`rounded-full px-2 py-0.5 text-[10px] ${item.kind.includes("结论") ? "bg-passed/10 text-passed" : "bg-warning/10 text-warning"}`}>{item.kind}</span>}
                    >
                      <div className="space-y-2 text-sm leading-6 text-ink2">
                        {item.detail && <p>{item.detail}</p>}
                        {scopeText(item.scope) && <p className="text-xs text-ink3">适用范围：{scopeText(item.scope)}</p>}
                        <p className="text-xs text-ink3">来源 / 状态：{item.confidence}</p>
                      </div>
                    </Card>
                  ))}
                </div>
              )}
            </div>
          );
        }}
      </QueryBoundary>
    </div>
  );
}
