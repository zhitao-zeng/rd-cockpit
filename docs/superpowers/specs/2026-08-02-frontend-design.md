# R&D Cockpit 前端设计文档

日期：2026-08-02
状态：已确认（用户以完整页面规格形式提供，见会话记录）
范围：在仓库的 `frontend/` 目录新增 React 前端，**不修改任何后端 Python 代码与数据模型**。

## 1. 背景与约束

后端是只读研发状态 API（FastAPI，`python -m rd_cockpit serve`，默认 `127.0.0.1:8787`），
数据来自 append-only 事件账本（SQLite）。前端职责：**如实呈现**，不伪造指标。

硬性约束：

- 只读：前端只发 GET 请求，不创建 mock API，不写假数据。
- 后端返回 `approximate` / `inferred` / `reported` / `observed` 时必须以不同标签区分；
  GPU-hours 等后端返回 `null`/`approximate` 的量必须显示"估算/无数据"，不得声称精确值。
- 所有关键指标标注数据来源（endpoint）或 evidence count。
- 加载用 skeleton，错误显示明确信息，空数据有 empty state。
- 刷新后状态正常（路由 + URL search params 保存过滤器/选中项目/Tab）。
- 移动端至少可浏览（响应式，侧边栏在小屏折叠为顶部导航）。

## 2. 技术选型

| 项 | 选择 | 理由 |
|---|---|---|
| 构建 | Vite 6 + React 18 + TypeScript | 用户指定；Node v20.20.2 满足 |
| 样式 | Tailwind CSS v4（`@tailwindcss/vite`） | 用户指定；v4 零配置、CSS 原生 theme |
| 数据 | TanStack Query v5 | 用户指定；缓存/重试/刷新语义 |
| 路由 | React Router v6 | 用户指定 |
| 图表 | **ECharts 6**（自写 15 行 React 封装） | 需求含关系图（graph）、漏斗（funnel）、雷达（radar）、气泡图，Recharts 不支持 graph/funnel；ECharts 全覆盖 |
| 测试 | Vitest + @testing-library/react + jsdom | 用户要求的 4 类测试 |
| npm registry | npmmirror（已配置） | 安装速度 |

### CORS 与 API 地址

后端无 CORSMiddleware 且不可修改。方案：

- 客户端：`const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8787"`
  （`??` 而非 `||`，允许空串表示"同源"）。
- `.env.development` 设 `VITE_API_BASE_URL=`（空串 → 同源），Vite dev server 把 API 路径
  proxy 到 `http://127.0.0.1:8787` —— `npm run dev` 开箱可用。
- `.env.example` 记录默认值 `http://127.0.0.1:8787`（生产部署时按实际地址设置）。

## 3. 视觉风格（深色科研驾驶舱）

```
bg-page:   #0a1420  （深蓝黑）
bg-card:   #101d2e  （卡片）
border:    #1e3a52  （细边框，低对比）
primary:   #22d3ee  （青色，cyan-400）
warning:   #facc15  （黄）
critical:  #f87171  （红）
passed:    #34d399  （绿）
text:      #e2e8f0 / #94a3b8（主次）
```

- 卡片 `rounded-lg`、细边框、无随机渐变、无发光特效、非营销风。
- 状态色语义固定：passed=绿 / failed|critical=红 / stale|warning=黄 / pending|active=青 / unknown=灰。
- 置信标签：observed=绿、reported=青、inferred=黄、approximate=橙、unknown=灰。

## 4. 架构

```
frontend/src/
├── lib/
│   ├── api.ts        # fetch 封装 + 全部 endpoint 函数（唯一网络层）
│   ├── types.ts      # API 响应类型（按后端真实结构声明，宽松处用 unknown）
│   ├── adapters.ts   # 纯函数：API JSON → 视图模型（进度、漏斗、趋势、状态推导）
│   ├── format.ts     # 时间（Asia/Shanghai）、数字、百分比格式化
│   └── query.ts      # QueryClient（staleTime 30s，retry 1）
├── components/       # Layout / StatCard / StatusBadge / ConfidenceTag / EvidenceRef
│                     # Skeleton / EmptyState / ErrorState / ChartCard(ECharts封装)
│                     # DataTable / FilterBar / StageFunnel
└── pages/            # 13 个路由页
```

