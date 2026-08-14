// API 响应类型 —— 按后端 rd_cockpit 真实返回结构声明。
// 后端是只读账本投影，部分字段在不同数据密度下可能缺失，故宽松处用 unknown/optional。

export interface StageInfo {
  status: string; // passed | pending | stale | unknown
  event_id?: string | null;
  commit?: string | null;
  machine?: string | null;
  reason?: string | null;
  tree_hash?: string | null;
  verified_at?: string | null;
  stale_reason?: string | null;
}

export interface RecentEvent {
  event_id: string;
  occurred_at: string;
  type: string;
  status: string | null;
  source?: string | null;
  commit?: string | null;
}

export interface ProjectState {
  project_id: string;
  name: string;
  lifecycle_status?: "active" | "dormant" | "historical";
  goal: string | null;
  repo_path: string;
  branch: string | null;
  head: string | null;
  dirty: boolean | null;
  verification: Record<string, StageInfo>;
  blockers: string[];
  remaining: string[];
  recent_events: RecentEvent[];
}

export interface TrendPoint {
  date: string;
  events: number;
  projects: string[];
  tests_passed: number;
  tests_failed: number;
  experiments: number;
  decisions: number;
}

export interface StatsEvent {
  event_id: string;
  occurred_at: string;
  type: string;
  project_id: string | null;
  status: string | null;
}

export interface StatsFacts {
  schema_version: number;
  period: string;
  label: string;
  generated_at: string;
  time: {
    human_active_hours: number;
    agent_hours: number;
    command_hours: number;
    context_switches: number;
    active_span_hours: number;
  };
  outputs: {
    events: number;
    commits: number;
    tests: { passed: number; failed: number };
    experiments: number;
    decisions: number;
    completed_milestones: number;
  };
  projects: Record<string, { events: number; types: Record<string, number>; commits: string[] }>;
  trend: TrendPoint[];
  unfinished: Array<{ project_id: string | null; text: string | null; status: string | null }>;
  events: StatsEvent[];
}

export interface HealthOk {
  ok: boolean;
  home?: string;
  database?: string;
}

export interface ProjectSummary {
  project_id: string;
  name: string;
  lifecycle_status?: "active" | "dormant" | "historical";
}
export interface SimpleAnalyticsPoint {
  date: string;
  project_id: string;
  activities: number;
  experiments: number;
  conclusions: number;
  tokens: number;
  codex_tokens: number;
  claude_tokens: number;
}

export interface SimpleAnalyticsResponse {
  days: number;
  daily: SimpleAnalyticsPoint[];
  project_names: Record<string, string>;
  totals: {
    tokens: number;
    activities: number;
    experiments: number;
    conclusions: number;
  };
  token_available: boolean;
  token_note: string | null;
  agent_activity: {
    totals: { completed: number; failed: number; duration_minutes: number; sessions: number };
    daily: Array<{ date: string; completed: number; failed: number; duration_minutes: number; sessions: number }>;
    projects: Array<{ project_id: string; name: string; completed: number; failed: number; duration_minutes: number; sessions: number }>;
    explanation: string;
  };
}

export interface SimpleKnowledgeItem {
  project_id: string | null;
  kind: string;
  title: string;
  detail: string | null;
  scope: unknown;
  date: string;
  confidence: string;
}

export interface SimpleKnowledgeResponse {
  items: SimpleKnowledgeItem[];
  summary?: {
    shown: number;
    hidden_task_results: number;
    deduplicated: number;
  };
  explanation: string;
}

// ---------- 现有 Markdown 日报（主数据源） ----------

export interface SourceReportTask {
  title: string;
  display_title: string;
  project_ids: string[];
  did: string[];
  why: string[];
  results: string[];
  files: string[];
  evidence: string[];
  conclusions?: string[];
  confidence?: "observed" | "reported" | "inferred" | "confirmed" | string;
}

export interface SourceReportGroup {
  title: string;
  project_ids: string[];
  tasks: SourceReportTask[];
}

