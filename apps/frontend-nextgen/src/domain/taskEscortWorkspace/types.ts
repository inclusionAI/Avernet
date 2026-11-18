/** Stable public workspace models; no backend DTOs or React dependencies. */
export type PageId = 'dashboard' | 'workbench' | 'runs' | 'issues' | 'experiences' | 'editor' | 'settings';
export type Period = 'today' | 'yesterday' | '7d' | '30d';
/** Legacy prototype type compatibility only; the formal runtime always uses live. */
export type TaskEscortWorkspaceMode = 'live' | 'demo';
export type IssueStatus = 'open' | 'applied' | 'verified' | 'recurring';
export type RunStatus = 'success' | 'failed' | 'running' | 'waiting' | 'cancelled' | 'unknown';
export type TaskEscortWorkspaceSupport = Record<PageId, { available: boolean; reason?: string }>;

export interface Workflow {
  id: string;
  name: string;
  description: string;
  bot: string;
  version: string;
  successRate: number | null;
  /** In live mode this is only the count in the returned run sample, never a global total. */
  runCount: number;
  status: 'healthy' | 'warning' | 'unknown';
  updatedAt: string | null;
  category: string;
  sampleCount?: number;
}
export interface Run {
  id: string;
  workflowId: string;
  name: string;
  bot: string;
  status: RunStatus;
  /** Seconds, or null when the backend supplies no valid duration. */
  duration: number | null;
  startedAt: string | null;
  trigger: string;
  node: string;
  succeededCount?: number | null;
  failedCount?: number | null;
  totalTokens?: number | null;
  sessionId?: string | null;
  rawStatus?: string | null;
}
export interface Issue {
  id: string;
  title: string;
  description: string;
  workflowId: string;
  workflowName: string;
  node: string;
  severity: 'high' | 'medium' | 'low';
  status: IssueStatus;
  affectedRuns: number;
  category: string;
  rootCause: string;
  recommendation: string;
  before: string;
  after: string;
  evidence: string[];
  confidence: number;
}
export interface Experience {
  id: string;
  title: string;
  description: string;
  category: string;
  workflowName: string;
  reuseCount: number;
  verified: boolean;
  sourceIssueId: string;
}
export interface Metrics {
  runs: number;
  successRate: number;
  anomalies: number;
  p50: number;
  running: number;
  waiting: number;
  blocked: number;
  queued: number;
  series: number[];
  successSeries: number[];
  labels: string[];
  /** Live global metrics are unavailable; their numeric placeholders must not be rendered as real totals. */
  available?: boolean;
  scope?: 'demo' | 'unavailable';
  reason?: string;
}
export interface TaskEscortSettings {
  enabled: boolean;
  autoAnalyze: boolean;
  humanReview: boolean;
  notifications: boolean;
  recurrence: boolean;
  sensitivity: string;
  webhook: string;
}
export interface TaskEscortWorkspaceSnapshot {
  workflows: Workflow[];
  runs: Run[];
  issues: Issue[];
  experiences: Experience[];
  settings: TaskEscortSettings;
  settingsDirty: boolean;
  loading: boolean;
  error: string | null;
}
export interface TaskEscortOperationResult {
  ok: boolean;
  message: string;
}
export interface DomainRunDetail {
  run: Run;
  /** Only actual backend node executions, never inferred topology. Empty in demo. */
  nodes: Array<{
    id: string;
    title: string;
    status: RunStatus;
    duration: number | null;
    error: string | null;
    input: string | null;
    output: string | null;
  }>;
}
export type TaskEscortRunDetail = DomainRunDetail;
export type TaskEscortWorkspaceRunDetail = DomainRunDetail;

/** Original prototype controller surface, with explicit mode and read lifecycle. */
export interface TaskEscortWorkspaceController {
  page: PageId;
  navigate: (page: PageId) => void;
  period: Period;
  setPeriod: (period: Period) => void;
  metrics: Metrics;
  workflows: Workflow[];
  runs: Run[];
  issues: Issue[];
  experiences: Experience[];
  query: string;
  setQuery: (query: string) => void;
  issueFilter: string;
  setIssueFilter: (filter: string) => void;
  runFilter: string;
  setRunFilter: (filter: string) => void;
  selectedIssue: string | null;
  setSelectedIssue: (id: string | null) => void;
  selectedRun: string | null;
  setSelectedRun: (id: string | null) => void;
  workflowId: string;
  setWorkflowId: (id: string) => void;
  applySuggestion: (id: string) => void;
  verifyIssue: (id: string) => void;
  saveExperience: (id: string) => void;
  settings: TaskEscortSettings;
  settingsDirty: boolean;
  updateSettings: (patch: Partial<TaskEscortSettings>) => void;
  saveSettings: () => boolean;
  notice: (message: string) => void;
  reset: () => void;
  mode: TaskEscortWorkspaceMode;
  loading: boolean;
  error: string | null;
  refresh: () => void;
  support: TaskEscortWorkspaceSupport;
}
export interface WorkspaceViewProps {
  demo: TaskEscortWorkspaceController;
}
export type ViewProps = WorkspaceViewProps;
/** Compatibility names for authored prototype view ports. */
export type DemoSettings = TaskEscortSettings;
export type DemoController = TaskEscortWorkspaceController;
