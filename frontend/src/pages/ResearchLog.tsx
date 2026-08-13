import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { DailyReportView } from "../components/DailyReportView";
import { PageHeader, QueryBoundary } from "../components/ui";
import { getSourceDailyReport, getSourceReportDates } from "../lib/api";

export function ResearchLog() {
  const [date, setDate] = useState("");
  const [project, setProject] = useState("");
  const dates = useQuery({ queryKey: ["source-report-dates"], queryFn: getSourceReportDates });
  const report = useQuery({
    queryKey: ["source-daily-report", date || "latest"],
    queryFn: () => getSourceDailyReport(date || undefined),
    refetchInterval: 60_000,
  });

  const selectedDate = date || dates.data?.latest || "";
  const projects = report.data?.project_ids ?? [];

  return (
    <div className="space-y-4">
      <PageHeader
        title="研究记录"
        description="按日期或项目阅读原 Daily Report，不展示底层事件流水"
        right={(
          <div className="flex flex-wrap gap-2">
            <select
              aria-label="记录日期"
              value={selectedDate}
              onChange={(event) => setDate(event.target.value)}
              className="rounded-md border border-line bg-card px-3 py-1.5 text-sm text-ink outline-none focus:border-primary"
            >
              {(dates.data?.dates ?? []).map((value) => <option key={value} value={value}>{value}</option>)}
            </select>
            <select
              aria-label="选择项目"
              value={project}
              onChange={(event) => setProject(event.target.value)}
              className="rounded-md border border-line bg-card px-3 py-1.5 text-sm text-ink outline-none focus:border-primary"
            >
              <option value="">全部项目</option>
              {projects.map((id) => <option key={id} value={id}>{report.data?.project_names?.[id] ?? id}</option>)}
            </select>
          </div>
        )}
      />

      <QueryBoundary query={report}>
        {(data) => <DailyReportView report={data} project={project} />}
      </QueryBoundary>
    </div>
  );
}
