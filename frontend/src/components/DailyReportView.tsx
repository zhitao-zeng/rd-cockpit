import type { DailySupplement, SourceDailyReport, SourceReportTask } from "../lib/types";
import { fmtDateTime, fmtInt, fmtTokens, todayLocal } from "../lib/format";
import { Card, DataTable, EmptyState, StatCard } from "./ui";

function BulletText({ items, muted = false }: { items: string[]; muted?: boolean }) {
  if (!items.length) return <span className="text-xs text-ink3">日报未填写这一项</span>;
  return (
    <ul className={`space-y-1.5 ${muted ? "text-xs text-ink3" : "text-sm text-ink2"}`}>
      {items.map((item, index) => (
        <li key={`${item}-${index}`} className="flex gap-2 leading-6">
          <span className="mt-[10px] h-1 w-1 shrink-0 rounded-full bg-current opacity-60" />
          <span>{item}</span>
        </li>
      ))}
    </ul>
  );
}

function Field({ label, items, tone = "default" }: { label: string; items: string[]; tone?: "default" | "result" | "muted" }) {
  const box = tone === "result"
    ? "border-passed/20 bg-passed/5"
    : tone === "muted" ? "border-line/70 bg-page/30" : "border-line bg-page/20";
  return (
    <div className={`rounded-md border px-3 py-2.5 ${box}`}>
      <div className={`mb-1 text-xs font-medium ${tone === "result" ? "text-passed" : "text-ink"}`}>{label}</div>
      <BulletText items={items} muted={tone === "muted"} />
    </div>
  );
}

function Task({ task, compact = false }: { task: SourceReportTask; compact?: boolean }) {
  const conclusions = task.conclusions ?? [];
  if (compact) {
    return (
      <article className="py-4 first:pt-0 last:pb-0">
        <h4 className="text-sm font-semibold text-ink" title={`原日报标题：${task.title}`}>{task.display_title || task.title}</h4>
        {task.results.length > 0 && <div className="mt-2"><Field label="结果" items={task.results} tone="result" /></div>}
        {conclusions.length > 0 && <div className="mt-2"><Field label="明确结论" items={conclusions} tone="result" /></div>}
        <details className="mt-2 rounded-md border border-line/70 bg-page/20 px-3 py-2">
          <summary className="cursor-pointer text-xs text-ink2">查看做了什么、为什么和关键文件</summary>
          <div className="mt-3 grid gap-3 lg:grid-cols-2">
            <Field label="做了什么" items={task.did} />
            <Field label="为什么做" items={task.why} />
            <div className="lg:col-span-2"><Field label="关键文件" items={task.files} tone="muted" /></div>
            {task.evidence.length > 0 && <div className="lg:col-span-2"><Field label="证据" items={task.evidence} tone="muted" /></div>}
          </div>
        </details>
      </article>
    );
  }
  return (
    <article className="py-4 first:pt-0 last:pb-0">
      <h4 className="mb-3 text-sm font-semibold text-ink" title={`原日报标题：${task.title}`}>{task.display_title || task.title}</h4>
      <div className="grid gap-3 lg:grid-cols-2">
        <Field label="做了什么" items={task.did} />
        <Field label="得到的结果" items={task.results} tone="result" />
        {conclusions.length > 0 && <Field label="明确结论" items={conclusions} tone="result" />}
        <Field label="为什么做" items={task.why} />
        <Field label="关键文件" items={task.files} tone="muted" />
        {task.evidence.length > 0 && <div className="lg:col-span-2"><Field label="证据" items={task.evidence} tone="muted" /></div>}
      </div>
    </article>
  );
}

function ListCard({ title, items, tone }: { title: string; items: string[]; tone?: "warn" | "good" }) {
  const titleClass = tone === "warn" ? "text-warning" : tone === "good" ? "text-primary" : "text-ink";
  return (
    <Card title={<span className={titleClass}>{title}</span>}>
      <BulletText items={items} />
    </Card>
  );
}

