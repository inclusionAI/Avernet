/** Browser Service API and host Plugin API for isolated YAML suggestions.
 * No save/deploy authority or database-backed task state crosses this boundary. */
export type WorkflowAiDraftStatus = 'pending' | 'running' | 'dispatch_uncertain' | 'completed' | 'failed' | 'cancelled';
export type WorkflowAiDraftCapabilities = { available: boolean; reason?: string };
export type WorkflowAiDraftRequest = {
  requestId: string; mode: 'create' | 'modify'; prompt: string; currentYaml: string;
  /** Absent on prepare; required and verified before create can dispatch. */
  receipt?: string;
};
export type WorkflowAiDraftPrepared = { draftId: string; receipt: string; inputSpecDigest: string };
export type WorkflowAiDraftContext = { receipt: string; currentYaml: string };
export type WorkflowAiDraftCancelContext = { receipt: string };
/** Server-resolved AutoHeal binding. Never supplied or overridden by a browser. */
export type WorkflowAiDraftTarget = { botId: string };
/** Signed metadata only: no prompt, YAML, transport credentials or signing key. Times are epoch milliseconds. */
export type WorkflowAiDraftReceiptClaims = {
  schemaVersion: 'workflow-ai-draft-receipt/v1'; draftId: string; actorId: string; workflowId: string;
  requestId: string; requestDigest: string; inputSpecDigest: string; target: WorkflowAiDraftTarget;
  issuedAt: number; expiresAt: number;
};
export type WorkflowAiDraftReceiptInputClaims = Omit<WorkflowAiDraftReceiptClaims, 'draftId' | 'schemaVersion'>;
/** Host-injected codec. Verify authenticates metadata/schema, not ACL or wall-clock validity. */
export interface WorkflowAiDraftReceipts {
  issue(inputClaims: WorkflowAiDraftReceiptInputClaims): WorkflowAiDraftPrepared;
  verify(token: string): WorkflowAiDraftReceiptClaims;
}
export type WorkflowAiDraftInput = {
  schemaVersion: 'workflow-yaml-draft-input/v1'; workflowId: string; mode: 'create' | 'modify'; prompt: string;
  workingCopy: { yaml: string; sha256: string };
};
export type WorkflowAiDraftEnvelope = {
  schemaVersion: 'workflow-yaml-draft/v1'; taskType: 'workflow_yaml_draft'; taskId: string;
  target: WorkflowAiDraftTarget; input: WorkflowAiDraftInput;
};
export type WorkflowAiDraftDetail = {
  draftId: string; workflowId: string; status: WorkflowAiDraftStatus; error?: string;
  result?: { yaml: string; summary: string }; inputSpecDigest: string;
};
export type WorkflowAiDraftResult = { yaml: string; summary: string };
export type WorkflowAiDraftExecutionRequest = { actorId: string; envelope: WorkflowAiDraftEnvelope };
/** Poll cannot dispatch: it has no generation input or invented prompt/mode.
 * The frozen task/target plus native jobId are sufficient for a read-only provider lookup. */
export type WorkflowAiDraftPollRequest = {
  actorId: string; envelope: Omit<WorkflowAiDraftEnvelope, 'input'>; jobId: string;
};
export type WorkflowAiDraftPollResult = { status: 'running'; error?: string } | { status: 'failed'; error: string } | { status: 'completed'; result: WorkflowAiDraftResult };
/** Host-owned AutoHeal/BaaS provider. Each explicit create dispatches once using
 * envelope.taskId as native message_id; native provider deduplication owns retries.
 * A lost acknowledgement can still be polled at that expected ID. A missing job
 * (404) is running, not permission to dispatch. Recovery only polls.
 * No remote-stop or durable cancellation/revocation guarantee is provided. */
export interface WorkflowAiDraftExecutionPort {
  resolveTarget(): Promise<WorkflowAiDraftTarget>;
  dispatch(request: WorkflowAiDraftExecutionRequest): Promise<{ jobId: string }>;
  poll(request: WorkflowAiDraftPollRequest): Promise<WorkflowAiDraftPollResult>;
}
export interface WorkflowAiDraftService {
  capabilities(actorId: string, workflowId: string): Promise<WorkflowAiDraftCapabilities>;
  prepare(actorId: string, workflowId: string, request: WorkflowAiDraftRequest): Promise<WorkflowAiDraftPrepared>;
  create(actorId: string, workflowId: string, request: WorkflowAiDraftRequest): Promise<{ draftId: string; status: WorkflowAiDraftStatus; error?: string }>;
  get(actorId: string, workflowId: string, draftId: string, context: WorkflowAiDraftContext): Promise<WorkflowAiDraftDetail>;
  /** Acknowledges cancellation for this call only. Later GET may return a result;
   * the browser must suppress late Apply for its locally cancelled attempt. */
  cancel(actorId: string, workflowId: string, draftId: string, context: WorkflowAiDraftCancelContext): Promise<WorkflowAiDraftDetail>;
  /** Compatibility no-op: no background scans, timers, polling or dispatch. */
  reconcile(limit?: number): Promise<number>;
}
export class WorkflowAiDraftError extends Error {
  constructor(public readonly code: 'INVALID_INPUT' | 'PAYLOAD_TOO_LARGE' | 'FORBIDDEN' | 'NOT_FOUND' | 'STATE_CONFLICT' | 'IDEMPOTENCY_CONFLICT' | 'CAPABILITY_UNAVAILABLE', message: string = code) { super(message); }
}
/** Provider positively rejected submission; safe to report for this call. */
export class WorkflowAiDraftDispatchRejectedError extends Error {
  constructor(public readonly code: 'AUTO_HEAL_BOT_UNAVAILABLE' | 'BAAS_AUTH_FAILED' | 'BAAS_REQUEST_REJECTED') { super(code); }
}
/** Reading a message failed, not a terminal provider execution or invalid YAML. */
export class WorkflowAiDraftPollUnavailableError extends Error {
  constructor(public readonly code: 'BAAS_AUTH_FAILED' | 'BAAS_POLL_UNAVAILABLE') { super(code); }
}
export class WorkflowAiDraftInvalidResultError extends Error {}
export const WORKFLOW_AI_DRAFT_MAX_YAML_BYTES = 100_000;
export const WORKFLOW_AI_DRAFT_MAX_PROMPT_CHARS = 4_000;
export const WORKFLOW_AI_DRAFT_RECEIPT_TTL_MS = 30 * 60_000;
