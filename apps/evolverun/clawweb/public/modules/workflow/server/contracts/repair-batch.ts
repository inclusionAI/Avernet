import { workflowSnapshotKey, type WorkflowSnapshotRef } from '@avernet/clawweb-shared/server/contracts/workflow-snapshot';
import { createHash } from 'node:crypto';
import type { AutomationOrigin } from './workflow-automation.js';

export const REPAIR_BATCH_SCHEMA = 'workflow-repair/v2' as const;
export const MAX_ITEMS = 100;
export const MAX_REQUEST_BYTES = 64 * 1024;
export const MAX_FROZEN_BYTES = 1024 * 1024;
// Separate bounded storage/transport budget; core inputs keep their existing 1 MiB limit.
export const MAX_MATERIAL_BYTES = 16 * 1024 * 1024 - 4096;
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
export type RepairDisposition = { action: 'no_action' | 'restore'; actorId: string; reason: string; contentRevision: number; requestId: string; atMs: number; untilMs?: number; evidenceFlowIds?: string[]; evidenceEventIds?: string[]; evidenceSeverity?: number };
export type StoredRepairItem = RepairItem & {
  workflowId: string; episodeKey: string; state: RepairItemState; stateVersion: number;
  activeTaskId: string | null; activeRevision: number | null;
  disposition: RepairDisposition | null; updatedAtMs: number;
};
export type RepairBaseline = {
  workflowId: string; packId: string; releaseRevision: number; activeDeployNumber: number | null;
  specDigest: string; repoId: string; specPath: string; packCommit: string; packDigest: string;
  /** Canonical deployed JSON context; manual drafts need not match it to Git. specDigest remains raw Git bytes. */
  proposalSpecDigest?: string;
};
export type RepairBatchInput = {
  schemaVersion: typeof REPAIR_BATCH_SCHEMA; taskId: string; revision: number; baseline: RepairBaseline;
  items: RepairItem[]; excludedSources: { count: number; digest: string }; instructions: string;
  materials?: {schemaVersion:'repair-frozen-materials/v1';recordId:string;sha256:string;bytes:number};
  automation?: AutomationOrigin;
  integration?: { schemaVersion: 'workflow-proposal-plan/v1'; baselineSpecDigest: string; plans: Array<{ id: string; originalItemIds: string[]; goals: string[]; relation: string; causes?: import('./repair-proposal.js').ProposalCause[]; unknowns?: string[] }> };
  parentCandidateCommit: string | null; feedback: string; previousReportRef: string | null; taskBranch: string;
};
export type RepairRevisionPhase = 'drafting' | 'review' | 'blocked' | 'publishing' | 'published' | 'no_change' | 'failed' | 'cancelled';
/** Optional AIS result record. Core input is the exact application prompt; materials are an index, not file bodies. */
export type RepairModelInput = { itemId: string; coreInput: string; materials: Record<string, unknown> };
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