export interface SourceReportToken {
  columns: string[];
  rows: Array<Record<string, string>>;
  notes: string[];
  total_tokens: number;
}

export interface DailySupplementProject {
  project_id: string;
  name: string;
  sessions: number;
  claude_sessions: number;
  codex_sessions: number;
  requests: number;
  tool_calls: number;
  duration_minutes: number;
  tokens: number;
  claude_tokens: number;
  codex_tokens: number;
  commits: number;
  changed_files: number;
}

export interface DailySupplement {
  available: boolean;
  date: string;
  totals: {
    sessions: number;
    requests: number;
    tool_calls: number;
    duration_minutes: number;
    tokens: number;
    commits: number;
    changed_files: number;
  };
  projects: DailySupplementProject[];
  coverage: {
    sessions_with_usage: number;
    sessions_total: number;
    attributed_sessions: number;
    attributed_tokens: number;
    token_attribution_ratio: number | null;
  };
  sources: Record<string, boolean>;
}

export interface SourceDailyReport {
  available: boolean;
  date: string | null;
  generated_at: string | null;
  source_path: string | null;
  groups: SourceReportGroup[];
  token: SourceReportToken;
  blockers: string[];
  next: string[];
  plan_closure: string[];
  knowledge: string[];
  decisions?: string[];
  data_quality: string[];
  day_summary?: string;
  no_activity?: boolean;
  normalization?: {
    available: boolean;
    generated_at: string | null;
    model: string | null;
    fallback_used: boolean;
    source_sha256: string | null;
  };
  push_summary: string;
  task_count: number;
  project_ids: string[];
  project_names?: Record<string, string>;
  message: string | null;
  supplement: DailySupplement | null;
}

export interface SourceReportDates {
  dates: string[];
  latest: string | null;
  directory: string;
  directories?: string[];
}

// ---------- 研究雷达（OpenAlex 元数据） ----------

export interface ResearchRadarItem {
  id: string;
  project_id: string;
  project_name: string;
  focus: string;
  title: string;
  publication_date: string | null;
  authors: string[];
  venue: string | null;
  cited_by_count: number;
  fwci: number;
  work_type: string;
  has_fulltext: boolean;
  url: string;
  pdf_url: string | null;
  doi: string | null;
  why_relevant: string;
  local_context: string[];
  relationship: string;
  abstract: string | null;
  title_zh: string | null;
  summary_zh: string | null;
  key_points_zh: string[];
  read_value_zh: string | null;
  summary_basis: "abstract" | "title_metadata";
  summary_model: string | null;
  relevance_score: number;
  quality_score: number;
  practical_score: number;
  total_score: number;
  quality_tier: "A" | "B" | "C" | "D";
  quality_reasons: string[];
  quality_risks: string[];
  preferred_venue: boolean;
  is_new: boolean;
  first_seen_at: string | null;
}

export interface ResearchRadarResponse {
  schema_version: number;
  generated_at: string | null;
  expires_at: string | null;
  source: string;
  source_url: string;
  lookback_days: number;
  cache_hours: number;
  cached: boolean;
  stale: boolean;
  projects: Record<string, { name: string; topics: string[]; result_count: number }>;
  items: ResearchRadarItem[];
  item_count: number;
  selection: {
    candidate_count: number;
    eligible_count: number;
    excluded_count: number;
    new_item_count: number;
    retained_anchor_count: number;
    per_project: number;
    minimum_score: number;
    method: string;
  };
  warnings: string[];
  summary_generation: {
    primary_model?: string;
    fallback_model?: string | null;
    generated_count: number;
    reused_count: number;
    missing_count: number;
    fallback_used: boolean;
    attempts: Array<{
      model: string;
      status: "ok" | "failed";
      generated?: number;
      error?: string;
      usage?: { input_tokens?: number; output_tokens?: number };
    }>;
  };
  explanation: string;
}

// ---------- 今日生活栏 ----------

