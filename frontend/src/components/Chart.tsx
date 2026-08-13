import { useEffect, useRef } from "react";
import * as echarts from "echarts";

// 统一图表封装：初始化、resize、option 更新。
// 主题常量见 chartTheme.ts，所有图表必须带 tooltip。

export function Chart({
  option,
  height = 260,
  onClick,
}: {
  option: echarts.EChartsOption;
  height?: number;
  onClick?: (params: echarts.ECElementEvent) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);

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
