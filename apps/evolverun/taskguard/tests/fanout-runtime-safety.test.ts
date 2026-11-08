import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { setTimeout as delay } from "node:timers/promises";
import { setCachedConfig } from "../src/config/config-cache.js";
import { defaults } from "../src/config/types.js";
import type { ControllerDeps } from "../src/controller.js";
import type { FlowState, ExecutorResult } from "../src/types.js";
import type { FanoutPersistence, FanoutRunRecord } from "../src/platform/fanout-persistence.js";
import type { FanoutChildRequest, FanoutChildCompletion, FanoutState } from "../src/executors/subworkflow-fanout-types.js";

// Initialize before importing controller/runtime modules: never load user config.
setCachedConfig({ database: { type: "sqlite", sqlitePath: ":memory:" }, app: structuredClone(defaults) });
const { executeWorkflowForTest } = await import("../src/controller.js");
const { createActionRegistry } = await import("../src/actions/registry.js");
const { createControllerFanoutRuntime } = await import("../src/platform/controller-fanout-runtime.js");
const { DurableFanoutTaskFlow } = await import("../src/platform/fanout-taskflow.js");
const { FanoutRuntimeError } = await import("../src/executors/subworkflow-fanout-types.js");
const copy = <T>(value: T): T => structuredClone(value);
const terminal = new Set(["succeeded", "failed", "cancelled", "timed_out", "completed"]);

type Write = { childFlowId: string; expected: number; status: string; state: Record<string, unknown> };

/** Explicit test persistence host: real CAS/terminal fences, controllable I/O faults. */
class SafetyStore implements FanoutPersistence {
  records = new Map<string, FanoutRunRecord>();
  committed: Array<Write & { priorStarted: unknown }> = [];
  beforeCheckpoint?: (write: Write) => Promise<void> | void;
  afterCheckpoint?: (write: Write) => Promise<void> | void;
  groupSaveError: Error | null = null;
  async readGroup() { return { group: null, parentStatus: "running" }; }
  async saveGroup(): Promise<void> { if (this.groupSaveError) throw this.groupSaveError; }
  async readChild(_parent: string, id: string) { return copy(this.records.get(id) ?? null); }
  async startChild(_parent: string, request: FanoutChildRequest, stateJson: string) {
    const existing = this.records.get(request.childFlowId);
    if (existing) return { created: false, run: copy(existing) };
    const run = { flowId: request.childFlowId, workflowId: request.childWorkflow.id,
      sessionKey: request.sessionKey, status: "running", revision: 1, stateJson };
    this.records.set(run.flowId, copy(run));
    return { created: true, run: copy(run) };
  }
  async checkpointChild(_parent: string, id: string, expected: number, stateJson: string, status: string) {
    const write = { childFlowId: id, expected, status, state: JSON.parse(stateJson) as Record<string, unknown> };
    await this.beforeCheckpoint?.(write);
    // Re-read AFTER I/O suspension so this actually arbitrates competing writers.
    const prior = this.records.get(id);
    if (!prior || prior.revision !== expected) throw new Error("Child revision conflict");
    if (terminal.has(prior.status)) throw new Error("Child is terminal");
    const oldState = JSON.parse(prior.stateJson);
    if (oldState._fanoutStarted === true && write.state._fanoutStarted === false) throw new Error("Claim cannot be reversed");
    const run = { ...prior, status, revision: expected + 1,
      stateJson: JSON.stringify({ ...write.state, _fanoutRevision: expected + 1 }) };
    this.records.set(id, copy(run));
    this.committed.push({ ...copy(write), priorStarted: oldState._fanoutStarted });
    await this.afterCheckpoint?.(write);
    return copy(run);
  }
}
function gate() {
  let open!: () => void;
  const promise = new Promise<void>(resolve => { open = resolve; });
  return { promise, open };
}
async function bounded<T>(promise: Promise<T>, label: string, ms = 2000): Promise<T> {
  let timer: ReturnType<typeof setTimeout>;
  try {
    return await Promise.race([promise, new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new Error(`Timed out waiting for ${label}`)), ms);
    })]);
  } finally { clearTimeout(timer!); }
}
const isUnknown = (error: unknown): boolean => error instanceof FanoutRuntimeError && error.disposition === "unknown";
function assertSuccessOrUncertainty(outcome: PromiseSettledResult<FanoutChildCompletion>, experimentId: string): void {
  if (outcome.status === "fulfilled") {
    assert.deepEqual(outcome.value, { status: "succeeded", outputs: { result: { experimentId } } });
  } else {
    assert.ok(isUnknown(outcome.reason) || (outcome.reason instanceof Error
      && /revision conflict|状态更新冲突/.test(outcome.reason.message)),
    `expected explicit uncertainty or a propagated CAS conflict, got ${String(outcome.reason)}`);
  }
}
function legacyForbidden(): ControllerDeps["boundTaskFlow"] {
  const unexpected = async (): Promise<never> => { throw new Error("Managed child must not use legacy TaskFlow fallback"); };
  return { createManaged: unexpected, setWaiting: unexpected, resume: unexpected, finish: unexpected,
    fail: unexpected, list: unexpected, get: unexpected, findLatest: unexpected, runTask: unexpected };
}

