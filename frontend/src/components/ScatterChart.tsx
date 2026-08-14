import type { ComponentProps } from "react";
import { ScatterChart as EChartsScatter } from "echarts/charts";
import * as echarts from "echarts/core";
import { Chart as BaseChart } from "./Chart";

// Scatter support stays out of analytics/experiment routes that only need
// line and bar charts.
echarts.use([EChartsScatter]);

export function Chart(props: ComponentProps<typeof BaseChart>) {
  return <BaseChart {...props} />;
}
