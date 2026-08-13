# R&D Cockpit Frontend

研发状态驾驶舱前端 —— 深色、只读、高密度。后端为 rd-cockpit 的只读 API（FastAPI）。

## 启动

终端 1（后端 API，端口 8787）：

```bash
cd /path/to/rd-cockpit
python -m rd_cockpit serve
```

终端 2（前端 dev server，端口 4016）：

```bash
cd /path/to/rd-cockpit/frontend
npm install   # 首次
npm run dev
```

打开 http://127.0.0.1:4016

## 构建与测试

```bash
npm run build   # tsc -b && vite build → dist/
npm test        # vitest run（api client / 数据适配 / 空态渲染）
```

## API 地址配置

- 客户端读取 `VITE_API_BASE_URL`，未设置时默认 `http://127.0.0.1:8787`。
- **开发模式**（`.env.development`）：`VITE_API_BASE_URL=`（空）→ 前端请求同源 `/api/*`，
  由 Vite dev server 代理到 `http://127.0.0.1:8787`（后端无 CORS 头，代理是必需而非可选）。
  `/api` 前缀是为了避免与 SPA 路由（`/projects`、`/insights` 等）冲突。
- **生产模式**（`.env.production`）：`VITE_API_BASE_URL=http://127.0.0.1:8787`。
  若前端与 API 不同源部署，需自行配置反向代理（同样建议 `/api` 前缀转发）。

## 数据原则

- 全部数据来自后端只读 GET 端点，无 mock、无假数据。
- `approximate` / `inferred` / `reported` / `observed` 以不同标签展示；
  GPU-hours 等后端返回 null 的指标显示"无精确数据"，不编造。
- 关键指标卡片标注来源端点或 evidence 数。
- 派生逻辑（状态推导、漏斗聚合、风险均分、GPU 时序）集中在 `src/lib/adapters.ts`（纯函数，有测试）。

## 结构

```
src/
├── lib/        # api.ts（唯一网络层）/ types.ts / adapters.ts / format.ts / chartTheme.ts
├── components/ # Layout / Chart(ECharts) / ui(Card,StatCard,Skeleton,EmptyState,ErrorState,QueryBoundary,DataTable)
│               # badges(StatusBadge,ConfidenceTag,EvidenceRef,ProgressBar) / controls / ContextPackView
├── pages/      # 13 个路由页面
└── test/       # 空态渲染等组件测试
```

## 页面 ↔ API 对照

详见 `../docs/superpowers/specs/2026-08-02-frontend-design.md` 第 5 节。