export interface LifeDashboard {
  date: string;
  timezone: string;
  config_path: string;
  employment: { configured: boolean; start_date: string | null; day_number: number | null };
  next_rest: { date: string | null; days: number | null; reason: string };
  next_holiday: {
    available: boolean;
    name: string | null;
    start: string | null;
    end: string | null;
    days: number | null;
    in_holiday: boolean;
    duration_days: number | null;
    source: string | null;
  };
  progress: { week: number; month: number; year: number };
  payday: { configured: boolean; date: string | null; days: number | null; day: number | null; rule?: "last_day" | "day_of_month" };
  annual_leave: { configured: boolean; total: number | null; used: number | null; remaining: number | null };
  projects: Array<{ project_id: string; name: string; start_date: string | null; days: number | null; source: string | null }>;
  report_streak: { current: number; longest: number; through: string | null; total_reports: number };
  longest_agent_day: { date: string | null; minutes: number; top_project: string | null };
  token_books: { tokens: number; token_per_book: number; books: number; note: string };
  research_weather: { icon: string; name: string; detail: string };
  last_year_today: { date: string; available: boolean; summary: string };
  random_knowledge: { available: boolean; text: string | null; date: string | null };
  gpu_pet: {
    icon: string;
    state: string;
    detail: string;
    observed_at: string | null;
    pets?: Array<{
      gpu: string;
      icon: string;
      state: string;
      detail: string;
      utilization_pct: number;
      memory_used_mb: number;
      temperature_c: number;
      stale: boolean;
    }>;
  };
  milestones: Array<{ name: string; date: string | null; days: number }>;
  notes: string[];
}

// ---------- 项目发展可视化 ----------

export interface DevelopmentTaskNode {
  id: string;
  date: string;
  project_id: string;
  group: string;
  title: string;
  original_title: string;
  did: string[];
  why: string[];
  results: string[];
  conclusions: string[];
  files: string[];
  phase: "探索" | "实现" | "执行" | "验证" | "交付" | "运维";
  work_types: Array<"探索" | "实现" | "执行" | "验证" | "交付" | "运维">;
  status: "working" | "result" | "blocked";
  source: string;
}

export interface DevelopmentThread {
  id: string;
  title: string;
  confidence: string;
  nodes: DevelopmentTaskNode[];
}

export interface DevelopmentMetric {
  project_id: string;
  date: string;
  name: string;
  value: number;
  unit: string;
  task: string;
  source: string;
  context: string;
}

export interface DevelopmentLifecycle {
  project_id: string;
  name: string;
  current_phase: string;
  phase_counts: Record<string, number>;
  work_type_counts: Record<string, number>;
  status: "active" | "blocked" | "dormant" | "historical";
  blockers: Array<{ date: string; text: string }>;
  last_activity: string;
  task_count: number;
  result_count: number;
}

export interface DevelopmentEffort {
  project_id: string;
  name: string;
  tokens: number;
  agent_minutes: number;
  tasks: number;
  results: number;
}

export interface DevelopmentSnapshotProject {
  project_id: string;
  name: string;
  phase: string;
  latest_task: string;
  latest_result: string | null;
  known_results: number;
  today_tasks: string[];
  blockers: string[];
  next: string[];
  not_known_yet: string | null;
  not_known_until: string | null;
}

export interface DevelopmentResponse {
  generated_for: string;
  days: number;
  source: string;
  report_count: number;
  project_names: Record<string, string>;
  storylines: Record<string, DevelopmentTaskNode[]>;
  threads: Record<string, DevelopmentThread[]>;
  metrics: DevelopmentMetric[];
  lifecycles: DevelopmentLifecycle[];
  effort_output: DevelopmentEffort[];
  activity: {
    dates: string[];
    projects: Array<{ project_id: string; name: string; activities: number[]; tokens: number[] }>;
  };
  plans: {
    counts: Record<string, number>;
    items: Array<{ date: string; text: string; status: string; project_ids: string[] }>;
    daily: Array<{ date: string; counts: Record<string, number> }>;
    total: number;
  };
  knowledge: {
    nodes: Array<{ id: string; name: string; full_text?: string; date?: string; category: string; symbol_size: number }>;
    edges: Array<{ source: string; target: string }>;
    explanation: string;
  };
  time_travel: Array<{ date: string; projects: DevelopmentSnapshotProject[] }>;
  explanation: string;
}

