import type { IFlowRunRepository } from '../db/repositories/types.js';
import { capturedRetryEpoch } from './attempt.js';
import { stateAttemptEpoch, nextAttemptState, type StateAttempt } from '../db/repositories/retry-epoch.js';
import { CompletionRejectedError, retryTerminalWrite } from './completion.js';

/** Freeze the next attempt before executing it; never reopen through a status mirror. */
export async function admitManualRetry(
  repository: IFlowRunRepository | null, flowId: string, state: StateAttempt,
): Promise<number> {
  const nextEpoch = stateAttemptEpoch(state) + 1;
  capturedRetryEpoch(nextEpoch);
  const next = { ...nextAttemptState(state, nextEpoch), retryStartedAtMs: Date.now() };
  if (repository) {
    const outcome = await retryTerminalWrite(() => repository.resumeForRetry(flowId, nextEpoch, JSON.stringify(next)));
    if (outcome !== 'resumed') throw new CompletionRejectedError();
  }
  Object.assign(state, next);
  return nextEpoch;
}
