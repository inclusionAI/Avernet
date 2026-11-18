/** Trusted read-only presentation models. Protocol DTOs belong to the backend Controller. */
export type DashboardRangeKey = 'today' | 'yesterday' | '7d' | '30d';
export type DashboardWidgetRange = 'global' | Exclude<DashboardRangeKey, 'today'>;
export type DashboardWidget = 'trend' | 'hotspots' | 'workflows';
/** All request timestamps are epoch seconds; durations in responses are milliseconds. */
export interface DashboardTimeRange {
  from: number;
  to: number;
}
export interface DashboardWindow extends DashboardTimeRange {
  label: string;
}
export interface DashboardScope {
  key: string;
  ready: boolean;
  loading?: boolean;
  error?: string;
  retry?: () => void;
}
export interface DashboardEvolution {
  available: boolean;
  verificationAvailable?: boolean;
  diagnosisCount: number | null;
  diagnosedRunCount?: number | null;
  issueClusterCount?: number | null;
  suggestionCount: number | null;
  applicationAttemptCount: number | null;
  applicationSucceededCount: number | null;
  applicationFailedCount: number | null;
  applicationSuccessRate: number | null;
  appliedUnverifiedCount: number | null;
  recurrenceDetectedCount: number | null;
  verifiedCount: number | null;
}
export interface DashboardOverview {
  workflowCount: number | null;
  totalRuns: number;
  succeededCount: number | null;
  /** Unsuccessful terminal states, not just literal failed. */
  failedCount: number | null;
  runningCount: number | null;
  terminalCount: number | null;
  nonTerminalCount: number | null;
  /** Fraction 0..1 of succeeded / all terminal runs. */
  successRate: number | null;
  prevSuccessRate: number | null;
  avgDurationMs: number | null;
  totalTokenUsage: number | null;
  statusDistribution: Record<string, number | null>;
  machineDurationP50?: number | null;
  prevMachineDurationP50?: number | null;
  evolution?: DashboardEvolution | null;
}
export interface DashboardDailyPoint {
  date: string;
  runCount: number | null;
  succeededCount: number | null;
  failedCount: number | null;
  successRate: number | null;
  avgDurationMs: number | null;
  tokenUsage: number | null;
}
export interface DashboardDailyTrend extends DashboardTimeRange {
  dates: DashboardDailyPoint[];
}
export interface DashboardFailureHotspot {
  nodeLabel: string;
  workflowTitle: string;
  sceneName: string | null;
  count: number | null;
  /** Percentage 0..100, unlike successRate. */
  sharePct: number | null;
  exampleError: string | null;
  ticketRef?: string | null;
}
export interface DashboardFailureHotspots extends DashboardTimeRange {
  hotspots: DashboardFailureHotspot[];
}
export interface DashboardWorkflowHealthRow {
  workflowId: string;
  workflowTitle: string;
  sceneName: string | null;
  released: boolean | null;
  runCount: number | null;
  /** Literal failed only; do not substitute overview's anomalous terminal count. */
  failedCount: number | null;
  completionSuccessRate: number | null;
  selfHealTriggeredRuns: number | null;
  selfHealSuccessRate: number | null;
  machineDurationP50: number | null;
  avgDurationMs: number | null;
}
export interface DashboardWorkflowHealth extends DashboardTimeRange {
  available?: boolean;
  workflows: DashboardWorkflowHealthRow[];
}
export interface DashboardWorkflowReleaseRow {
  workflowId: string;
  workflowTitle: string;
  createdAt: number | null;
  released: boolean | null;
  deployCount: number | null;
  rollbackCount: number | null;
  devCycleMs: number | null;
  lastDeployAt: number | null;
  firstRunSuccessRate: number | null;
}
/** Request is ranged; response deliberately has no from/to envelope. */
export interface DashboardWorkflowReleases {
  available: boolean;
  workflows: DashboardWorkflowReleaseRow[];
}
export interface DashboardApi {
  overview: (range: DashboardTimeRange, signal?: AbortSignal) => Promise<DashboardOverview>;
  dailyTrend: (range: DashboardTimeRange, signal?: AbortSignal) => Promise<DashboardDailyTrend>;
  failureHotspots: (range: DashboardTimeRange, signal?: AbortSignal) => Promise<DashboardFailureHotspots>;
  workflowHealth: (range: DashboardTimeRange, signal?: AbortSignal) => Promise<DashboardWorkflowHealth>;
  workflowReleaseStats: (range: DashboardTimeRange, signal?: AbortSignal) => Promise<DashboardWorkflowReleases>;
}
export interface DashboardResource<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  retry: () => void;
}
export type DashboardWorkflowStatus = 'released' | 'testing' | 'unpublished' | 'unknown';
export type DashboardWorkflowSort =
  | 'completionAsc'
  | 'runDesc'
  | 'failedDesc'
  | 'machineAsc'
  | 'healDesc'
  | 'deployDesc'
  | 'recentDeployDesc';
export interface DashboardWorkflowRow {
  workflowId: string;
  workflowTitle: string;
  status: DashboardWorkflowStatus;
  runCount: number | null;
  failedCount: number | null;
  completionSuccessRate: number | null;
  selfHealSuccessRate: number | null;
  selfHealTriggeredRuns: number | null;
  machineDurationP50: number | null;
  deployCount: number | null;
  devCycleMs: number | null;
  lastDeployAt: number | null;
  firstRunSuccessRate: number | null;
}
