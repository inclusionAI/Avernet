/** Read models: missing metrics remain null; no paginated sample is an aggregate. */
export interface OverviewRun {
  id: string;
  workflowId: string;
  workflowTitle: string | null;
  status: string;
  nodeCount: number | null;
  succeededCount: number | null;
  failedCount: number | null;
  startedAt: number | null;
  completedAt: number | null;
  durationMs: number | null;
  totalTokens: number | null;
  initiator: string | null;
  botId: string | null;
  pluginVersion: string | null;
  version: number | null;
  engine: string | null;
  analysisStatus: string | null;
}
export interface OverviewRuns {
  runs: OverviewRun[];
  total: number;
  statusCounts: Record<string, number> | null;
}
export interface OverviewNodeExecution {
  id: string;
  runId: string;
  nodeId: string;
  title: string | null;
  status: string;
  executor: string | null;
  initiator: string | null;
  phase: string | null;
  attempt: number | null;
  durationMs: number | null;
  startedAt: number | null;
  completedAt: number | null;
  input: string | null;
  output: string | null;
  error: string | null;
  tokens: string | null;
  context: string | null;
  prompt: string | null;
  progress: string | null;
  sessionKey: string | null;
  sessionId: string | null;
}
/** Display-only, redacted launch projection. Never retains the raw stored JSON. */
export interface OverviewLaunchField {
  label: string;
  source: string;
  text: string | null;
  note: string | null;
}
/** Detail-only fields: paginated run-list consumers do not require this projection. */
export interface OverviewDetailedRun extends OverviewRun {
  launchInput: OverviewLaunchField;
  launchParams: OverviewLaunchField;
  parentRunId: string | null;
  rootRunId: string | null;
  deviceId: string | null;
  botType: string | null;
  stage: string | null;
}
export interface OverviewRunDetail {
  run: OverviewDetailedRun;
  nodes: OverviewNodeExecution[];
}
export interface OverviewHealth {
  overallScore: number | null;
  p95DurationMs: number | null;
  successRate: number | null;
  nodeFailureRate: number | null;
  retryRate: number | null;
  totalTokens: number | null;
  bottleneckNode: string | null;
  fragileNode: string | null;
  recommendation: string | null;
}
export interface OverviewTrendPoint {
  date: string;
  totalRuns: number | null;
  succeededRuns: number | null;
  failedRuns: number | null;
  successRate: number | null;
}
export interface OverviewNodeStat {
  nodeId: string;
  title: string | null;
  executor: string | null;
  totalExecutions: number | null;
  succeededCount: number | null;
  failedCount: number | null;
  failureRate: number | null;
  avgRetryCount: number | null;
  p50DurationMs: number | null;
  p95DurationMs: number | null;
  avgTokens: number | null;
  lastError: string | null;
  errorCategories: Array<{ category: string; count: number | null; sampleError: string | null }>;
}
export interface OverviewNodeStats {
  workflowId: string;
  totalRuns: number | null;
  nodes: OverviewNodeStat[];
}
export interface OverviewWorkflow {
  id: string;
  version: string | null;
  nodes: Array<{ id: string; title: string; executor: string | null; dependsOn: string[] }>;
}
export interface OverviewLog {
  id: string;
  timestamp: number | null;
  relativeMs: number | null;
  nodeId: string | null;
  level: string;
  source: string | null;
  message: string;
}
export interface OverviewArchive {
  id: string;
  status: string | null;
  createdAt: number | null;
  errors: string[];
  failedNodeCount: number | null;
  failedNodes: Array<{ nodeId: string; title: string | null; error: string | null; attempt: number | null }>;
  rootCauseHints: string[];
  analysis: {
    source: string | null;
    status: string | null;
    summary: string | null;
    error: string | null;
    diagnoses: unknown[];
  } | null;
  suggestedYaml: {
    content: string | null;
    summary: string | null;
    confidence: string | null;
    proposal: unknown;
  } | null;
  sections: Array<{ key: string; label: string; value: unknown }>;
}
export interface OverviewRunFilters {
  page: number;
  status: string;
  query: string;
  time: string;
}
export type OverviewDays = 'today' | 'yesterday' | '7' | '30';
export interface OverviewReadState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  refresh: () => void;
}
