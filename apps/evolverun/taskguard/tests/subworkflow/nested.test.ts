import assert from "node:assert/strict";
import { after, test } from "node:test";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { executeWorkflowForTest, setEventRepository, type ControllerDeps } from "../../src/controller.js";
import { buildChildFlowState, type SubworkflowDeps } from "../../src/executors/subworkflow.js";
import { createDefaultExecutorDispatch } from "../../src/platform/default-executor.js";
import { createExecutorDispatch } from "../../src/index.js";
import type { FlowEvent, FlowState, WorkflowSpec } from "../../src/types.js";

const oldHome = process.env.HOME;
const home = mkdtempSync(join(tmpdir(), "taskguard-nested-"));
process.env.HOME = home;
after(async () => {
  await new Promise((resolve) => setTimeout(resolve, 50));
  if (oldHome === undefined) delete process.env.HOME;
  else process.env.HOME = oldHome;
  rmSync(home, { recursive: true, force: true });
});

const node = (id: string, workflowId: string) => ({
  id, title: id, phase: "P1", dependsOn: [], executor: { type: "subworkflow", workflowId },
});
const workflow = (id: string, nodes: unknown[]) => ({ id, title: id, version: "1", nodes }) as WorkflowSpec;
const A = workflow("A", [node("a-to-b", "B")]);
const B = workflow("B", [node("b-to-c", "C")]);
const C = workflow("C", []);
const catalog = [B, C].map((spec) => ({
  id: spec.id, spec, digest: "", absolutePath: "", source: { kind: "workspace-pack" as const, root: "" },
  pack: { id: "pack", version: "1", root: "", digest: "" },
}));

function initialState(): FlowState {
  const parent = { executionMode: "auto" } as FlowState;
  const state = buildChildFlowState(A, {}, parent, "unused", "unused", 0);
  delete state.subworkflowMeta;
  return state;
}

for (const mode of ["default", "openclaw"] as const) {
test(`parallel A→B→C runs link immediate parents with ${mode} dispatch`, { timeout: 10000 }, async () => {
  const events: FlowEvent[] = [];
  const created = new Map<string, { workflowId: string; state: FlowState }>();
  let nextId = 0;
  let bCount = 0;
  let specLookups = 0;
  let releaseB!: () => void;
  const bothB = new Promise<void>((resolve) => { releaseB = resolve; });
  setEventRepository({ insert: async (event: FlowEvent) => { events.push(event); } } as never);
  const actionRegistry = {} as ControllerDeps["actionRegistry"];
  const deps = {
    actionRegistry,
    resolvedWorkflows: catalog,
    workflowSpecApiRepo: { findByWorkflowId: async () => { specLookups++; return null; } },
    executeNode: mode === "default"
      ? createDefaultExecutorDispatch({ sessionKey: "test", actionRegistry })
      : createExecutorDispatch({} as never, "test", actionRegistry),
    chatInject: async () => {},
    chatInjectLevel: "none",
    boundTaskFlow: {
      createManaged: async (params: Record<string, unknown>) => {
        const id = `child-${++nextId}`;
        const state = JSON.parse(String(params.stateJson)) as FlowState;
        created.set(id, { workflowId: String(params.workflowId), state });
        if (params.workflowId === "B") {
          if (++bCount === 2) releaseB();
          await bothB;
        }
        return { flowId: id, revision: 1 };
      },
      resume: async (params: Record<string, unknown>) => ({ applied: true, flow: { revision: Number(params.expectedRevision) + 1 } }),
      setWaiting: async (params: Record<string, unknown>) => ({ applied: true, flow: { revision: Number(params.expectedRevision) + 1 } }),
      finish: async () => ({ applied: true }),
      fail: async () => ({ applied: true }),
    },
  } as unknown as ControllerDeps;
  const root1 = initialState();
  const root2 = initialState();
  const debug = mode === "openclaw";
  try {
    await Promise.all([
      executeWorkflowForTest({ deps, workflow: A, state: root1, flowId: "root-1", revision: 1, debug }),
      executeWorkflowForTest({ deps, workflow: A, state: root2, flowId: "root-2", revision: 1, debug }),
    ]);
    assert.equal(root1.nodeStates["a-to-b"].status, "succeeded");
    assert.equal(root2.nodeStates["a-to-b"].status, "succeeded");
    assert.equal(deps.subworkflowDeps, undefined, "automatic binding must not mutate shared deps");
    assert.equal(specLookups, debug ? 0 : 4, "debug pack-only resolution must propagate to nested children");
    const links = events.filter((event) => event.type === "subworkflow_linked");
    assert.equal(links.length, 4);
    for (const rootId of ["root-1", "root-2"]) {
      const rootLink = links.find((event) => event.flowId === rootId);
      assert.ok(rootLink);
      assert.equal(rootLink.nodeId, "a-to-b");
      assert.equal(rootLink.workflowId, "A");
      assert.equal(rootLink.data?.childWorkflowId, "B");
      assert.equal(rootLink.data?.depth, 1);
      const bId = String(rootLink.data?.childFlowId);
      assert.deepEqual(created.get(bId)?.state.subworkflowMeta, { parentFlowId: rootId, parentNodeId: "a-to-b", depth: 1 });
      const bLink = links.find((event) => event.flowId === bId);
      assert.ok(bLink, `missing B→C event on ${bId}`);
      assert.equal(bLink.workflowId, "B");
      assert.equal(bLink.nodeId, "b-to-c");
      assert.equal(bLink.data?.childWorkflowId, "C");
      assert.equal(bLink.data?.depth, 2);
      const cId = String(bLink.data?.childFlowId);
      assert.deepEqual(created.get(cId)?.state.subworkflowMeta, { parentFlowId: bId, parentNodeId: "b-to-c", depth: 2 });
      assert.equal(created.get(cId)?.workflowId, "C");
    }
  } finally {
    releaseB();
    setEventRepository(null);
  }
});
}

