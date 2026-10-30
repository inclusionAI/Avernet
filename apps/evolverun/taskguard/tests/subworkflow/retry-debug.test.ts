import assert from "node:assert/strict";
import { after, test } from "node:test";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { handleRetry, isAsyncExecutionActive, setEventRepository, type ControllerDeps } from "../../src/controller.js";
import { loadConfig } from "../../src/config/loader.js";
import { clearCachedConfig, getCachedConfig, setCachedConfig } from "../../src/config/config-cache.js";
import { buildChildFlowState } from "../../src/executors/subworkflow.js";
import { createDefaultExecutorDispatch } from "../../src/platform/default-executor.js";
import type { FlowEvent, FlowState, WorkflowSpec } from "../../src/types.js";

const originalHome = process.env.HOME;
const home = mkdtempSync(join(tmpdir(), "taskguard-retry-"));
process.env.HOME = home;
after(async () => {
  await new Promise((resolve) => setTimeout(resolve, 50));
  if (originalHome === undefined) delete process.env.HOME;
  else process.env.HOME = originalHome;
  rmSync(home, { recursive: true, force: true });
});

const child = { id: "child", title: "Child", version: "1", nodes: [] } as unknown as WorkflowSpec;
const workflow = {
  id: "parent", title: "Parent", version: "1", nodes: [{
    id: "sub", title: "Sub", phase: "P1", dependsOn: [], executor: { type: "subworkflow", workflowId: "child" },
  }],
} as unknown as WorkflowSpec;
const catalog = [{
  id: child.id, spec: child, digest: "", absolutePath: "",
  source: { kind: "workspace-pack" as const, root: "" },
  pack: { id: "pack", version: "1", root: "", digest: "" },
}];

for (const asyncRun of [false, true]) {
  for (const debug of [false, true]) {
    test(`retry passes debug=${debug} to child resolution (async=${asyncRun})`, { timeout: 10000 }, async () => {
      const originalConfig = getCachedConfig();
      const config = loadConfig();
      setCachedConfig({ ...config, app: { ...config.app, execution: { ...config.app.execution, asyncRun } } });
      const rootId = `retry-${asyncRun}-${debug}`;
      const root = buildChildFlowState(workflow, {}, { executionMode: "auto" } as FlowState, "unused", "unused", 0);
      delete root.subworkflowMeta;
      root.workflowSnapshot = workflow;
      root.nodeStates.sub.status = "failed";
      const events: FlowEvent[] = [];
      let lookups = 0;
      const actionRegistry = {} as ControllerDeps["actionRegistry"];
      const deps = {
        actionRegistry, resolvedWorkflows: catalog,
        workflowSpecApiRepo: { findByWorkflowId: async () => { lookups++; return null; } },
        executeNode: createDefaultExecutorDispatch({ sessionKey: rootId, actionRegistry }),
        sessionKey: rootId, chatInject: async () => {}, chatInjectLevel: "none",
        boundTaskFlow: {
          get: async () => ({ flowId: rootId, revision: 1, stateJson: JSON.stringify(root) }),
          createManaged: async () => ({ flowId: `${rootId}-child`, revision: 1 }),
          resume: async (params: Record<string, unknown>) => ({ applied: true, flow: { revision: Number(params.expectedRevision) + 1 } }),
          finish: async () => ({ applied: true }),
          fail: async () => ({ applied: true }),
        },
      } as unknown as ControllerDeps;
      setEventRepository({ insert: async (event: FlowEvent) => { events.push(event); return true; } } as never);
      try {
        await handleRetry(deps, { flowId: rootId, nodeId: "sub", debug });
        if (asyncRun) {
          const deadline = Date.now() + 5000;
          while (isAsyncExecutionActive(rootId) && Date.now() < deadline) {
            await new Promise((resolve) => setTimeout(resolve, 10));
          }
          assert.equal(isAsyncExecutionActive(rootId), false, "async retry did not finish");
        }
        assert.equal(lookups, debug ? 0 : 1);
        const linked = events.find((event) => event.type === "subworkflow_linked");
        assert.equal(linked?.flowId, rootId);
        assert.equal(linked?.nodeId, "sub");
        assert.deepEqual(linked?.data, { childFlowId: `${rootId}-child`, childWorkflowId: "child", depth: 1 });
        assert.equal(events.find((event) => event.type === "subworkflow_finished")?.data?.status, "succeeded");
      } finally {
        setEventRepository(null);
        if (originalConfig) setCachedConfig(originalConfig);
        else clearCachedConfig();
      }
    });
  }
}

test("async retry conflict redrive retains debug mode", { timeout: 10000 }, async () => {
  const oldConfig = getCachedConfig();
  const config = loadConfig();
  setCachedConfig({ ...config, app: { ...config.app, execution: { ...config.app.execution, asyncRun: true } } });
  const rootId = "retry-redrive-debug";
  const root = buildChildFlowState(workflow, {}, { executionMode: "auto" } as FlowState, "unused", "unused", 0);
  delete root.subworkflowMeta;
  root.workflowSnapshot = workflow;
  root.nodeStates.sub.status = "failed";
  let retryState = "";
  let rootFinishes = 0;
  let lookups = 0;
  let childCreates = 0;
  const events: FlowEvent[] = [];
  const actionRegistry = {} as ControllerDeps["actionRegistry"];
  const deps = {
    actionRegistry, resolvedWorkflows: catalog,
    workflowSpecApiRepo: { findByWorkflowId: async () => { lookups++; return null; } },
    executeNode: createDefaultExecutorDispatch({ sessionKey: rootId, actionRegistry }),
    sessionKey: rootId, chatInject: async () => {}, chatInjectLevel: "none",
    boundTaskFlow: {
      get: async () => retryState
        ? { flowId: rootId, revision: 2, status: "running", stateJson: retryState }
        : { flowId: rootId, revision: 1, stateJson: JSON.stringify(root) },
      createManaged: async () => ({ flowId: `${rootId}-child-${++childCreates}`, revision: 1 }),
      resume: async (params: Record<string, unknown>) => {
        if (params.flowId === rootId && !retryState) retryState = String(params.stateJson);
        return { applied: true, flow: { revision: Number(params.expectedRevision) + 1 } };
      },
      finish: async (params: Record<string, unknown>) => {
        if (params.flowId === rootId && ++rootFinishes === 1) throw new Error("状态更新冲突，请重试");
        return { applied: true };
      },
      fail: async () => ({ applied: true }),
    },
  } as unknown as ControllerDeps;
  setEventRepository({ insert: async (event: FlowEvent) => { events.push(event); return true; } } as never);
  try {
    await handleRetry(deps, { flowId: rootId, nodeId: "sub", debug: true });
    const deadline = Date.now() + 5000;
    while (isAsyncExecutionActive(rootId) && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 10));
    assert.equal(isAsyncExecutionActive(rootId), false);
    assert.equal(rootFinishes, 2);
    assert.equal(childCreates, 2);
    assert.equal(lookups, 0, "redriven child must still bypass API resolution");
    assert.equal(events.filter((event) => event.type === "subworkflow_linked").length, 2);
  } finally {
    setEventRepository(null);
    if (oldConfig) setCachedConfig(oldConfig);
    else clearCachedConfig();
  }
});
