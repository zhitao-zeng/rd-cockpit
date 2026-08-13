import { useQuery } from "@tanstack/react-query";
import { getLifeDashboard } from "../lib/api";
import { fmtDate, fmtDateTime, fmtTokens } from "../lib/format";
import type { LifeDashboard } from "../lib/types";
import { Card, QueryBoundary } from "./ui";

function daysText(days: number | null, today = "就是今天"): string {
  if (days === null) return "待配置";
  if (days === 0) return today;
  return `${days} 天`;
}

function ProgressLine({ label, value }: { label: string; value: number }) {
  const percentage = Math.min(100, Math.max(0, value * 100));
  return (
    <div>
      <div className="mb-1 flex justify-between text-[10px] text-ink3"><span>{label}</span><span>{percentage.toFixed(1)}%</span></div>
      <div className="h-1.5 overflow-hidden rounded-full bg-line"><div className="h-full rounded-full bg-primary" style={{ width: `${percentage}%` }} /></div>
    </div>
  );
}

function QuickCard({ label, value, detail, tone = "normal" }: { label: string; value: string; detail: string; tone?: "normal" | "fun" }) {
  return (
    <div className={`rounded-lg border px-4 py-3 ${tone === "fun" ? "border-primary/20 bg-primary/5" : "border-line bg-card"}`}>
      <div className="text-xs text-ink3">{label}</div>
      <div className="mt-1 text-xl font-semibold text-ink">{value}</div>
      <div className="mt-1 text-xs leading-5 text-ink3">{detail}</div>
    </div>
  );
}

