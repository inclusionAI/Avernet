import type { ExecutorResult } from "../types.js";

/** Caller-side grace only: this cannot terminate a runtime that ignores abort. */
export const EXECUTION_ABORT_GRACE_MS = 5_000;

export interface ExecutionAttempt {
  readonly signal: AbortSignal;
  isActive(): boolean;
  throwIfInactive(): void;
  guard<Args extends unknown[]>(
    callback: ((...args: Args) => Promise<void> | void) | undefined,
  ): ((...args: Args) => Promise<void>) | undefined;
  delay(ms: number): Promise<void>;
}

/**
 * Bound the caller's entire attempt, not just the native runtime call. The work
 * remains observed after the caller returns; its own finally must retain any
 * permit/resources until the underlying work actually settles. Started guarded
 * callbacks also belong to the attempt, even if the runtime does not await them.
 */
export async function withExecutionDeadline(
  timeoutMs: number,
  parentSignal: AbortSignal | undefined,
  execute: (attempt: ExecutionAttempt) => Promise<ExecutorResult>,
): Promise<ExecutorResult> {
  const controller = new AbortController();
  const expiresAt = Date.now() + timeoutMs;
  let active = true;
  let parentAborted = false;
  let stopReason: "timeout" | "abort" | undefined;
  let settled = false;
  let callbacksClosed = false;
  const pendingCallbacks = new Set<Promise<void>>();
  let deadlineTimer: ReturnType<typeof setTimeout> | undefined;
  let graceTimer: ReturnType<typeof setTimeout> | undefined;
  let notifyStopped: () => void;
  const stopped = new Promise<void>((resolve) => { notifyStopped = resolve; });
  let notifyGraceExpired: () => void;
  const graceExpired = new Promise<void>((resolve) => { notifyGraceExpired = resolve; });

  const stop = (reason: "timeout" | "abort") => {
    if (reason === "abort") parentAborted = true;
    if (!active) return;
    // Invalidate callbacks BEFORE abort: runtime abort listeners may invoke them.
    active = false;
    stopReason = reason;
    clearTimeout(deadlineTimer);
    graceTimer = setTimeout(notifyGraceExpired, EXECUTION_ABORT_GRACE_MS);
    notifyStopped();
    controller.abort(reason === "abort"
      ? parentSignal?.reason ?? new Error("embedded-agent execution aborted")
      : new Error(`embedded-agent execution deadline exceeded after ${timeoutMs}ms`));
  };
  const onParentAbort = () => stop("abort");
  const isActive = () => {
    if (active && Date.now() >= expiresAt) stop("timeout");
    return active;
  };
  const throwIfInactive = () => {
    if (!isActive()) throw controller.signal.reason ?? new Error("embedded-agent attempt is inactive");
  };
  const attempt: ExecutionAttempt = {
    signal: controller.signal,
    isActive,
    throwIfInactive,
    guard(callback) {
      if (!callback) return undefined;
      return (...args) => {
        if (!isActive() || callbacksClosed) return Promise.resolve();
        const pending = (async () => { await callback(...args); })();
        pendingCallbacks.add(pending);
        const completed = () => { pendingCallbacks.delete(pending); };
        // Observe even discarded callback rejections, but return the original
        // promise so callers that await it still receive the original error.
        void pending.then(completed, completed);
        return pending;
      };
    },
    delay(ms) {
      throwIfInactive();
      return new Promise<void>((resolve, reject) => {
        const onAbort = () => {
          clearTimeout(timer);
          controller.signal.removeEventListener("abort", onAbort);
          reject(controller.signal.reason);
        };
        const timer = setTimeout(() => {
          controller.signal.removeEventListener("abort", onAbort);
          resolve();
        }, ms);
        controller.signal.addEventListener("abort", onAbort, { once: true });
      });
    },
  };

  parentSignal?.addEventListener("abort", onParentAbort, { once: true });
  if (parentSignal?.aborted) onParentAbort();
  else if (timeoutMs <= 0) stop("timeout");
  else deadlineTimer = setTimeout(() => stop("timeout"), timeoutMs);

  const work = (async () => {
    try {
      throwIfInactive();
      const result = await execute(attempt);
      return result;
    } catch (error) {
      return {
        status: "failed" as const,
        error: `embedded-agent execution failed: ${error instanceof Error ? error.message : String(error)}`,
        rawError: error,
      };
    }
  })();
  const operation = work.then(async (result) => {
    // Drain only at the operation boundary, NEVER inside a guard: an outer
    // guarded callback may await an inner one. Re-snapshot because a running
    // callback can start another guarded callback while this drain is waiting.
    while (pendingCallbacks.size > 0) {
      await Promise.allSettled([...pendingCallbacks]);
    }
    // Close the gate synchronously with observing an empty set. Otherwise a
    // callback could enter between operation settlement and the caller resuming.
    callbacksClosed = true;
    settled = true;
    return result;
  });

  try {
    const result = await Promise.race([operation, stopped]);
    // Check the wall-clock budget as well, in case synchronous work delayed the
    // timeout callback. A result arriving after expiry must never become success.
    isActive();
    if (stopReason) {
      await Promise.race([operation, graceExpired]);
      const aborted = parentAborted || parentSignal?.aborted;
      return {
        status: "failed",
        error: aborted
          ? "embedded-agent execution aborted by parent signal"
          : `embedded-agent execution deadline exceeded after ${timeoutMs}ms${settled ? "" : "; runtime or callbacks did not settle within abort grace"}`,
        // Retry is safe only after native work AND every started callback exit.
        // Invalidation cannot interrupt side effects inside a callback's await.
        ...(aborted || !settled ? { retryable: false } : {}),
      };
    }
    return result as ExecutorResult;
  } finally {
    active = false;
    clearTimeout(deadlineTimer);
    clearTimeout(graceTimer);
    parentSignal?.removeEventListener("abort", onParentAbort);
  }
}
