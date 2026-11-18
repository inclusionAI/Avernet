/** Protocol DTOs only. No Domain imports and no mutation endpoints. */
import { backendRequest } from '../httpClient';
export interface EvolutionPageDto {
  page: number;
  pageSize: number;
  total: number;
  totalPages: number;
}
export interface EvolutionSourceDto {
  sourceId: string;
  analysisId: string;
  diagnosisId: string;
  flowId: string;
  flowIds?: string[];
  nodeId: string | null;
  failureMode: string;
  reasoning?: string | null;
  completedAtMs: number;
  evidenceEventIds?: string[];
  analysisContext?: { workflowSnapshot?: unknown };
  proposal?: Record<string, unknown>;
}
export interface EvolutionGroupDto {
  workflowId: string;
  signature: string;
  inputDigest: string;
  flowIds: string[];
  sources: EvolutionSourceDto[];
  summarySources?: EvolutionSourceDto[];
  summary: {
    summary: string;
    causes: { title: string; conclusion: string; certainty: string; sourceIds: string[] }[];
    unknowns: string[];
  } | null;
  stale: boolean;
  aggregationStatus: string;
  aggregationId: string | null;
  inputSummary?: Record<string, unknown>;
  aggregationInputSummary?: Record<string, unknown>;
  priority?: {
    severity: string | null;
    day: { affected: number };
    week: { affected: number };
    lastOccurredAtMs: number | null;
  };
}
export interface EvolutionLessonDto {
  id: number;
  lesson_id: string;
  workflow_id: string | null;
  node_id: string | null;
  failure_signature: string;
  failure_mode: string;
  executor_type?: string | null;
  fix_kind: string;
  fix_spec?: string;
  status: string;
  confidence?: number;
  hit_count: number | null;
  rescued_count: number | null;
  successRate: number | null;
  note?: string | null;
  created_by?: string | null;
  updated_by?: string | null;
  source?: string | null;
  gmt_create: number | string | null;
  gmt_modified?: number | string | null;
}
export interface EvolutionDiagnosisDto {
  id: number | string;
  diagnosis_id: string;
  workflow_id: string;
  flow_id: string;
  analysis_id?: string;
  failure_signature: string;
  reasoning?: string | null;
  node_id: string | null;
  failure_mode: string;
  gmt_create: number | string | null;
}
export interface EvolutionAnalysisDto {
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
export interface EvolutionSuggestionDto {
  id: string;
  signature: string;
  description: string;
  kind: string;
  status: string;
  weakNode?: string | null;
  evidenceRuns?: string[];
  proposal?: Record<string, unknown> | null;
  verificationStatus?: string;
  recurrenceCount?: number;
}
export interface EvolutionApplyTaskDto {
  taskId: string;
  suggestionId: string;
  status: string;
  summary: string | null;
  errorMessage: string | null;
  proposal: Record<string, unknown> | null;
  progress?: Record<string, unknown> | null;
}
export interface EvolutionGroupQueryDto {
  versionScope: 'current' | 'historical' | 'all';
  page?: number;
  pageSize?: number;
  signature?: string;
  sort?: string;
  severity?: string;
  nodeId?: string;
  failureMode?: string;
}
const base = '/openapi/v1/harnessflow/api';
function get<T>(path: string, userId: string, params: Record<string, unknown>, signal?: AbortSignal): Promise<T> {
  return backendRequest(`${base}${path}`, {
    method: 'GET',
    injectUserId: false,
    params: { ...params, user_id: userId },
    signal,
  });
}
export const evolutionController = {
  access: (workflowId: string, userId: string, signal?: AbortSignal) =>
    get<{ workflowId: string; canView: boolean; canEdit: boolean }>(
      `/workflows/${encodeURIComponent(workflowId)}/access`,
      userId,
      {},
      signal,
    ),
  groups: (workflowId: string, userId: string, query: EvolutionGroupQueryDto, signal?: AbortSignal) =>
    get<{ groups: EvolutionGroupDto[]; facets?: { nodes: string[]; modes: string[] }; page?: EvolutionPageDto }>(
      '/evolve/issue-groups',
      userId,
      { workflowId, ...query },
      signal,
    ),
  diagnoses: (
    workflowId: string,
    userId: string,
    query: { flowId?: string; analysisId?: string; limit: number; offset: number },
    signal?: AbortSignal,
  ) =>
    get<{ diagnoses: EvolutionDiagnosisDto[]; total: number; limit: number; offset: number }>(
      '/evolve/diagnoses',
      userId,
      { workflowId, ...query },
      signal,
    ),
  analysis: (flowId: string, analysisId: string | undefined, userId: string, signal?: AbortSignal) =>
    get<{ analysis: EvolutionAnalysisDto | null }>(
      `/evolve/runs/${encodeURIComponent(flowId)}/analysis-result`,
      userId,
      analysisId ? { analysisId } : {},
      signal,
    ),
  suggestions: (
    workflowId: string,
    userId: string,
    query: { status?: string; limit: number; offset: number },
    signal?: AbortSignal,
  ) =>
    get<{ suggestions: EvolutionSuggestionDto[]; total: number; limit?: number; offset?: number }>(
      '/evolve/suggestions',
      userId,
      { workflowId, ...query },
      signal,
    ),
  // backendRequest's generic params serializer stringifies arrays and cannot append to existing queries.
  // Serialize this one repeated-key protocol atomically, including the bound principal.
  applyTasks: (suggestionIds: string[], userId: string, signal?: AbortSignal) => {
    const query = new URLSearchParams({ user_id: userId });
    suggestionIds.forEach((id) => query.append('suggestionIds', id));
    return backendRequest<{ tasks: EvolutionApplyTaskDto[] }>(`${base}/evolve/suggestions/apply-tasks?${query}`, {
      method: 'GET',
      injectUserId: false,
      signal,
    });
  },
  lessons: (
    workflowId: string,
    userId: string,
    query: { status?: string; query?: string; limit: number },
    signal?: AbortSignal,
  ) =>
    get<{ lessons: EvolutionLessonDto[]; total: number; limit: number; offset: number }>(
      '/evolve/lessons',
      userId,
      { workflowId, ...query },
      signal,
    ),
};