/** Validate optional AIS display records against this frozen task before persisting the result. */
export function validateRepairModelInputs(value: unknown, input: RepairBatchInput): void {
  if (value === undefined) return;
  valid(Array.isArray(value), 'modelInputs');
  const itemIds = new Set(input.items.map(item => item.itemId));
  const seen = new Set<string>();
  for (const entry of value) {
    valid(object(entry) && text(entry.itemId, 64) && itemIds.has(entry.itemId) && !seen.has(entry.itemId)
      && text(entry.coreInput, MAX_RESULT_BYTES), 'modelInputs record');
    seen.add(entry.itemId);
    const materials = entry.materials;
    valid(object(materials) && materials.schemaVersion === 'repair-local-materials/v1'
      && materials.taskId === input.taskId && materials.revision === input.revision
      && text(materials.root, 4096) && Array.isArray(materials.files), 'modelInputs materials');
    const paths = new Set<string>();
    for (const file of materials.files) {
      valid(object(file) && text(file.kind, 64) && text(file.path, 4096) && !paths.has(file.path)
        && integer(file.bytes) && digest(file.sha256) && text(file.purpose, 4000)
        && Array.isArray(file.itemIds), 'modelInputs material file');
      valid(file.itemIds.every(id => text(id, 64) && itemIds.has(id))
        && new Set(file.itemIds).size === file.itemIds.length, 'modelInputs material sources');
      paths.add(file.path);
    }
  }
}

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
/** Structural validation for trusted reads; frozen task bytes are checked separately. */
export function validateRepairItemStructure(value: unknown): RepairItem {
  valid(object(value), 'item');
  valid(text(value.itemId, 64) && text(value.groupKey, 128) && digest(value.proposalKey) && integer(value.contentRevision, 1), 'item identity');
  valid(value.previousItemId === null || text(value.previousItemId, 64), 'previousItemId');
  valid(value.proposal === null || object(value.proposal), 'proposal');
  valid(value.context === undefined || object(value.context), 'context');
  valid(typeof value.instruction === 'string', 'instruction');
  valid(Array.isArray(value.sources) && value.sources.length > 0, 'item sources');
  value.sources.forEach(validateSourceRef);
  return value as RepairItem;
}
export function validateRepairItem(value: unknown): RepairItem {
  const item = validateRepairItemStructure(value);
  valid(text(item.instruction, MAX_INSTRUCTIONS_CHARS, true), 'instruction');
  try { boundedRepairJson(item, MAX_FROZEN_BYTES); } catch (error) {
    if (error instanceof RepairBatchError) throw new RepairBatchError(error.code, error.message, { ...error.details, stage: 'frozen_item', reasonCode: 'REPAIR_FROZEN_ITEM_TOO_LARGE' });
    throw error;
  }
  return item;
}
/** Complete source bodies use the separate material budget; inline/core items keep their original cap. */
export function validateRepairMaterialItem(value:unknown):RepairItem{
 const item=validateRepairItemStructure(value);valid(text(item.instruction,MAX_INSTRUCTIONS_CHARS,true),'instruction');
 try{boundedRepairJson(item,MAX_MATERIAL_BYTES);}catch(error){if(error instanceof RepairBatchError)throw new RepairBatchError(error.code,error.message,{...error.details,stage:'source_material',reasonCode:'REPAIR_MATERIALS_TOO_LARGE'});throw error;}
 return item;
}
export function validateAutomationOrigin(value: unknown): AutomationOrigin {
  valid(object(value) && value.kind === 'daily' && digest(value.intentId)
    && integer(value.policyVersion, 1) && integer(value.evidenceCutoffMs, 1), 'automation origin');
  if (value.roundId !== undefined || value.batchIndex !== undefined || value.batchCount !== undefined) {
    valid(digest(value.roundId) && integer(value.batchCount, 1) && integer(value.batchIndex)
      && Number(value.batchIndex) < Number(value.batchCount), 'automatic round batch');
  }
  if (value.totalItems !== undefined) valid(integer(value.totalItems, 1) && value.roundId !== undefined, 'automatic scope size');
  return value as AutomationOrigin;
}
export function validateRepairBatchInput(value: unknown): RepairBatchInput {
  try { boundedRepairJson(value, MAX_FROZEN_BYTES); } catch (error) {
    if (error instanceof RepairBatchError) throw new RepairBatchError(error.code, error.message, { ...error.details, stage: 'frozen_batch', reasonCode: 'REPAIR_FROZEN_BATCH_TOO_LARGE' });
    throw error;
  }
  valid(object(value) && value.schemaVersion === REPAIR_BATCH_SCHEMA, 'schemaVersion');
  valid(gitIdentity(value.taskId, 64) && integer(value.revision, 1), 'revision identity');
  valid(Array.isArray(value.items) && value.items.length > 0 && value.items.length <= MAX_ITEMS, 'items');
  value.items.forEach(validateRepairItem);
  if(value.materials!==undefined){const ref=value.materials;valid(object(ref)&&ref.schemaVersion==='repair-frozen-materials/v1'&&digest(ref.recordId)&&digest(ref.sha256)&&integer(ref.bytes,1)&&Number(ref.bytes)<=MAX_MATERIAL_BYTES,'frozen materials');}
  if (value.automation !== undefined) validateAutomationOrigin(value.automation);
  if (value.integration !== undefined) {
    const integration = value.integration;
    valid(object(integration) && integration.schemaVersion === 'workflow-proposal-plan/v1' && integration.baselineSpecDigest === (value.baseline as Record<string, unknown>)?.specDigest
      && Array.isArray(integration.plans) && integration.plans.length > 0 && integration.plans.length <= MAX_ITEMS, 'proposal integration');
    const ids: string[] = [];
    for (const plan of integration.plans) {
      valid(object(plan) && text(plan.id,64) && Array.isArray(plan.originalItemIds) && plan.originalItemIds.length > 0
        && plan.originalItemIds.every((id:unknown) => text(id,64)) && Array.isArray(plan.goals) && plan.goals.every((goal:unknown)=>text(goal,400))
        && ['exact','equivalent','complementary','uncertain'].includes(String(plan.relation)), 'integration plan');
      ids.push(...plan.originalItemIds as string[]);
    }
    valid(new Set(ids).size === ids.length && canonicalRepairJson([...ids].sort()) === canonicalRepairJson(value.items.map((item:any)=>item.itemId).sort()), 'integration coverage');
  }

  valid(new Set(value.items.map(item => item.itemId)).size === value.items.length, 'duplicate items');
  valid(object(value.excludedSources) && integer(value.excludedSources.count) && digest(value.excludedSources.digest), 'excludedSources');
  valid(text(value.instructions, MAX_INSTRUCTIONS_CHARS, true) && text(value.feedback, MAX_INSTRUCTIONS_CHARS, true), 'instructions/feedback');
  valid(value.parentCandidateCommit === null || gitObject(value.parentCandidateCommit), 'parentCandidateCommit');
  valid(value.previousReportRef === null || text(value.previousReportRef, 2048), 'previousReportRef');
  const baseline = value.baseline;
  valid(object(baseline), 'baseline');
  // The shared MySQL renderer maps VARCHAR(255) to VARCHAR(190).
  valid(gitIdentity(baseline.workflowId, 190) && text(baseline.packId, 255) && text(baseline.repoId, 255), 'baseline identity');
  valid(integer(baseline.releaseRevision) && (baseline.activeDeployNumber === null || integer(baseline.activeDeployNumber, 1)), 'baseline revision');
  for (const item of value.items) {
    if (item.context?.referenceOnly !== true) continue;
    const binding = item.context.sourceSnapshot, target = item.context.targetSnapshot;
    valid(value.integration && object(binding) && binding.status === 'resolved' && object(binding.snapshot) && object(target), 'historical reference');
    const source = binding.snapshot;
    valid(source.kind === 'deployment' && integer(source.workflowVersion, 1) && integer(source.deployNumber, 1)
      || source.kind === 'saved' && gitObject(source.commit) && text(source.sourceFlowId, 255), 'historical source identity');
    valid(source.workflowId === baseline.workflowId && target.workflowId === baseline.workflowId
      && target.kind === 'deployment' && integer(target.workflowVersion, 1) && target.deployNumber === baseline.activeDeployNumber
      && digest(target.configDigest) && digest(source.configDigest)
      && workflowSnapshotKey(source as WorkflowSnapshotRef) !== workflowSnapshotKey(target as WorkflowSnapshotRef), 'historical target/source identity');
  }

  valid(digest(baseline.specDigest) && gitObject(baseline.packCommit) && gitObject(baseline.packDigest), 'baseline digests');
  valid(baseline.proposalSpecDigest === undefined || digest(baseline.proposalSpecDigest), 'baseline proposalSpecDigest');
  valid(text(baseline.specPath, 1024) && !baseline.specPath.startsWith('/') && !baseline.specPath.includes('\\')
    && !baseline.specPath.split('/').some(part => part === '..' || part === '.' || part === ''), 'specPath');
  valid(value.taskBranch === `repair/${baseline.workflowId}/${value.taskId}`, 'taskBranch');
  return value as RepairBatchInput;
}

