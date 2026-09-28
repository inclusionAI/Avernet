import { createHash } from 'node:crypto';

export const REPAIR_BATCH_SCHEMA = 'workflow-repair/v2' as const;
export const MAX_ITEMS = 100;
export const MAX_REQUEST_BYTES = 64 * 1024;
export const MAX_FROZEN_BYTES = 1024 * 1024;
export const MAX_MODEL_INPUT_BYTES = 256 * 1024;
export const MAX_RESULT_BYTES = 512 * 1024;
export const MAX_PACK_BYTES = 20 * 1024 * 1024;
export const MAX_PACK_FILES = 500;
export const MAX_SINGLE_FILE_BYTES = 2 * 1024 * 1024;
export const MAX_REPORT_BYTES = 256 * 1024;
export const MAX_INSTRUCTIONS_CHARS = 20000;

export type SourceRef =
  | { kind: 'suggestion'; suggestionId: string; proposalDigest: string | null; instructionDigest: string }
  | { kind: 'diagnosis_candidate'; signature: string; inputDigest: string; candidateId: string; analysisId: string; diagnosisId: string; flowId: string };

export type RepairItem = {
  itemId: string; groupKey: string; proposalKey: string; contentRevision: number;
  previousItemId: string | null; proposal: Record<string, unknown> | null; instruction: string; sources: SourceRef[];
  /** Current source evidence, frozen independently in each batch input. Not a proposal identity. */
  context?: Record<string, unknown>;
};
export type RepairItemState = 'pending' | 'processing' | 'awaiting_verification' | 'verified' | 'ineffective' | 'no_action';
export type RepairDisposition = { action: 'no_action' | 'restore'; actorId: string; reason: string; contentRevision: number; requestId: string; atMs: number };
export type StoredRepairItem = RepairItem & {
  workflowId: string; episodeKey: string; state: RepairItemState; stateVersion: number;
  activeTaskId: string | null; activeRevision: number | null;
  disposition: RepairDisposition | null; updatedAtMs: number;
};
export type RepairBaseline = {
  workflowId: string; packId: string; releaseRevision: number; activeDeployNumber: number | null;
  specDigest: string; repoId: string; specPath: string; packCommit: string; packDigest: string;
};
export type RepairBatchInput = {
  schemaVersion: typeof REPAIR_BATCH_SCHEMA; taskId: string; revision: number; baseline: RepairBaseline;
  items: RepairItem[]; excludedSourceRefs: SourceRef[]; instructions: string;
  parentCandidateCommit: string | null; feedback: string; previousReportRef: string | null; taskBranch: string;
};
export type RepairRevisionPhase = 'drafting' | 'review' | 'blocked' | 'publishing' | 'published' | 'no_change' | 'failed' | 'cancelled';
export type RepairRevision = {
  workflowId: string; taskId: string; revision: number; phase: RepairRevisionPhase; stateVersion: number;
  requestId: string; requestDigest: string; input: RepairBatchInput;
  draft: Record<string, unknown> | null; checks: Record<string, unknown> | null;
  candidateDigest: string | null; checksDigest: string | null; error: Record<string, unknown> | null;
  createdAtMs: number; updatedAtMs: number;
};

export type RepairBatchErrorCode = 'INVALID_INPUT' | 'PAYLOAD_TOO_LARGE' | 'IDEMPOTENCY_MISMATCH'
  | 'WORKFLOW_NOT_FOUND' | 'ITEM_NOT_FOUND' | 'ITEM_BUSY' | 'CONTENT_MISMATCH'
  | 'ACTIVE_TASK_CONFLICT' | 'STATE_CONFLICT' | 'CAPABILITY_UNAVAILABLE'
  | 'TASK_NOT_FOUND' | 'REVISION_NOT_FOUND' | 'SOURCE_CHANGED';
export class RepairBatchError extends Error {
  constructor(public readonly code: RepairBatchErrorCode, message: string,
    public readonly details: Record<string, unknown> = {}) { super(message); this.name = 'RepairBatchError'; }
}

