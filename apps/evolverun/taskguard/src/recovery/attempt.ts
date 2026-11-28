import { stateAttemptEpoch, type StateAttempt } from '../db/repositories/retry-epoch.js';

/** Missing epochs are historical attempt zero, never permission to adopt the current attempt. */
export function capturedRetryEpoch(value: unknown = 0): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0) {
    throw new Error('Invalid retry epoch; terminal write deferred');
  }
  return value;
}

/** Capture from the original state; the associated record is only a consistency fence. */
export function captureStateAttempt(record: Record<string, unknown>, state: StateAttempt): number {
  const epoch = stateAttemptEpoch(state);
  for (const key of ['retry_epoch', 'retryEpoch']) {
    if (!Object.hasOwn(record, key)) continue;
    if (record[key] === undefined) throw new Error('Invalid record retry epoch; execution deferred');
    if (capturedRetryEpoch(record[key]) !== epoch) {
      throw new Error('Flow record/state attempt mismatch; execution deferred');
    }
  }
  state.retryEpoch = epoch;
  return epoch;
}