async function fixture(run: (f: Awaited<ReturnType<typeof createFixture>>) => Promise<void>): Promise<void> {
  const f = await createFixture();
  try { await run(f); } finally { await f.cleanup(); }
}
async function createFixture() {
  const root = await mkdtemp(join(tmpdir(), "fanout-safety-"));
  const store = new SafetyStore();
  const suffix = randomUUID();
  const parentFlowId = `parent-${suffix}`;
  const childFlowId = `child-${suffix}`;
  const request: FanoutChildRequest = {
    parentFlowId, parentNodeId: "fanout", groupExecutionId: `group-${suffix}`,
    itemId: "scene/partition", itemIndex: 0, attemptNumber: 1, childFlowId,
    sessionKey: `fanout:${childFlowId}`, stateDirRef: `state:${childFlowId}`, params: { scene: "scene" }, depth: 1,
    childWorkflow: { id: "safety-child", title: "Safety child", version: 1,
      // The explicitly injected test host executes this external-action boundary;
      // no OS command or user configuration is used by these tests.
      nodes: [{ id: "finalize", title: "Finalize external action", phase: "P1", dependsOn: [], executor: { type: "cli-script", command: "test-host-only" } }],
      outputs: { result: { from: "nodeOutput.finalize" } } },
  };
  const parentState: FlowState = { workflowId: "parent", workflowVersion: 7, params: {}, executionMode: "private",
    businessStatus: "RUNNING", currentPhase: "P5", activeNodes: [], nodeStates: {}, workflowData: {},
    actionOutputs: {}, flowHooks: {}, auditLog: [], runInitiator: { id: "test-user", source: "delivery-context" } };
  const deps: ControllerDeps = { actionRegistry: createActionRegistry(), boundTaskFlow: legacyForbidden(),
    chatInject: async () => {}, executeNode: async () => { throw new Error("Parent executor must not run child actions"); },
    sessionKey: `parent-session-${suffix}`, skillRoot: root, workflowLogDir: root, user: { id: "test-user" } };
  const controls = { dispatches: 0,
    action: async (_signal: AbortSignal, _nodeId?: string): Promise<ExecutorResult> => ({ status: "succeeded", result: { experimentId: "exp-safe" } }) };
  const gates: ReturnType<typeof gate>[] = [];
  const controllers: AbortController[] = [];
  const pending: Promise<unknown>[] = [];
  const newGate = () => { const next = gate(); gates.push(next); return next; };
  const newController = () => { const next = new AbortController(); controllers.push(next); return next; };
  const makeRuntime = () => createControllerFanoutRuntime({ deps, parentState, parentFlowId, store,
    executeWorkflow: executeWorkflowForTest, pollIntervalMs: 2,
    authorizeChild: async candidate => { assert.deepEqual(candidate, request); },
    createChildExecutor: ({ signal, sessionKey }) => async (node, ctx) => {
      assert.equal(sessionKey, request.sessionKey);
      assert.equal((ctx.initiator as { id: string }).id, "test-user");
      controls.dispatches++;
      return controls.action(signal, node.id);
    } });
  const runtime = makeRuntime();
  function track<T>(promise: Promise<T>): Promise<T> { pending.push(promise); void promise.catch(() => undefined); return promise; }
  function start(target = runtime, signal = newController().signal) { return track(target.startOrGetChild(copy(request), signal)); }
  function row() { const value = store.records.get(childFlowId); assert.ok(value, "child row must exist"); return copy(value); }
  function state() { return JSON.parse(row().stateJson) as FlowState & Record<string, unknown>; }
  function seedAdapter() {
    const seeded = { ...parentState, workflowId: request.childWorkflow.id, _fanoutRevision: 1, _fanoutStarted: true,
      subworkflowMeta: { parentFlowId, parentNodeId: "fanout", depth: 1 }, fanoutRuntimeContext: { stateDir: root },
      workflowData: { durable: "original" } };
    const record = { flowId: childFlowId, workflowId: request.childWorkflow.id, sessionKey: request.sessionKey,
      revision: 1, status: "running", stateJson: JSON.stringify(seeded) };
    store.records.set(childFlowId, copy(record));
    return new DurableFanoutTaskFlow(store, parentFlowId, record, deps.boundTaskFlow);
  }
  async function cleanup() {
    store.beforeCheckpoint = undefined; store.afterCheckpoint = undefined;
    for (const controller of controllers) controller.abort();
    for (const hold of gates) hold.open();
    try { await bounded(Promise.allSettled(pending), "fixture shutdown", 500); }
    finally { await rm(root, { recursive: true, force: true }); }
  }
  return { runtime, makeRuntime, start, store, request, parentState, controls, newGate, newController,
    row, state, seedAdapter, cleanup, track };
}

