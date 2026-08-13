import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { DailyReportView } from "../components/DailyReportView";
import { LifeBar } from "../components/LifeBar";
import { ProjectDiscoveryPanel } from "../components/ProjectDiscoveryPanel";
import { PageHeader, QueryBoundary } from "../components/ui";
import { getSourceDailyReport } from "../lib/api";

export function SimpleOverview() {
  const report = useQuery({
    queryKey: ["source-daily-report", "latest"],
    queryFn: () => getSourceDailyReport(),
    refetchInterval: 60_000,
  });

  return (
    <div className="space-y-4">
      <LifeBar />
      <div className="border-t border-line pt-4">
      <PageHeader
        title="研究日报"
        description="从你原来的 Daily Report 开始：做了什么、为什么、结果、关键文件"
        right={<Link to="/records" className="rounded-md border border-line px-3 py-1.5 text-xs text-ink2 hover:border-primary hover:text-primary">选择日期或项目</Link>}
      />
      <ProjectDiscoveryPanel />
      <QueryBoundary query={report}>
        {(data) => <DailyReportView report={data} compact />}
      </QueryBoundary>
      </div>
    </div>
  );
}