export interface DevelopmentSummaryResponse {
  generated_for: string;
  days: number;
  source: string;
  report_count: number;
  project_names: Record<string, string>;
  lifecycles: DevelopmentLifecycle[];
  effort_output: DevelopmentEffort[];
  activity: DevelopmentResponse["activity"];
  counts: { nodes: number; projects: number; metrics: number; plans: number };
  project_identity: { registered: number; unmapped_ids: string[] };
  explanation: string;
}

export interface DevelopmentProjectResponse {
  generated_for: string;
  days: number;
  project_id: string;
  project_name: string;
  storyline: DevelopmentTaskNode[];
  timeline_total: number;
  timeline_limit: number;
  threads: DevelopmentThread[];
  metrics: DevelopmentMetric[];
  lifecycle: DevelopmentLifecycle | null;
  effort: DevelopmentEffort | null;
  activity: DevelopmentResponse["activity"];
  latest_snapshot: { date: string; project: DevelopmentSnapshotProject } | null;
  explanation: string;
}

export interface DevelopmentGlobalResponse {
  generated_for: string;
  days: number;
  plans: DevelopmentResponse["plans"];
  explanation: string;
}

export interface DevelopmentTimelineResponse {
  project_id: string | null;
  offset: number;
  limit: number;
  total: number;
  has_more: boolean;
  items: DevelopmentTaskNode[];
}

export interface DevelopmentHistoryResponse {
  offset: number;
  limit: number;
  total: number;
  has_more: boolean;
  items: Array<{ date: string; projects: Array<Pick<DevelopmentSnapshotProject,
    "project_id" | "name" | "phase" | "latest_task" | "latest_result" | "blockers">> }>;
}

// ---------- 项目情报 ----------

export interface IntelligencePulse {
  project_id: string;
  name: string;
  phase: string;
  status: "active" | "blocked" | "dormant" | "historical";
  latest_result: string | null;
  current_blocker: string | null;
  next_action: string | null;
  open_unknowns: number;
  last_meaningful: string;
  tokens: number;
  result_items: number;
  source_mode: "audited" | "historical_audited" | "historical_fallback" | "stale_last_good" | "empty";
}

export interface IntelligenceUnknown {
  unknown_id: string;
  project_id: string;
  question: string;
  priority: "high" | "medium" | "low";
  missing_evidence: string;
  first_seen: string;
  last_seen: string;
  evidence: string[];
  confidence: string;
  source_mode: string;
}

export interface IntelligenceBreakthrough {
  project_id: string;
  date: string;
  title: string;
  change: string;
  significance: string;
  evidence: string[];
  confidence: string;
  source_mode: string;
}

export interface IntelligenceDeltaItem {
  date: string;
  text: string;
  source: string;
}

export interface IntelligenceDetail {
  delta: {
    from: string;
    to: string;
    results: IntelligenceDeltaItem[];
    knowledge: IntelligenceDeltaItem[];
    blockers: IntelligenceDeltaItem[];
    plan_closure: IntelligenceDeltaItem[];
    unknowns_opened: IntelligenceDeltaItem[];
    unknowns_resolved: IntelligenceDeltaItem[];
    blockers_opened: IntelligenceDeltaItem[];
    blockers_resolved: IntelligenceDeltaItem[];
    change_count: number;
  };
  unknowns: IntelligenceUnknown[];
  stale_unknown_count: number;
  hidden_unknown_count: number;
  stale_blocker_count: number;
  breakthroughs: IntelligenceBreakthrough[];
  storyline: {
    project_id: string;
    summary: string;
    source_mode: string;
    evidence: string[];
    source_dates?: string[];
  };
}

