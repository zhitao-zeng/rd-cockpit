import { useQuery } from "@tanstack/react-query";
import { getBackgroundTaskStatus } from "../lib/api";

const LABELS: Record<string, string> = {
  reports: "日报整理",
  classification: "项目归类",
  intelligence: "项目情报审计",
  discovery: "新项目发现",
  experiments: "实验提炼",
  architecture: "算法架构",
  views: "页面缓存",
  maintenance: "备份归档",
};

function localTime(value?: string | null) {
  if (!value) return "尚未运行";
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

export function BackgroundTaskStatusPanel() {
  const query = useQuery({
    queryKey: ["background-task-status"],
    queryFn: getBackgroundTaskStatus,
    refetchInterval: 60_000,
  });
  if (!query.data) return null;
  const pipeline = query.data.stages.pipeline;
  const failed = Object.entries(query.data.stages).filter(([, item]) => item.state === "failed");
  const codex = query.data.model_tools.codex;
  const activity = query.data.model_activity;

  return (
    <section className="rounded-xl border border-line bg-card px-4 py-3 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <span className="font-medium text-ink">后台资料更新</span>
          <span className="ml-2 text-xs text-ink3">01:15 增量整理 · 03:30 备份归档</span>
        </div>
        <span className={failed.length ? "text-critical" : pipeline?.state === "running" ? "text-warning" : "text-passed"}>
          {failed.length ? `${failed.length} 项失败` : pipeline?.state === "running" ? "正在更新" : pipeline?.state === "ok" ? "最近一次正常" : "等待首次运行"}
        </span>
      </div>
      <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-7">
        {Object.entries(LABELS).map(([key, label]) => {
          const stage = query.data.stages[key];
          return (
            <div key={key} className="rounded-lg bg-surface px-3 py-2">
              <div className="flex justify-between gap-2"><span className="text-ink2">{label}</span><span className={stage?.state === "failed" ? "text-critical" : stage?.state === "running" ? "text-warning" : "text-passed"}>{stage?.state === "ok" ? "正常" : stage?.state === "failed" ? "失败" : stage?.state === "running" ? "运行中" : "未运行"}</span></div>
              <div className="mt-1 truncate text-[11px] text-ink3" title={stage?.message || ""}>{localTime(stage?.finished_at || stage?.started_at)}</div>
            </div>
          );
        })}
      </div>
      {activity && (
        <p className="mt-2 text-xs text-ink3">
          最近 24 小时：实际调用模型 {activity.counts.model_calls ?? 0} 次
          · 缓存命中 {activity.counts.cache_hits ?? 0} 项
          · 延后 {activity.counts.deferred ?? 0} 项
          · Token {Number(activity.tokens.total ?? 0).toLocaleString("zh-CN")}
        </p>
      )}
      {!codex?.available && <p className="mt-2 text-xs text-critical">Codex 后台命令不可用，语义提炼会保留旧缓存并标记失败。</p>}
    </section>
  );
}
