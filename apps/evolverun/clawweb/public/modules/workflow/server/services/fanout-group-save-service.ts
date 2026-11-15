/** Route-level orchestration for strict fanout group saves.
 * The repository remains a pure storage primitive: this service owns 409 diagnostics
 * and idempotent acknowledgement of a save whose canonical result is already durable.
 */
import { randomUUID } from 'node:crypto';
import { Buffer } from 'node:buffer';
import { FanoutStorageError, type FanoutStateRepository } from '../repositories/fanout-state-repository.js';
import { RunLogRepository } from '../repositories/run-log-repository.js';

type ObjectValue = Record<string, unknown>;
export type FanoutConflictObservation = {
  group: ObjectValue | null;
  parentStatus: string | null;
  readStatus: 'ok' | 'failed';
};
export type FanoutGroupSaveInput = {
  group: unknown;
  expectedRevision: number | null;
  parentState?: ObjectValue;
};
type ConflictInput = {
  parentFlowId: string;
  nodeId: string;
  expectedRevision: number | null;
  group: unknown;
  errorMessage: string;
  observation: FanoutConflictObservation;
  recovery: 'reloaded_equivalent_state' | 'conflict_retained' | 'reload_failed';
};
type LogSink = Pick<RunLogRepository, 'insertBatch'>;

const isObject = (value: unknown): value is ObjectValue =>
  value !== null && typeof value === 'object' && !Array.isArray(value);
const object = (value: unknown): ObjectValue => isObject(value) ? value : {};
const text = (value: unknown): string | null => typeof value === 'string' ? value.slice(0, 256) : null;
const number = (value: unknown): number | null => Number.isSafeInteger(value) ? value as number : null;
const maxChildren = 20;
/** Validated fanout snapshots are JSON-safe, so this canonical form is deterministic. */
function canonical(value: unknown): string {
  const parsed = JSON.parse(JSON.stringify(value) ?? 'null') as unknown;
  const stable = (item: unknown): unknown => {
    if (Array.isArray(item)) return item.map(stable);
    if (item === null || typeof item !== 'object') return item;
    const record = (item as Record<string, unknown>);
    return Object.keys(record).sort().reduce((acc, child) => {
      acc[child] = stable(record[child]);
      return acc;
    }, {} as Record<string, unknown>);
  };
  return JSON.stringify(stable(parsed));
}
const equal = (a: unknown, b: unknown): boolean => canonical(a) === canonical(b);
/** Revision is intentionally ignored: two equivalent writes differ only by the CAS counter. */
const sameCanonicalStateExceptRevision = (a: ObjectValue, b: ObjectValue) =>
  equal({ ...a, revision: 0 }, { ...b, revision: 0 });

function groupSummary(value: unknown) {
  const group = object(value);
  const children = Array.isArray(group.children) ? group.children : [];
  return {
    groupExecutionId: text(group.groupExecutionId), revision: number(group.revision), status: text(group.status),
    deadlineAt: text(group.deadlineAt), childCount: children.length,
    childrenOmitted: Math.max(0, children.length - maxChildren),
    children: children.slice(0, maxChildren).map(value => {
      const child = object(value);
      return { childFlowId: text(child.childFlowId), nativeFlowId: text(child.nativeFlowId), itemId: text(child.itemId),
        itemIndex: number(child.itemIndex), attemptNumber: number(child.attemptNumber), status: text(child.status),
        hasDispatchClaim: typeof child.dispatchClaimToken === 'string' };
    }),
  };
}

function diagnosis(message: string) {
  if (message === 'Group revision conflict') return {
    code: 'FANOUT_REVISION_CONFLICT',
    possibleCause: 'Stale expectedRevision, a concurrent writer, or a repeated save after a lost response.',
    recoveryAction: 'Reload the group and revision. Accept equivalent non-dispatch state; otherwise reconcile against current state before retrying. Never blindly overwrite with a refreshed revision.',
  };
  if (/dispatch claim|dispatch claims/i.test(message)) return {
    code: 'FANOUT_DISPATCH_CLAIM_CONFLICT',
    possibleCause: 'Claim is not isolated, parent is not running, child is ineligible/already exists, or claim is being changed.',
    recoveryAction: 'Reload parent/group/child and reconcile the existing dispatch. Do not replay a one-shot claim or launch another child.',
  };
  if (message === 'Concurrent parent state update or expired deadline') return {
    code: 'FANOUT_CAS_OR_DEADLINE_CONFLICT',
    possibleCause: 'The bounded CAS attempts were exhausted or the deadline expired before the write.',
    recoveryAction: 'Reload authoritative state and check deadline and parent status. Reconcile before retrying; do not bypass deadline checks.',
  };
  if (/terminal|cancel|queued/i.test(message)) return {
    code: 'FANOUT_LIFECYCLE_CONFLICT',
    possibleCause: 'The requested update reverses lifecycle progress or changes a terminal/cancelled state.',
    recoveryAction: 'Reload parent and child outcomes; keep terminal and cancellation state authoritative instead of replaying stale state.',
  };
  return {
    code: 'FANOUT_IDENTITY_CONFLICT',
    possibleCause: 'The proposed group conflicts with immutable launch, child, or request-key identity.',
    recoveryAction: 'Compare durable group/child identities and reconcile the existing run. Do not replace an identity just to retry the save.',
  };
}

