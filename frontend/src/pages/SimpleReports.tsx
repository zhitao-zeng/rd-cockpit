import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { DailyReportView } from "../components/DailyReportView";
import { PageHeader, QueryBoundary } from "../components/ui";
import { getSourceDailyReport, getSourceReportDates } from "../lib/api";

export function SimpleReports() {
  const [date, setDate] = useState("");
  const dates = useQuery({ queryKey: ["source-report-dates"], queryFn: getSourceReportDates });
  const report = useQuery({
    queryKey: ["source-daily-report", date || "latest"],
    queryFn: () => getSourceDailyReport(date || undefined),
  });
  const selectedDate = date || dates.data?.latest || "";

  return (
    <div className="space-y-4">
      <PageHeader
        title="日报归档"
        description={`直接浏览原日报目录中的 ${dates.data?.dates.length ?? 0} 份历史记录`}
        right={(
          <select
            aria-label="报告日期"
            value={selectedDate}
            onChange={(event) => setDate(event.target.value)}
            className="rounded-md border border-line bg-card px-3 py-1.5 text-sm text-ink outline-none focus:border-primary"
          >
            {(dates.data?.dates ?? []).map((value) => <option key={value} value={value}>{value}</option>)}
          </select>
        )}
      />
      <QueryBoundary query={report}>
        {(data) => <DailyReportView report={data} />}
      </QueryBoundary>
    </div>
  );
}