数据流：`api.ts`（原始 JSON）→ `adapters.ts`（视图模型，纯函数，可测）→ 页面组件。
所有派生逻辑（状态推导 Active/Blocked/Stale/Done、验证进度、风险评分聚合）只放在 adapters，
组件不做计算 —— 这也是 4 类测试的落点。

项目状态推导规则（Projects 页 / Research Map 共用）：

```
done    = 验证进度 == 100%
blocked = blockers.length > 0
stale   = 任一验证阶段 status == "stale"
active  = 其他
```

Research Map 坐标推导（后端无现成字段，规则显式写在 adapters）：

```
横轴阶段 = progress < 0.25 探索 | < 0.5 实现 | < 0.75 验证 | ≥ 0.75 交付
纵轴风险 = risk 四维度均值（high=3, medium=2, low=1, unknown=0）
气泡大小 = bubble（事件数）；颜色 = 上面的状态
```

## 5. 路由与页面 ↔ API 映射

| 路由 | 页面 | 使用端点 |
|---|---|---|
| `/` | Overview 总览 | `/reports/daily/{today}/semantic`（成果/阻塞/建议）、`/projects`+`/insights/twin`（健康/验证进度）、`/stats?period=week`（趋势/活动/最近事件）、`/anomalies`、`/insights/gpu`、`/advanced/map`（风险分布）、`/advanced/health?project=`×N |
| `/projects` | Projects 列表 | `/projects`、`/advanced/map`、`/advanced/health?project=`×N |
| `/projects/:id` | Project Detail（9 Tabs） | `state`: `/projects/{id}/state`；`funnel`: state+`/insights/impact`；`timeline`: `/projects/{id}/timeline`；`experiments`: `/insights/efficiency`+`/insights/reproducibility`+`/advanced/fingerprints`；`decisions`: `/advanced/confidence`+`/insights/conflicts`+`/insights/freshness`；`parameters`: `/insights/lineage`；`risks`: `/advanced/risk`+`/advanced/health`+`/insights/coverage`+`/advanced/why-not-done`；`failed`: `/insights/context`.failed_paths+`/advanced/errors`；`context`: `/insights/context` |
| `/timeline` | Timeline | `/stats?period=week|month`.events（全集，含 unassigned 的 GPU 采样）+ `/projects/{id}/timeline`×N（按 event_id 合并补 payload/evidence）+ `/sessions` + `/insights/switches` + `/insights/sessions` |
| `/experiments` | Experiments | `/insights/efficiency`、`/advanced/information-gain`、`/insights/reproducibility`、`/advanced/fingerprints`、`/advanced/hypotheses`、timeline 合并（结果趋势） |
| `/decisions` | Decisions | `/advanced/confidence`、`/insights/freshness`、`/insights/conflicts`、`/insights/graph`+`/advanced/metric-lineage`（关系图合并）、`/insights/lineage`（参数演化）、`/insights/suggest`、`/insights/counterfactual`（查询框）、`/advanced/countdown` |
| `/resources` | Resources | `/insights/gpu`、`/insights/changed?query=<近7天>`（取 resource_snapshot payload → GPU 利用率/显存时序 + Docker 容器）、`/insights/resource-cost`、`/advanced/budget`、`/anomalies` |
| `/reports` | Reports（日报/周报/月报/Replay/Wrapped/Card 6 Tabs） | `/reports/daily/{date}` + `/semantic`、`/stats?period=week|month`、`/insights/replay?query=`、`/insights/wrapped?query=`、`/advanced/card?query=` |
| `/anomalies` | Anomalies / Risk | `/anomalies`、`/advanced/hidden-blockers`、`/insights/freshness`、`/advanced/debt`、`/advanced/errors`、`/advanced/risk?project=`×N（风险雷达） |
| `/map` | Research Map | `/advanced/map` + `/projects`（stale 判定） |
| `/context-pack` | Context Pack（Agent 接管视图） | `/insights/context?project=` + `/advanced/brief?project=`，含"复制 JSON" |
| `/insights` | 高级洞察 | `/advanced/attention`、`/advanced/rhythm`、`/advanced/handoff-quality`、`/advanced/agent-blindspots`、`/advanced/memory?project=`×N、`/advanced/knowledge`、`/advanced/achievements`、`/advanced/countdown` |
| `/dont` | 今天不要做什么 | `/advanced/dont`、`/insights/freshness`、`/advanced/information-gain`（低增益）、`/advanced/errors`、`/projects`（blockers → 不具备条件） |