test("safety: concurrent startOrGetChild callers execute a stable child only once", async () => {
  await fixture(async f => {
    const bothClaims = f.newGate(); const actionEntered = f.newGate(); const releaseAction = f.newGate(); let claims = 0;
    f.store.beforeCheckpoint = async write => {
      if (write.expected === 1 && write.state._fanoutStarted === true) {
        if (++claims === 2) bothClaims.open();
        await bothClaims.promise;
      }
    };
    f.controls.action = async () => { actionEntered.open(); await releaseAction.promise;
      return { status: "succeeded", result: { experimentId: "one-actual-action" } }; };
    const first = f.start(); const second = f.start(f.makeRuntime());
    await bounded(actionEntered.promise, "winning claim to execute");
    assert.equal(f.controls.dispatches, 1);
    releaseAction.open();
    const results = await bounded(Promise.all([first, second]), "both callers to observe completion");
    assert.deepEqual(results, [
      { status: "succeeded", outputs: { result: { experimentId: "one-actual-action" } } },
      { status: "succeeded", outputs: { result: { experimentId: "one-actual-action" } } },
    ]);
    assert.equal(f.store.records.size, 1);
    assert.equal(f.store.committed.filter(w => w.priorStarted === false && w.state._fanoutStarted === true).length, 1);
    assert.equal(f.controls.dispatches, 1);
  });
});

test("safety: a rejected durable execution claim cannot execute a node", async () => {
  await fixture(async f => {
    f.store.beforeCheckpoint = write => { if (write.expected === 1) throw new Error("claim storage unavailable"); };
    await assert.rejects(bounded(f.start(), "failed claim"), /claim storage unavailable/);
    assert.equal(f.controls.dispatches, 0);
    assert.equal(f.state()._fanoutStarted, false);
    assert.equal(f.row().revision, 1);
  });
});

test("safety: a committed claim with a lost acknowledgement is unknown, never replayed", async () => {
  await fixture(async f => {
    const claimed = f.newGate(); const abort = f.newController();
    f.store.afterCheckpoint = write => { if (write.expected === 1) { claimed.open(); throw new Error("claim acknowledgement lost"); } };
    const pending = f.start(f.runtime, abort.signal);
    await bounded(claimed.promise, "durable claim"); abort.abort();
    await assert.rejects(bounded(pending, "claim reconciliation"), isUnknown);
    assert.equal(f.state()._fanoutStarted, true);
    assert.equal(f.controls.dispatches, 0);
    await assert.rejects(f.start(f.makeRuntime(), abort.signal), isUnknown);
    assert.equal(f.controls.dispatches, 0);
  });
});

for (const category of ["business", "execution"] as const) {
  test(`safety: confirmed ${category} fanoutError survives the real controller failure path`, async () => {
    await fixture(async f => {
      f.controls.action = async () => ({ status: "failed", error: "confirmed action outcome",
        result: { fanoutError: { category, code: "CONFIRMED_FAILURE", message: "action definitely not accepted", acceptanceKnown: true } } });
      const completion = await bounded(f.start(), "confirmed failure");
      assert.equal(completion.status, "failed");
      assert.deepEqual(completion.error, { category, code: "CONFIRMED_FAILURE", message: "action definitely not accepted",
        retryable: false, sourceNodeId: "finalize" });
      assert.equal(f.row().status, "failed");
      assert.deepEqual(await f.start(f.makeRuntime()), completion);
      assert.equal(f.controls.dispatches, 1);
    });
  });
}

