import { flowTimeoutElapsedMs } from './timeout-clock.js';
import type { FlowRunRow, IFlowRunRepository } from '../db/repositories/types.js';
import type { FlowState } from '../types.js';
import { capturedRetryEpoch } from './attempt.js';
import { stateAttemptEpoch } from '../db/repositories/retry-epoch.js';

let scanning = false;
/** Startup recovery and interval watchdog share one non-overlapping scan lane. */
export async function withFlowScan<T>(operation: () => Promise<T>, busy: T): Promise<T> {
  if (scanning) return busy;
  scanning = true;
  try { return await operation(); }
  finally { scanning = false; }
}

function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

export type RunningSnapshot = { row: FlowRunRow; state?: FlowState; retryEpoch: number; wallClockTimeout: boolean };
/** Lists are discovery only. Admission and policy always use a full detail snapshot. */
export async function loadRunningSnapshot(
  repository: IFlowRunRepository, flowId: string,
  getState?: (flowId: string) => Promise<FlowState | undefined>,
): Promise<RunningSnapshot | undefined> {
  const row = await repository.findByFlowId(flowId);
  if (!row || row.status !== 'running') return undefined;
  const retryEpoch = capturedRetryEpoch(row.retry_epoch);
  function parseState(value: unknown): FlowState | undefined {
    if (value == null) return undefined;
    const parsed: unknown = typeof value === 'string' ? JSON.parse(value) : value;
    if (!object(parsed)) throw new Error('Invalid persisted flow state object; timeout claim deferred');
    return parsed as FlowState;
  }
  const canonical = parseState(row.state_json);
  let embedded: FlowState | undefined;
  if (row.params_json != null) {
    const params: unknown = JSON.parse(row.params_json);
    if (object(params) && Object.hasOwn(params, '_clawmind_state')) {
      if (params._clawmind_state == null) throw new Error('Invalid embedded flow state; timeout claim deferred');
      embedded = parseState(params._clawmind_state);
    }
  }
  let state = canonical ?? embedded;
  // OpenClaw owns TaskFlow state separately. Read it before admission, never
  // re-read its epoch/revision after claiming an old attempt.
  const taskState = getState ? await getState(flowId) : undefined;
  if (getState && !state && !taskState) throw new Error('TaskFlow state unavailable; timeout claim deferred');
  let wallClockTimeout = true;
  let retryAnchor: unknown;
  for (const candidate of [canonical, embedded, taskState]) {
    if (!candidate) continue;
    if (!object(candidate)) throw new Error('Invalid TaskFlow state; timeout claim deferred');
    if (candidate.workflowId != null && candidate.workflowId !== row.workflow_id) {
      throw new Error('Flow state identity mismatch; timeout claim deferred');
    }
    // Mind checkpoints are online-clock owned, including epoch zero and retries.
    // Any original snapshot evidence wins over a lagging mirror. Unknown policies
    // also fail closed; this process cannot arbitrate a remote recovery journal.
    if (Object.hasOwn(candidate, 'recoveryPolicy') || Object.hasOwn(candidate, 'recoveryEpoch')) wallClockTimeout = false;
    if (Object.hasOwn(candidate, 'retryStartedAtMs')) {
      flowTimeoutElapsedMs(row, candidate);
      if (retryAnchor !== undefined && retryAnchor !== candidate.retryStartedAtMs) {
        throw new Error('Flow retry timeout anchor mismatch; timeout claim deferred');
      }
      retryAnchor = candidate.retryStartedAtMs;
    }
    if (stateAttemptEpoch(candidate) !== retryEpoch) {
      throw new Error('Flow state attempt mismatch; timeout claim deferred');
    }
  }
  state ??= taskState;
  return { row, state: state ? structuredClone(state) : undefined, retryEpoch, wallClockTimeout };
}
