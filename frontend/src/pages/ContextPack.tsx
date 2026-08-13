import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getBrief, getContextPack, getProjects } from "../lib/api";
import { Card, EmptyState, PageHeader, QueryBoundary } from "../components/ui";
import { ConfidenceTag, StatusBadge } from "../components/badges";
import { ProjectSelect } from "../components/controls";
import { ContextPackView } from "../components/ContextPackView";

export function ContextPack() {
  const [params, setParams] = useSearchParams();
  const projects = useQuery({ queryKey: ["projects"], queryFn: getProjects });
  const firstId = Object.keys(projects.data ?? {}).sort()[0] ?? "";
  const project = params.get("project") ?? firstId;
  const [copied, setCopied] = useState(false);

  const context = useQuery({
    queryKey: ["context", project],
    queryFn: () => getContextPack(project),
    enabled: Boolean(project),
  });
  const brief = useQuery({
    queryKey: ["brief", project],
    queryFn: () => getBrief(project),
    enabled: Boolean(project),
  });

  const setProject = (v: string) =>
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (v) next.set("project", v);
      else next.delete("project");
      return next;
    });

  const copyJson = async () => {
    if (!context.data) return;
    await navigator.clipboard.writeText(JSON.stringify(context.data, null, 2));
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className="space-y-4">
      <PageHeader
        title="上下文包（Agent 接管视图）"
        description="来源: /insights/context?project= + /advanced/brief?project= · 可整包复制交给 Agent"
        right={
          <div className="flex items-center gap-2">
            <ProjectSelect value={project} onChange={setProject} allowAll={false} />
            <button
              onClick={copyJson}
              disabled={!context.data}
              className="rounded-md border border-line px-3 py-1.5 text-xs text-ink2 hover:border-primary hover:text-primary disabled:opacity-40"
            >
              {copied ? "已复制 ✓" : "复制 JSON"}
            </button>
          </div>
        }
      />

      {!project ? (
        <EmptyState text="无项目" />
      ) : (
        <>
          {/* Brief 摘要行 */}
          <QueryBoundary query={brief}>
            {(b) => (
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <Card>
                  <div className="text-xs text-ink3">健康评分</div>
                  <div
                    className={`mt-1 text-2xl font-semibold tabular-nums ${
                      b.health.score >= 70 ? "text-passed" : b.health.score >= 40 ? "text-warning" : "text-critical"
                    }`}
                  >
                    {b.health.score}
                  </div>
                  <div className="mt-1 text-[10px] text-ink3">来源: brief.health</div>
                </Card>
                <Card>
                  <div className="text-xs text-ink3">风险</div>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {Object.entries(b.risks.risks).map(([k, level]) => (
                      <span key={k} className="text-[10px] text-ink2">
                        {k} <StatusBadge status={level} />
                      </span>
                    ))}
                  </div>
                  <div className="mt-1"><ConfidenceTag value={b.risks.confidence} /></div>
                </Card>
                <Card>
                  <div className="text-xs text-ink3">知识卡片</div>
                  <div className="mt-1 text-2xl font-semibold tabular-nums text-ink">{b.knowledge_cards.length}</div>
                  <div className="mt-1 text-[10px] text-ink3">来源: brief.knowledge_cards</div>
                </Card>
              </div>
            )}
          </QueryBoundary>

          {/* 知识卡片 */}
          <QueryBoundary query={brief}>
            {(b) =>
              b.knowledge_cards.length === 0 ? null : (
                <Card title={`知识卡片（${b.knowledge_cards.length}）`} subtitle="来源: brief.knowledge_cards（项目经验）">
                  <ul className="grid gap-2 md:grid-cols-2">
                    {b.knowledge_cards.map((k, i) => (
                      <li key={i} className="rounded-md border border-line/60 px-3 py-2">
                        <div className="flex items-center gap-2">
                          <span className="text-sm text-ink">{k.title}</span>
                          <StatusBadge status={k.status} />
                          <ConfidenceTag value={k.confidence} />
                        </div>
                        {k.experience && <p className="mt-1 text-xs text-ink3">{k.experience}</p>}
                      </li>
                    ))}
                  </ul>
                </Card>
              )
            }
          </QueryBoundary>

          {/* 主上下文包 */}
          <QueryBoundary query={context}>
            {(d) => <ContextPackView data={d} />}
          </QueryBoundary>

          <p className="text-[10px] text-ink3">
            复制 JSON 会把完整 context pack（含 evidence ID）交给下一会话使用。
            {brief.data ? ` brief 生成于 ${brief.data.generated_at}（UTC）` : ""}
          </p>
        </>
      )}
    </div>
  );
}