for (const [label, explicit] of [
  ["absent structured evidence", undefined],
  ["unconfirmed acceptance", { category: "execution", code: "EXIT_1", message: "script exited", acceptanceKnown: false }],
  ["missing acceptance flag", { category: "business", code: "NO_PROOF", message: "unknown action" }],
  ["wrong confirmation type", { category: "execution", code: "NO_PROOF", message: "unknown action", acceptanceKnown: "true" }],
  ["authorization failure", { category: "authorization", code: "DENIED", message: "permission denied", acceptanceKnown: true }],
] as const) {
  test(`safety: ${label} remains unknown on recovery without replaying the action`, async () => {
    await fixture(async f => {
      f.controls.action = async () => ({ status: "failed", error: "action acceptance unknown",
        ...(explicit ? { result: { fanoutError: explicit } } : {}) });
      await assert.rejects(bounded(f.start(), "unconfirmed failure"), isUnknown);
      assert.equal(f.row().status, "failed");
      await assert.rejects(f.start(f.makeRuntime()), isUnknown);
      assert.equal(f.controls.dispatches, 1);
    });
  });
}

for (const boundary of ["before node action", "node result", "terminal success"] as const) {
  test(`safety: failed persistence at ${boundary} cannot return a successful completion`, async () => {
    await fixture(async f => {
      let faulted = false;
      f.store.beforeCheckpoint = write => {
        const results = write.state._fanoutNodeResults as Record<string, unknown> | undefined;
        const shouldFail = boundary === "before node action" ? write.expected > 1
          : boundary === "node result" ? !!results?.finalize : write.status === "succeeded";
        if (shouldFail) { faulted = true; throw new Error(`durable failure: ${boundary}`); }
      };
      const abort = f.newController(); const failureSeen = f.newGate();
      const originalHook = f.store.beforeCheckpoint;
      f.store.beforeCheckpoint = write => {
        try { return originalHook!(write); } catch (error) { failureSeen.open(); throw error; }
      };
      const execution = f.start(f.runtime, abort.signal);
      await bounded(failureSeen.promise, "injected persistence failure");
      // Unconfirmed failed closes may remain observable rather than propagate the
      // exact storage exception. Either outcome must reject, not return success.
      const fallbackAbort = setTimeout(() => abort.abort(), 30);
      try {
        await assert.rejects(bounded(execution, "persistence failure propagation"), error =>
          isUnknown(error) || (error instanceof Error && error.message.includes("durable failure:")));
      }
      finally { clearTimeout(fallbackAbort); }
      assert.ok(faulted); assert.notEqual(f.row().status, "succeeded");
      if (boundary === "before node action") assert.equal(f.controls.dispatches, 0);
      else assert.equal(f.controls.dispatches, 1);
    });
  });
}

test("safety: post-side-effect checkpoint failure does not retry the external action", async () => {
  await fixture(async f => {
    f.request.childWorkflow.nodes[0].retry = { maxAttempts: 2, backoffMs: 0, on: ["executor-failed"] };
    const checkpointFailed = f.newGate();
    let failedOnce = false;
    f.store.beforeCheckpoint = write => {
      if (write.state._fanoutNodeResults?.finalize && !failedOnce) {
        failedOnce = true;
        checkpointFailed.open();
        throw new Error("result checkpoint unavailable");
      }
    };
    const abort = f.newController();
    const execution = f.start(f.runtime, abort.signal);
    await bounded(checkpointFailed.promise, "post-side-effect checkpoint failure");
    await delay(20);
    assert.equal(f.controls.dispatches, 1);
    abort.abort();
    await assert.rejects(bounded(execution, "uncertain child reconciliation"), isUnknown);
    assert.equal(f.controls.dispatches, 1);
    assert.notEqual(f.row().status, "succeeded");
  });
});

