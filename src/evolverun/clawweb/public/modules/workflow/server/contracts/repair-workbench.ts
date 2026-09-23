// Browser-safe boundary: type-only dependencies; no database, HTTP or AIS implementation.
import type { RepairBaseline, RepairBatchInput, RepairItem, RepairItemState, RepairRevision, StoredRepairItem } from './repair-batch.js';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';

export type RepairCapabilities = { generation: boolean; diff: boolean; publication: false; reason: string | null };
export type RepairInboxItem = StoredRepairItem & { sourceAvailable: boolean };
export type RepairTaskSummary = { taskId: string; revision: number; phase: RepairRevision['phase']; updatedAtMs: number; itemCount: number };
export type RepairCandidatesResponse = {
  schemaVersion: 'workflow-repair/v2'; workflowId: string; inputDigest: string;
  items: RepairInboxItem[]; tasks: RepairTaskSummary[]; capabilities: RepairCapabilities;
  limits: { maxItems: number; maxRequestBytes: number }; canEdit: boolean;
};
export type RepairTaskDetail = {
  workflowId: string; taskId: string; latestAttempt: RepairRevision;
  latestSuccessful: RepairRevision | null; revisions: RepairRevision[]; capabilities: RepairCapabilities;
  execution?: { status: 'created' | 'dispatching' | 'dispatched' | 'running' | 'dispatch_failed' | 'succeeded' | 'failed' | 'cancelled';
    errorCode: string | null; jobId: string | null; attempt: number; executionId: string | null; rawStatus: string | null };
};
export type RepairSelectionRequest = {
  workflowId: string; itemIds: string[]; inputDigest: string; instructions: string; requestId: string;
};
export type RepairFeedbackRequest = RepairSelectionRequest & {
  expectedAttemptRevision: number; parentCandidateCommit: string | null; feedback: string;
};
export type RepairDiff = {
  baseCommit: string; candidateCommit: string;
  files: Array<{ path: string; status: string; before: string | null; after: string | null; binary: boolean; truncated: boolean }>;
  truncated: boolean;
};

/** Trusted server source read. Never accept proposal, evidence, baseline or actor from the browser. */
export interface RepairSourcePort {
  load(db: IDatabase, workflowId: string): Promise<Array<{ item: RepairItem; episodeKey: string; initialState?: RepairItemState }>>;
}
export type RepairExecutionIdentity = { stepId: string; attempt: number; executionId: string };
export type RepairDispatchRequest = { actorId: string; identity: RepairExecutionIdentity; input: RepairBatchInput };
export type RepairGeneratedResult =
  | { status: 'succeeded'; draft: Record<string, unknown>; checks: Record<string, unknown> }
  | { status: 'failed'; error: Record<string, unknown> };
export type RepairJobStatus = { status: 'running' | 'success' | 'failed' | 'stopped'; rawStatus: string; errorMessage: string | null };
export type RepairExecutionReadRequest = RepairDispatchRequest & { jobId: string };
/** Host-owned AIS/Git integration. AIStudio only supplies lifecycle status. Business results are
 * read from the exact task repair branch after success; no browser callback or OSS artifact is used.
 */
export interface RepairExecutionPort {
  resolveBaseline(workflowId: string): Promise<RepairBaseline>;
  dispatch(input: RepairDispatchRequest): Promise<{ jobId: string }>;
  status(input: RepairExecutionReadRequest): Promise<RepairJobStatus>;
  readResult(input: RepairExecutionReadRequest): Promise<RepairGeneratedResult>;
  stop?(input: RepairExecutionReadRequest): Promise<void>;
  diff?(input: { workflowId: string; taskId: string; revision: number; baseCommit: string; candidateCommit: string }): Promise<RepairDiff>;
}
export interface RepairWorkbenchService {
  candidates(workflowId: string): Promise<Omit<RepairCandidatesResponse, 'canEdit'>>;
  task(taskId: string): Promise<RepairTaskDetail>;
  create(actorId: string, request: RepairSelectionRequest): Promise<RepairRevision>;
  revise(actorId: string, taskId: string, request: RepairFeedbackRequest): Promise<RepairRevision>;
  disposition(actorId: string, input: { workflowId: string; itemId: string; inputDigest: string; expectedStateVersion: number;
    contentRevision: number; action: 'no_action' | 'restore'; reason: string; requestId: string }): Promise<StoredRepairItem>;
  cancel(actorId: string, taskId: string, expectedRevision: number): Promise<void>;
  retryDispatch(actorId: string, taskId: string, expectedRevision: number): Promise<void>;
  reconcile(limit?: number): Promise<number>;
  diff(taskId: string, revision: number, base: 'baseline' | 'parent'): Promise<RepairDiff>;
}
