import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getSimpleAnalytics } from "../lib/api";
import { fmtTokens } from "../lib/format";
import { Chart } from "../components/Chart";
import { Card, EmptyState, PageHeader, QueryBoundary, StatCard } from "../components/ui";
import { axisBase, barSeries, baseChartOption, catColor, C, legendBase, lineSeries, tooltipBase } from "../lib/chartTheme";

export function SimpleAnalytics() {
  const [days, setDays] = useState(30);
  const query = useQuery({
    queryKey: ["simple-analytics", days],
    queryFn: () => getSimpleAnalytics(days),
    refetchInterval: 60_000,
  });

  return (
    <div className="space-y-4">
      <PageHeader
        title="数据分析"
        description="看每天的投入趋势，以及哪些项目消耗了 Agent Token"
        right={(
          <select
            value={days}
            onChange={(event) => setDays(Number(event.target.value))}
            className="rounded-md border border-line bg-card px-3 py-1.5 text-sm text-ink outline-none focus:border-primary"
          >
            <option value={7}>最近 7 天</option>
            <option value={30}>最近 30 天</option>
            <option value={90}>最近 90 天</option>
          </select>
        )}
      />

      <QueryBoundary query={query}>
        {(data) => <AnalyticsContent data={data} />}
      </QueryBoundary>
    </div>
  );
}

