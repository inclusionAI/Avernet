/** Read-only evolution models. Missing measurements remain null. */
export type VersionScope = 'current' | 'historical' | 'all';
export interface PageInfo {
  page: number;
  pageSize: number;
  total: number;
  totalPages: number;
}
export interface IssueQuery {
  versionScope: VersionScope;
  page: number;
  pageSize: number;
  sort?: 'priority' | 'frequency' | 'recent';
  severity?: string;
  nodeId?: string;
  failureMode?: string;
}
export interface Snapshot {
  status: string;
  snapshot?: {
    kind: string;
    workflowId: string;
    workflowVersion?: number;
    deployNumber?: number;
    commit?: string;
    configDigest?: string;
    repoId?: string;
    specPath?: string;
  };
}
export interface IssueSource {
  sourceId: string;
  analysisId: string;
  diagnosisId: string;
  flowId: string;
  flowIds: string[];
  nodeId: string | null;
  failureMode: string;
  reasoning: string | null;
  completedAtMs: number | null;
  evidenceEventIds: string[];
  snapshot: Snapshot | null;
  proposal?: Record<string, unknown> | null;
}
export interface IssueGroup {
  workflowId: string;
  signature: string;
  inputDigest: string;
  flowIds: string[];
  sources: IssueSource[];
  summarySources: IssueSource[] | null;
  summaryScopeNote?: string;
  summary: {
    summary: string;
    causes: { title: string; conclusion: string; certainty: string; sourceIds: string[] }[];
    unknowns: string[];
  } | null;
  stale: boolean;
  aggregationStatus: string;
  aggregationId: string | null;
  inputSummary?: Record<string, unknown> | null;
  priority: {
    severity: string | null;
    day: { affected: number | null };
    week: { affected: number | null };
    lastOccurredAtMs: number | null;
  } | null;
}
export interface IssuePageData {
  groups: IssueGroup[];
  facets: { nodes: string[]; modes: string[] };
  page: PageInfo;
}
export interface WorkflowAccess {
  workflowId: string;
  canView: boolean;
  canEdit: boolean;
}
export interface Lesson {
  id: string;
  kind: string;
  status: string;
  workflowId: string | null;
  nodeId: string | null;
  signature: string;
  mode: string;
  source: string | null;
  createdAt: number | string | null;
  hits: number | null;
  rescued: number | null;
  successRate: number | null;
}
export interface LessonPage {
  lessons: Lesson[];
  total: number;
  limit: number;
  offset: number;
}
export interface Diagnosis {
  id: string;
  workflowId: string;
  flowId: string;
  analysisId: string | null;
  signature: string;
  reasoning: string | null;
  nodeId: string | null;
  mode: string;
  createdAt: number | string | null;
}
export interface DiagnosisPage {
  diagnoses: Diagnosis[];
  total: number;
  limit: number;
  offset: number;
}
export interface Analysis {
  analysisId: string;
  flowId: string;
  workflowId: string;
  status: string;
  evidenceStatus: string | null;
  facts: string[];
  inferences: string[];
  unknowns: string[];
  diagnoses: Record<string, unknown>[];
}
export interface Suggestion {
  id: string;
  signature: string;
  description: string;
  kind: string;
  status: string;
  nodeId: string | null;
  evidenceRuns: string[];
  proposal: Record<string, unknown> | null;
  verificationStatus: string | null;
  recurrenceCount: number | null;
}
export interface SuggestionPage {
  suggestions: Suggestion[];
  total: number;
  limit: number;
  offset: number;
}
export interface ApplyTask {
  taskId: string;
  suggestionId: string;
  status: string;
  summary: string | null;
  errorMessage: string | null;
  proposal: Record<string, unknown> | null;
  progress: Record<string, unknown> | null;
}
