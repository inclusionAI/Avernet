/** Browser-safe GET-only protocol. Full originals and frozen materials are separate endpoints. */
import { backendRequest } from '../httpClient';
export interface RepairCapabilityDto {
  generation: boolean;
  diff: boolean;
  publication: boolean;
  reason: string | null;
}
export interface RepairItemDto {
  itemId: string;
  groupKey: string;
  proposalKey: string;
  contentRevision: number;
  previousItemId: string | null;
  proposal: Record<string, unknown> | null;
  instruction: string;
  sources: Record<string, unknown>[];
  context?: Record<string, unknown>;
  workflowId?: string;
  state?: string;
  stateVersion?: number;
  sourceAvailable?: boolean;
  activeTaskId?: string | null;
  readSummary?: Record<string, unknown>;
}
export interface RepairRevisionDto {
  workflowId: string;
  taskId: string;
  revision: number;
  phase: string;
  stateVersion: number;
  input: {
    items: RepairItemDto[];
    baseline: Record<string, unknown>;
    materials?: Record<string, unknown>;
    [key: string]: unknown;
  };
  draft: Record<string, unknown> | null;
  checks: Record<string, unknown> | null;
  error: Record<string, unknown> | null;
  createdAtMs: number;
  updatedAtMs: number;
}
export interface RepairTaskDto {
  workflowId: string;
  taskId: string;
  title?: string;
  source?: string;
  latestAttempt: RepairRevisionDto;
  latestSuccessful: RepairRevisionDto | null;
  revisions: RepairRevisionDto[];
  capabilities: RepairCapabilityDto;
  revisionPage?: { page: number; pageSize: number; total: number; totalPages: number };
  release?: { approval: Record<string, unknown> | null; deployments: Record<string, unknown>[] };
  execution?: Record<string, unknown>;
  executions?: Record<string, Record<string, unknown>>;
  automaticProgress?: Record<string, unknown>;
}
export interface RepairCandidatesDto {
  schemaVersion: string;
  workflowId: string;
  inputDigest: string;
  items: RepairItemDto[];
  tasks: {
    taskId: string;
    revision: number;
    phase: string;
    updatedAtMs: number;
    itemCount: number;
    title?: string;
    source?: string;
  }[];
  capabilities: RepairCapabilityDto;
  activeSnapshot?: unknown;
  includeHistorical: boolean;
  facets: { nodes: string[]; failureModes: string[] };
  counts: Record<string, number>;
  page: { page: number; pageSize: number; total: number; totalPages: number };
  limits: { maxItems: number; maxRequestBytes: number };
  canEdit: boolean;
  metadata?: { page: number; totalPages: number; digest: string };
}
export interface RepairQueryDto {
  versionScope?: 'current' | 'historical' | 'all';
  state?: string;
  page?: number;
  pageSize?: number;
  includeHistorical?: boolean;
  includeTasks?: boolean;
  nodeId?: string;
  failureMode?: string;
  signature?: string;
  snapshotKey?: string;
  metadataPage?: number;
}
const base = '/openapi/v1/harnessflow/api/workflow-repairs';
const seg = encodeURIComponent;
function get<T>(path: string, userId: string, params: Record<string, unknown>, signal?: AbortSignal): Promise<T> {
  return backendRequest(`${base}${path}`, {
    method: 'GET',
    injectUserId: false,
    params: { ...params, user_id: userId },
    signal,
  });
}
export const repairController = {
  candidates: (workflowId: string, userId: string, query: RepairQueryDto, signal?: AbortSignal) =>
    get<RepairCandidatesDto>('/candidates', userId, { workflowId, ...query }, signal),
  item: (workflowId: string, itemId: string, userId: string, signal?: AbortSignal) =>
    get<RepairItemDto>(`/items/${seg(itemId)}`, userId, { workflowId, originalOnly: true }, signal),
  task: (taskId: string, userId: string, revisionPage: number, signal?: AbortSignal) =>
    get<RepairTaskDto>(`/${seg(taskId)}`, userId, { revisionPage }, signal),
  material: (taskId: string, revision: number, itemId: string, userId: string, signal?: AbortSignal) =>
    get<RepairItemDto>(`/${seg(taskId)}/revisions/${revision}/materials/${seg(itemId)}`, userId, {}, signal),
  diff: (taskId: string, revision: number, base: 'baseline' | 'parent', userId: string, signal?: AbortSignal) =>
    get<{
      baseCommit: string;
      candidateCommit: string;
      files: {
        path: string;
        status: string;
        before: string | null;
        after: string | null;
        binary: boolean;
        truncated: boolean;
        patch?: string;
      }[];
      truncated: boolean;
    }>(`/${seg(taskId)}/revisions/${revision}/diff`, userId, { base }, signal),
  trials: (taskId: string, offset: number, userId: string, signal?: AbortSignal) =>
    get<{ configured: boolean; trials: Record<string, unknown>[]; nextOffset?: number | null }>(
      `/${seg(taskId)}/trials`,
      userId,
      { offset },
      signal,
    ),
  verification: (taskId: string, deploymentId: string, userId: string, signal?: AbortSignal) =>
    get<{ evidence: Record<string, unknown>; conclusions: Record<string, unknown>[] }>(
      `/${seg(taskId)}/deployment/${seg(deploymentId)}/verification`,
      userId,
      {},
      signal,
    ),
};
