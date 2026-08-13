// 时间与数字格式化 —— 统一使用 Asia/Shanghai（后端 period/report 同样使用该时区）

const TZ = "Asia/Shanghai";

const dateTimeFmt = new Intl.DateTimeFormat("zh-CN", {
  timeZone: TZ,
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

const dateFmt = new Intl.DateTimeFormat("en-CA", {
  timeZone: TZ,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

/** ISO 时间 → "08/02 17:16"（本地时区）；非法输入原样返回 */
export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return dateTimeFmt.format(d).replace(/\//g, "-");
}

/** ISO 时间 → "2026-08-02"（本地时区） */
export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso.slice(0, 10);
  return dateFmt.format(d);
}

/** 本地时区的今天 YYYY-MM-DD（用于默认查询日期） */
export function todayLocal(): string {
  return dateFmt.format(new Date());
}

/** 本地时区 n 天前 YYYY-MM-DD */
export function daysAgoLocal(n: number): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - n);
  return dateFmt.format(d);
}

/** 小时数 → "1.5h"；null → "—" */
export function fmtHours(hours: number | null | undefined): string {
  if (hours === null || hours === undefined) return "—";
  if (hours < 0.01) return `${Math.round(hours * 3600)}s`;
  return `${hours.toFixed(1)}h`;
}

/** 0~1 比例 → "85.0%" */
export function fmtPct(ratio: number | null | undefined): string {
  if (ratio === null || ratio === undefined) return "—";
  return `${(ratio * 100).toFixed(1)}%`;
}

/** 完整百分比数值（已是 0~100）→ "85.0%" */
export function fmtPct100(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${value.toFixed(1)}%`;
}

/** 大数字 → "12,345" */
export function fmtInt(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return n.toLocaleString("en-US");
}

/** Token 数量 → 便于扫读的短格式；悬浮/详情仍可显示完整数字。 */
export function fmtTokens(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(1)}B`;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toLocaleString("en-US");
}

/** 显存 MB → "12.4 GB" */
export function fmtMb(mb: number | null | undefined): string {
  if (mb === null || mb === undefined) return "—";
  if (mb >= 1024) return `${(mb / 1024).toFixed(1)} GB`;
  return `${Math.round(mb)} MB`;
}

/** commit sha → 短哈希 */
export function shortSha(sha: string | null | undefined): string {
  if (!sha) return "—";
  return sha.slice(0, 8);
}

/** 事件/证据 ID 缩略显示（evt_20260802T081600_65f8a6264d → …65f8a6264d） */
export function shortId(id: string | null | undefined): string {
  if (!id) return "—";
  if (id.length <= 14) return id;
  const tail = id.split("_").pop() ?? id;
  return `…${tail}`;
}

/** 相对时间："3 分钟前" */
export function fmtRelative(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const sec = Math.max(0, Math.floor((now.getTime() - d.getTime()) / 1000));
  if (sec < 60) return `${sec} 秒前`;
  if (sec < 3600) return `${Math.floor(sec / 60)} 分钟前`;
  if (sec < 86400) return `${Math.floor(sec / 3600)} 小时前`;
  return `${Math.floor(sec / 86400)} 天前`;
}