export interface IntelligenceEffortProgress {
  project_id: string;
  name: string;
  tokens: number;
  agent_minutes: number;
  progress_items: number;
  result_items: number;
  completed_plans: number;
  breakthroughs: number;
  resolved_unknowns: number;
  resolved_blockers: number;
  quadrant: "heavy_wins" | "attention_needed" | "efficient_wins" | "low_activity";
}

export interface ProjectIntelligenceResponse {
  generated_for: string;
  days: number;
  latest_report_date: string | null;
  baseline_date: string | null;
  available_dates: string[];
  pulses: IntelligencePulse[];
  effort_progress: IntelligenceEffortProgress[];
  project_details: Record<string, IntelligenceDetail>;
  audit_coverage: {
    report_count: number;
    audited_count: number;
    stale_last_good_count: number;
    fallback_count: number;
    failed_dates: string[];
    last_audited_date: string | null;
  };
  data_quality: string[];
  explanation: string;
}

export type SemanticFeedbackRating = "accurate" | "noise" | "incorrect" | "wrong_project" | "missing";

export interface SemanticFeedbackInput {
  view: string;
  item_id: string;
  project_id: string;
  rating: SemanticFeedbackRating;
  text: string;
  source_dates: string[];
  comment?: string;
  corrected_project_id?: string;
}

export interface SemanticFeedback extends SemanticFeedbackInput {
  event_id: string;
  occurred_at?: string;
}

// ---------- 算法架构快照（离线 Codex 审计，页面只读） ----------

export type AlgorithmNodeStatus = "current" | "candidate" | "optional" | "legacy" | "rejected" | "unknown";

export interface AlgorithmEvidenceSummary {
  bundled: number;
  cited: number;
  models: number;
  explained_models: number;
  metrics: number;
}

export interface AlgorithmArchitectureIndexProject {
  project_id: string;
  name: string;
  priority: string;
  status: "ready" | "not_analyzed" | "insufficient_evidence" | "analysis_failed";
  summary: string;
  algorithm_type: string;
  models: Array<{
    id: string;
    name: string;
    variant: string;
    status: AlgorithmNodeStatus;
    architecture_status: "verified" | "partial" | "opaque";
    architecture_basis?: "deployment_evidence" | "family_reference" | "mixed" | "undisclosed";
  }>;
  generated_at: string | null;
  head: string | null;
  dirty: boolean | null;
  evidence_summary: AlgorithmEvidenceSummary | null;
}

export interface AlgorithmArchitectureIndex {
  schema_version: number;
  generated_at: string;
  projects: AlgorithmArchitectureIndexProject[];
  counts: { total: number; ready: number; not_analyzed: number; insufficient: number; failed: number };
}

export interface AlgorithmPipelineNode {
  id: string;
  label: string;
  category: "input" | "preprocess" | "router" | "model" | "fusion" | "postprocess" | "decision" | "output";
  summary: string;
  status: AlgorithmNodeStatus;
  evidence: string[];
}

export interface AlgorithmPipelineEdge {
  source: string;
  target: string;
  label: string;
  data: string;
  evidence: string[];
}

export interface AlgorithmModelBlock {
  id: string;
  name: string;
  type: string;
  role: string;
  details: string;
  evidence: string[];
}

export interface AlgorithmMetric {
  name: string;
  value: string;
  unit: string;
  scope: string;
  verification: "reported" | "observed" | "platform";
  evidence: string[];
}

export interface AlgorithmModel {
  id: string;
  node_id: string;
  name: string;
  variant: string;
  role: string;
  status: AlgorithmNodeStatus;
  architecture_status: "verified" | "partial" | "opaque";
  architecture_basis?: "deployment_evidence" | "family_reference" | "mixed" | "undisclosed";
  architecture_summary: string;
  input: string;
  output: string;
  blocks: AlgorithmModelBlock[];
  quantization: string;
  parameters: string;
  artifact_size: string;
  design_rationale: string[];
  limitations: string[];
  metrics: AlgorithmMetric[];
  evidence: string[];
}