test("safety: one unknown failed node prevents confirmed child convergence", async () => {
  await fixture(async f => {
    f.request.childWorkflow.nodes = [
      { id: "confirmed", phase: "P1", dependsOn: [], parallel: true, executor: { type: "cli-script", command: "confirmed" } },
      { id: "unknown", phase: "P1", dependsOn: [], parallel: true, executor: { type: "cli-script", command: "unknown" } },
    ];
    f.request.childWorkflow.outputs = { result: { from: "nodeOutput.confirmed" } };
    f.controls.action = async (_signal, nodeId) => nodeId === "confirmed"
      ? { status: "failed", error: "confirmed failure", result: { fanoutError: { category: "execution", code: "CONFIRMED", message: "not accepted", acceptanceKnown: true } } }
      : { status: "failed", error: "acceptance unknown" };

    await assert.rejects(bounded(f.start(), "unknown child failure reconciliation"), isUnknown);
    assert.equal(f.controls.dispatches, 2);
    assert.equal(f.row().status, "failed");
  });
});

test("safety: failed group save does not update the in-memory parent mirror", async () => {
  await fixture(async f => {
    f.store.groupSaveError = new Error("group commit failed");
    const group: FanoutState = {
      schemaVersion: "fanout.v1", kind: "subworkflowFanoutState", parentFlowId: f.request.parentFlowId,
      groupNodeId: "fanout", groupExecutionId: f.request.groupExecutionId, revision: 0, status: "running",
      createdAt: new Date().toISOString(), deadlineAt: new Date(Date.now() + 5000).toISOString(),
      sealedAt: null, cancelRequestedAt: null, cancelDeadlineAt: null,
      config: { schemaVersion: "fanout.v1", items: [], itemIdFrom: ["id"], concurrency: 1,
        successPolicy: "all_done", finishMode: "wait_all", resultPolicy: "collect_all",
        groupTimeoutSeconds: 5, cancelGraceSeconds: 1 },
      childWorkflow: copy(f.request.childWorkflow), childPublishedVersion: 1,
      inputDigest: `sha256:${"0".repeat(64)}`, children: [], error: null, cleanupPendingChildIds: [], result: null,
    };
    await assert.rejects(f.runtime.save(group, null), /group commit failed/);
    assert.equal(f.parentState.subworkflowFanouts, undefined);
    assert.equal(f.controls.dispatches, 0);
  });
});

test("safety: accepting cancellation does not claim an in-flight action has stopped", async () => {
  await fixture(async f => {
    const entered = f.newGate(); const releaseAction = f.newGate();
    f.controls.action = async () => { entered.open(); await releaseAction.promise;
      return { status: "succeeded", result: { experimentId: "late-success" } }; };
    let settled = false;
    const execution = f.start(); void execution.then(() => { settled = true; }, () => { settled = true; });
    await bounded(entered.promise, "in-flight action");
    await f.runtime.cancelChild(f.request);
    assert.notEqual(f.row().status, "cancelled");
    assert.equal(f.state()._fanoutCancelRequested, true);
    await delay(5); assert.equal(settled, false);
    releaseAction.open();
    const outcome = await bounded(Promise.allSettled([execution]), "completion after cancellation");
    assertSuccessOrUncertainty(outcome[0], "late-success");
  });
});

test("safety: cancellation racing an already committed success preserves the success fact", async () => {
  await fixture(async f => {
    const committed = f.newGate(); const acknowledge = f.newGate();
    f.store.afterCheckpoint = async write => { if (write.status === "succeeded") { committed.open(); await acknowledge.promise; } };
    const execution = f.start();
    await bounded(committed.promise, "durable success before response");
    await f.runtime.cancelChild(f.request);
    assert.equal(f.row().status, "succeeded");
    acknowledge.open();
    const completion = await bounded(execution, "success acknowledgement");
    assert.equal(completion.status, "succeeded");
    assert.deepEqual(completion.outputs, { result: { experimentId: "exp-safe" } });
  });
});

test("safety: a failed cancellation write rejects without claiming confirmed termination", async () => {
  await fixture(async f => {
    const entered = f.newGate(); const releaseAction = f.newGate();
    f.controls.action = async () => { entered.open(); await releaseAction.promise;
      return { status: "succeeded", result: { experimentId: "survived-cancel" } }; };
    const execution = f.start(); await bounded(entered.promise, "in-flight action");
    f.store.beforeCheckpoint = write => { if (write.state._fanoutCancelRequested) throw new Error("cancel write failed"); };
    await assert.rejects(f.runtime.cancelChild(f.request), /cancel write failed/);
    assert.notEqual(f.row().status, "cancelled");
    assert.notEqual(f.state()._fanoutCancelRequested, true);
    f.store.beforeCheckpoint = undefined; releaseAction.open();
    const outcome = await bounded(Promise.allSettled([execution]), "action after cancellation write failure");
    assertSuccessOrUncertainty(outcome[0], "survived-cancel");
  });
});

