import { NavLink, Outlet } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getHealth } from "../lib/api";
import { FEATURE_GUIDES } from "../lib/featureGuides";

function HealthDot() {
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth, refetchInterval: 30_000, retry: 0 });
  const ok = health.data?.ok === true;
  return (
    <span
      className="inline-flex items-center gap-1.5 text-xs text-ink2"
      title={health.isError ? `API 不可达：${health.error.message}` : `API 正常 · ${health.data?.home ?? ""}`}
    >
      <span className={`h-2 w-2 rounded-full ${ok ? "bg-passed" : health.isPending ? "bg-ink3" : "bg-critical"}`} />
      API {ok ? "已连接" : health.isPending ? "连接中" : "断开"}
    </span>
  );
}

function navClass({ isActive }: { isActive: boolean }) {
  return `block rounded-md px-3 py-2 transition-colors ${
    isActive ? "bg-primary/10 text-primary" : "text-ink2 hover:bg-cardhover hover:text-ink"
  }`;
}

export function Layout() {
  return (
    <div className="min-h-screen bg-page text-ink">
      {/* 桌面侧边栏 */}
      <aside className="fixed inset-y-0 left-0 z-20 hidden w-60 flex-col border-r border-line bg-card/60 md:flex">
        <div className="border-b border-line px-4 py-4">
          <div className="text-sm font-semibold tracking-wide text-ink">研究记录</div>
          <div className="mt-0.5 text-[10px] text-ink3">每天做了什么 · Token · 结论</div>
        </div>
        <nav className="flex-1 space-y-0.5 overflow-y-auto px-2 py-3">
          {FEATURE_GUIDES.map((item) => (
            <NavLink key={item.path} to={item.path} end={item.end} className={navClass} title={item.purpose}>
              <span className="block text-sm font-medium">{item.navLabel}</span>
              <span className="mt-0.5 block text-[10px] leading-4 opacity-70">{item.short}</span>
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-line px-4 py-3">
          <HealthDot />
          <p className="mt-2 text-[10px] leading-4 text-ink3">页面以现有 Daily Report 为准；Agent、Git 和 Token 只补充统计。</p>
        </div>
      </aside>

      {/* 移动端顶部导航 */}
      <header className="sticky top-0 z-20 border-b border-line bg-card/80 backdrop-blur md:hidden">
        <div className="flex items-center justify-between px-3 py-2">
          <span className="text-sm font-semibold">研究记录</span>
          <HealthDot />
        </div>
        <nav className="flex gap-1 overflow-x-auto px-2 pb-2">
          {FEATURE_GUIDES.map((item) => (
            <NavLink key={item.path} to={item.path} end={item.end} className={navClass} title={item.purpose}>
              <span className="whitespace-nowrap text-sm">{item.navLabel}</span>
            </NavLink>
          ))}
        </nav>
      </header>

      <main className="px-4 py-4 md:pl-64 md:pr-5 lg:px-6 lg:pl-64">
        <div className="mx-auto max-w-[1400px]">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