export interface AlgorithmGroundedItem {
  title?: string;
  rationale?: string;
  name?: string;
  reason?: string;
  before?: string;
  after?: string;
  kind?: "added" | "removed" | "changed" | "warning";
  status?: string;
  question?: string;
  missing_evidence?: string;
  priority?: "high" | "medium" | "low";
  detail?: string;
  evidence: string[];
}

export interface AlgorithmArchitectureSnapshot {
  schema_version: number;
  snapshot_id: string;
  project_id: string;
  project_name: string;
  status: "ready" | "insufficient_evidence" | "analysis_failed";
  algorithm_type: string;
  objective: string;
  summary: string;
  pipeline: { nodes: AlgorithmPipelineNode[]; edges: AlgorithmPipelineEdge[] };
  models: AlgorithmModel[];
  design_decisions: AlgorithmGroundedItem[];
  alternatives: AlgorithmGroundedItem[];
  algorithm_diff: AlgorithmGroundedItem[];
  open_questions: AlgorithmGroundedItem[];
  warnings: AlgorithmGroundedItem[];
  source_state: { head: string | null; branch: string | null; dirty: boolean | null; source_hash: string };
  generated_at: string;
  model_run: {
    model: string | null;
    provider: string;
    reasoning_effort?: string;
    usage?: { input_tokens?: number; cached_input_tokens?: number; output_tokens?: number; reasoning_output_tokens?: number };
  };
  validation_errors: string[];
  evidence_summary: AlgorithmEvidenceSummary;
  evidence_catalog?: Record<string, {
    kind: "source" | "report" | "external";
    source_id: string;
    path: string;
    line_start: number | null;
    line_end: number | null;
    sha256: string;
    text: string;
    scope?: "family_reference" | "official_undisclosed";
    source_type?: "official_docs" | "official_repository" | "official_model_card" | "official_paper";
    url?: string;
    retrieved_at?: string;
  }>;
}

export interface AlgorithmArchitectureDetail {
  snapshot: AlgorithmArchitectureSnapshot;
  history: Array<{ snapshot_id: string; generated_at: string; head: string | null; status: string; summary: string }>;
  research_brief?: ProjectResearchBrief | null;
}

export interface ProjectResearchBrief {
  schema_version: number;
  project_id: string;
  title: string;
  reviewed_at: string;
  overview: string;
  evidence_note: string;
  models: Array<{
    id: string; name: string; variant: string; role: string; summary: string;
    specs: Array<{ label: string; value: string }>;
    stages: Array<{ name: string; kind: string; role: string; detail: string }>;
  }>;
  metric_lanes: Array<{
    level: string; label: string; tone: "good" | "primary" | "warning";
    values: Array<{ name: string; value: string; note: string }>;
  }>;
  experiment_phases: Array<{
    period: string; title: string; question: string; experiments: string[]; takeaway: string;
  }>;
  insights: Array<{ title: string; observation: string; implication: string }>;
  future_directions: Array<{
    priority: "P0" | "P1" | "P2" | "P3"; title: string; hypothesis: string;
    smallest_experiment: string; promotion_gate: string;
  }>;
}

// ---------- 日报实验情报（离线 Codex 提炼，页面只读） ----------

export interface ExperimentNamedItem {
  name: string;
  role?: string;
  scope?: string;
}

export interface ExperimentMetric {
  name: string;
  value: string;
  unit: string;
  scope: string;
  direction: "higher" | "lower" | "target" | "unknown";
}

export interface ExperimentTokenContext {
  total_tokens: number;
  codex_tokens: number;
  claude_tokens: number;
  sessions: number;
  attribution: "project_day_delta" | "unavailable";
  quality: "counter_delta" | "estimated" | "unavailable";
  long_sessions: number;
  counter_regressions: number;
  note: string;
  shared_by_records: number;
}