async function observe(repository: FanoutStateRepository, parentFlowId: string, nodeId: string): Promise<FanoutConflictObservation> {
  try { return { ...await repository.getGroup(parentFlowId, nodeId), readStatus: 'ok' }; }
  catch { return { group: null, parentStatus: null, readStatus: 'failed' }; }
}

/** Acknowledge a stale save only when the proposed ordinary state is already current.
 * Claim-bearing writes stay conflicted because a replay must never grant dispatch twice. */
function isEquivalentRevisionRetry(error: FanoutStorageError, observation: FanoutConflictObservation, input: FanoutGroupSaveInput): boolean {
  if (error.message !== 'Group revision conflict') return false;
  const current = observation.group;
  const next = object(input.group);
  const children = Array.isArray(next.children) ? next.children : [];
  if (!current || Object.keys(next).length === 0 || observation.parentStatus !== 'running') return false;
  if (['succeeded', 'failed', 'cancelled', 'canceled', 'timed_out', 'rejected', 'completed', 'aborted'].includes(String(current.status))) return false;
  if (current.deadlineAt !== null && (!text(current.deadlineAt) || !Number.isFinite(Date.parse(String(current.deadlineAt))))) return false;
  if (current.deadlineAt !== null && Date.parse(String(current.deadlineAt)) <= Date.now()) return false;
  return Number(current.revision) >= Number(next.revision)
    && !children.some(child => object(child).dispatchClaimToken !== undefined)
    && sameCanonicalStateExceptRevision(current, next);
}

function buildDiagnostic(input: ConflictInput) {
  const submitted = groupSummary(input.group);
  const observed = groupSummary(input.observation.group);
  const diagnostic = {
    event: 'fanout.save.conflict', eventId: randomUUID(), stage: 'save_group', httpStatus: 409,
    ...diagnosis(input.errorMessage), parentFlowId: text(input.parentFlowId), nodeId: text(input.nodeId),
    expectedRevision: input.expectedRevision, submittedRevision: submitted.revision, observedRevision: observed.revision,
    errorMessage: text(input.errorMessage), parentStatus: text(input.observation.parentStatus),
    snapshotReadStatus: input.observation.readStatus, observationTiming: 'after_conflict',
    recovery: input.recovery, submitted, observed,
  };
  // Remove whole child samples only. Truncating serialized JSON would make run_logs unusable.
  while (Buffer.byteLength(JSON.stringify(diagnostic), 'utf8') > 60_000
    && (submitted.children.length > 0 || observed.children.length > 0)) {
    for (const summary of [submitted, observed]) {
      if (summary.children.pop()) summary.childrenOmitted++;
    }
  }
  return { diagnostic, submitted, observed };
}

export async function recordFanoutSaveConflict(logs: LogSink | null, input: ConflictInput): Promise<void> {
  const { diagnostic } = buildDiagnostic(input);
  try {
    const inserted = await logs?.insertBatch([{
      flow_id: input.parentFlowId, node_id: input.nodeId, level: 'warn', source: 'fanout-server',
      message: JSON.stringify(diagnostic), timestamp: Date.now(), seq: 0,
    }]);
    if (inserted !== undefined && inserted > 0) return;
  } catch { /* A committed write may still throw after the response; never retry an ambiguous insert. */ }
  console.error('[fanout-save-conflict]', JSON.stringify({ ...diagnostic,
    logPersistence: 'failed', logRecoveryAction: 'Check run_logs schema/database availability and reconcile this eventId from server logs.' }));
}

export async function saveFanoutGroupWithDiagnostics(
  repository: FanoutStateRepository,
  logs: LogSink | null,
  parentFlowId: string,
  nodeId: string,
  input: FanoutGroupSaveInput,
): Promise<{ group: ObjectValue; parentStatus: string }> {
  try {
    return await repository.saveGroup(parentFlowId, nodeId, input);
  } catch (error) {
    if (!(error instanceof FanoutStorageError) || error.statusCode !== 409 || !object(input)) throw error;
    const observation = await observe(repository, parentFlowId, nodeId);
    const equivalent = isEquivalentRevisionRetry(error, observation, input);
    await recordFanoutSaveConflict(logs, {
      parentFlowId, nodeId, expectedRevision: input.expectedRevision, group: input.group,
      errorMessage: error.message, observation,
      recovery: equivalent ? 'reloaded_equivalent_state' : observation.readStatus === 'failed' ? 'reload_failed' : 'conflict_retained',
    });
    if (equivalent && observation.group) {
      return { group: observation.group, parentStatus: observation.parentStatus ?? 'running' };
    }
    throw error;
  }
}
