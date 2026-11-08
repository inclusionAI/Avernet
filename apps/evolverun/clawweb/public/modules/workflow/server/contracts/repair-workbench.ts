import type { WorkflowSnapshotBinding } from '@avernet/clawweb-shared/server/contracts/workflow-snapshot';
import type { RepairVersionScope } from './repair-version-scope.js';
// Browser-safe boundary: type-only dependencies; no database, HTTP or AIS implementation.
import type { RepairBaseline, RepairBatchInput, RepairItem, RepairItemState, RepairRevision, StoredRepairItem } from './repair-batch.js';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import type { AutomationOrigin } from './workflow-automation.js';
import type { RepairReleaseDetail } from './repair-release.js';

export type RepairCapabilities = { generation: boolean; diff: boolean; publication: boolean; reason: string | null };
export type RepairInboxItem = StoredRepairItem & { sourceAvailable: boolean;
  /** Trusted identity of original executable content; independent of supporting runs and list pagination. */
  selectionDigest?: string;
  /** Bounded display projection. Fetch item detail for original proposal/evidence; never freeze this projection. */
  readSummary?: { sourceCount: number; runCount: number; diagnosisCount: number; originalBytes: number;
    operations: Array<{nodeId: string; path: string; identity: string}>; conflictsComplete: boolean };
  aggregation?: import('./repair-proposal.js').ProposalAggregation };
export function repairSignatureKey(signature: string): string {
  const bytes = new TextEncoder().encode(signature);
  const hash = (seed: bigint) => {
    let value = seed;
    for (const byte of bytes) value = BigInt.asUintN(64, (value ^ BigInt(byte)) * 0x100000001b3n);
    return value.toString(16).padStart(16, '0');
  };
  return hash(0xcbf29ce484222325n) + hash(0x84222325cbf29ce4n);
}
export type RepairInboxFilter = 'pending' | 'processing' | 'awaiting_verification' | 'closed' | 'no_action' | 'all';
export type RepairCandidatesQuery = { state?: RepairInboxFilter; page?: number; pageSize?: number; includeHistorical?: boolean; includeTasks?: boolean;
  nodeId?: string; failureMode?: string; signature?: string; previewSignatures?: string[]; rawItems?: boolean; metadataPage?: number; versionScope?: RepairVersionScope; snapshotKey?: string };
export type RepairTaskSummary = { taskId: string; revision: number; phase: RepairRevision['phase']; updatedAtMs: number; itemCount: number; title?: string; source?: 'automatic' | 'manual' };
export type RepairCandidatesResponse = {
  schemaVersion: 'workflow-repair/v2'; workflowId: string; inputDigest: string;
  activeSnapshot?: WorkflowSnapshotBinding | null; versionScope?: RepairVersionScope;
  items: RepairInboxItem[]; tasks: RepairTaskSummary[]; capabilities: RepairCapabilities;
  metadata?: { page: number; totalPages: number; digest: string; tasks: number; signatureKeys: number; nodes: number; failureModes: number };
  /** Up to 3 bounded summaries per issue within the shared response byte budget. Total is never truncated. */
  issuePreviews?: Array<{ signature: string; total: number; items: RepairInboxItem[] }>;
  includeHistorical: boolean; activeLookbackDays: number;
  repairSignatureKeys: string[];
  /** Visible scope before filters/item pagination, paged by metadata.page. Browser API assembles all metadata pages. */
  facets: { nodes: string[]; failureModes: string[] };
  counts: Record<RepairInboxFilter, number>;
  page: { page: number; pageSize: number; total: number; totalPages: number; snapshotDigest?: string };
  limits: { maxItems: number; maxRequestBytes: number }; canEdit: boolean;
};
export type RepairTaskExecution = { status: 'created' | 'dispatching' | 'dispatch_uncertain' | 'dispatched' | 'running' | 'dispatch_failed' | 'succeeded' | 'failed' | 'cancelled';
  errorCode: string | null; jobId: string | null; attempt: number; executionId: string | null; rawStatus: string | null };
export type RepairTaskDetail = {
  workflowId: string; taskId: string; title?: string; source?: 'automatic' | 'manual'; latestAttempt: RepairRevision;
  latestSuccessful: RepairRevision | null; revisions: RepairRevision[]; capabilities: RepairCapabilities;
  release?: RepairReleaseDetail;
  revisionPage?: { page: number; pageSize: number; total: number; totalPages: number };
  scopeItemCount?: number;
  automaticProgress?: { completedBatches: number; totalBatches: number; changedItems?: number; publishable?: boolean };
  /** Latest attempt only, retained for existing clients and task-level recovery actions. */
  execution?: RepairTaskExecution;
  /** Read-only metadata keyed by generation revision; not part of frozen task input. */
  executions?: Record<number, RepairTaskExecution>;
};
export type RepairSelectionRequest = {
  automation?: AutomationOrigin;
  targetSnapshotKey?: string; historicalItemIds?: string[];
  workflowId: string; itemIds: string[]; inputDigest: string; instructions: string; requestId: string; includeHistorical?: boolean;
  selection?: Record<string, string>;
};
export type RepairSelectionState = { activeSnapshot?: WorkflowSnapshotBinding | null; inputDigest: string; items: RepairInboxItem[]; missing: string[]; canEdit: boolean;
  plans?: Array<Omit<import('./repair-proposal.js').ProposalAggregation, 'originals' | 'flowIds'>> };
