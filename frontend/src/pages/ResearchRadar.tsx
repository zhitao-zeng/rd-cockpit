import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getResearchRadar } from "../lib/api";
import { fmtDate, fmtDateTime } from "../lib/format";
import type { ResearchRadarItem } from "../lib/types";
import { Card, PageHeader, QueryBoundary } from "../components/ui";

const tierStyle: Record<ResearchRadarItem["quality_tier"], string> = {
  A: "border-passed/30 bg-passed/10 text-passed",
  B: "border-primary/30 bg-primary/10 text-primary",
  C: "border-warning/30 bg-warning/10 text-warning",
  D: "border-critical/30 bg-critical/10 text-critical",
};

function ScoreChip({ label, value }: { label: string; value: number }) {
  return (
    <span className="rounded border border-line bg-page/40 px-2 py-1 text-[11px] text-ink3">
      {label} <strong className="font-semibold text-ink">{value}</strong>
    </span>
  );
}

function PaperCard({ paper }: { paper: ResearchRadarItem }) {
  const authorText = paper.authors.length
    ? `${paper.authors.join("、")}${paper.authors.length >= 4 ? " 等" : ""}`
    : "作者信息暂缺";
  return (
    <Card
      title={<a href={paper.url} target="_blank" rel="noreferrer" className="whitespace-normal text-base leading-6 hover:text-primary">{paper.title_zh || paper.title}</a>}
      subtitle={paper.focus}
      right={(
        <div className="flex flex-wrap items-center justify-end gap-1.5">
          {paper.is_new && <span className="rounded-full bg-passed/10 px-2 py-0.5 text-[10px] text-passed">新发现</span>}
          <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${tierStyle[paper.quality_tier]}`}>
            {paper.quality_tier} 级 · {paper.total_score}
          </span>
          <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[10px] text-primary">{fmtDate(paper.publication_date)}</span>
        </div>
      )}
    >
      <div className="space-y-3 text-sm leading-6 text-ink2">
        <div className="flex flex-wrap gap-1.5">
          <ScoreChip label="相关" value={paper.relevance_score} />
          <ScoreChip label="质量" value={paper.quality_score} />
          <ScoreChip label="实用" value={paper.practical_score} />
          {paper.preferred_venue && <span className="rounded border border-passed/25 bg-passed/5 px-2 py-1 text-[11px] text-passed">优先 venue</span>}
        </div>

        {paper.summary_zh ? (
          <div className="rounded-lg border border-primary/20 bg-primary/5 px-3.5 py-3">
            <div className="mb-1 text-xs font-semibold text-primary">中文速读</div>
            <p className="text-ink">{paper.summary_zh}</p>
          </div>
        ) : (
          <div className="rounded-lg border border-warning/25 bg-warning/5 px-3 py-2 text-xs text-warning">
            中文摘要暂不可用，可以展开查看英文摘要。
          </div>
        )}

        {paper.key_points_zh.length > 0 && (
          <div>
            <div className="text-xs font-medium text-ink">抓住这几点</div>
            <ul className="mt-1 grid gap-1.5 text-sm text-ink2">
              {paper.key_points_zh.map((text, index) => (
                <li key={`${text}-${index}`} className="flex gap-2">
                  <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-primary" />
                  <span>{text}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="rounded-md border border-line bg-page/30 px-3 py-2">
          <div className="text-xs font-medium text-ink">为什么值得你看</div>
          <p className="mt-0.5 text-sm">{paper.read_value_zh || paper.why_relevant}</p>
        </div>

        {(paper.quality_reasons.length > 0 || paper.quality_risks.length > 0) && (
          <div className="grid gap-2 md:grid-cols-2">
            <div className="rounded-md border border-passed/20 bg-passed/5 px-3 py-2">
              <div className="text-xs font-medium text-passed">为什么进入推荐</div>
              <ul className="mt-1 text-xs leading-5 text-ink2">
                {(paper.quality_reasons.length ? paper.quality_reasons : ["项目相关度达到候选阈值"]).map((text) => <li key={text}>· {text}</li>)}
              </ul>
            </div>
            <div className="rounded-md border border-warning/20 bg-warning/5 px-3 py-2">
              <div className="text-xs font-medium text-warning">阅读前注意</div>
              <ul className="mt-1 text-xs leading-5 text-ink2">
                {(paper.quality_risks.length ? paper.quality_risks : ["暂无明显元数据风险"]).map((text) => <li key={text}>· {text}</li>)}
              </ul>
            </div>
          </div>
        )}

        <details className="rounded-md border border-line px-3 py-2 text-xs">
          <summary className="cursor-pointer font-medium text-ink2 hover:text-primary">英文原文、作者和与你工作的连接点</summary>
          <div className="mt-3 space-y-3 text-ink3">
            {paper.title_zh && <div><div className="font-medium text-ink2">英文标题</div><p>{paper.title}</p></div>}
            <div>
              <div className="font-medium text-ink2">英文摘要</div>
              <p className="mt-1 whitespace-pre-line leading-5">{paper.abstract || "OpenAlex 暂未提供摘要。当前中文导读仅依据标题和元数据。"}</p>
            </div>
            {paper.local_context.length > 0 && (
              <div>
                <div className="font-medium text-ink2">你最近做过的相关工作</div>
                <ul className="mt-1 space-y-1">{paper.local_context.map((text, index) => <li key={`${text}-${index}`}>· {text}</li>)}</ul>
              </div>
            )}
            <div className="flex flex-wrap gap-x-4 gap-y-1">
              <span>{authorText}</span>
              {paper.venue && <span>{paper.venue}</span>}
              <span>被引 {paper.cited_by_count}</span>
              {paper.fwci > 0 && <span>FWCI {paper.fwci.toFixed(2)}</span>}
              <span>{paper.work_type}</span>
              <span>{paper.relationship}</span>
            </div>
          </div>
        </details>

        <div className="flex flex-wrap items-center gap-3 border-t border-line pt-2 text-xs">
          <a href={paper.url} target="_blank" rel="noreferrer" className="text-primary hover:underline">查看论文页面 ↗</a>
          {paper.pdf_url && <a href={paper.pdf_url} target="_blank" rel="noreferrer" className="text-ink2 hover:text-primary">PDF ↗</a>}
          <span className="ml-auto text-[10px] text-ink3">
            {paper.summary_basis === "abstract" ? "基于摘要" : "仅据标题"}
            {paper.summary_model && ` · ${paper.summary_model}`}
          </span>
        </div>
      </div>
    </Card>
  );
}

export function ResearchRadar() {
  const [project, setProject] = useState("");
  const [qualityView, setQualityView] = useState<"recommended" | "all">("recommended");
  const key = ["research-radar", project];
  const radar = useQuery({
    queryKey: key,
    queryFn: () => getResearchRadar(project || undefined),
    staleTime: 10 * 60_000,
  });

  return (
    <div className="space-y-4">
      <PageHeader
        title="研究雷达"
        description="先用中文快速判断值不值得读，英文原文按需展开"
        right={(
          <div className="flex items-center gap-2">
            <select
              value={qualityView}
              onChange={(event) => setQualityView(event.target.value as "recommended" | "all")}
              className="rounded-md border border-line bg-card px-3 py-1.5 text-sm text-ink outline-none focus:border-primary"
            >
              <option value="recommended">只看 A/B 级</option>
              <option value="all">包括 C 级候选</option>
            </select>
            <select
              value={project}
              onChange={(event) => setProject(event.target.value)}
              className="rounded-md border border-line bg-card px-3 py-1.5 text-sm text-ink outline-none focus:border-primary"
            >
              <option value="">全部项目</option>
              {Object.entries(radar.data?.projects ?? {}).map(([id, value]) => (
                <option key={id} value={id}>{value.name}</option>
              ))}
            </select>
            <span className="rounded-md border border-line px-3 py-1.5 text-xs text-ink3">每日后台更新</span>
          </div>
        )}
      />

      <div className="rounded-lg border border-primary/20 bg-primary/5 px-4 py-3 text-xs leading-5 text-ink2">
        <span className="font-medium text-ink">现在不是按“最新”硬塞论文：</span>
        相关度 40 分、研究质量 35 分、实际价值 25 分。默认只展示 A/B 级；C 级保留在候选视图，D 级直接排除。更新时保留高质量锚点，其余位置优先换成从未展示过的合格论文。
      </div>

      <QueryBoundary
        query={radar}
        isEmpty={(data) => data.items.filter((item) =>
          (!project || item.project_id === project)
          && (qualityView === "all" || item.quality_tier === "A" || item.quality_tier === "B")
        ).length === 0}
        emptyText={qualityView === "recommended" ? "当前没有达到 A/B 级的论文" : "暂时没有检索到合格论文"}
        emptyDetail={qualityView === "recommended" ? "可以切换到“包括 C 级候选”，但这些论文需要更谨慎判断。" : "系统不会为了刷新感而填充 D 级论文。"}
      >
        {(data) => (
          <>
            {(() => {
              const visible = data.items.filter((item) =>
                (!project || item.project_id === project)
                && (qualityView === "all" || item.quality_tier === "A" || item.quality_tier === "B")
              );
              const summarized = visible.filter((item) => item.summary_zh).length;
              const newCount = visible.filter((item) => item.is_new).length;
              const grouped = visible.reduce<Record<string, ResearchRadarItem[]>>((output, item) => {
                (output[item.project_id] ??= []).push(item);
                return output;
              }, {});
              return (
                <>
                  <div className="grid gap-2 rounded-lg border border-line bg-card px-4 py-3 text-xs text-ink3 sm:grid-cols-4">
                    <span>近 {data.lookback_days} 天 · {visible.length} 篇论文</span>
                    <span className={newCount > 0 ? "text-passed" : ""}>本轮新发现 {newCount} 篇</span>
                    <span className={summarized === visible.length ? "text-passed" : "text-warning"}>中文摘要 {summarized}/{visible.length}</span>
                    <span className="sm:text-right">
                      更新于 {fmtDateTime(data.generated_at)}
                      {data.cached && " · 本地缓存"}
                      {data.stale && <span className="text-warning"> · 缓存可能过期</span>}
                    </span>
                  </div>

                  {data.selection && (
                    <div className="rounded-lg border border-line bg-card px-4 py-2 text-xs text-ink3">
                      候选 {data.selection.candidate_count} 篇 · 达标 {data.selection.eligible_count} 篇 · 排除 D 级 {data.selection.excluded_count} 篇 · {data.selection.method}
                    </div>
                  )}

                  {data.warnings.length > 0 && (
                    <details className="rounded-lg border border-warning/30 bg-warning/5 px-4 py-3 text-xs text-ink2">
                      <summary className="cursor-pointer text-warning">{data.warnings.length} 个检索或摘要提示</summary>
                      <ul className="mt-2 space-y-1">{data.warnings.map((warning) => <li key={warning}>· {warning}</li>)}</ul>
                    </details>
                  )}

                  <div className="space-y-7">
                    {Object.entries(grouped).map(([projectId, papers]) => (
                      <section key={projectId} className="space-y-3">
                        <div className="flex items-end justify-between border-b border-line pb-2">
                          <div>
                            <h2 className="text-base font-semibold text-ink">{data.projects[projectId]?.name || papers[0].project_name}</h2>
                            <p className="mt-0.5 text-xs text-ink3">{data.projects[projectId]?.topics.join(" · ")}</p>
                          </div>
                          <span className="text-xs text-ink3">{papers.length} 篇</span>
                        </div>
                        <div className="grid gap-4 xl:grid-cols-2">
                          {papers.map((paper) => <PaperCard key={`${paper.project_id}-${paper.id}`} paper={paper} />)}
                        </div>
                      </section>
                    ))}
                  </div>
                </>
              );
            })()}
          </>
        )}
      </QueryBoundary>
    </div>
  );
}
