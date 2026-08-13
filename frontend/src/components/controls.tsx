import { useQuery } from "@tanstack/react-query";
import { getProjects } from "../lib/api";

/** 共享项目选择器（值受控；projects 来自 /projects） */
export function ProjectSelect({
  value,
  onChange,
  allowAll = true,
  allLabel = "全部项目",
  className = "",
}: {
  value: string;
  onChange: (value: string) => void;
  allowAll?: boolean;
  allLabel?: string;
  className?: string;
}) {
  const projects = useQuery({ queryKey: ["projects"], queryFn: getProjects });
  const ids = Object.keys(projects.data ?? {}).sort();
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={`rounded-md border border-line bg-card px-2.5 py-1.5 text-sm text-ink outline-none focus:border-primary ${className}`}
    >
      {allowAll && <option value="">{allLabel}</option>}
      {ids.map((id) => (
        <option key={id} value={id}>
          {projects.data?.[id]?.name ?? id}
        </option>
      ))}
    </select>
  );
}

/** Tab 条（受控，值存 URL 由页面负责） */
export function Tabs({
  tabs,
  value,
  onChange,
}: {
  tabs: Array<{ key: string; label: string }>;
  value: string;
  onChange: (key: string) => void;
}) {
  return (
    <div className="mb-4 flex gap-1 overflow-x-auto border-b border-line">
      {tabs.map((tab) => (
        <button
          key={tab.key}
          onClick={() => onChange(tab.key)}
          className={`whitespace-nowrap border-b-2 px-3 py-2 text-sm transition-colors ${
            value === tab.key
              ? "border-primary text-primary"
              : "border-transparent text-ink2 hover:text-ink"
          }`}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}
