import { AsyncLocalStorage } from 'node:async_hooks';
import { CompletionPersistenceError } from './completion.js';

const owners = new Map<string, object>();
const executionOwners = new AsyncLocalStorage<{ flowId: string; isCurrent: () => boolean; retryEpoch: number; closeoutAdmitted: boolean }>();

/** A replacement revokes all old driver cleanup/closeout authority for this flow. */
export function claimAsyncDriver(flowId: string, retryEpoch: number) {
  const token = {};
  owners.set(flowId, token);
  const isCurrent = () => owners.get(flowId) === token;
  const execution = { flowId, isCurrent, retryEpoch, closeoutAdmitted: false };
  return {
    isCurrent,
    closeoutAdmitted: () => execution.closeoutAdmitted,
    run<T>(operation: () => T): T { return executionOwners.run(execution, operation); },
    finish(cleanup: () => void): void {
      if (!isCurrent()) return;
      owners.delete(flowId); // A cleanup-triggered replacement must retain its new token.
      cleanup();
    },
  };
}

/** Check again after every awaited admission boundary, before reconcile/publication. */
export function assertDriverCloseoutCurrent(flowId: string): void {
  const execution = executionOwners.getStore();
  if (execution?.flowId === flowId && !execution.isCurrent()) {
    throw new CompletionPersistenceError(new Error('Async driver superseded by a replacement generation'));
  }
}

/** The original driver generation cannot be replaced by mutable/newer state.
 * Synchronous child workflows inherit ALS, but never their parent's authority. */
export function asyncDriverRetryEpoch(flowId: string): number | undefined {
  const execution = executionOwners.getStore();
  return execution?.flowId === flowId ? execution.retryEpoch : undefined;
}

/** Later publication/cleanup errors cannot re-enter the business crash writer. */
export function markDriverCloseoutAdmitted(flowId: string): void {
  const execution = executionOwners.getStore();
  if (execution?.flowId === flowId) execution.closeoutAdmitted = true;
}

/** Observe the FINAL driver promise, including rejection-handler and finally failures.
 * Keep the original promise rejected for callers; even a broken reporter is supervised.
 * This observer has no terminal-write, notification, slot-release or recovery authority. */
export function observeDriverPromise(promise: Promise<unknown>, report: (error: unknown) => void | Promise<void>): void {
  void promise.catch(error => Promise.resolve().then(() => report(error)).catch(() => {}));
}