test("explicit caller-supplied subworkflow dependencies remain authoritative", async () => {
  const calls: Array<{ parentFlowId: string; parentNodeId: string; depth: number }> = [];
  const injected: SubworkflowDeps = {
    resolvedWorkflows: catalog,
    executeChildWorkflow: async ({ parentFlowId, parentNodeId, depth }) => {
      calls.push({ parentFlowId, parentNodeId, depth });
      return { status: "succeeded", workflowData: {} };
    },
  };
  const actionRegistry = {} as ControllerDeps["actionRegistry"];
  const deps = {
    actionRegistry, resolvedWorkflows: catalog, subworkflowDeps: injected,
    executeNode: createDefaultExecutorDispatch({ sessionKey: "test", actionRegistry }),
    chatInject: async () => {}, chatInjectLevel: "none",
    boundTaskFlow: {
      resume: async (params: Record<string, unknown>) => ({ applied: true, flow: { revision: Number(params.expectedRevision) + 1 } }),
      finish: async () => ({ applied: true }),
    },
  } as unknown as ControllerDeps;
  const state = initialState();
  await executeWorkflowForTest({ deps, workflow: A, state, flowId: "injected-root", revision: 1 });
  assert.equal(state.nodeStates["a-to-b"].status, "succeeded");
  assert.equal(deps.subworkflowDeps, injected);
  assert.deepEqual(calls, [{ parentFlowId: "injected-root", parentNodeId: "a-to-b", depth: 1 }]);
});


for (const mode of ["default", "openclaw"] as const) {
test(`${mode} dispatch preserves explicitly injected executor dependencies`, async () => {
  const calls: string[] = [];
  const injected: SubworkflowDeps = {
    resolvedWorkflows: catalog,
    executeChildWorkflow: async ({ parentFlowId }) => {
      calls.push(parentFlowId);
      return { status: "succeeded", workflowData: {} };
    },
  };
  const actionRegistry = {} as ControllerDeps["actionRegistry"];
  const deps = {
    actionRegistry, resolvedWorkflows: catalog,
    executeNode: mode === "default"
      ? createDefaultExecutorDispatch({ sessionKey: "test", actionRegistry, subworkflowDeps: injected })
      : createExecutorDispatch({} as never, "test", actionRegistry, undefined, undefined, undefined, undefined, () => injected),
    chatInject: async () => {}, chatInjectLevel: "none",
    boundTaskFlow: {
      resume: async (params: Record<string, unknown>) => ({ applied: true, flow: { revision: Number(params.expectedRevision) + 1 } }),
      finish: async () => ({ applied: true }),
    },
  } as unknown as ControllerDeps;
  const state = initialState();
  await executeWorkflowForTest({ deps, workflow: A, state, flowId: "option-root", revision: 1 });
  assert.equal(state.nodeStates["a-to-b"].status, "succeeded");
  assert.deepEqual(calls, ["option-root"]);
});
}
