import type { WorkflowNode, FlowState, WorkflowSpec, ExecutorResult } from "../types.js";
import type { TemplateContext } from "../runner.js";
import { FanoutRuntimeError } from "./subworkflow-fanout-types.js";
import type {
  FanoutRuntime, FanoutState, FanoutChildCompletion, FanoutError, FanoutGroupStatus,
} from "./subworkflow-fanout-types.js";
import {
  createState, snapshot, terminalChild, terminalGroup, childRequest,
  projectResult, errorDetail, applyCompletion, fatalChild,
} from "./subworkflow-fanout-state.js";

export type { FanoutConfig, FanoutState, FanoutRuntime, FanoutChildRequest, FanoutChildCompletion } from "./subworkflow-fanout-types.js";
export { FanoutRuntimeError } from "./subworkflow-fanout-types.js";

type Event = { index: number; completion: FanoutChildCompletion } | { index: number; thrown: unknown }
  | { cancelError: unknown };

function executorResult(state: FanoutState): ExecutorResult {
  if (!state.result || !terminalGroup(state)) throw new Error("fanout has not been durably sealed");
  return state.status === "succeeded"
    ? { status: "succeeded", result: snapshot(state.result) }
    : { status: "failed", result: snapshot(state.result), error: `${state.error?.code ?? state.status}: ${state.error?.message ?? "Fanout did not succeed"}` };
}

function assertIdentity(state: FanoutState, parentFlowId: string, nodeId: string): void {
  if (state.schemaVersion !== "fanout.v1" || state.kind !== "subworkflowFanoutState"
    || state.parentFlowId !== parentFlowId || state.groupNodeId !== nodeId
    || !Number.isSafeInteger(state.revision) || state.revision < 0
    || !Number.isFinite(Date.parse(state.deadlineAt))
    || !Number.isSafeInteger(state.config.concurrency) || state.config.concurrency < 1) {
    throw new Error("invalid persisted fanout identity/state");
  }
  const ids = new Set<string>();
  state.children.forEach((c, index) => {
    if (c.itemIndex !== index || c.parentFlowId !== parentFlowId || c.parentNodeId !== nodeId
      || c.groupExecutionId !== state.groupExecutionId || ids.has(c.childFlowId)) throw new Error("invalid persisted child identity");
    ids.add(c.childFlowId);
  });
  if (state.children.filter(c => c.status !== "queued" && !terminalChild(c)).length > state.config.concurrency) {
    throw new Error("persisted fanout exceeds reserved capacity");
  }
  if (state.status === "cancelling" && (!state.error || !Number.isFinite(Date.parse(state.cancelDeadlineAt)))) {
    throw new Error("invalid persisted cancellation deadline");
  }
}

/**
 * Library-style collector. All production persistence/execution is injected.
 * Every mutation is serialized through durable CAS; child promises only enqueue
 * observations, never mutate state. Lost writes/unknown starts reject this call so
 * a later invocation can reconcile the same durable intent, not invent a retry.
 */