export type RepairFeedbackRequest = RepairSelectionRequest & {
  expectedAttemptRevision: number; parentCandidateCommit: string | null; feedback: string;
};
export type RepairDiff = {
  baseCommit: string; candidateCommit: string;
  files: Array<{ path: string; status: string; before: string | null; after: string | null; binary: boolean; truncated: boolean; patch?: string }>;
  truncated: boolean;
};

/** Trusted server source read. Never accept proposal, evidence, baseline or actor from the browser. */
export interface RepairSourcePort {
  activeSnapshot?(db: IDatabase, workflowId: string): Promise<WorkflowSnapshotBinding | null>;
  load(db: IDatabase, workflowId: string, mode?: 'summary' | 'full', scope?: { itemIds?: readonly string[]; includeHistorical?: boolean }): Promise<Array<{ item: RepairItem; episodeKey: string; initialState?: RepairItemState }>>;
  /** Hydrate only trusted server-side summaries, without rebuilding unrelated sources. */
  hydrate?(db: IDatabase, workflowId: string, items: RepairItem[]): Promise<RepairItem[]>;
  /** Cheap revision of all source and disposition tables. Only GET snapshots may reuse it. */
  readVersion?(db: IDatabase, workflowId: string): Promise<string>;
}
export type RepairExecutionIdentity = { stepId: string; attempt: number; executionId: string };
export type RepairMaterialAccess = {schemaVersion:'repair-material-download/v1';token:string;files:Array<{itemId:string;bytes:number;sha256:string}>};
export type RepairMaterialChunk = {schemaVersion:'repair-material-chunk/v1';itemId:string;offset:number;nextOffset:number|null;totalBytes:number;sha256:string;base64:string};
export type RepairDispatchRequest = { actorId: string; identity: RepairExecutionIdentity; input: RepairBatchInput; materialAccess?:RepairMaterialAccess };
export type RepairGeneratedResult =
  | { status: 'succeeded' | 'blocked' | 'no_change'; draft: Record<string, unknown>; checks: Record<string, unknown> }
  | { status: 'failed'; error: Record<string, unknown> };
export type RepairJobStatus = { status: 'running' | 'success' | 'failed' | 'stopped'; rawStatus: string; errorMessage: string | null };
export type RepairExecutionReadRequest = RepairDispatchRequest & { jobId: string };
/** Host-owned AIS/Git integration. AIStudio only supplies lifecycle status. Business results are
 * read from the exact task repair branch after success; no browser callback or OSS artifact is used.
 */
export interface RepairExecutionPort {
  materialDeliveryVersion?: 'repair-material-download/v1';
  integrationVersion?: 'workflow-proposal-plan/v1';
  resolveBaseline(workflowId: string, purpose?: 'draft'): Promise<RepairBaseline>;
  dispatch(input: RepairDispatchRequest): Promise<{ jobId: string }>;
  status(input: RepairExecutionReadRequest): Promise<RepairJobStatus>;
  readResult(input: RepairExecutionReadRequest): Promise<RepairGeneratedResult>;
  stop?(input: RepairExecutionReadRequest): Promise<void>;
  diff?(input: { workflowId: string; taskId: string; revision: number; baseCommit: string; candidateCommit: string }): Promise<RepairDiff>;
}
export interface RepairWorkbenchService {
  selectionState(workflowId: string, itemIds: string[], includeHistorical: boolean): Promise<Omit<RepairSelectionState, 'canEdit'>>;
  candidates(workflowId: string, query?: RepairCandidatesQuery): Promise<Omit<RepairCandidatesResponse, 'canEdit'>>;
  item(workflowId: string, itemId: string, originalOnly?: boolean): Promise<RepairInboxItem>;
  task(taskId: string): Promise<RepairTaskDetail>;
  materialChunk?(taskId:string,revision:number,itemId:string,token:string,offset:number):Promise<RepairMaterialChunk|null>;
  material?(taskId:string,revision:number,itemId:string):Promise<RepairItem>;
  items(workflowId: string, itemIds: string[]): Promise<RepairInboxItem[]>;
  request(workflowId: string, requestId: string): Promise<RepairRevision | null>;
  create(actorId: string, request: RepairSelectionRequest): Promise<RepairRevision>;
  revise(actorId: string, taskId: string, request: RepairFeedbackRequest): Promise<RepairRevision>;
  disposition(actorId: string, input: { workflowId: string; itemId: string; inputDigest: string; expectedStateVersion: number;
    contentRevision: number; untilMs?: number; action: 'no_action' | 'restore'; reason: string; requestId: string; includeHistorical?: boolean }): Promise<StoredRepairItem>;
  cancel(actorId: string, taskId: string, expectedRevision: number): Promise<void>;
  retryResult(actorId: string, taskId: string, expectedRevision: number): Promise<void>;
  retryGeneration(actorId: string, taskId: string, request: { expectedRevision: number; requestId: string }): Promise<RepairRevision>;
  retryDispatch(actorId: string, taskId: string, expectedRevision: number): Promise<void>;
  retryProposalIntegration?(workflowId: string): Promise<void>;
  reconcile(limit?: number): Promise<number>;
  diff(taskId: string, revision: number, base: 'baseline' | 'parent'): Promise<RepairDiff>;
}
/** Trusted adapter has positively confirmed there is no result for this execution. */
export class RepairResultAbsentError extends Error {}
