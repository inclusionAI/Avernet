import type { PageInfo, Snapshot, VersionScope } from './evolutionTypes';
export interface RepairCapabilities {
  generation: boolean;
  diff: boolean;
  publication: boolean;
  reason: string | null;
}
export interface RepairItem {
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
export interface RepairTaskSummary {
  taskId: string;
  revision: number;
  phase: string;
  updatedAtMs: number;
  itemCount: number;
  title?: string;
  source?: string;
}
export interface RepairCandidates {
  workflowId: string;
  inputDigest: string;
  items: RepairItem[];
  tasks: RepairTaskSummary[];
  capabilities: RepairCapabilities;
  activeSnapshot: Snapshot | null;
  includeHistorical: boolean;
  facets: { nodes: string[]; failureModes: string[] };
  counts: Record<string, number>;
  page: PageInfo;
  limits: { maxItems: number; maxRequestBytes: number };
  canEdit: boolean;
}
export interface RepairQuery {
  versionScope?: VersionScope;
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
export interface RepairRevision {
  workflowId: string;
  taskId: string;
  revision: number;
  phase: string;
  stateVersion: number;
  input: {
    items: RepairItem[];
    baseline: Record<string, unknown>;
    materials?: Record<string, unknown>;
    automation?: Record<string, unknown>;
    [key: string]: unknown;
  };
  draft: Record<string, unknown> | null;
  checks: Record<string, unknown> | null;
  error: Record<string, unknown> | null;
  createdAtMs: number;
  updatedAtMs: number;
}
export interface RepairDeployment {
  id: string;
  workflowId: string;
  taskId: string;
  revision: number;
  status: string;
  receipt: { candidateCommit: string; deployNumber: number; releaseVersion: number; [key: string]: unknown } | null;
  error: string | null;
  [key: string]: unknown;
}
export interface RepairTask {
  workflowId: string;
  taskId: string;
  title?: string;
  source?: string;
  latestAttempt: RepairRevision;
  latestSuccessful: RepairRevision | null;
  revisions: RepairRevision[];
  capabilities: RepairCapabilities;
  revisionPage?: PageInfo;
  release?: { approval: Record<string, unknown> | null; deployments: RepairDeployment[] };
  execution?: Record<string, unknown>;
  executions?: Record<string, Record<string, unknown>>;
  automaticProgress?: Record<string, unknown>;
}
export interface RepairDiff {
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
}
export interface RepairTrials {
  configured: boolean;
  trials: Record<string, unknown>[];
  nextOffset: number | null;
}
export interface RepairVerificationScope {
  workflowId: string;
  revision: number;
  deployNumber: number;
  candidateCommit: string;
  itemIds: string[];
}
export interface RepairVerification {
  evidence: {
    deploymentId: string;
    deployNumber: number;
    candidateCommit: string;
    samples: Record<string, unknown>[];
    total: number;
    status: string;
    note: string;
    baseline?: Record<string, unknown>;
    metrics: Record<string, unknown>;
    recurrence: Record<string, unknown>[];
    automatic?: Record<string, unknown>[];
    excludedUnknownVersions?: number;
  };
  conclusions: Record<string, unknown>[];
}