/** Canonical JSON used only for exact identities; never semantic text deduplication. */
export function canonicalRepairJson(value: unknown): string {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return JSON.stringify(value);
  if (typeof value === 'number' && Number.isFinite(value)) return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalRepairJson).join(',')}]`;
  if (value && typeof value === 'object' && Object.getPrototypeOf(value) === Object.prototype) {
    return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonicalRepairJson((value as Record<string, unknown>)[key])}`).join(',')}}`;
  }
  throw new RepairBatchError('INVALID_INPUT', 'Expected finite JSON data');
}
export function digestRepairJson(value: unknown): string { return createHash('sha256').update(canonicalRepairJson(value)).digest('hex'); }
export function boundedRepairJson(value: unknown, limit: number): string {
  const json = canonicalRepairJson(value);
  const actualBytes = Buffer.byteLength(json, 'utf8');
  if (actualBytes > limit) throw new RepairBatchError('PAYLOAD_TOO_LARGE', 'Repair payload exceeds its byte limit', { limit, actualBytes });
  return json;
}
function valid(condition: unknown, field: string): asserts condition {
  if (!condition) throw new RepairBatchError('INVALID_INPUT', `Invalid ${field}`);
}
function object(value: unknown): value is Record<string, unknown> { return !!value && typeof value === 'object' && !Array.isArray(value); }
function text(value: unknown, max: number, empty = false): value is string { return typeof value === 'string' && value.length <= max && (empty || value.trim().length > 0); }
function integer(value: unknown, min = 0): value is number { return Number.isSafeInteger(value) && Number(value) >= min; }
function digest(value: unknown): value is string { return typeof value === 'string' && /^[a-f0-9]{64}$/.test(value); }
function gitObject(value: unknown): value is string { return typeof value === 'string' && /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(value); }
function gitIdentity(value: unknown, max: number): value is string { return text(value, max) && /^[A-Za-z0-9][A-Za-z0-9_-]*$/.test(value); }

export function validateSourceRef(value: unknown): SourceRef {
  valid(object(value), 'source');
  if (value.kind === 'suggestion') {
    valid(text(value.suggestionId, 64) && (value.proposalDigest === null || digest(value.proposalDigest)) && digest(value.instructionDigest), 'suggestion source');
  } else {
    valid(value.kind === 'diagnosis_candidate' && text(value.signature, 512) && digest(value.inputDigest)
      && ['candidateId', 'analysisId', 'diagnosisId', 'flowId'].every(key => text(value[key], 255)), 'diagnosis source');
  }
  return value as SourceRef;
}
export function validateRepairItem(value: unknown): RepairItem {
  valid(object(value), 'item');
  valid(text(value.itemId, 64) && text(value.groupKey, 128) && digest(value.proposalKey) && integer(value.contentRevision, 1), 'item identity');
  valid(value.previousItemId === null || text(value.previousItemId, 64), 'previousItemId');
  valid(value.proposal === null || object(value.proposal), 'proposal');
  valid(value.context === undefined || object(value.context), 'context');
  valid(text(value.instruction, MAX_INSTRUCTIONS_CHARS, true), 'instruction');
  valid(Array.isArray(value.sources) && value.sources.length > 0, 'item sources');
  value.sources.forEach(validateSourceRef);
  boundedRepairJson(value, MAX_FROZEN_BYTES);
  return value as RepairItem;
}
export function validateRepairBatchInput(value: unknown): RepairBatchInput {
  boundedRepairJson(value, MAX_FROZEN_BYTES);
  valid(object(value) && value.schemaVersion === REPAIR_BATCH_SCHEMA, 'schemaVersion');
  valid(gitIdentity(value.taskId, 64) && integer(value.revision, 1), 'revision identity');
  valid(Array.isArray(value.items) && value.items.length > 0 && value.items.length <= MAX_ITEMS, 'items');
  value.items.forEach(validateRepairItem);
  valid(new Set(value.items.map(item => item.itemId)).size === value.items.length, 'duplicate items');
  valid(Array.isArray(value.excludedSourceRefs), 'excludedSourceRefs');
  value.excludedSourceRefs.forEach(validateSourceRef);
  valid(text(value.instructions, MAX_INSTRUCTIONS_CHARS, true) && text(value.feedback, MAX_INSTRUCTIONS_CHARS, true), 'instructions/feedback');
  valid(value.parentCandidateCommit === null || gitObject(value.parentCandidateCommit), 'parentCandidateCommit');
  valid(value.previousReportRef === null || text(value.previousReportRef, 2048), 'previousReportRef');
  const baseline = value.baseline;
  valid(object(baseline), 'baseline');
  // The shared MySQL renderer maps VARCHAR(255) to VARCHAR(190).
  valid(gitIdentity(baseline.workflowId, 190) && text(baseline.packId, 255) && text(baseline.repoId, 255), 'baseline identity');
  valid(integer(baseline.releaseRevision) && (baseline.activeDeployNumber === null || integer(baseline.activeDeployNumber, 1)), 'baseline revision');
  valid(digest(baseline.specDigest) && gitObject(baseline.packCommit) && gitObject(baseline.packDigest), 'baseline digests');
  valid(text(baseline.specPath, 1024) && !baseline.specPath.startsWith('/') && !baseline.specPath.includes('\\')
    && !baseline.specPath.split('/').some(part => part === '..' || part === '.' || part === ''), 'specPath');
  valid(value.taskBranch === `repair/${baseline.workflowId}/${value.taskId}`, 'taskBranch');
  return value as RepairBatchInput;
}
