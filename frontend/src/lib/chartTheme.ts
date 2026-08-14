// ECharts 深色主题常量 —— 与 index.css @theme 保持一致。
// 规则（dataviz）：文字用 ink tokens，categorical 固定顺序不循环，
// status 色仅用于状态语义，网格/坐标轴弱化，标记细。

import type { SeriesOption } from "echarts";
import type { EChartsCoreOption } from "echarts/core";

export const C = {
  primary: "#22d3ee",
  passed: "#34d399",
  warning: "#facc15",
  critical: "#f87171",
  cat: ["#3987e5", "#d95926", "#9085e9", "#d55181"] as const,
  neutral: "#64748b",
  ink: "#e2e8f0",
  ink2: "#94a3b8",
  ink3: "#64748b",
  line: "#1e3a52",
  card: "#101d2e",
};

/** categorical 取色：固定顺序，超出折叠为 neutral（不循环生成新 hue） */
export function catColor(index: number): string {
  return index < C.cat.length ? C.cat[index] : C.neutral;
}

export const tooltipBase = {
  backgroundColor: C.card,
  borderColor: C.line,
  textStyle: { color: C.ink, fontSize: 12 },
  confine: true,
} as const;

export function axisBase(): Record<string, unknown> {
  return {
    axisLine: { lineStyle: { color: C.line } },
    axisTick: { show: false },
    axisLabel: { color: C.ink2, fontSize: 11 },
    splitLine: { lineStyle: { color: C.line, opacity: 0.5, type: "dashed" } },
  };
}

export function legendBase(): Record<string, unknown> {
  return {
    textStyle: { color: C.ink2, fontSize: 11 },
    itemWidth: 14,
    itemHeight: 8,
    top: 0,
  };
}

export const gridBase = { left: 8, right: 12, top: 32, bottom: 4, containLabel: true } as const;

/** 单系列折线（2px 细线 + 小标记 + tooltip） */
export function lineSeries(
  name: string,
  data: Array<number | null>,
  color: string,
  extra: Record<string, unknown> = {},
): SeriesOption {
  return {
    name,
    type: "line",
    data,
    showSymbol: data.length <= 40,
    symbolSize: 5,
    lineStyle: { width: 2, color },
    itemStyle: { color },
    connectNulls: false,
    ...extra,
  } as SeriesOption;
}

/** 单 hue 柱状（柱宽自适应、圆角数据端） */
export function barSeries(
  name: string,
  data: Array<number | null>,
  color: string,
  extra: Record<string, unknown> = {},
): SeriesOption {
  return {
    name,
    type: "bar",
    data,
    barMaxWidth: 28,
    itemStyle: { color, borderRadius: [3, 3, 0, 0] },
    ...extra,
  } as SeriesOption;
}

export function baseChartOption(): EChartsCoreOption {
  return {
    tooltip: { ...tooltipBase },
    grid: { ...gridBase },
    textStyle: { color: C.ink2 },
  };
}