/** Flow identity, rather than analysis IDs or rewritten text, detects materially new ignored-scope evidence. */
export function repairEvidenceFlows(item: RepairItem): string[] {
  const diagnoses = Array.isArray(item.context?.diagnoses) ? item.context.diagnoses : [];
  const summary=item.context?.evidenceSummary as {flowIds?:string[]}|undefined;
  return [...new Set([...(Array.isArray(summary?.flowIds)?summary.flowIds:[]),...item.sources.flatMap(source => source.kind === 'diagnosis_candidate' ? [source.flowId] : []),
    ...diagnoses.flatMap((entry: any) => [entry?.flowId, ...(Array.isArray(entry?.flowIds) ? entry.flowIds : [])]),
    ...(Array.isArray(item.context?.suggestions) ? item.context.suggestions : []).flatMap((entry:any)=>Array.isArray(entry?.runIds)?entry.runIds:Array.isArray(entry?.impactRunIds)?entry.impactRunIds:[])]
    .filter((id): id is string => typeof id === 'string' && !!id))].sort();
}

export function repairEvidenceFacts(item: RepairItem) {
  const diagnoses = Array.isArray(item.context?.diagnoses) ? item.context.diagnoses : [];
  const summary=item.context?.evidenceSummary as {eventIds?:string[];severity?:number}|undefined;
  const eventIds = [...new Set([...(Array.isArray(summary?.eventIds)?summary.eventIds:[]),...diagnoses.flatMap((entry: any) => Array.isArray(entry?.evidenceEventIds) ? entry.evidenceEventIds : [])]
    .filter((id: unknown): id is string => typeof id === 'string'))].sort() as string[];
  const severity = diagnoses.reduce((rank: number, entry: any) => Math.max(rank, ['low', 'medium', 'high', 'critical'].indexOf(entry?.severity) + 1), 0);
  return { eventIds, severity:Math.max(severity,summary?.severity??0) };
}
