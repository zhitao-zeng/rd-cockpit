import { useQuery } from "@tanstack/react-query";
import { getDont, getFreshness, getInformationGain, getProjects } from "../lib/api";
import { Card, PageHeader, QueryBoundary, StatCard } from "../components/ui";
import { ConfidenceTag, EvidenceRef, StatusBadge } from "../components/badges";

export function DontDoToday() {
  const dont = useQuery({ queryKey: ["dont"], queryFn: () => getDont() });
  const freshness = useQuery({ queryKey: ["freshness"], queryFn: () => getFreshness() });
  const infoGain = useQuery({ queryKey: ["info-gain"], queryFn: () => getInformationGain() });
  const projects = useQuery({ queryKey: ["projects"], queryFn: getProjects });

  const lowGain = (infoGain.data ?? []).filter((i) => i.classification === "low");
  const blockedProjects = Object.values(projects.data ?? {}).filter((p) => p.blockers.length > 0);

  return (
    <div className="space-y-4">
      <PageHeader
        title="今天不要做什么"
        description="来源: /advanced/dont + /insights/freshness + /advanced/information-gain + /projects"
      />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatCard label="不建议事项" value={dont.data?.length ?? "…"} tone="warn" source="来源: /advanced/dont" />
        <StatCard label="过期决策/参数" value={freshness.data?.length ?? "…"} source="来源: /insights/freshness" />
        <StatCard label="低信息增益实验" value={lowGain.length} source="来源: /advanced/information-gain" />
        <StatCard label="被阻塞项目" value={blockedProjects.length} tone={blockedProjects.length > 0 ? "bad" : "default"} source="来源: /projects" />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        {/* 不建议重复的实验 */}
        <Card title="不建议做的事" subtitle="来源: /advanced/dont（过期决策回归 + 重复配置）">
          <QueryBoundary query={dont} isEmpty={(d) => d.length === 0} emptyText="今天没有明确的禁忌 🎉">
            {(d) => (
              <ul className="space-y-2">
                {d.map((item, i) => (
                  <li key={i} className="rounded-md border border-critical/40 px-3 py-2">
                    <div className="flex items-center gap-2">
                      <span className="text-critical">⛔</span>
                      <span className="text-sm text-ink">{item.dont}</span>
                      {item.project_id && <span className="text-xs text-primary">{item.project_id}</span>}
                    </div>
                    <p className="mt-1 text-xs text-ink3">{item.reason}</p>
                    <EvidenceRef ids={item.basis.map((b) => (typeof b === "string" ? b : null))} max={2} label="basis" />
                  </li>
                ))}
              </ul>
            )}
          </QueryBoundary>
        </Card>

        {/* 过期参数/决策 */}
        <Card title="不要继续依赖这些过期结论" subtitle="来源: /insights/freshness（决策后代码/数据已变化）">
          <QueryBoundary query={freshness} isEmpty={(d) => d.length === 0} emptyText="无过期结论">
            {(d) => (
              <ul className="space-y-2">
                {d.map((f) => (
                  <li key={f.event_id} className="rounded-md border border-warning/40 px-3 py-2">
                    <div className="flex items-center gap-2">
                      <StatusBadge status="stale" />
                      <span className="text-xs text-primary">{f.project_id}</span>
                    </div>
                    <p className="mt-1 text-sm text-ink2">{f.text ?? f.event_id}</p>
                    <p className="mt-0.5 text-xs text-warning">{f.reasons.join("；")}</p>
                  </li>
                ))}
              </ul>
            )}
          </QueryBoundary>
        </Card>

        {/* 没有新增信息的实验 */}
        <Card title="不要再跑这些无新增信息的实验" subtitle="来源: /advanced/information-gain（classification=low，配置重复）">
          <QueryBoundary query={infoGain} isEmpty={() => lowGain.length === 0} emptyText="无低信息增益实验">
            {() => (
              <ul className="space-y-2">
                {lowGain.slice(0, 10).map((i) => (
                  <li key={i.event_id} className="flex items-center gap-2 rounded-md border border-line px-3 py-2">
                    <span className="text-sm font-semibold text-ink3 tabular-nums">{i.information_gain}</span>
                    <div className="min-w-0 flex-1">
                      <code className="font-mono text-[10px] text-ink2">{i.fingerprint}</code>
                      {i.similar_to && (
                        <p className="truncate text-[10px] text-ink3">与 {i.similar_to} 配置相同</p>
                      )}
                    </div>
                    <EvidenceRef ids={[i.event_id]} max={1} />
                  </li>
                ))}
              </ul>
            )}
          </QueryBoundary>
        </Card>
      </div>

      {/* 当前不具备条件的任务 */}
      <Card title="当前不具备条件的任务" subtitle="来源: /projects → blockers（先解除 blocker 再开工）">
        <QueryBoundary
          query={projects}
          isEmpty={() => blockedProjects.length === 0}
          emptyText="没有被阻塞的项目 🎉"
        >
          {() => (
            <ul className="space-y-2">
              {blockedProjects.map((p) => (
                <li key={p.project_id} className="rounded-md border border-critical/40 px-3 py-2">
                  <div className="flex items-center gap-2">
                    <StatusBadge status="blocked" />
                    <span className="text-sm text-primary">{p.name}</span>
                  </div>
                  <ul className="mt-1 list-disc space-y-0.5 pl-6 text-sm text-ink2">
                    {p.blockers.map((b, i) => (
                      <li key={i}>{b}</li>
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
          )}
        </QueryBoundary>
      </Card>

      <p className="text-[10px] text-ink3">
        <ConfidenceTag value="inferred" /> 本页所有条目均为账本投影推导，执行前请结合证据事件自行判断。
      </p>
    </div>
  );
}
