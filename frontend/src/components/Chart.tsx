import { useEffect, useRef } from "react";
import { BarChart, LineChart } from "echarts/charts";
import {
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import type { ECElementEvent, EChartsCoreOption, EChartsType } from "echarts/core";

echarts.use([
  BarChart,
  LineChart,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  CanvasRenderer,
]);

// 统一图表封装：初始化、resize、option 更新。
// 主题常量见 chartTheme.ts，所有图表必须带 tooltip。

export function Chart({
  option,
  height = 260,
  onClick,
}: {
  option: EChartsCoreOption;
  height?: number;
  onClick?: (params: ECElementEvent) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<EChartsType | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    chartRef.current = chart;
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(ref.current);
    return () => {
      observer.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!chartRef.current) return;
    chartRef.current.setOption(option, { notMerge: true });
  }, [option]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !onClick) return;
    chart.on("click", onClick);
    return () => {
      chart.off("click", onClick);
    };
  }, [onClick]);

  return <div ref={ref} style={{ height, width: "100%", minWidth: 280 }} />;
}
