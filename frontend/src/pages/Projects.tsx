import { Link } from "react-router-dom";
import { useQueries, useQuery } from "@tanstack/react-query";
import { getHealthScore, getProjects } from "../lib/api";
import { buildProjectView, PROJECT_STATUS_LABEL, type ProjectStatus } from "../lib/adapters";
import { fmtPct, fmtRelative } from "../lib/format";
import { Card, DataTable, PageHeader, QueryBoundary } from "../components/ui";
import { DirtyBadge, ProgressBar, StatusBadge } from "../components/badges";
import type { HealthInfo } from "../lib/types";

const STATUS_ORDER: Record<ProjectStatus, number> = {
  blocked: 0, stale: 1, active: 2, done: 3, dormant: 4, historical: 5,
};

export function Projects() {
  const projects = useQuery({ queryKey: ["projects"], queryFn: getProjects });
  const ids = Object.keys(projects.data ?? {}).sort();
  const healthQueries = useQueries({
    queries: ids.map((id) => ({ queryKey: ["health", id], queryFn: () => getHealthScore(id) })),
  });
  const healthById: Record<string, HealthInfo> = {};
  ids.forEach((id, i) => {
    const data = healthQueries[i]?.data;
    if (data) healthById[id] = data;
  });

  return (
    <div>
      <PageHeader title="项目" description="当前项目优先展示；休眠和历史项目保留记录，但不混入当前推进状态" />
      <Card pad={false}>
        <QueryBoundary
          query={projects}
          isEmpty={(d) => Object.keys(d).length === 0}
          emptyText="没有配置项目"
          emptyDetail="config/projects.yaml 为空"
        >
          {(data) => {
            const views = Object.values(data)
              .map(buildProjectView)
              .sort((a, b) => STATUS_ORDER[a.status] - STATUS_ORDER[b.status] || a.id.localeCompare(b.id));
            return (
              <DataTable
                columns={[
                  { key: "name", label: "项目名称" },
                  { key: "goal", label: "当前目标" },
                  { key: "status", label: "状态" },
                  { key: "health", label: "健康评分", align: "right" },
                  { key: "progress", label: "验证进度", className: "min-w-[140px]" },
                  { key: "blockers", label: "Blocker", align: "right" },
                  { key: "last", label: "最近活动", align: "right" },
                  { key: "dirty", label: "Dirty" },
                ]}
                rows={views.map((v) => {
                  const health = healthById[v.id];
                  return {
                    name: (
                      <Link to={`/projects/${v.id}`} className="font-medium text-primary hover:underline">
                        {v.name}
                      </Link>
                    ),
                    goal: <span className="line-clamp-2 max-w-[280px] text-xs text-ink2">{v.goal ?? "—"}</span>,
                    status: <StatusBadge status={v.status} />,
                    health: health ? (
                      <span
                        className={`font-semibold tabular-nums ${
                          health.score >= 70 ? "text-passed" : health.score >= 40 ? "text-warning" : "text-critical"
                        }`}
                      >
                        {health.score}
                      </span>
                    ) : (
                      <span className="text-xs text-ink3">…</span>
                    ),
                    progress: (
                      <div>
                        <ProgressBar ratio={v.progress} tone={v.progress >= 1 ? "good" : "primary"} />
                        <span className="mt-0.5 block text-[10px] text-ink3">
                          {v.passedStages}/{v.totalStages} · {fmtPct(v.progress)}
                        </span>
                      </div>
                    ),
                    blockers: (
                      <span className={v.blockerCount > 0 ? "font-semibold text-critical" : "text-ink2"}>
                        {v.blockerCount}
                      </span>
                    ),
                    last: (
                      <span className="text-xs text-ink2" title={v.lastActivity ?? undefined}>
                        {v.lastActivity ? fmtRelative(v.lastActivity) : "—"}
                      </span>
                    ),
                    dirty: <DirtyBadge dirty={v.dirty} />,
                  };
                })}
                keyFn={(row) => {
                  const link = row.name as React.ReactElement<{ to: string }>;
                  return link.props.to;
                }}
              />
            );
          }}
        </QueryBoundary>
      </Card>
      <p className="mt-2 text-[10px] text-ink3">
        状态说明：{Object.entries(PROJECT_STATUS_LABEL).map(([k, v]) => `${v}(${k})`).join(" / ")}
      </p>
    </div>
  );
}