function SupplementFacts({ data, project = "" }: { data: DailySupplement; project?: string }) {
  const projects = data.projects.filter((item) => !project || item.project_id === project);
  const rows = projects.map((item) => ({
    _key: item.project_id,
    project: <span className="font-medium text-ink">{item.name}</span>,
    sessions: item.sessions,
    agents: <span className="whitespace-nowrap text-xs text-ink2">Codex {item.codex_sessions} · Claude {item.claude_sessions}</span>,
    tokens: <span title={fmtInt(item.tokens)}>{fmtTokens(item.tokens)}</span>,
    commits: item.commits,
    files: item.changed_files,
  }));
  const ratio = data.coverage.token_attribution_ratio;
  return (
    <Card title="自动补充的客观统计" subtitle="来自日报采集器的会话、Token、Git 和文件数据，不改写上面的研究结论">
      {!project && (
        <div className="mb-3 grid gap-2 border-b border-line pb-3 sm:grid-cols-4">
          <div><div className="text-[10px] text-ink3">Agent 会话</div><div className="text-lg font-semibold text-ink">{data.totals.sessions}</div></div>
          <div><div className="text-[10px] text-ink3">工具调用</div><div className="text-lg font-semibold text-ink">{fmtInt(data.totals.tool_calls)}</div></div>
          <div><div className="text-[10px] text-ink3">Git commits</div><div className="text-lg font-semibold text-ink">{data.totals.commits}</div></div>
          <div><div className="text-[10px] text-ink3">修改文件</div><div className="text-lg font-semibold text-ink">{data.totals.changed_files}</div></div>
        </div>
      )}
      <DataTable
        columns={[
          { key: "project", label: "项目" },
          { key: "sessions", label: "会话", align: "right" },
          { key: "agents", label: "来源", align: "right" },
          { key: "tokens", label: "Token（约）", align: "right" },
          { key: "commits", label: "提交", align: "right" },
          { key: "files", label: "改动文件", align: "right" },
        ]}
        rows={rows}
        keyFn={(row) => String(row._key)}
      />
      <p className="mt-3 text-[11px] leading-5 text-ink3">
        {ratio === null
          ? "这一天没有可统计的 Token。"
          : `Token 项目归属覆盖率 ${(ratio * 100).toFixed(1)}%；${data.coverage.attributed_sessions}/${data.coverage.sessions_total} 个会话能归到具体项目。`}
        会话跨度可能包含等待时间，不作为人工工作时长。
      </p>
    </Card>
  );
}

