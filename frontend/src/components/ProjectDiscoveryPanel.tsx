import { useQuery } from "@tanstack/react-query";
import { getProjectDiscovery } from "../lib/api";
import type { ProjectDiscoveryCandidate, ProjectDiscoveryDecision } from "../lib/types";
import { Card, EmptyState, QueryBoundary } from "./ui";

const DECISION: Record<ProjectDiscoveryDecision, { label: string; style: string }> = {
  new_project: { label: "Codex 建议登记", style: "border-passed/30 bg-passed/10 text-passed" },
  existing_project: { label: "属于已有项目", style: "border-primary/30 bg-primary/10 text-primary" },
  temporary_or_reference: { label: "临时 / 参考仓库", style: "border-line bg-page/40 text-ink3" },
  insufficient_evidence: { label: "证据不足", style: "border-warning/30 bg-warning/10 text-warning" },
};

function Candidate({ item }: { item: ProjectDiscoveryCandidate }) {
  const review = item.review;
  const decision = review?.decision ?? "insufficient_evidence";
  const badge = DECISION[decision];
  return <article className="rounded-lg border border-line bg-page/30 px-3 py-3">
    <div className="flex flex-wrap items-start justify-between gap-2">
      <div>
        <div className="text-sm font-semibold text-ink">{review?.suggested_name || item.repo_name}</div>
        <div className="mt-0.5 font-mono text-[10px] text-ink3">{item.repo_path}</div>
      </div>
      <span className={`rounded-full border px-2 py-0.5 text-[10px] ${badge.style}`}>{badge.label}</span>
    </div>
    <p className="mt-2 text-xs leading-5 text-ink2">{review?.summary || "Codex 尚未完成审查，当前只保留仓库证据。"}</p>
    {(item.group_size ?? 1) > 1 && <p className="mt-1 text-[10px] leading-4 text-primary">同一项目包含 {item.group_size} 个仓库：{item.related_repos?.map((path) => path.split("/").pop()).join("、")}</p>}
    {review?.reason && <p className="mt-1 text-[10px] leading-4 text-ink3">判断依据：{review.reason}</p>}
    <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-ink3">
      <span>{item.session_count} 个 Session</span>
      <span>{item.write_evidence_count} 条写入路径证据</span>
      <span>{item.agents.join(" + ")}</span>
      {review && <span>置信度 {Math.round(review.confidence * 100)}%</span>}
    </div>
    {item.accept_command && <div className="mt-2 rounded border border-passed/20 bg-passed/5 px-2 py-1.5 text-[10px] text-ink2">
      确认登记：<code className="font-mono text-passed">{item.accept_command}</code>
    </div>}
  </article>;
}

export function ProjectDiscoveryPanel() {
  const query = useQuery({
    queryKey: ["project-discovery"], queryFn: getProjectDiscovery,
    staleTime: 5 * 60_000, refetchInterval: 10 * 60_000, retry: false,
  });
  return <Card title="新项目发现" subtitle="Session 路径先确定性筛选，Codex 再审查；页面不会自动登记项目">
    <QueryBoundary query={query}>
      {(data) => data.candidates.length === 0
        ? <EmptyState text="最近没有需要确认的新项目" detail="只访问一次的参考仓库不会自动进入正式项目列表。" />
        : <div className="space-y-3">
          <div className="text-[10px] leading-4 text-ink3">
            最近 {data.scan_days} 天扫描到 {data.counts.total_discovered} 个未登记仓库；当前只展示 {data.counts.new_projects} 个新项目和 {data.counts.insufficient_evidence + data.counts.pending_review} 个待判断项。另有 {data.counts.existing_projects} 个已归入现有项目、{data.counts.temporary_or_reference} 个临时或参考仓库，不占用你的确认列表。
          </div>
          <div className="grid gap-3 lg:grid-cols-2">{data.candidates.slice(0, 6).map((item) => <Candidate key={item.candidate_id} item={item} />)}</div>
        </div>}
    </QueryBoundary>
  </Card>;
}
