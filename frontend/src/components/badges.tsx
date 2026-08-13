import { shortId } from "../lib/format";

// ---------- 状态徽章：状态色语义固定，非颜色单独承载（均带文字） ----------

const STATUS_STYLE: Record<string, string> = {
  // 绿：通过/完成
  passed: "border-passed/50 bg-passed/10 text-passed",
  completed: "border-passed/50 bg-passed/10 text-passed",
  adopted: "border-passed/50 bg-passed/10 text-passed",
  confirmed: "border-passed/50 bg-passed/10 text-passed",
  clean: "border-passed/50 bg-passed/10 text-passed",
  done: "border-passed/50 bg-passed/10 text-passed",
  supports_hypothesis: "border-passed/50 bg-passed/10 text-passed",
  decision_producing: "border-passed/50 bg-passed/10 text-passed",
  // 红：失败/阻塞/严重
  failed: "border-critical/50 bg-critical/10 text-critical",
  failure: "border-critical/50 bg-critical/10 text-critical",
  rejected: "border-critical/50 bg-critical/10 text-critical",
  superseded: "border-critical/50 bg-critical/10 text-critical",
  critical: "border-critical/50 bg-critical/10 text-critical",
  blocked: "border-critical/50 bg-critical/10 text-critical",
  high: "border-critical/50 bg-critical/10 text-critical",
  environment_failure: "border-critical/50 bg-critical/10 text-critical",
  rejects_hypothesis: "border-critical/50 bg-critical/10 text-critical",
  // 黄：过期/警告/脏
  stale: "border-warning/50 bg-warning/10 text-warning",
  stale_candidate: "border-warning/50 bg-warning/10 text-warning",
  warning: "border-warning/50 bg-warning/10 text-warning",
  dirty: "border-warning/50 bg-warning/10 text-warning",
  medium: "border-warning/50 bg-warning/10 text-warning",
  partial: "border-warning/50 bg-warning/10 text-warning",
  dormant: "border-warning/40 bg-warning/5 text-warning",
  partially_supports: "border-warning/50 bg-warning/10 text-warning",
  // 青：进行/信息/低
  active: "border-primary/50 bg-primary/10 text-primary",
  pending: "border-primary/50 bg-primary/10 text-primary",
  proposed: "border-primary/50 bg-primary/10 text-primary",
  supported: "border-primary/50 bg-primary/10 text-primary",
  conditionally_adopted: "border-primary/50 bg-primary/10 text-primary",
  info: "border-primary/50 bg-primary/10 text-primary",
  low: "border-primary/50 bg-primary/10 text-primary",
  running: "border-primary/50 bg-primary/10 text-primary",
  open: "border-primary/50 bg-primary/10 text-primary",
  completed_without_decision: "border-primary/50 bg-primary/10 text-primary",
  historical: "border-line bg-card text-ink3",
};

export function StatusBadge({ status, className = "" }: { status: string | null | undefined; className?: string }) {
  const value = status ?? "unknown";
  const style = STATUS_STYLE[value] ?? "border-line bg-card text-ink2";
  return (
    <span className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] leading-none ${style} ${className}`}>
      {value}
    </span>
  );
}

// ---------- 置信/来源标签：observed/reported/inferred/approximate/unknown 必须区分 ----------

const CONFIDENCE_STYLE: Record<string, string> = {
  observed: "border-passed/50 text-passed",
  user_confirmed: "border-passed/50 text-passed",
  corroborated: "border-passed/50 text-passed",
  reported: "border-primary/50 text-primary",
  inferred: "border-warning/50 text-warning",
  approximate: "border-cat2/50 text-cat2",
  heuristic: "border-warning/50 text-warning",
  disputed: "border-critical/50 text-critical",
  retracted: "border-critical/50 text-critical",
  unknown: "border-line text-ink3",
};

const CONFIDENCE_LABEL: Record<string, string> = {
  approximate: "估算",
  inferred: "推断",
  observed: "观测",
  reported: "报告",
  heuristic: "启发式",
  user_confirmed: "用户确认",
  unknown: "未知",
};

export function ConfidenceTag({ value, className = "" }: { value: string | null | undefined; className?: string }) {
  if (!value) return null;
  const style = CONFIDENCE_STYLE[value] ?? "border-line text-ink3";
  const label = CONFIDENCE_LABEL[value] ?? value;
  return (
    <span
      className={`inline-flex items-center rounded border border-dashed px-1.5 py-0.5 text-[10px] leading-none ${style} ${className}`}
      title={`置信/来源: ${value}`}
    >
      {label}
    </span>
  );
}

// ---------- 证据引用（event ID） ----------

export function EvidenceRef({
  ids,
  max = 3,
  label = "evidence",
}: {
  ids: Array<string | null | undefined>;
  max?: number;
  label?: string;
}) {
  const clean = ids.filter((x): x is string => Boolean(x));
  if (clean.length === 0) return <span className="text-[10px] text-ink3">无 {label}</span>;
  const shown = clean.slice(0, max);
  return (
    <span className="inline-flex flex-wrap items-center gap-1" title={clean.join("\n")}>
      <span className="text-[10px] text-ink3">{label}:</span>
      {shown.map((id) => (
        <code key={id} className="rounded bg-line/40 px-1 py-0.5 font-mono text-[10px] text-ink2" title={id}>
          {shortId(id)}
        </code>
      ))}
      {clean.length > max && <span className="text-[10px] text-ink3">+{clean.length - max}</span>}
    </span>
  );
}

// ---------- dirty 状态 ----------

export function DirtyBadge({ dirty }: { dirty: boolean | null | undefined }) {
  if (dirty === null || dirty === undefined) return <StatusBadge status="unknown" />;
  return dirty ? <StatusBadge status="dirty" /> : <StatusBadge status="clean" />;
}

// ---------- 进度条 ----------

export function ProgressBar({ ratio, tone = "primary" }: { ratio: number; tone?: "primary" | "good" | "warn" | "bad" }) {
  const pct = Math.max(0, Math.min(1, ratio)) * 100;
  const bar = {
    primary: "bg-primary",
    good: "bg-passed",
    warn: "bg-warning",
    bad: "bg-critical",
  }[tone];
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-line/50">
      <div className={`h-full rounded-full ${bar}`} style={{ width: `${pct}%` }} />
    </div>
  );
}
