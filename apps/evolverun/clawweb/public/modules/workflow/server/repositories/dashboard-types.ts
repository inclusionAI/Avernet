// --- Types ---

export interface OverviewRow {
  workflowCount: number;
  totalRuns: number;
  succeededCount: number;
  failedCount: number;
  runningCount: number;
  terminalCount: number;
  nonTerminalCount: number;
  avgDurationMs: number | null;
  totalTokenUsage: number;
  statusDistribution: Record<string, number>;
  prevSucceededCount: number;
  prevFailedCount: number;
  // ── 三主线(可算;缺字段者在 getOverview 里留 null)──
  completionSuccessRate: number | null;       // 长程完成率
  prevCompletionSuccessRate: number | null;
  machineDurationP50: number | null;          // 端到端完成耗时 P50(ms)
  machineDurationP95: number | null;
  prevMachineDurationP50: number | null;
  durationSampleCount: number;
  dau: number | null;                         // 近 1 天 distinct user_id
  wau: number | null;                         // 近 7 天
  prevDau: number | null;
  // ── 发布相关(MySQL only;SQLite 留 null)──
  releasedWorkflowCount: number | null;
  newReleasedThisWeek: number | null;
  monthlyReleasedCount: number | null;
  windowReleasedCount: number | null;      // 窗口 [from,to] 内 deploy 过的 distinct 工作流数(跟页面 timeRange)
}

export interface EvolutionMetricsRow {
  available: boolean;
  verificationAvailable: boolean;
  diagnosisCount: number;
  diagnosedRunCount: number;
  issueClusterCount: number;
  suggestionCount: number;
  applicationAttemptCount: number;
  applicationSucceededCount: number;
  applicationFailedCount: number;
  applicationSuccessRate: number | null;
  appliedUnverifiedCount: number | null;
  recurrenceDetectedCount: number | null;
  verifiedCount: number | null;
}

export interface DailyTrendPoint {
  date: string;
  runCount: number;
  succeededCount: number;
  failedCount: number;
  avgDurationMs: number | null;
  tokenUsage: number;
  // 三主线(运行质量演进图):长程完成率 / 耗时 P50 真实可算;自愈缺字段恒 null
  completionSuccessRate?: number | null;
  machineDurationP50?: number | null;
  selfHealSuccessRate?: number | null;
}

export interface DurationBucket {
  label: string;
  minMs: number;
  maxMs: number | null;
  count: number;
  percentage: number;
}

export interface TopWorkflowRow {
  workflowId: string;
  workflowTitle: string;
  runCount: number;
  succeededCount: number;
  failedCount: number;
  successRate: number | null;
  avgDurationMs: number | null;
}

export interface FailureHotspotRow {
  nodeLabel: string;
  workflowTitle: string;
  sceneName: string;
  count: number;
  sharePct: number;
  exampleError: string;
  ticketRef?: string;
}

export interface ReleaseEventRow {
  workflowId: string;
  workflowTitle: string;
  sceneName: string;
  releasedAt: number;
  releaseDurationMs: number;
  firstRuns: number;
  firstRunSuccessCount: number;
  firstRunSuccessRate: number | null;
  rolledBack: boolean;
}

export interface ReleaseEfficiencyRow {
  available: boolean;
  from: number;
  to: number;
  releaseCount: number;
  rollbackCount: number;
  rollbackRate: number | null;
  successRate: number | null;
  avgReleaseDurationMs: number | null;
  releases: ReleaseEventRow[];
}

export type MetricKey =
  | "runs"
  | "activeWorkflows"
  | "dau"
  | "releases"
  | "completionRate"
  | "successRate"
  | "machineP50"
  | "deploys"
  | "releaseSuccessRate"
  | "rollbackRate"
  | "deliveryLagHours"
  // 字段未落地(self_heal_triggered / track),恒 available:false;前端 demo 有 mock
  | "selfHealRate"
  | "onlineCompletionRate"
  | "onlineRuns"
  | "testRuns";
export type TrendGranularity = "day" | "week" | "month";

export interface MetricTrendPoint {
  bucket: string;
  value: number | null;   // null = 该桶无样本(比率类/拖期类可能出现),图上断点
}

export interface MetricTrendRow {
  available: boolean;
  metric: MetricKey;
  granularity: TrendGranularity;
  from: number;
  to: number;
  points: MetricTrendPoint[];
}

export interface WorkflowHealthRow {
  workflowId: string;
  workflowTitle: string;
  sceneName: string;
  released: boolean;
  runCount: number;
  failedCount: number;
  completionSuccessRate: number | null;
  selfHealTriggeredRuns: number;
  selfHealSuccessRate: number | null;
  machineDurationP50: number | null;
  avgDurationMs: number | null;
}

export interface WorkflowHealthResult {
  available: boolean;
  from: number;
  to: number;
  workflows: WorkflowHealthRow[];
}

export interface ReleaseQualityPoint {
  bucket: string;
  releaseCount: number;
  rollbackCount: number;
  successRate: number | null;   // 该桶内 deploy 的"前 10 条 run 成功率≥0.9"占比
  rollbackRate: number | null;
}

export interface ReleaseQualityTrendRow {
  available: boolean;
  from: number;
  to: number;
  granularity: TrendGranularity;
  points: ReleaseQualityPoint[];
}

export interface WorkflowReleaseStatRow {
  workflowId: string;
  workflowTitle: string;
  createdAt: number | null;          // 工作流创建时间(workflow_specs.gmt_create)
  released: boolean;                 // deployCount > 0
  deployCount: number;
  rollbackCount: number;
  devCycleMs: number | null;         // 研发周期 = 首次部署 − 首次保存(含排期等待,参考量)
  lastDeployAt: number | null;
  firstRunSuccessRate: number | null; // 首次部署后前 10 条 run 成功率
}

export interface WorkflowReleaseStatsResult {
  available: boolean;                // false = 非 MySQL,无 deploy 历史表
  workflows: WorkflowReleaseStatRow[];
}
