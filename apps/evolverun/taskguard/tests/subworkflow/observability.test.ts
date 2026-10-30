import assert from "node:assert/strict";
import { after, test } from "node:test";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  executeSubworkflowChildForTest,
  setEventRepository,
  setFlowRunRepository,
} from "../../src/controller.js";
import { buildWorkflowLogRecord } from "../../src/workflow-log.js";
import type { ControllerDeps } from "../../src/controller.js";
import type { FlowState, WorkflowSpec } from "../../src/types.js";

// Controller logging is fire-and-forget; keep test output out of the user's logs.
const originalHome = process.env.HOME;
const testHome = mkdtempSync(join(tmpdir(), "taskguard-subworkflow-"));
process.env.HOME = testHome;
after(async () => {
  await new Promise((resolve) => setTimeout(resolve, 50));
  if (originalHome === undefined) delete process.env.HOME;
  else process.env.HOME = originalHome;
  rmSync(testHome, { recursive: true, force: true });
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

function parentState(): FlowState {
  return {
    workflowId: "parent-workflow", workflowVersion: "1", params: {},
    executionMode: "auto", businessStatus: "INIT", currentPhase: "P1",
    activeNodes: [], nodeStates: {}, workflowData: {}, actionOutputs: {},
    flowHooks: {}, auditLog: [],
  } as FlowState;
}

const child = { id: "child-workflow", title: "Child", version: "1", nodes: [] } as unknown as WorkflowSpec;
const params = { marker: "omitted-from-link" };

function deps(createManaged: () => Promise<Record<string, unknown>>): ControllerDeps {
  return {
    boundTaskFlow: {
      createManaged,
      finish: async () => ({ applied: true, flow: {} }),
    },
    chatInject: async () => {},
  } as unknown as ControllerDeps;
}

test("links the child on creation while it is still running and preserves terminal events", async () => {
  const create = deferred<Record<string, unknown>>();
  const lookup = deferred<null>();
  const lookupStarted = deferred<void>();
  const state = parentState();
  let insertedFlowRuns = 0;
  setFlowRunRepository({
    findByFlowId: async () => { lookupStarted.resolve(); return lookup.promise; },
    updateCurrentPhase: async () => {},
    updateCompletion: async () => {},
    insert: async () => { insertedFlowRuns++; },
  } as never);
  // Rejected event writes must not turn a successful child into a failure.
  setEventRepository({ insert: async () => { throw new Error("event store unavailable"); } } as never);
  try {
    const execution = executeSubworkflowChildForTest(deps(() => create.promise), child, params, state, "parent-node", "parent-flow", 2);
    assert.deepEqual(state.flowEvents?.map((event) => event.type), ["subworkflow_started"]);
    create.resolve({ flow_id: "child-flow", revision: 1 });
    await lookupStarted.promise;
    assert.deepEqual(state.flowEvents?.map((event) => event.type), ["subworkflow_started", "subworkflow_linked"]);
    const linked = state.flowEvents![1];
    assert.equal(linked.flowId, "parent-flow");
    assert.equal(linked.workflowId, "parent-workflow");
    assert.equal(linked.nodeId, "parent-node");
    assert.deepEqual(linked.data, { childFlowId: "child-flow", childWorkflowId: "child-workflow", depth: 2 });
    assert.equal(JSON.stringify(linked).includes(params.marker), false);
    const log = buildWorkflowLogRecord({ event: linked });
    assert.equal(log.event_type, "subworkflow_linked");
    assert.deepEqual(log.details.data, linked.data);
    lookup.resolve(null);
    const result = await execution;
    assert.equal(result.status, "succeeded");
    assert.equal(insertedFlowRuns, 0);
    assert.deepEqual(state.flowEvents?.map((event) => event.type), ["subworkflow_started", "subworkflow_linked", "subworkflow_finished"]);
    assert.deepEqual(state.flowEvents![2].data, { childFlowId: "child-flow", childWorkflowId: "child-workflow", depth: 2, status: "succeeded" });
    assert.deepEqual(state.flowEvents![0].data, { childWorkflowId: "child-workflow", depth: 2, params });
  } finally {
    lookup.resolve(null);
    setEventRepository(null);
    setFlowRunRepository(null);
  }
});

test("creation failure propagates and does not claim a child was linked or finished", async () => {
  const state = parentState();
  const failure = new Error("create failed");
  await assert.rejects(
    executeSubworkflowChildForTest(deps(async () => { throw failure; }), child, params, state, "parent-node", "parent-flow", 1),
    (error) => error === failure,
  );
  assert.deepEqual(state.flowEvents?.map((event) => event.type), ["subworkflow_started"]);
});

test("a created child that does not complete retains the failed terminal event", async () => {
  const state = parentState();
  const pendingChild = { ...child, nodes: [{ id: "pending", phase: "P1", executor: { type: "done" } }] } as WorkflowSpec;
  setFlowRunRepository({
    findByFlowId: async () => ({ status: "cancelled" }),
    updateCurrentPhase: async () => {},
  } as never);
  try {
    const result = await executeSubworkflowChildForTest(deps(async () => ({ flowId: "child-flow" })), pendingChild, params, state, "parent-node", "parent-flow", 1);
    assert.equal(result.status, "failed");
    assert.equal(result.error, "workflow did not complete");
    assert.deepEqual(state.flowEvents?.map((event) => event.type), ["subworkflow_started", "subworkflow_linked", "subworkflow_finished"]);
    assert.deepEqual(state.flowEvents![2].data, { childFlowId: "child-flow", childWorkflowId: "child-workflow", depth: 1, status: "failed" });
  } finally {
    setFlowRunRepository(null);
  }
});

test("a child execution exception retains the failed terminal event", async () => {
  const state = parentState();
  // Simulate an exception when the child execution accesses its dependencies.
  const failingDeps = deps(async () => ({ flowId: "child-flow" }));
  Object.defineProperty(failingDeps, "chatInject", { get() { throw new Error("execution crashed"); } });
  const result = await executeSubworkflowChildForTest(failingDeps, child, params, state, "parent-node", "parent-flow", 1);
  assert.equal(result.status, "failed");
  assert.equal(result.error, "execution crashed");
  assert.deepEqual(state.flowEvents?.map((event) => event.type), ["subworkflow_started", "subworkflow_linked", "subworkflow_finished"]);
  assert.deepEqual(state.flowEvents![2].data, { childFlowId: "child-flow", childWorkflowId: "child-workflow", depth: 1, status: "failed" });
  assert.equal(state.flowEvents![2].error, "execution crashed");
});