test("safety: aborting a waiting remote action does not fabricate a cancelled child", async () => {
  await fixture(async f => {
    const entered = f.newGate(); const abort = f.newController();
    f.controls.action = async signal => {
      entered.open();
      if (!signal.aborted) await new Promise<void>(resolve => signal.addEventListener("abort", () => resolve(), { once: true }));
      // Real controller waiting path: remote acceptance is unresolved, not a
      // terminal cancellation acknowledgement. No remote stop confirmation exists.
      return { status: "waiting", waitConfig: { waitKind: "remote-reconciliation", prompt: "Remote action still unresolved" } };
    };
    const execution = f.start(f.runtime, abort.signal);
    await bounded(entered.promise, "remote action to be accepted"); abort.abort();
    await assert.rejects(bounded(execution, "unconfirmed cancellation"), isUnknown);
    assert.notEqual(f.row().status, "cancelled");
  });
});

test("safety: TaskFlow persistence failure leaves both durable and controller revisions unchanged", async () => {
  await fixture(async f => {
    const adapter = f.seedAdapter();
    f.store.beforeCheckpoint = () => { throw new Error("taskflow disk failure"); };
    await assert.rejects(adapter.finish({ flowId: f.request.childFlowId, expectedRevision: 1, stateJson: '{"workflowData":{"uncommitted":true}}' }), /taskflow disk failure/);
    assert.equal(f.row().revision, 1); assert.equal(f.row().status, "running");
    assert.deepEqual(f.state().workflowData, { durable: "original" });
    assert.equal((await adapter.get(f.request.childFlowId))?.revision, 1);
    f.store.beforeCheckpoint = undefined;
    await adapter.finish({ flowId: f.request.childFlowId, expectedRevision: 1, stateJson: '{"workflowData":{"recovered":true}}' });
    assert.equal(f.row().status, "succeeded"); assert.equal(f.row().revision, 2);
    assert.deepEqual(f.state().workflowData, { recovered: true });
  });
});

test("safety: concurrent TaskFlow transitions cannot both consume the same controller revision", async () => {
  await fixture(async f => {
    const adapter = f.seedAdapter();
    const outcomes = await Promise.allSettled([
      adapter.resume({ flowId: f.request.childFlowId, expectedRevision: 1, stateJson: '{"workflowData":{"winner":1}}' }),
      adapter.finish({ flowId: f.request.childFlowId, expectedRevision: 1, stateJson: '{"workflowData":{"loser":2}}' }),
    ]);
    assert.equal(outcomes[0].status, "fulfilled"); assert.equal(outcomes[1].status, "rejected");
    assert.equal(f.row().revision, 2); assert.equal(f.row().status, "running");
    assert.deepEqual(f.state().workflowData, { winner: 1 });
  });
});

test("safety: a stale completion writer cannot overwrite a durable terminal cancellation", async () => {
  await fixture(async f => {
    const stale = f.seedAdapter();
    const cancelling = new DurableFanoutTaskFlow(f.store, f.request.parentFlowId, f.row(), legacyForbidden());
    await cancelling.fail({ flowId: f.request.childFlowId, expectedRevision: 1, status: "cancelled", stateJson: JSON.stringify(f.state()) });
    const sealed = f.row();
    await assert.rejects(stale.finish({ flowId: f.request.childFlowId, expectedRevision: 1, stateJson: JSON.stringify(f.state()) }), /conflict|terminal/);
    assert.deepEqual(f.row(), sealed);
    assert.equal((await stale.get(f.request.childFlowId))?.status, "cancelled");
  });
});

test("safety: cancellation intent survives refresh after a racing stale checkpoint", async () => {
  await fixture(async f => {
    const adapter = f.seedAdapter(); const before = f.row();
    await f.store.checkpointChild(f.request.parentFlowId, f.request.childFlowId, before.revision,
      JSON.stringify({ ...f.state(), _fanoutCancelRequested: true }), "running");
    await assert.rejects(adapter.checkpoint({ workflowData: { stale: true } }), /conflict/);
    await adapter.get(f.request.childFlowId);
    await adapter.checkpoint({ workflowData: { refreshed: true } });
    assert.equal(f.state()._fanoutCancelRequested, true);
    assert.equal(f.row().status, "running", "a cancellation request is not terminal proof");
    assert.deepEqual(f.state().workflowData, { refreshed: true });
  });
});
