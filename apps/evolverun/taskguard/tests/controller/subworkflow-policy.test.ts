import assert from "node:assert/strict";
import { test } from "node:test";
import { executeWorkflowForTest, reapStaleRunningFlows } from "../../src/controller.js";
import type { WorkflowSpec } from "../../src/types.js";
import { fixture, until } from "./fixtures.js";

for (const override of [0, 3, undefined]) {
  test(`new child flow freezes its own timeout policy before persistence (override=${override})`, async (t) => {
    const f = fixture(t);
    const childId = `${f.flowId}-child`;
    const child: WorkflowSpec = {
      id: "child", title: "Child", version: 1,
      ...(override === undefined ? {} : { flowTimeoutMinutes: override }),
      nodes: [{ id: "child-work", title: "Child work", phase: "P1", dependsOn: [], executor: { type: "done" } }],
    };
    const gate = f.hold();
    f.deps.resolvedWorkflows = [];
    f.workflow.nodes[0].executor = { type: "subworkflow", workflowId: child.id };
    f.state.nodeStates.work.executor = "subworkflow";
    f.deps.boundTaskFlow.createManaged = async (params) => {
      const flow = { ...params, flowId: childId, revision: 1 };
      f.records.set(childId, flow);
      return flow;
    };
    f.deps.executeNode = async (_node, _ctx, _state, id) => {
      if (id !== f.flowId) return gate.promise;
      const result = await f.deps.subworkflowDeps!.executeChildWorkflow({
        childWorkflow: child, childParams: {}, parentFlowId: f.flowId, parentNodeId: "work", depth: 1,
      });
      return result.status === "succeeded"
        ? { status: "succeeded", result: result.workflowData }
        : { status: "failed", error: result.error };
    };
    const execution = executeWorkflowForTest({ deps: f.deps, workflow: f.workflow, state: f.state, flowId: f.flowId, revision: 1 });
    try {
      await until(() => f.records.has(childId));
      const persisted = JSON.parse(f.records.get(childId)!.stateJson as string);
      const policy = persisted.flowTimeoutPolicy;
      assert.equal(policy.startedAt, persisted.flowEvents[0].time);
      assert.equal(policy.timeoutMs, (override ?? 120) * 60_000);
      assert.equal(policy.deadlineAt, override === 0 ? null : policy.startedAt + policy.timeoutMs);
      assert.notEqual(policy.startedAt, f.state.flowTimeoutPolicy!.startedAt, "child must not inherit parent deadline");
      assert.equal(await reapStaleRunningFlows(1), 0, "synchronous child without an owner is outside watchdog scope");
    } finally {
      gate.resolve({ status: "succeeded", result: { child: true } });
      await execution;
    }
  });
}
