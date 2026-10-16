import { canonicalRepairJson, repairEvidenceFlows } from '../contracts/repair-batch.js';
import { repairOperationIdentity } from '../contracts/repair-selection.js';
import type { RepairInboxItem } from '../contracts/repair-workbench.js';

const bytes = (value: unknown) => Buffer.byteLength(canonicalRepairJson(value), 'utf8');
/** Display text only. Never used to freeze instructions, identities or executable values. */
export function inboxText(value: unknown, limit = 512): string {
  if (typeof value !== 'string') return '';
  if (Buffer.byteLength(value, 'utf8') <= limit) return value;
  let result = '', size = 0;
  for (const character of value) {
    const length = Buffer.byteLength(character, 'utf8');
    if (size + length > limit - 3) break;
    result += character; size += length;
  }
  return result + '…';
}
function originalSummary(item: RepairInboxItem, force = false): RepairInboxItem {
  const { aggregation: _aggregation, ...original } = item;
  if (!force && bytes(original) <= 4096) return original;
  const operations = Array.isArray(item.proposal?.operations) ? item.proposal.operations : [];
  const keys: NonNullable<RepairInboxItem['readSummary']>['operations'] = [];
  let remaining = 4096;
  for (const op of operations) {
    if (!op || typeof op !== 'object' || typeof op.path !== 'string' || op.op === 'test') continue;
    const key = { nodeId: String(op.nodeId ?? '工作流'), path: op.path.startsWith('/') ? op.path : '/' + op.path.replaceAll('.', '/'),
      identity: repairOperationIdentity(op) };
    const size = bytes(key);
    if (size > remaining) break;
    keys.push(key); remaining -= size;
  }
  const relevant = operations.filter(op => op && typeof op === 'object' && typeof op.path === 'string' && op.op !== 'test');
  return { itemId: item.itemId, workflowId: item.workflowId, groupKey: item.groupKey, proposalKey: item.proposalKey,
    contentRevision: item.contentRevision, previousItemId: item.previousItemId, episodeKey: item.episodeKey,
    state: item.state, stateVersion: item.stateVersion, sourceAvailable: item.sourceAvailable,
    activeTaskId: item.activeTaskId, activeRevision: item.activeRevision, updatedAtMs: item.updatedAtMs,
    disposition: item.disposition ? { ...item.disposition, reason: inboxText(item.disposition.reason), evidenceFlowIds: [], evidenceEventIds: [] } : null,
    proposal: item.proposal ? { summary: inboxText(item.proposal.summary) } : null, instruction: inboxText(item.instruction), sources: [],
    context: { nodeId: item.context?.nodeId ?? null, failureMode: item.context?.failureMode ?? null, signature: item.context?.signature ?? null },
    readSummary: { sourceCount: item.sources.length, runCount: repairEvidenceFlows(item).length,
      diagnosisCount: Array.isArray(item.context?.diagnoses) ? item.context.diagnoses.length : 0,
      originalBytes: bytes(original), operations: keys, conflictsComplete: keys.length === relevant.length } };
}
export function inboxSummary(item: RepairInboxItem): RepairInboxItem {
  const summary = originalSummary(item);
  if (!item.aggregation) return summary;
  const plan = item.aggregation;
  return { ...summary, aggregation: { ...plan, title: inboxText(plan.title), changes: plan.changes.map(text => inboxText(text)),
    originals: plan.originals.map(original => originalSummary(original, true)), flowIds: [],
    ...(plan.causes ? { causes: plan.causes.map(cause => ({ ...cause, title: inboxText(cause.title) })) } : {}),
    ...(plan.unknowns ? { unknowns: plan.unknowns.map(text => inboxText(text)) } : {}),
    conflicts: plan.conflicts.map(conflict => ({ ...conflict, reason: inboxText(conflict.reason) })) },
    readSummary: { sourceCount: summary.readSummary?.sourceCount ?? item.sources.length,
      runCount: plan.flowIds.length, diagnosisCount: summary.readSummary?.diagnosisCount ?? 0,
      originalBytes: bytes(item), operations: summary.readSummary?.operations ?? [],
      conflictsComplete: summary.readSummary?.conflictsComplete ?? true } };
}
/** Page boundaries are derived from bytes and row count; no row/member is discarded. */
export function inboxPages(items: RepairInboxItem[], pageSize: number, budget = 2 * 1024 * 1024): RepairInboxItem[][] {
  const pages: RepairInboxItem[][] = [[]]; let used = 2;
  for (const item of items) {
    const size = bytes(item) + 1;
    if (pages.at(-1)!.length && (pages.at(-1)!.length >= pageSize || used + size > budget)) { pages.push([]); used = 2; }
    pages.at(-1)!.push(item); used += size;
  }
  return pages;
}