function LifeContent({ data }: { data: LifeDashboard }) {
  const missing = [
    !data.employment.configured && "入职日期",
    !data.payday.configured && "发薪日",
    !data.annual_leave.configured && "年假余额",
  ].filter(Boolean) as string[];
  return (
    <div className="space-y-4">
      {missing.length > 0 && (
        <div className="rounded-lg border border-warning/25 bg-warning/5 px-4 py-2.5 text-xs leading-5 text-ink2">
          <span className="font-medium text-warning">还有 {missing.length} 项个人信息待填写：</span>
          {missing.join("、")}。配置文件：<span className="font-mono text-[10px] text-ink3">{data.config_path}</span>
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <QuickCard
          label="入职纪念"
          value={data.employment.day_number ? `第 ${data.employment.day_number} 天` : "待填写入职日期"}
          detail={data.employment.start_date ? `从 ${data.employment.start_date} 开始` : "填写后自动计算，不会猜测"}
        />
        <QuickCard
          label="距离周末 / 休息日"
          value={daysText(data.next_rest.days)}
          detail={data.next_rest.date ? `${fmtDate(data.next_rest.date)} · ${data.next_rest.reason}` : data.next_rest.reason}
        />
        <QuickCard
          label="下一个法定假期"
          value={data.next_holiday.available ? `${data.next_holiday.name} · ${daysText(data.next_holiday.days)}` : "等待假期安排"}
          detail={data.next_holiday.start ? `${data.next_holiday.start} 开始，共 ${data.next_holiday.duration_days} 天` : "当前年度没有可用数据"}
        />
        <div className="rounded-lg border border-line bg-card px-4 py-3">
          <div className="mb-2 text-xs text-ink3">时间进度</div>
          <div className="space-y-2">
            <ProgressLine label="本周" value={data.progress.week} />
            <ProgressLine label="本月" value={data.progress.month} />
            <ProgressLine label="今年" value={data.progress.year} />
          </div>
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <QuickCard label="距离发薪日" value={daysText(data.payday.days, "今天发薪")} detail={data.payday.date ? `${data.payday.date} · ${data.payday.rule === "last_day" ? "每月最后一天" : `每月 ${data.payday.day} 日`}` : "在个人配置中填写日期"} />
        <QuickCard label="今年剩余年假" value={data.annual_leave.configured ? `${data.annual_leave.remaining} 天` : "待填写年假"} detail={data.annual_leave.configured && data.annual_leave.total !== null && data.annual_leave.used !== null ? `总计 ${data.annual_leave.total} 天 · 已用 ${data.annual_leave.used} 天` : data.annual_leave.configured ? "当前手工填写的剩余额度" : "支持半天，例如 2.5"} />
        <QuickCard label={`${data.research_weather.icon} 今日科研天气`} value={data.research_weather.name} detail={data.research_weather.detail} tone="fun" />
        <QuickCard label={`${data.gpu_pet.icon} GPU 小宠物`} value={data.gpu_pet.state} detail={`${data.gpu_pet.detail}${data.gpu_pet.observed_at ? ` · ${fmtDateTime(data.gpu_pet.observed_at)}` : ""}`} tone="fun" />
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card title="研究记录小成就" subtitle="只做回顾，不做绩效评价">
          <div className="grid grid-cols-2 gap-3">
            <div><div className="text-xs text-ink3">当前连续记录</div><div className="mt-1 text-xl font-semibold text-passed">{data.report_streak.current} 天</div></div>
            <div><div className="text-xs text-ink3">历史最长</div><div className="mt-1 text-xl font-semibold text-ink">{data.report_streak.longest} 天</div></div>
            <div><div className="text-xs text-ink3">累计日报</div><div className="mt-1 text-xl font-semibold text-ink">{data.report_streak.total_reports} 份</div></div>
            <div><div className="text-xs text-ink3">最热闹 Agent 日</div><div className="mt-1 text-xl font-semibold text-ink">{(data.longest_agent_day.minutes / 60).toFixed(1)} agent-h</div></div>
          </div>
          <p className="mt-3 text-[10px] leading-4 text-ink3">
            {data.longest_agent_day.date ? `${data.longest_agent_day.date}；` : ""}多个并行 Agent 会话累计，可能包含等待，不等于人工专注时长。
          </p>
        </Card>

        <Card title="Token 趣味换算" subtitle="不是工作量指标">
          <div className="text-3xl font-semibold text-primary">≈ {data.token_books.books} 本书</div>
          <p className="mt-2 text-sm text-ink2">最近日报统计了约 {fmtTokens(data.token_books.tokens)} Token。</p>
          <p className="mt-2 text-[10px] leading-4 text-ink3">{data.token_books.note}</p>
        </Card>

        <Card title="去年今日">
          <div className="text-xs text-ink3">{data.last_year_today.date}</div>
          <p className="mt-2 line-clamp-5 text-sm leading-6 text-ink2">{data.last_year_today.summary}</p>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="今日随机回顾" subtitle="每天从历史明确结论中抽一条">
          {data.random_knowledge.available ? (
            <>
              <p className="text-sm leading-7 text-ink2">{data.random_knowledge.text}</p>
              <p className="mt-2 text-xs text-ink3">记录于 {data.random_knowledge.date}</p>
            </>
          ) : <p className="text-sm text-ink3">历史日报中还没有可抽取的明确结论。</p>}
        </Card>

        <Card title="接下来的小里程碑">
          <div className="grid gap-2 sm:grid-cols-2">
            {data.milestones.map((item) => (
              <div key={`${item.name}-${item.date}`} className="rounded-md border border-line bg-page/25 px-3 py-2">
                <div className="text-sm text-ink">{item.name}</div>
                <div className="mt-0.5 text-xs text-ink3">还有 {item.days} 天{item.date ? ` · ${item.date}` : ""}</div>
              </div>
            ))}
          </div>
        </Card>
      </div>

      <details className="rounded-lg border border-line bg-card px-4 py-3">
        <summary className="cursor-pointer text-sm font-medium text-ink">项目年龄与纪念日</summary>
        <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          {data.projects.map((project) => (
            <div key={project.project_id} className="rounded-md border border-line bg-page/25 px-3 py-2">
              <div className="text-xs text-ink3">{project.name}</div>
              <div className="mt-1 text-lg font-semibold text-ink">{project.days ? `第 ${project.days} 天` : "日期未知"}</div>
              <div className="mt-0.5 text-[10px] text-ink3">{project.start_date ?? "可在 personal.yaml 填写"}</div>
            </div>
          ))}
        </div>
      </details>
    </div>
  );
}

export function LifeBar() {
  const life = useQuery({ queryKey: ["life-dashboard"], queryFn: () => getLifeDashboard(), refetchInterval: 15 * 60_000 });
  return (
    <section className="space-y-3">
      <div>
        <h2 className="text-lg font-semibold text-ink">今天</h2>
        <p className="mt-0.5 text-xs text-ink3">生活倒计时、项目纪念和一点不严肃的研究统计</p>
      </div>
      <QueryBoundary query={life}>{(data) => <LifeContent data={data} />}</QueryBoundary>
    </section>
  );
}
