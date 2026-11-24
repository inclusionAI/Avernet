import { after, beforeEach, test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, writeFile, rm } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { createExecutorDispatch } from "../../src/index.js";
import { createActionRegistry } from "../../src/actions/registry.js";
import { defaults } from "../../src/config/types.js";
import { clearCachedConfig, setCachedConfig } from "../../src/config/config-cache.js";
import { resetLlmSemaphore } from "../../src/executors/llm-semaphore.js";
import { setFlowInjectLevel, clearFlowInjectLevel } from "../../src/inject-gating.js";
import type { FlowState, WorkflowNode, WorkflowSpec } from "../../src/types.js";
import type { PluginApi } from "../../src/platform/openclaw-types.js";

const homeBefore = process.env.HOME;
const temp = await mkdtemp(join(tmpdir(), "taskguard-dispatch-signal-"));
process.env.HOME = temp;
const sessionFile = join(temp, "session.jsonl");
await writeFile(sessionFile, "");

beforeEach(() => {
  resetLlmSemaphore();
  setCachedConfig({ database: { type: "sqlite", sqlitePath: join(temp, "unused.db") }, app: structuredClone(defaults) });
});
after(async () => {
  resetLlmSemaphore();
  clearCachedConfig();
  if (homeBefore === undefined) delete process.env.HOME;
  else process.env.HOME = homeBefore;
  // Diagnostic JSONL writes are detached; allow them to drain before removal.
  await new Promise((resolve) => setTimeout(resolve, 30));
  await rm(temp, { recursive: true, force: true });
});

function fixture() {
  const node: WorkflowNode = {
    id: "check", title: "Signal check", phase: "P1", dependsOn: [],
    executor: { type: "embedded-agent", prompt: "return ok", timeoutSeconds: 30, contextPolicy: { history: "inherit" } },
  };
  const workflow = { id: "signal-flow", title: "Signal flow", version: 1, nodes: [node] } as WorkflowSpec;
  const state: FlowState = {
    workflowId: workflow.id, workflowVersion: 1, params: {}, executionMode: "private",
    businessStatus: "RUNNING", currentPhase: "P1", activeNodes: [node.id],
    nodeStates: { [node.id]: { status: "running", phase: "P1", executor: "embedded-agent", attempts: 1 } },
    workflowData: {}, actionOutputs: {}, flowHooks: {}, auditLog: [],
  };
  let started = 0;
  let capturedSignal: AbortSignal | undefined;
  const api = {
    runtime: {
      config: { loadConfig: async () => ({}) },
      agent: {
        runEmbeddedPiAgent: async (params: Record<string, unknown>) => {
          started += 1;
          capturedSignal = params.abortSignal as AbortSignal;
          return { output: "ok" };
        },
      },
    },
  } as unknown as PluginApi;
  return { node, workflow, state, api, started: () => started, signal: () => capturedSignal };
}

test("dispatch uses the invocation flow signal instead of a previously captured live session signal", async () => {
  const f = fixture();
  const session = new AbortController();
  const flow = new AbortController();
  flow.abort(new Error("flow cancelled before dispatch"));
  const dispatch = createExecutorDispatch(f.api, "agent:main:signal-test", createActionRegistry(), undefined,
    { sessionId: "11111111-1111-4111-8111-111111111111", sessionFile, workspaceDir: temp, agentDir: temp }, session.signal, f.workflow);
  const result = await dispatch(f.node, { params: {} } as never, f.state, "cancelled-flow", { abortSignal: flow.signal });
  assert.equal(result.status, "failed");
  assert.equal(result.retryable, false);
  assert.match(result.error ?? "", /cancel|abort/i);
  assert.equal(f.started(), 0);
});

test("an invocation flow signal overrides an aborted stale session closure without cancelling a sibling", async () => {
  const f = fixture();
  const session = new AbortController();
  session.abort(new Error("old session cancelled"));
  const flow = new AbortController();
  const dispatch = createExecutorDispatch(f.api, "agent:main:signal-test", createActionRegistry(), undefined,
    { sessionId: "11111111-1111-4111-8111-111111111111", sessionFile, workspaceDir: temp, agentDir: temp }, session.signal, f.workflow);
  const flowId = "signal-override";
  setFlowInjectLevel("perf", flowId);
  try {
    const result = await dispatch(f.node, { params: {} } as never, f.state, flowId, { abortSignal: flow.signal });
    assert.equal(result.status, "succeeded", result.error);
    assert.equal(f.started(), 1);
    assert.equal(f.signal()?.aborted, false);
    assert.notEqual(f.signal(), session.signal);
  } finally {
    clearFlowInjectLevel(flowId);
  }
});

test("legacy four-argument dispatch continues to honor its session signal", async () => {
  const f = fixture();
  const session = new AbortController();
  session.abort(new Error("legacy session cancelled"));
  const dispatch = createExecutorDispatch(f.api, "agent:main:signal-test", createActionRegistry(), undefined,
    { sessionId: "11111111-1111-4111-8111-111111111111", sessionFile, workspaceDir: temp, agentDir: temp }, session.signal, f.workflow);
  const result = await dispatch(f.node, { params: {} } as never, f.state, "legacy-flow");
  assert.equal(result.status, "failed");
  assert.equal(result.retryable, false);
  assert.equal(f.started(), 0);
});
