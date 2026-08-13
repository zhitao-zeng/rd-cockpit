import type { SimpleDailyRecord, SimpleUsage } from "../lib/types";
import { fmtInt, fmtTokens } from "../lib/format";
import { Card, EmptyState } from "./ui";

const AGENT_NAMES: Record<string, string> = {
  codex: "Codex",
  claude_code: "Claude Code",
};

export function UsageSummary({ usage, compact = false }: { usage: SimpleUsage; compact?: boolean }) {
  if (!usage.available) {
    return <p className="text-xs text-ink3">{usage.note ?? "这一天没有同步到 Agent Token 用量。"}</p>;
  }
  return (
    <div>
      <div className="flex flex-wrap items-end gap-x-5 gap-y-2">
        <div>
          <div className="text-xs text-ink3">总 Token（约）</div>
          <div className={`${compact ? "text-lg" : "text-2xl"} font-semibold tabular-nums text-primary`} title={fmtInt(usage.total_tokens)}>
            {fmtTokens(usage.total_tokens)}
          </div>
        </div>
        {Object.entries(usage.agents).map(([agent, item]) => (
          <div key={agent} className="min-w-28">
            <div className="text-xs text-ink3">{AGENT_NAMES[agent] ?? agent}</div>
            <div className="text-sm font-medium tabular-nums text-ink" title={`${fmtInt(item.total_tokens)} tokens`}>
              {fmtTokens(item.total_tokens)} · {item.sessions} 个会话
            </div>
            {!compact && (
              <div className="mt-0.5 text-[10px] text-ink3">
                输出 {fmtTokens(item.output_tokens)} · 缓存 {fmtTokens(item.cached_tokens)}
              </div>
            )}
          </div>
        ))}
      </div>
      {!compact && (
        <p className="mt-2 text-[11px] leading-5 text-ink3">
          Token 来自本机 Codex / Claude Code 会话统计；总量包含输入、输出及缓存相关用量，因此适合看趋势，不等同于账单费用。
        </p>
      )}
    </div>
  );
}

function TextList({ items, empty }: { items: string[]; empty: string }) {
  if (!items.length) return <p className="text-xs leading-5 text-ink3">{empty}</p>;
  return (
    <ul className="space-y-1.5">
      {items.map((item, index) => (
        <li key={`${item}-${index}`} className="flex gap-2 text-sm leading-5 text-ink2">
          <span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-primary/80" />
          <span>{item}</span>
        </li>
      ))}
    </ul>
  );
}

export function SimpleRecordCard({ record }: { record: SimpleDailyRecord }) {
  const sessions = Object.values(record.usage.agents).reduce((sum, item) => sum + item.sessions, 0);
  return (
    <Card
      title={record.project_name}
      subtitle={record.goal ? `当前方向：${record.goal}` : "尚未写入当前研究方向"}
      right={record.has_activity ? <span className="text-xs text-passed">今天有记录</span> : <span className="text-xs text-ink3">今天暂无记录</span>}
    >
      {!record.has_activity ? (
        <EmptyState
          text="这一天没有发现该项目的研究记录"
          detail="项目仍然保留；这里只表示所选日期没有同步到会话、代码、实验或日报内容。"
        />
      ) : (
        <div className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <h4 className="mb-1.5 text-xs font-medium text-ink">今天做了什么</h4>
              <TextList
                items={record.work}
                empty={sessions ? `检测到 ${sessions} 个 Agent 会话，但没有可读的工作摘要。后续会由日报或会话交接补充。` : "没有可读的工作摘要。"}
              />
            </div>
            <div>
              <h4 className="mb-1.5 text-xs font-medium text-ink">得到的结果 / 做出的决定</h4>
              <TextList items={record.results} empty="今天还没有识别到明确的结果或决定。" />
            </div>
            <div>
              <h4 className="mb-1.5 text-xs font-medium text-ink">问题与阻塞</h4>
              <TextList items={record.problems} empty="目前没有记录到问题。" />
            </div>
            <div>
              <h4 className="mb-1.5 text-xs font-medium text-ink">接下来</h4>
              <TextList items={record.next} empty="还没有写入下一步。" />
            </div>
          </div>
          <div className="border-t border-line pt-3">
            <UsageSummary usage={record.usage} compact />
          </div>
        </div>
      )}
    </Card>
  );
}
