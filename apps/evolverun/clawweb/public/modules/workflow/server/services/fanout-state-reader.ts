import { FanoutReadError, type FanoutErrorSummary, type StoredRunSnapshot } from '../contracts/run-subworkflows.js';

type RecordValue = Record<string, unknown>;
export type FanoutAttempt = { attemptNumber: number; nativeFlowId: string | null; associationKey: string | null; status: string; error: FanoutErrorSummary | null };
export type FanoutItem = { itemIndex: number; itemId: string; activeAttemptNumber: number; attempts: FanoutAttempt[] };
export type FanoutStateView = {
  parentFlowId: string; groupNodeId: string; groupExecutionId: string; revision: number;
  status: string; concurrency: number; successPolicy: 'all_done'; childWorkflowId: string; children: FanoutItem[];
};
const childStatuses = new Set(['queued', 'pending', 'dispatch_pending', 'dispatch_unknown', 'running', 'waiting',
  'cancel_requested', 'succeeded', 'failed', 'cancelled', 'timed_out', 'rejected']);
const groupStatuses = new Set(['pending', 'running', 'cancelling', 'cancel_requested', 'succeeded', 'failed', 'cancelled', 'timed_out']);
const errorCategories = new Set(['business', 'execution', 'configuration', 'contract', 'orchestration', 'authorization', 'persistence', 'timeout', 'cancellation']);
function invalid(): never { throw new FanoutReadError('invalid_state', 'Stored fanout state is invalid'); }
export function object(value: unknown): RecordValue {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return invalid();
  return value as RecordValue;
}
function text(value: unknown): string { if (typeof value !== 'string' || !value.trim()) return invalid(); return value; }
function integer(value: unknown, min = 0): number {
  if (!Number.isSafeInteger(value) || (value as number) < min) return invalid();
  return value as number;
}
function errorSummary(value: unknown): FanoutErrorSummary | null {
  if (value === null) return null;
  const row = object(value);
  if (!errorCategories.has(text(row.category)) || (row.retryable !== undefined && typeof row.retryable !== 'boolean')) return invalid();
  return { category: text(row.category), code: text(row.code), message: text(row.message), retryable: row.retryable === true,
    ...(row.sourceNodeId === undefined ? {} : { sourceNodeId: text(row.sourceNodeId) }) };
}

export function readFanoutMap(run: StoredRunSnapshot): RecordValue {
  if (run.state_json === null || run.state_json === '') return {};
  let state: RecordValue;
  try { state = object(JSON.parse(run.state_json)); } catch { return invalid(); }
  if (state.flowId !== undefined && state.flowId !== run.flow_id) {
    throw new FanoutReadError('identity_conflict', 'Stored parent identity does not match');
  }
  return state.subworkflowFanouts === undefined ? {} : object(state.subworkflowFanouts);
}

/** Validate the taskguard v1 state; legacy approved attempts snapshots remain readable.
 * Never infer identity from a URL, result or node output. */
export function readFanoutState(value: unknown, parentFlowId: string, nodeId: string): FanoutStateView {
  const row = object(value);
  if (row.schemaVersion !== 'fanout.v1' || row.kind !== 'subworkflowFanoutState') return invalid();
  if (row.parentFlowId !== parentFlowId || row.groupNodeId !== nodeId) {
    throw new FanoutReadError('identity_conflict', 'Stored group identity does not match');
  }
  const current = row.config !== undefined;
  const config = current ? object(row.config) : row;
  if (!groupStatuses.has(text(row.status)) || config.successPolicy !== 'all_done'
    || config.finishMode !== 'wait_all' || config.resultPolicy !== 'collect_all' || !Array.isArray(row.children)) return invalid();
  const items = new Set<string>(); const indices = new Set<number>(); const flowIds = new Set<string>();
  const nativeIds = new Set<string>();
  const children = row.children.map(value => {
    const item = object(value); const itemId = text(item.itemId); const itemIndex = integer(item.itemIndex);
    if (current && (item.parentFlowId !== parentFlowId || item.parentNodeId !== nodeId || item.groupExecutionId !== row.groupExecutionId)) {
      throw new FanoutReadError('identity_conflict', 'Stored child identity does not match its group');
    }
    const storedAttempts = current ? [item] : item.attempts;
    if (items.has(itemId) || indices.has(itemIndex) || !Array.isArray(storedAttempts)) return invalid();
    items.add(itemId); indices.add(itemIndex);
    const attemptsSeen = new Set<number>();
    const attempts = storedAttempts.map(value => {
      const attempt = object(value); const attemptNumber = integer(attempt.attemptNumber, 1);
      const childFlowId = text(attempt.childFlowId); const status = text(attempt.status);
      if (attemptsSeen.has(attemptNumber) || flowIds.has(childFlowId) || childFlowId === parentFlowId || !childStatuses.has(status)) return invalid();
      attemptsSeen.add(attemptNumber); flowIds.add(childFlowId);
      // Only the approved attempts layout is legacy. Flat snapshots require
      // a host-created ordinary association; a missing native ID stays unbound.
      const parsedNative = !current ? childFlowId : attempt.nativeFlowId === undefined || attempt.nativeFlowId === null
        ? null : text(attempt.nativeFlowId);
      // The writer may currently admit native=parent. Hide rather than turning
      // that persisted group into a permanent invalid-state response.
      const nativeFlowId = parsedNative === parentFlowId && current ? null : parsedNative;
      if (nativeFlowId !== null) {
        if (nativeFlowId === parentFlowId || nativeIds.has(nativeFlowId)) return invalid();
        nativeIds.add(nativeFlowId);
      }
      return { attemptNumber, nativeFlowId, associationKey: current ? childFlowId : null, status, error: errorSummary(attempt.error) };
    });
    const activeAttemptNumber = integer(current ? item.attemptNumber : item.activeAttemptNumber, 1);
    if (!attemptsSeen.has(activeAttemptNumber)) return invalid();
    return { itemIndex, itemId, activeAttemptNumber, attempts };
  }).sort((a, b) => a.itemIndex - b.itemIndex);
  return { parentFlowId, groupNodeId: nodeId, groupExecutionId: text(row.groupExecutionId), revision: integer(row.revision),
    status: text(row.status), concurrency: integer(config.concurrency, 1), successPolicy: 'all_done',
    childWorkflowId: text(current ? object(row.childWorkflow).id : object(row.dependencySnapshot).workflowId), children };
}
