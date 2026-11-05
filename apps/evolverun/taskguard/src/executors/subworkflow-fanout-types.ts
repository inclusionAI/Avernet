import type { WorkflowSpec } from "../types.js";

/** First-version, items-only policy. No replica count or automatic retries. */
export type FanoutConfig = {
  schemaVersion: "fanout.v1";
  items: string | Record<string, unknown>[];
  itemIdFrom: string[];
  concurrency: number;
  successPolicy: "all_done";
  finishMode: "wait_all";
  resultPolicy: "collect_all";
  groupTimeoutSeconds: number;
  cancelGraceSeconds: number;
};

export type FanoutError = {
  category: "business" | "execution" | "contract" | "orchestration"
    | "authorization" | "persistence" | "timeout" | "cancellation";
  code: string;
  message: string;
  retryable?: boolean;
  sourceNodeId?: string;
};

/**
 * Throw for start/query failures, never return them as a confirmed child failure.
 * unknown: acceptance/side effects are not established; retain capacity and rejoin
 * the SAME childFlowId. rejected: definitively not created, fail the group.
 * Ordinary thrown errors also fail closed and propagate, leaving durable intent.
 */
export class FanoutRuntimeError extends Error {
  constructor(
    public readonly detail: FanoutError,
    public readonly disposition: "unknown" | "rejected" = "unknown",
  ) {
    super(detail.message);
    this.name = "FanoutRuntimeError";
  }
}

export type FanoutChildRequest = {
  parentFlowId: string;
  parentNodeId: string;
  groupExecutionId: string;
  itemId: string;
  itemIndex: number;
  attemptNumber: number;
  childFlowId: string;
  /** Logical identities, NOT physical paths. Runtime reserves/realizes isolation. */
  sessionKey: string;
  stateDirRef: string;
  childWorkflow: WorkflowSpec;
  params: Record<string, unknown>;
  depth: number;
};

/**
 * Only return a durable, confirmed terminal fact for this request's exact identity.
 * In particular business/execution failure certifies that the managed child exists
 * and the downstream action's acceptance/termination is known. Otherwise throw
 * FanoutRuntimeError(…, "unknown"). outputs contains explicit workflow exports;
 * never supply workflowData as an implicit substitute.
 */
export type FanoutChildCompletion = {
  status: "succeeded" | "failed" | "cancelled" | "timed_out" | "rejected";
  outputs?: Record<string, unknown>;
  error?: FanoutError;
};

export type FanoutChildStatus = FanoutChildCompletion["status"]
  | "queued" | "dispatch_pending" | "dispatch_unknown" | "running" | "cancel_requested";
export type FanoutGroupStatus = "running" | "cancelling" | "succeeded"
  | "failed" | "cancelled" | "timed_out";
export type FanoutBusinessOutcome = "empty" | "all_success" | "partial_failure"
  | "all_failed" | "incomplete";

/** One attempt per item in v1; rejoin is not a retry. */
export type FanoutChildState = Omit<FanoutChildRequest, "childWorkflow"> & {
  item: Record<string, unknown>;
  status: FanoutChildStatus;
  result: Record<string, unknown> | null;
  error: FanoutError | null;
};

export type FanoutCounts = {
  total: number;
  successCount: number;
  failedCount: number;
  cancelledCount: number;
  timedOutCount: number;
  rejectedCount: number;
  pendingCount: number;
};

export type FanoutItemResult = {
  itemIndex: number;
  itemId: string;
  item: Record<string, unknown>;
  attemptNumber: number;
  childFlowId: string;
  status: FanoutChildStatus;
  result: Record<string, unknown> | null;
  error: FanoutError | null;
};

export type FanoutResult = Record<string, unknown> & FanoutCounts & {
  schemaVersion: "fanout.v1";
  kind: "subworkflowFanoutResult";
  parentFlowId: string;
  groupNodeId: string;
  groupExecutionId: string;
  groupRevision: number;
  childWorkflowId: string;
  childPublishedVersion: number;
  status: FanoutGroupStatus;
  businessOutcome: FanoutBusinessOutcome;
  complete: boolean;
  successPolicy: "all_done";
  finishMode: "wait_all";
  resultPolicy: "collect_all";
  counts: FanoutCounts;
  items: FanoutItemResult[];
  error: FanoutError | null;
};

/** JSON-serializable value stored at FlowState.subworkflowFanouts[groupNodeId]. */
export type FanoutState = {
  schemaVersion: "fanout.v1";
  kind: "subworkflowFanoutState";
  parentFlowId: string;
  groupNodeId: string;
  groupExecutionId: string;
  /** Starts at 0; each subsequent save advances by exactly one. */
  revision: number;
  status: FanoutGroupStatus;
  createdAt: string;
  deadlineAt: string;
  sealedAt: string | null;
  cancelRequestedAt: string | null;
  /** Fixed on first cancellation; restart must NOT reset the grace period. */
  cancelDeadlineAt: string | null;
  config: FanoutConfig;
  childWorkflow: WorkflowSpec;
  /** Release identity from the parent lock; independent of optional spec.version. */
  childPublishedVersion: number;
  inputDigest: string;
  children: FanoutChildState[];
  error: FanoutError | null;
  /** Durable cleanup responsibility; nonterminal children remain here on sealing. */
  cleanupPendingChildIds: string[];
  /** Immutable once sealed. Later cleanup must not rewrite this projection. */
  result: FanoutResult | null;
};

export interface FanoutRuntime {
  /** Authoritative, durable read; not found alone is null. Read failures throw. */
  load(parentFlowId: string, nodeId: string): Promise<FanoutState | null>;
  /**
   * Atomic durable CAS (including children/cleanup/result), unique per parent/node.
   * First create: expectedRevision=null, state.revision=0. Update: expected+1.
   * Conflicts and uncertain commits MUST throw; never silently overwrite/succeed.
   * The store must atomically arbitrate a succeeded seal against parent cancellation
   * in the SAME durable boundary; a separate parentStatus read is not sufficient.
   * Parent cancellation winning that race must reject the succeeded write. Also
   * reject a succeeded seal if deadlineAt has expired at the commit linearization
   * point; the caller cannot enforce time spent queued inside a store transaction.
   */
  save(state: FanoutState, expectedRevision: number | null): Promise<void>;
  /** Authoritative lifecycle status. Failed/unknown reads must not report running. */
  parentStatus(parentFlowId: string): Promise<string>;
  /**
   * Idempotent on childFlowId; same ID with different snapshot/input MUST conflict.
   * Rejoin an existing child, even if the earlier response was lost. Enforce host
   * capacity/authorization here. New creation must atomically honor durable
   * parent/group cancellation, deadline, and per-child cancellation fences.
   * Aborting observation does NOT confirm termination.
   * During cancel_requested, rejoin only: an aborted signal must not create a child.
   */
  startOrGetChild(request: FanoutChildRequest, signal: AbortSignal): Promise<FanoutChildCompletion>;
  /**
   * Idempotently request cancellation of this identity, including unknown startup.
   * Resolve means request accepted, NOT terminal confirmation. Must fence delayed
   * start requests for the same ID. Confirm termination via startOrGetChild.
   */
  cancelChild(request: FanoutChildRequest): Promise<void>;
}
