import type { ReactNode } from "react";
import { featureGuideForTitle } from "../lib/featureGuides";
import type { UseQueryResult } from "@tanstack/react-query";
import { ApiError } from "../lib/api";

// ---------- 卡片 ----------

export function Card({
  title,
  subtitle,
  right,
  children,
  className = "",
  pad = true,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
  pad?: boolean;
}) {
  return (
    <section className={`rounded-lg border border-line bg-card ${className}`}>
      {(title || right) && (
        <header className="flex items-start justify-between gap-2 border-b border-line px-4 py-2.5">
          <div className="min-w-0">
            <h3 className="truncate text-sm font-medium text-ink">{title}</h3>
            {subtitle && <p className="mt-0.5 text-xs text-ink3">{subtitle}</p>}
          </div>
          {right && <div className="shrink-0">{right}</div>}
        </header>
      )}
      <div className={pad ? "px-4 py-3" : ""}>{children}</div>
    </section>
  );
}

// ---------- 统计卡（标注数据来源 / evidence 数） ----------

export function StatCard({
  label,
  value,
  hint,
  source,
  tone = "default",
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  source?: string; // 例如 "来源: /insights/efficiency" 或 "evidence: 12"
  tone?: "default" | "good" | "warn" | "bad" | "primary";
}) {
  const toneClass = {
    default: "text-ink",
    good: "text-passed",
    warn: "text-warning",
    bad: "text-critical",
    primary: "text-primary",
  }[tone];
  return (
    <div className="rounded-lg border border-line bg-card px-4 py-3">
      <div className="text-xs text-ink2">{label}</div>
      <div className={`mt-1 text-2xl font-semibold tabular-nums ${toneClass}`}>{value}</div>
      {hint && <div className="mt-1 text-xs text-ink3">{hint}</div>}
      {source && <div className="mt-1.5 text-[10px] text-ink3/80">{source}</div>}
    </div>
  );
}

// ---------- 骨架屏 ----------

export function Skeleton({ lines = 3, height }: { lines?: number; height?: number }) {
  if (height) {
    return <div className="animate-pulse rounded-md bg-line/40" style={{ height }} />;
  }
  return (
    <div className="space-y-2 py-1">
      {Array.from({ length: lines }).map((_, i) => (
        <div
          key={i}
          className="h-3.5 animate-pulse rounded bg-line/40"
          style={{ width: `${88 - i * 14}%` }}
        />
      ))}
    </div>
  );
}

export function SkeletonCard({ height = 160 }: { height?: number }) {
  return (
    <div className="rounded-lg border border-line bg-card p-4">
      <div className="h-4 w-1/3 animate-pulse rounded bg-line/50" />
      <div className="mt-3 animate-pulse rounded-md bg-line/40" style={{ height: height - 60 }} />
    </div>
  );
}

// ---------- 空态 ----------

export function EmptyState({
  text = "暂无数据",
  detail,
}: {
  text?: string;
  detail?: string;
}) {
  return (
    <div className="flex flex-col items-center justify-center py-8 text-center" data-testid="empty-state">
      <div className="flex h-10 w-10 items-center justify-center rounded-full border border-line text-ink3">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <circle cx="12" cy="12" r="9" />
          <path d="M8 12h8" />
        </svg>
      </div>
      <p className="mt-2 text-sm text-ink2">{text}</p>
      {detail && <p className="mt-1 max-w-md text-xs text-ink3">{detail}</p>}
    </div>
  );
}

