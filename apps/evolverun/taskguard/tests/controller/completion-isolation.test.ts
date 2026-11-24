import assert from "node:assert/strict";
import { test } from "node:test";
import {
  abortAsyncExecutionForFlow,
  reapStaleRunningFlows,
  setFlowRunRepository,
  setNodeExecutionRepository,
  setEventRepository,
  setRunLogUploader,
} from "../../src/controller.js";
import type { IFlowRunRepository, INodeExecutionRepository } from "../../src/db/repositories/types.js";
import { deferred, fixture, until } from "./fixtures.js";

function nodeRepository(reconcile: INodeExecutionRepository["reconcileStaleRunning"]): INodeExecutionRepository {
  return {
    insert: async () => ({ insertId: 1, affectedRows: 1 }),
    updateCompletionByFlowNode: async () => true,
    reconcileStaleRunning: reconcile,
  } as unknown as INodeExecutionRepository;
}

for (const switchAt of ["before sweep", "pending CAS"]) {
  test(`timeout queries and completes only its launch repository when targets switch ${switchAt}`, async (t) => {
    const f = fixture(t);
    const originReconcile: string[] = [];
    const foreignWrites: string[] = [];
    setNodeExecutionRepository(nodeRepository(async (id) => { originReconcile.push(id); return 1; }));
    t.after(() => setNodeExecutionRepository(null));
    const gate = f.hold();
    const handle = f.launch();
    await until(() => f.calls.execute.length === 1);
    f.calls.lookups.length = 0;
    const foreignRow = { ...f.rows.get(f.flowId)! };
    const foreign = {
      findByFlowId: async () => { foreignWrites.push("lookup"); return foreignRow; },
      updateCompletion: async (_id, value) => { foreignWrites.push("completion"); Object.assign(foreignRow, value); return true; },
    } as unknown as IFlowRunRepository;
    const switchTargets = () => {
      setFlowRunRepository(foreign);
      setNodeExecutionRepository(nodeRepository(async () => { foreignWrites.push("reconcile"); return 1; }));
    };
    const casGate = deferred<void>();
    const fail = f.deps.boundTaskFlow.fail;
    let casPending = false;
    f.deps.boundTaskFlow.fail = async (params) => {
      casPending = true;
      await casGate.promise;
      return fail(params);
    };
    if (switchAt === "before sweep") switchTargets();
    const sweep = reapStaleRunningFlows(1);
    try {
      await until(() => casPending);
      if (switchAt === "pending CAS") switchTargets();
      casGate.resolve();
      assert.equal(await sweep, 1);
      await until(() => originReconcile.length === 1);
      assert.deepEqual(f.calls.lookups, [f.flowId]);
      assert.equal(f.calls.completion.length, 1);
      assert.equal(f.rows.get(f.flowId)!.status, "failed");
      assert.equal(foreignRow.status, "running");
      assert.deepEqual(foreignWrites, []);
      assert.deepEqual(originReconcile, [f.flowId]);
      assert.equal(f.calls.inject.some(({ key }) => key.endsWith(":flow:failed")), false, "unsafe ancillary notification must be suppressed after rebinding");
    } finally {
      casGate.resolve();
      await sweep;
      gate.resolve({ status: "succeeded", result: { drained: true } });
      await handle.promise;
    }
  });
}

test("a watchdog continues to use its owned repository after the global repository is cleared", async (t) => {
  const f = fixture(t);
  f.hold();
  f.launch();
  await until(() => f.calls.execute.length === 1);
  f.calls.lookups.length = 0;
  setFlowRunRepository(null);
  assert.equal(await reapStaleRunningFlows(1), 1);
  assert.deepEqual(f.calls.lookups, [f.flowId]);
  assert.equal(f.calls.completion.length, 1);
});

test("terminal node reconciliation retries keep the original repository after a later rebind", async (t) => {
  const f = fixture(t);
  let originCalls = 0;
  let foreignCalls = 0;
  setNodeExecutionRepository(nodeRepository(async () => {
    originCalls += 1;
    if (originCalls === 1) throw new Error("transient reconciliation failure");
    return 1;
  }));
  t.after(() => setNodeExecutionRepository(null));
  f.hold();
  f.launch();
  await until(() => f.calls.execute.length === 1);
  assert.equal(await reapStaleRunningFlows(1), 1);
  assert.equal(originCalls, 1);
  setNodeExecutionRepository(nodeRepository(async () => { foreignCalls += 1; return 1; }));
  await until(() => originCalls + foreignCalls >= 2);
  assert.equal(originCalls, 2);
  assert.equal(foreignCalls, 0);
});

