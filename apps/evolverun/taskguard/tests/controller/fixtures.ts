import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import type { TestContext } from "node:test";
import { createActionRegistry } from "../../src/actions/registry.js";
import { setCachedConfig } from "../../src/config/config-cache.js";
import { defaults } from "../../src/config/types.js";
import {
  __launchAsyncExecutionForTest,
  abortAsyncExecutionForFlow,
  setFlowRunRepository,
  type AsyncExecutionHandle,
  type ControllerDeps,
} from "../../src/controller.js";
import type { FlowRunRow, IFlowRunRepository } from "../../src/db/repositories/types.js";
import { freezeFlowTimeoutPolicy } from "../../src/runtime/owned-flow-timeout.js";
import type { ExecutorResult, FlowState, WorkflowSpec } from "../../src/types.js";

// All controller-generated artifacts stay outside the checkout and user state.
process.env.HOME = mkdtempSync(join(tmpdir(), "taskguard-owned-controller-"));
let sequence = 0;

export function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

export async function until(predicate: () => boolean, message = "condition did not become true") {
  for (let i = 0; i < 200; i += 1) {
    if (predicate()) return;
    await delay(5);
  }
  assert.ok(predicate(), message);
}

export function fixture(t: TestContext, timeoutMinutes: number | undefined = 1) {
  const app = structuredClone(defaults);
  app.execution.asyncRun = true;
  app.execution.flowTimeoutMinutes = 120;
  app.guardian.enabled = false;
  setCachedConfig({ app, database: { type: "sqlite", sqlitePath: ":memory:" } });
  const flowId = `owned-${++sequence}`;
  const workflow: WorkflowSpec = {
    id: `workflow-${sequence}`, title: "Owned controller integration", version: 1,
    identity: { key: `identity-${sequence}`, duplicatePolicy: "allow" },
    ...(timeoutMinutes === undefined ? {} : { flowTimeoutMinutes: timeoutMinutes }),
    nodes: [{ id: "work", title: "Work", phase: "P1", dependsOn: [], executor: { type: "done" },
      retry: { maxAttempts: 3, backoffMs: 0, on: ["executor-failed"] } }],
  };
  const startedAt = Date.now() - 70_000;
  const state: FlowState = {
    workflowId: workflow.id, workflowVersion: 1, params: {}, executionMode: "private",
    currentPhase: "P1", businessStatus: "INIT", activeNodes: [],
    nodeStates: { work: { status: "pending", phase: "P1", executor: "done" } },
    workflowData: {}, actionOutputs: {}, flowHooks: {}, auditLog: [],
    flowEvents: [{ id: "start", type: "workflow_started", flowId, workflowId: workflow.id, time: startedAt }],
    flowTimeoutPolicy: freezeFlowTimeoutPolicy(startedAt, timeoutMinutes, app.execution.flowTimeoutMinutes),
  };
  const records = new Map<string, Record<string, unknown>>();
  records.set(flowId, { flowId, revision: 1, status: "running", stateJson: JSON.stringify(state) });
  const rows = new Map<string, FlowRunRow>();
  rows.set(flowId, { flow_id: flowId, workflow_id: workflow.id, status: "running",
    started_at: Math.floor(startedAt / 1000), current_phase: "P1" } as FlowRunRow);
  const calls = {
    execute: [] as Array<{ flowId: string; signal: AbortSignal | undefined }>,
    fail: [] as Array<Record<string, unknown>>,
    completion: [] as Array<{ flowId: string; value: Record<string, unknown> }>,
    lookups: [] as string[],
    hooks: 0,
    inject: [] as Array<{ message: string; key: string }>,
  };
  const control = {
    failApplied: true,
    lookup: undefined as ((id: string) => Promise<FlowRunRow | null>) | undefined,
    execute: async (): Promise<ExecutorResult> => ({ status: "succeeded", result: { ok: true } }),
  };
  const persist = async (params: Record<string, unknown>, status?: string) => {
    const id = params.flowId as string;
    const previous = records.get(id)!;
    if (params.expectedRevision !== previous.revision) return { applied: false, flow: previous };
    const flow = { ...previous, ...params, revision: Number(previous.revision) + 1,
      ...(status ? { status } : {}) };
    records.set(id, flow);
    return { applied: true, flow };
  };
  const deps: ControllerDeps = {
    boundTaskFlow: {
      createManaged: async (params) => {
        const flow = { ...params, flowId, revision: 1 };
        records.set(flowId, flow);
        return flow;
      },
      get: async (id) => records.get(id) ?? null,
      list: async () => ({ flows: [] }), findLatest: async () => null,
      resume: async (params) => persist(params, "running"),
      setWaiting: async (params) => persist(params, "waiting"),
      finish: async (params) => persist(params, "completed"),
      fail: async (params) => {
        calls.fail.push(params);
        if (!control.failApplied) return { applied: false, flow: records.get(params.flowId as string) };
        return persist(params, "failed");
      },
      runTask: async () => ({}),
    },
    actionRegistry: createActionRegistry(),
    chatInject: async (message, key) => { calls.inject.push({ message, key }); }, chatInjectLevel: "perf",
    sessionKey: `session-${sequence}`, skillRoot: process.env.HOME!,
    workflowRegistry: { [workflow.id]: workflow },
    executeNode: async (_node, _ctx, _state, id, context) => {
      calls.execute.push({ flowId: id, signal: context?.abortSignal });
      return control.execute();
    },
  };
  const repository = {
    findByFlowId: async (id: string) => {
      calls.lookups.push(id);
      return control.lookup ? control.lookup(id) : rows.get(id) ?? null;
    },
    findStaleRunning: async () => { throw new Error("Global candidate scans are forbidden"); },
    updateCompletion: async (id: string, value: Record<string, unknown>) => {
      calls.completion.push({ flowId: id, value });
      if (rows.has(id)) Object.assign(rows.get(id)!, value);
      return true;
    },
    insert: async () => true, incrementNodeCount: async () => true,
    updateCurrentPhase: async () => true, updateStatus: async () => true,
    updateResultJson: async () => true, updateNodeCount: async () => true,
  } as unknown as IFlowRunRepository;
  setFlowRunRepository(repository);
  const handles: AsyncExecutionHandle[] = [];
  const pending: ReturnType<typeof deferred<ExecutorResult>>[] = [];
  const launch = (nextState = state) => {
    const handle = __launchAsyncExecutionForTest(deps, workflow, nextState, flowId, Number(records.get(flowId)!.revision));
    if (!handles.includes(handle)) handles.push(handle);
    return handle;
  };
  const hold = () => {
    const gate = deferred<ExecutorResult>();
    pending.push(gate);
    control.execute = () => gate.promise;
    return gate;
  };
  t.after(async () => {
    for (const handle of handles) abortAsyncExecutionForFlow(handle.flowId);
    for (const gate of pending) gate.resolve({ status: "succeeded", result: { drained: true } });
    await Promise.all(handles.map((handle) => handle.promise));
    setFlowRunRepository(null);
  });
  return { app, flowId, workflow, state, deps, rows, records, calls, control, launch, hold, handles, repository };
}