function AnalyticsContent({ data }: { data: Awaited<ReturnType<typeof getSimpleAnalytics>> }) {
  const derived = useMemo(() => {
    const dates = [...new Set(data.daily.map((item) => item.date))].sort();
    const byDate = new Map(dates.map((date) => [date, { tokens: 0, codex: 0, claude: 0, activities: 0, experiments: 0, conclusions: 0 }]));
    const byProject = new Map<string, number>();
    for (const item of data.daily) {
      const day = byDate.get(item.date);
      if (day) {
        day.tokens += item.tokens;
        day.codex += item.codex_tokens;
        day.claude += item.claude_tokens;
        day.activities += item.activities;
        day.experiments += item.experiments;
        day.conclusions += item.conclusions;
      }
      byProject.set(item.project_id, (byProject.get(item.project_id) ?? 0) + item.tokens);
    }
    return { dates, byDate, projects: [...byProject.entries()].filter(([, tokens]) => tokens > 0).sort((a, b) => b[1] - a[1]) };
  }, [data]);

  if (!data.daily.length) {
    return <EmptyState text="这段时间还没有可分析的数据" detail="正常使用 Codex / Claude Code 并同步后，这里会自动出现趋势图。" />;
  }

  const tokenValues = derived.dates.map((date) => derived.byDate.get(date)?.tokens ?? 0);
  const tokenAxis = derived.dates.map((date) => date.slice(5));
  return (
    <>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Agent Token（约）" value={fmtTokens(data.totals.tokens)} hint={`最近 ${data.days} 天`} tone="primary" />
        <StatCard label="可读工作记录" value={data.totals.activities} hint="已汇总的工作/结果条目" />
        <StatCard label="实验 / 评测 / 验证" value={data.totals.experiments} hint="日报中明确包含实验、评测、测试或验证的任务数" />
        <StatCard label="明确结论" value={data.totals.conclusions} hint="已写入知识汇总的结论" tone="good" />
      </div>

      {(data.agent_activity.totals.completed > 0 || data.agent_activity.totals.failed > 0) && (
        <Card title="Agent 实际执行概况" subtitle="来自 Codex / Claude Code 生命周期 Hook 的汇总，不展示难懂的原始事件">
          <div className="grid gap-3 sm:grid-cols-4">
            <StatCard label="涉及会话" value={data.agent_activity.totals.sessions} hint={`最近 ${data.days} 天`} />
            <StatCard label="成功操作" value={data.agent_activity.totals.completed} hint="工具与阶段完成的聚合计数" tone="good" />
            <StatCard label="失败操作" value={data.agent_activity.totals.failed} hint="用于发现执行质量，不等于实验失败" tone={data.agent_activity.totals.failed ? "bad" : undefined} />
            <StatCard label="可观测执行耗时" value={`${data.agent_activity.totals.duration_minutes.toFixed(1)} 分钟`} hint="仅统计 Hook 提供了耗时的操作" />
          </div>
          <p className="mt-3 text-[10px] leading-4 text-ink3">{data.agent_activity.explanation}</p>
        </Card>
      )}

      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="每天用了多少 Token" subtitle="看投入变化；峰值不代表产出更好，需要结合下方研究结果一起看">
          <Chart
            height={270}
            option={{
              ...baseChartOption(),
              tooltip: { ...tooltipBase, trigger: "axis", valueFormatter: (value: unknown) => fmtTokens(Number(value)) },
              xAxis: { type: "category", data: tokenAxis, ...axisBase() },
              yAxis: { type: "value", ...axisBase(), axisLabel: { color: C.ink2, formatter: (value: number) => fmtTokens(value) } },
              series: [lineSeries("Token", tokenValues, C.primary, { areaStyle: { color: "rgba(34,211,238,0.10)" } })],
            }}
          />
        </Card>

        <Card title="Codex 与 Claude 用量" subtitle="原日报按 Agent 来源统计；没有证据时不强行把 Token 分摊到项目">
          <Chart
            height={270}
            option={{
              ...baseChartOption(),
              tooltip: { ...tooltipBase, trigger: "axis", axisPointer: { type: "shadow" }, valueFormatter: (value: unknown) => fmtTokens(Number(value)) },
              legend: { ...legendBase() },
              xAxis: { type: "category", data: tokenAxis, ...axisBase() },
              yAxis: { type: "value", ...axisBase(), axisLabel: { color: C.ink2, formatter: (value: number) => fmtTokens(value) } },
              series: [
                barSeries("Codex", derived.dates.map((date) => derived.byDate.get(date)?.codex ?? 0), catColor(0), { stack: "agent" }),
                barSeries("Claude", derived.dates.map((date) => derived.byDate.get(date)?.claude ?? 0), catColor(1), { stack: "agent" }),
              ],
            }}
          />
        </Card>

        <Card title="Token 花在哪些项目" subtitle="按 session 的 cwd 和实际改动文件归属；证据不足的部分保留为“未按项目归属”" className="xl:col-span-2">
          <Chart
            height={300}
            option={{
              ...baseChartOption(),
              tooltip: { ...tooltipBase, trigger: "axis", axisPointer: { type: "shadow" }, valueFormatter: (value: unknown) => fmtTokens(Number(value)) },
              grid: { left: 8, right: 20, top: 12, bottom: 4, containLabel: true },
              xAxis: { type: "value", ...axisBase(), axisLabel: { color: C.ink2, formatter: (value: number) => fmtTokens(value) } },
              yAxis: { type: "category", data: derived.projects.map(([id]) => data.project_names[id] ?? id), ...axisBase() },
              series: [barSeries("Token", derived.projects.map(([, tokens]) => tokens), C.primary)],
            }}
          />
        </Card>

        <Card title="每天产生了什么" subtitle="工作记录、实验、结论是三种不同产出，不再用内部事件数混在一起" className="xl:col-span-2">
          <Chart
            height={280}
            option={{
              ...baseChartOption(),
              tooltip: { ...tooltipBase, trigger: "axis" },
              legend: { ...legendBase() },
              xAxis: { type: "category", data: tokenAxis, ...axisBase() },
              yAxis: { type: "value", minInterval: 1, ...axisBase() },
              series: [
                barSeries("工作记录", derived.dates.map((date) => derived.byDate.get(date)?.activities ?? 0), catColor(0)),
                barSeries("实验/评测/验证", derived.dates.map((date) => derived.byDate.get(date)?.experiments ?? 0), catColor(1)),
                barSeries("结论", derived.dates.map((date) => derived.byDate.get(date)?.conclusions ?? 0), catColor(2)),
              ],
            }}
          />
        </Card>
      </div>

      <p className="text-xs leading-5 text-ink3">
        {data.token_note ?? "Token 总量包含不同 Agent 的输入、输出与缓存口径，适合比较时间和项目趋势，不直接代表费用或工作质量。"}
      </p>
    </>
  );
}