export interface ExperimentRecord {
  record_id: string;
  project_id: string;
  date: string;
  title: string;
  kind: "experiment" | "benchmark" | "evaluation" | "ablation" | "training" | "deployment_validation";
  question: string;
  method: string;
  models: ExperimentNamedItem[];
  datasets: ExperimentNamedItem[];
  parameters: Array<{ name: string; value: string }>;
  metrics: ExperimentMetric[];
  result_status: "improved" | "regressed" | "mixed" | "failed" | "inconclusive" | "validated" | "observed";
  result_summary: string;
  conclusion: string;
  decision_impact: string;
  verification_scope: string;
  machine: string;
  commit_sha: string;
  artifacts: string[];
  session_ids: string[];
  evidence: string[];
  confidence: string;
  source_mode: string;
  token_context: ExperimentTokenContext;
}

export interface ExperimentMetricSeries {
  project_id: string;
  name: string;
  unit: string;
  scope: string;
  points: Array<{ date: string; value: number; display_value: string; record_id: string; title: string }>;
}

export interface ExperimentIntelligenceResponse {
  schema_version: number;
  generated_from: string;
  target: string;
  since: string;
  project_filter: string | null;
  counts: {
    records: number;
    projects: number;
    metrics: number;
    conclusions: number;
    analyzed_days: number;
    validation_errors: number;
  };
  projects: Array<{
    project_id: string;
    name: string;
    record_count: number;
    metric_count: number;
    latest_date: string;
    result_status: Record<string, number>;
    token_pool_total: number;
    token_pool_days: number;
  }>;
  records: ExperimentRecord[];
  metric_series: ExperimentMetricSeries[];
  token_pools: Array<Omit<ExperimentTokenContext, "shared_by_records"> & { date: string; project_id: string }>;
  validation_errors: Array<{ date: string; error: string }>;
  backfill_status: Record<string, unknown>;
  notes: string[];
}

// ---------- Agent Session 新项目发现（离线 Codex 审查，页面只读） ----------

export type ProjectDiscoveryDecision =
  | "new_project"
  | "existing_project"
  | "temporary_or_reference"
  | "insufficient_evidence";

export interface ProjectDiscoveryReview {
  decision: ProjectDiscoveryDecision;
  project_group?: string;
  suggested_project_id: string;
  suggested_name: string;
  summary: string;
  existing_project_id: string;
  confidence: number;
  reason: string;
}

export interface ProjectDiscoveryCandidate {
  candidate_id: string;
  repo_path: string;
  repo_name: string;
  agents: string[];
  session_ids: string[];
  session_count: number;
  topics: string[];
  observed_paths: string[];
  write_paths: string[];
  write_evidence_count: number;
  first_seen: string | null;
  last_seen: string | null;
  evidence_strength: "strong" | "weak";
  git: { branch: string; last_commit: string; tracked_files: number };
  review: ProjectDiscoveryReview | null;
  review_model?: string;
  review_error?: string;
  accept_command: string;
  related_repos?: string[];
  group_size?: number;
}

export interface ProjectDiscoveryResponse {
  updated_at: string | null;
  scan_days: number;
  counts: {
    candidates: number;
    total_discovered: number;
    new_projects: number;
    existing_projects: number;
    temporary_or_reference: number;
    insufficient_evidence: number;
    pending_review: number;
  };
  candidates: ProjectDiscoveryCandidate[];
  model_policy: { reviewer: string; fallback: null; registry_write: string };
}

export interface BackgroundTaskStage {
  state: "running" | "ok" | "failed" | "skipped";
  started_at?: string | null;
  finished_at?: string | null;
  message?: string;
}

export interface BackgroundTaskStatus {
  schema_version: number;
  updated_at: string | null;
  stages: Record<string, BackgroundTaskStage>;
  model_tools: Record<string, { command: string; path: string; available: boolean }>;
  model_activity?: {
    counts: { model_calls?: number; cache_hits?: number; deferred?: number; failed?: number; fallbacks?: number };
    tokens: { total?: number; input?: number; output?: number; cached?: number };
    duration_ms: number;
  };
}
