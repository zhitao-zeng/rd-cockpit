import { type FormEvent, type ReactNode, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getApiToken, getAuthStatus, setApiToken } from "../lib/api";

export function AuthGate({ children }: { children: ReactNode }) {
  const [token, setToken] = useState(getApiToken);
  const [message, setMessage] = useState("");
  const status = useQuery({
    queryKey: ["auth-status", token],
    queryFn: getAuthStatus,
    retry: false,
    staleTime: 60_000,
  });

  if (status.data && (!status.data.required || status.data.authenticated)) {
    return <>{children}</>;
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setApiToken(token);
    const result = await status.refetch();
    if (!result.data?.authenticated) setMessage("访问令牌不正确，请重新输入。");
    else setMessage("");
  };

  return (
    <main className="flex min-h-screen items-center justify-center bg-page px-4 text-ink">
      <form onSubmit={submit} className="w-full max-w-md rounded-xl border border-line bg-card p-6 shadow-xl">
        <h1 className="text-lg font-semibold">研究记录访问验证</h1>
        <p className="mt-2 text-sm leading-6 text-ink2">该页面正在局域网地址上运行。请输入本机配置中的访问令牌，令牌只保存在当前浏览器。</p>
        <input
          autoFocus
          type="password"
          value={token}
          onChange={(event) => setToken(event.target.value)}
          placeholder="RD_API_TOKEN"
          className="mt-5 w-full rounded-md border border-line bg-page px-3 py-2 font-mono text-sm outline-none focus:border-primary"
        />
        {message && <p className="mt-2 text-xs text-critical">{message}</p>}
        {status.isError && <p className="mt-2 text-xs text-critical">API 暂时不可达，请确认服务已经启动。</p>}
        <button className="mt-4 w-full rounded-md bg-primary px-4 py-2 text-sm font-medium text-page">进入驾驶舱</button>
      </form>
    </main>
  );
}