`/health` → Layout 顶栏 API 状态灯。`/dashboard`（HTML）不使用（本前端即其替代）。

## 6. 关键页面的数据要点

- **Overview**：今日成果/阻塞/建议用 `semantic`（该端点是实时计算的，即使日报 JSON 未生成 404 也可用）；
  验证漏斗 = `/projects` 各项目 stage 状态聚合（按 config 中 stage 首次出现顺序排序）；
  风险分布 = `/advanced/map` 每项目 4 维 risk 堆叠条形图。
- **Timeline 过滤器**：项目 / 事件类型 / 成功失败 / 时间范围 / 只看决策（`decision_*`）/
  只看实验（`experiment_*`）/ 只看异常（status=failed ∪ anomalies 的 evidence event_id）。
  过滤器存 URL search params。
- **Resources**：GPU 时序来自 `/insights/changed?query=<7天前日期>` 过滤 `resource_snapshot`
  （只读端点，payload 含 gpus 与 containers，属真实数据非伪造）。
  `/advanced/budget` 的 `gpu_hours: null`、`unit_cost: "requires..."` 原样显示"无精确数据（需 GPU 生命周期事件）"，
  `cost_is_approximate: true` 一律标"估算"。
- **Decisions 关系图**：`/insights/graph` 的 nodes/edges 与 `/advanced/metric-lineage` 的
  based_on 边合并（按 id 去重），ECharts graph 布局，节点类型分色
  （decision=青、experiment=绿、metric=黄、artifact/commit=灰）。
- **反事实查询**：Decisions 页内 GET 查询框（project 选择 + query 输入），
  答案区显示后端原文与 confidence 标签（`inferred`）。

## 7. 工程细节

- **Skeleton**：卡片/表格骨架屏（animate-pulse）。
- **ErrorState**：显示 endpoint + HTTP 状态 + 后端 detail 原文 + 重试按钮。
- **EmptyState**：图标 + "暂无数据" + 说明哪个端点返回为空。
- **EvidenceRef**：`evt_...` 等 evidence ID 以等宽小字 badge 展示，title 显示全量；
  指标卡底部标注 `来源: /insights/efficiency`。
- **移动端**：<768px 侧边栏收起为顶部横向滚动导航；图表 `min-width` 横向滚动。
- **数字格式**：时长 xh、百分比 1 位小数、时间 Asia/Shanghai `MM-dd HH:mm`。

## 8. 测试（Vitest）

1. `api.test.ts` — mock fetch：URL 拼接（base、query 参数、路径参数）、HTTP 错误抛错含 detail、空 base 走同源。
2. `adapters.overview.test.ts` — 趋势映射、验证漏斗聚合、风险分布聚合、空 stats → null/空数组（不编造）。
3. `adapters.project.test.ts` — 状态推导（done/blocked/stale/active 四分支）、进度计算、stale 原因提取。
4. `empty-state.test.tsx` — 空数据渲染 EmptyState；Projects 表格空 map → empty 文案。

## 9. 实施阶段

- **Phase 1**：脚手架（Vite/TS/Tailwind/Query/Router/ECharts 封装）+ api.ts + adapters + Layout + Overview + Projects + Project Detail + 测试。
- **Phase 2**：Timeline / Reports / Anomalies / Resources / Experiments / Decisions。
- **Phase 3**：Research Map / Context Pack / Insights（高级洞察）/ Dont / 收尾验证。

每阶段结束跑 `npm run build` + `npm test` 验证。

## 10. 明确不做

- 不写任何 POST/PUT/DELETE；不实现"记录事件"类交互。
- 不修改后端；不加鉴权（后端绑定 localhost）。
- 不用假数据填充图表；后端无数据的图表显示 empty state 而不是样例数据。
- 不做暗色/亮色切换（规格只要深色）。