// ---------- 错误态 ----------

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const isApi = error instanceof ApiError;
  const status = isApi ? error.status : null;
  const detail = isApi ? error.detail : error instanceof Error ? error.message : String(error);
  const url = isApi ? error.url : null;
  return (
    <div className="rounded-md border border-critical/40 bg-critical/5 px-4 py-3" data-testid="error-state">
      <div className="flex items-center gap-2 text-sm text-critical">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M12 9v4m0 4h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" />
        </svg>
        <span className="font-medium">
          {status === 0 ? "无法连接 API" : `请求失败${status ? `（HTTP ${status}）` : ""}`}
        </span>
      </div>
      <p className="mt-1 break-all text-xs text-ink2">{detail}</p>
      {url && <p className="mt-0.5 break-all font-mono text-[10px] text-ink3">{url}</p>}
      {onRetry && (
        <button
          onClick={onRetry}
          className="mt-2 rounded border border-line px-2.5 py-1 text-xs text-ink2 hover:border-primary hover:text-primary"
        >
          重试
        </button>
      )}
    </div>
  );
}

// ---------- 查询边界：统一 loading / error / empty / 数据 四态 ----------

export function QueryBoundary<T>({
  query,
  isEmpty,
  emptyText,
  emptyDetail,
  children,
}: {
  query: UseQueryResult<T, Error>;
  isEmpty?: (data: T) => boolean;
  emptyText?: string;
  emptyDetail?: string;
  children: (data: T) => ReactNode;
}) {
  if (query.isPending) return <Skeleton lines={4} />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => query.refetch()} />;
  const data = query.data;
  if (isEmpty && isEmpty(data)) return <EmptyState text={emptyText} detail={emptyDetail} />;
  return <>{children(data)}</>;
}

// ---------- 页头 ----------

export function PageHeader({
  title,
  description,
  right,
}: {
  title: string;
  description?: string;
  right?: ReactNode;
}) {
  const guide = featureGuideForTitle(title);
  return (
    <div className="mb-4 space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-ink">{title}</h2>
          {description && <p className="mt-0.5 text-xs text-ink3">{description}</p>}
        </div>
        {right}
      </div>
      {guide && (
        <section aria-label={`${title}功能说明`} className="rounded-lg border border-line bg-card/70 px-4 py-3">
          <div className="mb-2 flex items-center gap-2">
            <span className="rounded bg-primary/10 px-2 py-0.5 text-[10px] font-semibold text-primary">怎么用这一页</span>
            <span className="text-xs text-ink2">{guide.purpose}</span>
          </div>
          <div className="grid gap-2 text-[11px] leading-5 md:grid-cols-3">
            <p><strong className="font-medium text-ink">数据来源：</strong><span className="text-ink3">{guide.source}</span></p>
            <p><strong className="font-medium text-ink">建议看法：</strong><span className="text-ink3">{guide.reading}</span></p>
            <p><strong className="font-medium text-warning">注意口径：</strong><span className="text-ink3">{guide.caution}</span></p>
          </div>
        </section>
      )}
    </div>
  );
}

// ---------- 键值对 ----------

export function KeyValue({ k, v, mono }: { k: string; v: ReactNode; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1">
      <span className="shrink-0 text-xs text-ink3">{k}</span>
      <span className={`truncate text-right text-sm text-ink ${mono ? "font-mono text-xs" : ""}`}>{v ?? "—"}</span>
    </div>
  );
}

// ---------- 简易表格 ----------

export function DataTable({
  columns,
  rows,
  empty,
  keyFn,
}: {
  columns: Array<{ key: string; label: ReactNode; align?: "left" | "right"; className?: string }>;
  rows: Array<Record<string, ReactNode>>;
  empty?: ReactNode;
  keyFn?: (row: Record<string, ReactNode>, index: number) => string | number;
}) {
  if (rows.length === 0) return <>{empty ?? <EmptyState />}</>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-line text-left text-xs text-ink3">
            {columns.map((c) => (
              <th
                key={c.key}
                className={`px-3 py-2 font-medium ${c.align === "right" ? "text-right" : ""} ${c.className ?? ""}`}
              >
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={keyFn ? keyFn(row, i) : i} className="border-b border-line/50 last:border-0 hover:bg-cardhover/50">
              {columns.map((c) => (
                <td
                  key={c.key}
                  className={`px-3 py-2 align-top ${c.align === "right" ? "text-right tabular-nums" : ""} ${c.className ?? ""}`}
                >
                  {row[c.key] ?? "—"}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