export async function executeSubworkflowFanout(
  node: WorkflowNode,
  templateCtx: TemplateContext,
  parentState: FlowState,
  parentFlowId: string,
  resolvedChildWorkflow: WorkflowSpec,
  runtime: FanoutRuntime,
  options: { signal?: AbortSignal } = {},
): Promise<ExecutorResult> {
  let state = await runtime.load(parentFlowId, node.id);
  if (state) {
    state = snapshot(state);
    assertIdentity(state, parentFlowId, node.id);
    if (terminalGroup(state)) return executorResult(state);
  } else {
    try {
      state = createState(node, templateCtx, parentState, parentFlowId, resolvedChildWorkflow);
    } catch (error) {
      return { status: "failed", error: `FANOUT_CONFIG_INVALID: ${error instanceof Error ? error.message : String(error)}`, rawError: error };
    }
    // Saving is deliberately outside the validation catch. No memory-only success.
    await runtime.save(snapshot(state), null);
  }

  const observers = new Map<number, AbortController>();
  const cancelSent = new Set<number>();
  const lastObservedAt = new Map<number, number>();
  const events: Event[] = [];
  let wake: (() => void) | undefined;
  let closed = false;
  const enqueue = (event: Event) => { if (!closed) { events.push(event); wake?.(); } };

  async function commit(change: (next: FanoutState) => void): Promise<void> {
    const next = snapshot(state);
    next.revision++;
    change(next);
    next.cleanupPendingChildIds = next.children
      .filter(c => c.status !== "queued" && !terminalChild(c)).map(c => c.childFlowId);
    await runtime.save(snapshot(next), state.revision);
    state = next;
  }

  function observe(index: number): void {
    if (observers.has(index) || (state.status !== "cancelling" && localStopReason())) return;
    const controller = new AbortController();
    observers.set(index, controller);
    lastObservedAt.set(index, Date.now());
    if (state.status === "cancelling") controller.abort();
    const request = childRequest(state, state.children[index]);
    // Convert synchronous throws too. Always attach rejection handlers, even if
    // the collector subsequently exits due to failed persistence or a timeout.
    try {
      Promise.resolve(runtime.startOrGetChild(request, controller.signal)).then(
        completion => enqueue({ index, completion }), thrown => enqueue({ index, thrown }),
      );
    } catch (thrown) { enqueue({ index, thrown }); }
  }

  async function beginStop(detail: FanoutError): Promise<void> {
    if (state.status === "cancelling") return;
    const now = Date.now();
    await commit(next => {
      next.status = "cancelling";
      next.error = detail;
      next.cancelRequestedAt = new Date(now).toISOString();
      next.cancelDeadlineAt = new Date(Math.min(Date.parse(next.deadlineAt), now + next.config.cancelGraceSeconds * 1000)).toISOString();
      for (const c of next.children) {
        if (terminalChild(c)) continue;
        if (c.status === "queued") { c.status = "cancelled"; c.error = detail; }
        else c.status = "cancel_requested";
      }
    });
  }

  function requestCancellations(): void {
    for (const c of state.children) {
      if (terminalChild(c) || c.status === "queued" || cancelSent.has(c.itemIndex)) continue;
      cancelSent.add(c.itemIndex);
      const request = childRequest(state, c);
      Promise.resolve().then(() => runtime.cancelChild(request)).catch(cancelError => enqueue({ cancelError }));
      observers.get(c.itemIndex)?.abort();
    }
  }

  function localStopReason(): FanoutError | null {
    if (options.signal?.aborted) return errorDetail("FANOUT_CANCELLED", "Caller requested cancellation", "cancellation");
    if (Date.now() >= Date.parse(state.deadlineAt)) return errorDetail("GROUP_TIMEOUT", "Fanout group deadline exceeded", "timeout");
    return null;
  }

  async function readStopReason(): Promise<FanoutError | null> {
    // A durable stop is monotonic; unavailable lifecycle reads must not block cleanup.
    if (state.status === "cancelling") return null;
    const local = localStopReason();
    if (local) return local;
    // The lifecycle port has no AbortSignal. Race observation, not the remote read,
    // against our durable deadline/caller abort. Late read responses are ignored.
    const observation = await new Promise<string | FanoutError>((resolve, reject) => {
      const done = (value: string | FanoutError) => { cleanup(); resolve(value); };
      const check = () => { const stop = localStopReason(); if (stop) done(stop); };
      const timer = setInterval(check, Math.max(1, Math.min(25, Date.parse(state.deadlineAt) - Date.now())));
      const cleanup = () => { clearInterval(timer); options.signal?.removeEventListener("abort", check); };
      options.signal?.addEventListener("abort", check, { once: true });
      Promise.resolve().then(() => runtime.parentStatus(parentFlowId)).then(done, error => { cleanup(); reject(error); });
      check();
    });
    if (typeof observation !== "string") return observation;
    const interrupted = localStopReason();
    if (interrupted) return interrupted;
    const parentStatus = observation.toLowerCase();
    if (["cancelled", "canceled", "cancelling", "canceling"].includes(parentStatus)) {
      return errorDetail("FANOUT_CANCELLED", "Parent requested cancellation", "cancellation");
    }
    if (["failed", "completed", "succeeded", "timed_out"].includes(parentStatus)) {
      return errorDetail("PARENT_NOT_RUNNING", `Parent is ${parentStatus}`, "orchestration");
    }
    if (parentStatus !== "running") throw new FanoutRuntimeError(errorDetail("PARENT_STATUS_UNKNOWN", `Cannot dispatch for parent status ${parentStatus}`, "orchestration"));
    return null;
  }

  async function drainEvents(): Promise<void> {
    while (events.length) {
      const event = events.shift()!;
      if ("cancelError" in event) throw event.cancelError;
      const child = state.children[event.index];
      observers.delete(event.index);
      if (terminalChild(child)) continue;
      if ("thrown" in event) {
        const thrown = event.thrown;
        if (thrown instanceof FanoutRuntimeError && thrown.disposition === "rejected") {
          await commit(next => applyCompletion(next.children[event.index], { status: "rejected", error: thrown.detail }));
        } else if (state.status === "cancelling") {
          // An aborted/unreachable observer proves nothing about child termination.
          // Keep the reservation and cleanup obligation until confirmation/grace.
          if (thrown instanceof FanoutRuntimeError) {
            await commit(next => { next.children[event.index].error = thrown.detail; });
          }
          continue;
        } else {
          if (thrown instanceof FanoutRuntimeError) {
            await commit(next => {
              next.children[event.index].status = "dispatch_unknown";
              next.children[event.index].error = thrown.detail;
            });
          }
          throw thrown;
        }
      } else {
        await commit(next => applyCompletion(next.children[event.index], event.completion));
      }
      const updated = state.children[event.index];
      if (state.status !== "cancelling" && fatalChild(updated)) await beginStop(updated.error!);
    }
  }

  async function seal(status: FanoutGroupStatus, error = state.error): Promise<ExecutorResult> {
    await commit(next => {
      next.status = status; next.error = error;
      next.sealedAt = new Date().toISOString();
      next.result = projectResult(next);
    });
    // A synchronous cancellation-port failure observed while sealing still throws.
    const cancelFailure = events.find(e => "cancelError" in e);
    if (cancelFailure && "cancelError" in cancelFailure) throw cancelFailure.cancelError;
    return executorResult(state);
  }

  async function waitForObservation(): Promise<void> {
    if (events.length) return;
    const deadline = state.status === "cancelling" ? Date.parse(state.cancelDeadlineAt) : Date.parse(state.deadlineAt);
    await new Promise<void>(resolve => {
      const timer = setTimeout(done, Math.max(1, Math.min(25, deadline - Date.now())));
      function done() { clearTimeout(timer); wake = undefined; resolve(); }
      wake = done;
    });
  }

  try {
    for (;;) {
      const stop = await readStopReason();
      if (stop) await beginStop(stop);
      await drainEvents();
      if (state.status === "cancelling") {
        requestCancellations();
        // Let immediate cancellations/confirmed completions enter the queue.
        await Promise.resolve();
        await drainEvents();
        const unsettled = state.children.filter(c => !terminalChild(c));
        const timedOut = state.error?.category === "timeout";
        if (!unsettled.length) {
          return await seal(timedOut ? "timed_out" : state.error?.category === "cancellation" ? "cancelled" : "failed");
        }
        if (Date.now() >= Date.parse(state.cancelDeadlineAt)) {
          return await seal(timedOut ? "timed_out" : state.error?.category === "cancellation" ? "timed_out" : "failed",
            state.error?.category === "cancellation"
              ? errorDetail("CANCEL_CONFIRMATION_TIMEOUT", "Cancellation grace expired with unconfirmed children", "timeout") : state.error);
        }
        // Recovery attaches to existing runs only: an aborted signal forbids creation.
        for (const c of unsettled) {
          if (!observers.has(c.itemIndex) && Date.now() - (lastObservedAt.get(c.itemIndex) ?? 0) >= 25) observe(c.itemIndex);
        }
        await waitForObservation();
        continue;
      }
      // A crash may have left a non-tolerable completion durable before beginStop.
      const fatal = state.children.find(fatalChild);
      if (fatal) { await beginStop(fatal.error!); continue; }
      if (state.children.every(terminalChild)) {
        const barrier = await readStopReason();
        if (barrier) { await beginStop(barrier); continue; }
        // Runtime.save is also required to check the parent barrier atomically.
        return await seal("succeeded");
      }
      // Completion persistence above may have accepted cancellation or crossed the
      // deadline. Recheck AFTER every awaited write, immediately before observation.
      const dispatchBarrier = await readStopReason();
      if (dispatchBarrier) { await beginStop(dispatchBarrier); continue; }
      if (events.length) continue;
      // Persisted reservations (including unknown starts) occupy capacity on resume.
      for (const c of state.children) {
        if (c.status !== "queued" && !terminalChild(c)) observe(c.itemIndex);
        // Ports may throw synchronously. Do not start another reserved child once
        // its predecessor has already reported a known rejection/unknown outcome.
        if (events.length || localStopReason()) break;
      }
      if (events.length || localStopReason()) continue;
      const reserved = state.children.filter(c => c.status !== "queued" && !terminalChild(c)).length;
      const queued = state.children.find(c => c.status === "queued");
      if (queued && reserved < state.config.concurrency) {
        await commit(next => { next.children[queued.itemIndex].status = "dispatch_pending"; });
        // Reenter the lifecycle/observation barrier before sending this intent.
        continue;
      }
      await waitForObservation();
    }
  } finally {
    closed = true;
    // Detach observation; this is not a durable cancellation acknowledgement.
    for (const observer of observers.values()) observer.abort();
    wake?.();
  }
}