export function DailyReportView({ report, project = "", showSummary = true, compact = false }: { report: SourceDailyReport; project?: string; showSummary?: boolean; compact?: boolean }) {
  if (!report.available) {
    return (
      <EmptyState text={report.message ?? "这一天还没有正式日报"} detail="页面不会用零散 Agent 对话伪造总结；正式日报生成后再显示。" />
    );
  }

  const groups = report.groups
    .map((group) => ({
      ...group,
      tasks: project
        ? group.tasks.filter((task) => task.project_ids.includes(project))
        : group.tasks,
    }))
    .filter((group) => group.tasks.length > 0);
  const shownTasks = groups.reduce((sum, group) => sum + group.tasks.length, 0);
  const today = todayLocal();
  const isLatestDay = report.date === today;

  const tokenColumns = report.token.columns.map((column, index) => ({
    key: column,
    label: column,
    align: index === 0 ? "left" as const : "right" as const,
    className: index === 0 ? "whitespace-nowrap text-ink" : "whitespace-nowrap font-mono text-xs text-ink2",
  }));

  return (
    <div className="space-y-4">
      {showSummary && (
        <>
          {!isLatestDay && (
            <div className="rounded-lg border border-warning/30 bg-warning/5 px-4 py-3 text-sm leading-6 text-ink2">
              当前展示的是最近已生成的正式日报（{report.date}），不是今天的零散实时对话。日报生成后页面会自动读取新文件。
            </div>
          )}
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard label="日报项目方向" value={groups.length} hint="保持原日报分组" />
            <StatCard label="完成与研究事项" value={shownTasks} hint="每项都有过程与结果" tone="good" />
            <StatCard label="Token 总量（约）" value={fmtTokens(report.token.total_tokens)} hint="沿用日报统计口径" tone="primary" />
            <StatCard label="阻塞 / 下一步" value={`${report.blockers.length} / ${report.next.length}`} hint="阻塞项 / 明日计划" tone={report.blockers.length ? "warn" : "default"} />
          </div>
          <div className="rounded-lg border border-primary/20 bg-primary/5 px-4 py-3 text-xs leading-5 text-ink2">
            <span className="font-medium text-ink">数据来源：</span>
            直接读取 {report.date}.md；更新时间 {fmtDateTime(report.generated_at)}。
            {report.normalization?.available
              ? ` 旧日报已由 ${report.normalization.model ?? "本地模型"} 做一次性结构提炼，原文未修改，源文件变化后缓存自动失效。`
              : " Agent、Git 和实验事件只用于后续统计与追溯，不参与改写这份总结。"}
          </div>
        </>
      )}

      {report.day_summary && (
        <Card title="当天研究摘要" subtitle="从原日报提炼；下面仍保留各项目的完整记录和原文行号">
          <p className="whitespace-pre-line text-sm leading-7 text-ink2">{report.day_summary}</p>
        </Card>
      )}

      {compact && report.push_summary && (
        <Card title="核心摘要" subtitle="沿用原日报的推送摘要，不重新编写">
          <p className="whitespace-pre-line text-sm leading-7 text-ink2">{report.push_summary}</p>
        </Card>
      )}

      {report.supplement?.available && <SupplementFacts data={report.supplement} project={project} />}

      {groups.map((group) => (
        <Card
          key={group.title}
          title={group.title}
          subtitle={`${group.tasks.length} 项进展`}
          right={group.project_ids.length ? <span className="text-[10px] text-ink3">{group.project_ids.join(" · ")}</span> : undefined}
        >
          <div className="divide-y divide-line">
            {group.tasks.map((task) => <Task key={task.title} task={task} compact={compact} />)}
          </div>
        </Card>
      ))}

      {!groups.length && <EmptyState text="这份日报中没有该项目的记录" detail="切换到全部项目，或选择另一日期。" />}

      {!project && (
        <>
          <div className="grid gap-4 xl:grid-cols-2">
            <ListCard title="阻塞 / 待解决" items={report.blockers} tone="warn" />
            <ListCard title="明日计划" items={report.next} tone="good" />
          </div>

          {(report.plan_closure.length > 0 || report.knowledge.length > 0 || (report.decisions?.length ?? 0) > 0) && (
            <div className="grid gap-4 xl:grid-cols-2">
              {report.plan_closure.length > 0 && <ListCard title="昨日计划闭环" items={report.plan_closure} />}
              {report.knowledge.length > 0 && <ListCard title="关键结论与知识" items={report.knowledge} tone="good" />}
              {(report.decisions?.length ?? 0) > 0 && <ListCard title="当天决策" items={report.decisions ?? []} tone="good" />}
            </div>
          )}

          {report.data_quality.length > 0 && <ListCard title="数据完整性说明" items={report.data_quality} />}

          {report.token.rows.length > 0 && (
            <Card title="Token 消耗" subtitle={`总量约 ${fmtTokens(report.token.total_tokens)}（${fmtInt(report.token.total_tokens)}）`}>
              <DataTable
                columns={tokenColumns}
                rows={report.token.rows}
                keyFn={(row) => String(row[report.token.columns[0]])}
              />
              {report.token.notes.length > 0 && <div className="mt-3 border-t border-line pt-3"><BulletText items={report.token.notes} muted /></div>}
            </Card>
          )}

          {!compact && report.push_summary && (
            <details className="rounded-lg border border-line bg-card px-4 py-3">
              <summary className="cursor-pointer text-sm font-medium text-ink">日报推送摘要</summary>
              <p className="mt-3 whitespace-pre-line text-sm leading-7 text-ink2">{report.push_summary}</p>
            </details>
          )}
        </>
      )}
    </div>
  );
}
