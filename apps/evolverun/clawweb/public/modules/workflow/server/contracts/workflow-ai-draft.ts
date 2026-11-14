/** Browser Service API and host Plugin API for isolated YAML suggestions.
 * No save/deploy authority crosses this boundary. */
export type WorkflowAiDraftStatus = 'pending' | 'running' | 'dispatch_uncertain' | 'completed' | 'failed' | 'cancelled';
export type WorkflowAiDraftCapabilities = { available: boolean; reason?: string };
export type WorkflowAiDraftRequest = { requestId: string; mode: 'create' | 'modify'; prompt: string; currentYaml: string };
/** Server-resolved AutoHeal binding. Never supplied or overridden by a browser. */
export type WorkflowAiDraftTarget = { botId: string };
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
export type WorkflowAiDraftPollResult = { status: 'running' } | { status: 'failed'; error: string } | { status: 'completed'; result: WorkflowAiDraftResult };
/** Host-owned AutoHeal/BaaS provider. Dispatch once; unknown outcomes must never
 * be blindly resubmitted. Poll uses the acknowledged BaaS message ID. Cancel is
 * local: BaaS has no supported stop contract and late replies are ignored. */
export interface WorkflowAiDraftExecutionPort {
  resolveTarget(): Promise<WorkflowAiDraftTarget>;
  dispatch(request: WorkflowAiDraftExecutionRequest): Promise<{ jobId: string }>;
  poll(request: WorkflowAiDraftExecutionRequest & { jobId: string }): Promise<WorkflowAiDraftPollResult>;
}
export interface WorkflowAiDraftService {
  capabilities(actorId: string, workflowId: string): Promise<WorkflowAiDraftCapabilities>;
  create(actorId: string, workflowId: string, request: WorkflowAiDraftRequest): Promise<{ draftId: string; status: WorkflowAiDraftStatus }>;
  get(actorId: string, workflowId: string, draftId: string): Promise<WorkflowAiDraftDetail>;
  cancel(actorId: string, workflowId: string, draftId: string): Promise<WorkflowAiDraftDetail>;
  reconcile(limit?: number): Promise<number>;
}
export class WorkflowAiDraftError extends Error {
  constructor(public readonly code: 'INVALID_INPUT' | 'PAYLOAD_TOO_LARGE' | 'FORBIDDEN' | 'NOT_FOUND' | 'STATE_CONFLICT' | 'IDEMPOTENCY_CONFLICT' | 'CAPABILITY_UNAVAILABLE', message: string = code) { super(message); }
}
/** Provider positively rejected submission; safe to report as terminal. */
export class WorkflowAiDraftDispatchRejectedError extends Error {
  constructor(public readonly code: 'AUTO_HEAL_BOT_UNAVAILABLE' | 'BAAS_AUTH_FAILED' | 'BAAS_REQUEST_REJECTED') { super(code); }
}
export class WorkflowAiDraftInvalidResultError extends Error {}
export const WORKFLOW_AI_DRAFT_MAX_YAML_BYTES = 100_000;
export const WORKFLOW_AI_DRAFT_MAX_PROMPT_CHARS = 4_000;
