import type { ComponentProps } from "react";
import { GraphChart as EChartsGraph } from "echarts/charts";
import * as echarts from "echarts/core";
import { Chart as BaseChart } from "./Chart";

// The force-directed graph is only downloaded with the architecture route.
echarts.use([EChartsGraph]);

export function Chart(props: ComponentProps<typeof BaseChart>) {
  return <BaseChart {...props} />;
}