for (const errorAt of ["get rejects", "fail rejects", "CAS false", "CAS unconfirmed"]) {
  test(`parent cancellation does not publish terminal state when ${errorAt}`, async (t) => {
    const f = fixture(t);
    const upstream = new AbortController();
    f.deps.abortSignal = upstream.signal;
    const gate = f.hold();
    const handle = f.launch();
    await until(() => f.calls.execute.length === 1);
    const errors: unknown[][] = [];
    const warnings: unknown[][] = [];
    t.mock.method(console, "error", (...args: unknown[]) => { errors.push(args); });
    t.mock.method(console, "warn", (...args: unknown[]) => { warnings.push(args); });
    if (errorAt === "get rejects") f.deps.boundTaskFlow.get = async () => { throw new Error("read unavailable"); };
    if (errorAt === "fail rejects") f.deps.boundTaskFlow.fail = async () => { throw new Error("CAS unavailable"); };
    if (errorAt === "CAS false") f.control.failApplied = false;
    if (errorAt === "CAS unconfirmed") f.deps.boundTaskFlow.fail = async () => undefined;
    f.calls.inject.length = 0;
    upstream.abort(new Error("parent flow cancelled"));
    gate.resolve({ status: "succeeded", result: { late: true } });
    await handle.promise;
    assert.equal(f.records.get(f.flowId)!.status, "running", "failed persistence must not fabricate a terminal TaskFlow state");
    assert.equal(f.rows.get(f.flowId)!.status, "running");
    assert.equal(f.calls.completion.length, 0);
    assert.equal(f.calls.inject.some(({ key }) => key.includes(":flow:")), false, "no terminal user notification without a confirmed CAS");
    assert.ok([...errors, ...warnings].some((args) => args.some((value) => String(value).includes(f.flowId))), "failure remains observable");
  });
}

for (const closeout of ["parent abort", "crash"]) {
  test(`${closeout} completion uses its launch repository after a target switch`, async (t) => {
    const f = fixture(t);
    const upstream = new AbortController();
    f.deps.abortSignal = upstream.signal;
    const gate = f.hold();
    // Force the wrapper crash lane through intermediate persistence.
    if (closeout === "crash") {
      f.workflow.nodes[0].executor = { type: "action", action: "work" };
      f.state.nodeStates.work.executor = "action";
      f.workflow.nodes.push({ id: "next", title: "Next", phase: "P1", dependsOn: ["work"], executor: { type: "done" } });
      f.state.nodeStates.next = { status: "pending", phase: "P1", executor: "done" };
      f.deps.boundTaskFlow.resume = async () => { throw new Error("persistence crashed"); };
    }
    const handle = f.launch();
    await until(() => f.calls.execute.length === 1);
    const foreignWrites: string[] = [];
    setFlowRunRepository({
      updateCompletion: async () => { foreignWrites.push("completion"); return true; },
      incrementNodeCount: async () => { foreignWrites.push("count"); return true; },
      updateCurrentPhase: async () => { foreignWrites.push("phase"); return true; },
      updateStatus: async () => { foreignWrites.push("status"); return true; },
    } as unknown as IFlowRunRepository);
    setEventRepository({ insert: async () => { foreignWrites.push("event"); return true; } } as unknown as Parameters<typeof setEventRepository>[0]);
    setRunLogUploader({ enqueue: () => { foreignWrites.push("log"); }, start() {}, shutdown() {}, flushAll: async () => {} });
    t.after(() => { setEventRepository(null); setRunLogUploader(null); });
    if (closeout === "parent abort") upstream.abort(new Error("parent flow cancelled"));
    gate.resolve({ status: "succeeded", result: { late: true } });
    await handle.promise;
    assert.equal(f.calls.completion.length, 1);
    assert.deepEqual(foreignWrites, []);
  });
}

test("normal owned execution keeps its original status reads and completion after rebinding", async (t) => {
  const f = fixture(t);
  const gate = f.hold();
  f.workflow.nodes[0].executor = { type: "action", action: "work" };
  f.state.nodeStates.work.executor = "action";
  f.workflow.nodes.push({ id: "next", title: "Next", phase: "P1", dependsOn: ["work"], executor: { type: "done" } });
  f.state.nodeStates.next = { status: "pending", phase: "P1", executor: "done" };
  const handle = f.launch();
  await until(() => f.calls.execute.length === 1);
  const foreignWrites: string[] = [];
  setFlowRunRepository({
    findByFlowId: async () => { foreignWrites.push("lookup"); return { ...f.rows.get(f.flowId)!, status: "failed" }; },
    updateCompletion: async () => { foreignWrites.push("completion"); return true; },
    incrementNodeCount: async () => { foreignWrites.push("count"); return true; },
    updateCurrentPhase: async () => { foreignWrites.push("phase"); return true; },
  } as unknown as IFlowRunRepository);
  gate.resolve({ status: "succeeded", result: { ok: true } });
  await handle.promise;
  assert.equal(f.calls.execute.length, 2);
  assert.equal(f.calls.completion.length, 1);
  assert.equal(f.rows.get(f.flowId)!.status, "succeeded");
  assert.deepEqual(foreignWrites, []);
});
