import type { RunViewScope, RunViewPermissions } from '@avernet/clawweb-shared/server/services/run-view-permissions';

/** Module-local view contract; the host injects its existing owner-preserving resolver. */
export interface RunSubviewPermissions extends RunViewPermissions {
  resolveRunViewScope(workflowId: string, actorId: string): Promise<RunViewScope>;
}
export type RunReader = { actorId: string | null; isAdmin: boolean };
export type RunIdentity = { flow_id: string; workflow_id: string; origin_bot_id: string | null };
export type RunListMetadata = RunIdentity & {
  status: string; workflow_title: string | null; started_at: number; completed_at: number | null;
  total_duration_ms: number | null; [key: string]: unknown;
};
export type StoredRunSnapshot = RunListMetadata & { state_json: string | null; origin_session_key: string | null; origin_session_id: string | null;
  ordinary_run_marker?: string | null };
/** Internal proof; markers are only used to pin authorization/cursors and are never returned as API data. */
export type AssociatedRunIdentity = RunIdentity & { request_key_hash: string; parent_run_marker: string; child_run_marker: string };
export type AssociationCandidate = { requestKeyHash: string; nativeFlowId: string };
/** Trusted-engine metadata correlation only; no provenance or incarnation guarantee. */
export type FanoutOriginCandidate = {
  nativeFlowId: string; parentFlowId: string; parentNodeId: string; groupExecutionId: string;
  itemId: string; itemIndex: number; attemptNumber: number; childFlowId: string; workflowId: string;
  requestDigest?: unknown;
};
export interface RunSubworkflowsStore {
  listRuns(query: AuthorizedRunListQuery, scopes: Map<string, RunViewScope> | null): Promise<AuthorizedRunList>;
  readRun(flowId: string): Promise<StoredRunSnapshot | null>;
  readIdentities(flowIds: string[]): Promise<RunIdentity[]>;
  readFanoutOriginIdentities(candidates: FanoutOriginCandidate[]): Promise<RunIdentity[]>;
  readAssociatedIdentities(parent: StoredRunSnapshot, childWorkflowId: string, candidates: AssociationCandidate[]): Promise<AssociatedRunIdentity[]>;
}
export type AuthorizedRunListQuery = {
  workflowId?: string; botId?: string; status?: string; statuses?: string[]; from?: number; to?: number;
  inputQuery?: string; query?: string; limit: number; offset: number;
};
export type AuthorizedRunList = { runs: RunListMetadata[]; total: number; limit: number; offset: number; statusCounts?: Record<string, number> };
export type AuthorizedWorkflowStats = { workflow_id: string; workflow_title: string | null; run_count: number; last_status: string | null; last_run_at: number | null };
export type FanoutReadErrorCode = 'unauthenticated' | 'forbidden' | 'not_found' | 'invalid_query'
  | 'identity_conflict' | 'snapshot_changed' | 'invalid_state' | 'unavailable';
export class FanoutReadError extends Error {
  constructor(public readonly code: FanoutReadErrorCode, message: string, options?: ErrorOptions) { super(message, options); }
}
export type FanoutErrorSummary = { category: string; code: string; message: string; retryable: boolean; sourceNodeId?: string };
export type FanoutChildView = {
  displayIndex: number; itemIndex?: number; itemId: string; attemptNumber: number;
  childFlowId: string; status: string; error: FanoutErrorSummary | null;
};
export type FanoutCounts = { successCount: number; failedCount: number; pendingCount: number;
  cancelledCount: number; timedOutCount: number; rejectedCount: number };
export type FanoutPage = {
  schemaVersion: 'fanout.v1'; parentFlowId: string; groupNodeId: string; groupExecutionId: string;
  revision: number; snapshotId: string; status: string; childWorkflowId: string; successPolicy: 'all_done';
  concurrency: number; activeCount: number; countScope: 'full' | 'visible'; groupTotal: number | null;
  visibleTotal: number; returnedCount: number; counts: FanoutCounts; nextCursor: string | null; children: FanoutChildView[];
};
export type FanoutQuery = { groupExecutionId: string; limit?: number; cursor?: string };
export type RunReadInspection = {
  run: StoredRunSnapshot; managed: boolean; restricted: boolean; sensitiveChild?: boolean; sessionConfigured?: boolean;
  groups: { nodeId: string; groupExecutionId: string }[];
};
/** Read-only Service API. No engine lifecycle/command dependency is permitted. */
export interface RunSubworkflowsReader {
  /** Internal classifier for the temporary public write guard, independent of caller identity. */
  isFanoutOriginChild(flowId: string): Promise<boolean | null>;
  listRuns(actor: RunReader, query: AuthorizedRunListQuery): Promise<AuthorizedRunList>;
  workflowStats(actor: RunReader): Promise<{ stats: AuthorizedWorkflowStats[]; visibleWorkflowIds: string[] | null }>;
  readGroup(actor: RunReader, flowId: string, nodeId: string, query: FanoutQuery): Promise<FanoutPage>;
  inspectRun(actor: RunReader, flowId: string): Promise<RunReadInspection>;
}
