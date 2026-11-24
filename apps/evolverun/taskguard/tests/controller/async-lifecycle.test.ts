import assert from "node:assert/strict";
import { test } from "node:test";
import {
  __getAsyncExecutionResourceStateForTest,
  abortAsyncExecutionForFlow,
  abortAsyncExecutionsForSession,
  handleRun,
  isAsyncExecutionActive,
} from "../../src/controller.js";
import { fixture, until } from "./fixtures.js";

test("initial startAsync registers before the detached timer, runs start hooks once, and forwards its owned signal", async (t) => {
  const f = fixture(t);
  const upstream = new AbortController();
  f.deps.abortSignal = upstream.signal;
  f.hold();
  f.workflow.workflow = { onStart: [{ id: "started", action: "started" }] };
  f.deps.actionRegistry.register({ name: "started", execute: async () => { f.calls.hooks += 1; return {}; } });
  const flowId = await handleRun(f.deps, { workflowId: f.workflow.id, params: {}, executionMode: "private", startAsync: true });
  assert.equal(flowId, f.flowId);
  assert.equal(isAsyncExecutionActive(flowId), true);
  assert.equal(f.calls.execute.length, 0, "must not re-enter executor before the tool returns");
  const persisted = JSON.parse(f.records.get(flowId)!.stateJson as string);
  const handle = f.launch(persisted);
  assert.equal(handle.timeoutPolicy?.startedAt, persisted.flowEvents[0].time);
  assert.equal(handle.timeoutPolicy?.deadlineAt, persisted.flowEvents[0].time + 60_000);
  await until(() => f.calls.execute.length === 1);
  assert.equal(f.calls.hooks, 1);
  assert.equal(f.calls.execute[0].signal, handle.abortController.signal);
  assert.notEqual(f.calls.execute[0].signal, upstream.signal);
  assert.equal(upstream.signal.aborted, false);
});

test("stop between initial registration and timer prevents hooks and executor from starting", async (t) => {
  const f = fixture(t);
  const flowId = await handleRun(f.deps, { workflowId: f.workflow.id, params: {}, executionMode: "private", startAsync: true });
  const state = JSON.parse(f.records.get(flowId)!.stateJson as string);
  const handle = f.launch(state);
  assert.equal(abortAsyncExecutionsForSession(f.deps.sessionKey), 1);
  assert.equal(isAsyncExecutionActive(flowId), true, "retain ownership while cancelling");
  assert.equal(f.launch(state), handle, "cannot replace a cancelling driver");
  await handle.promise;
  assert.equal(f.calls.execute.length, 0);
  assert.equal(f.calls.fail.length, 1);
  assert.deepEqual(__getAsyncExecutionResourceStateForTest(flowId), {
    active: false, workflowSpec: false, chatInject: false, verbosity: false,
  });
});

test("flow cancellation drains its current executor without starting another retry", async (t) => {
  const f = fixture(t);
  const gate = f.hold();
  const handle = f.launch();
  await until(() => f.calls.execute.length === 1);
  assert.equal(abortAsyncExecutionForFlow(f.flowId), true);
  assert.equal(f.calls.execute[0].signal?.aborted, true);
  assert.equal(f.launch(), handle);
  // Even an executor that resolves success late must not resume the cancelled DAG.
  gate.resolve({ status: "succeeded", result: { late: true } });
  await handle.promise;
  assert.equal(f.calls.execute.length, 1);
  assert.equal(f.records.get(f.flowId)!.status, "failed");
});

test("upstream cancellation propagates to the actual execution context and suppresses retries", async (t) => {
  const f = fixture(t);
  const upstream = new AbortController();
  f.deps.abortSignal = upstream.signal;
  f.control.execute = () => new Promise((_resolve, reject) => {
    const signal = f.calls.execute.at(-1)!.signal!;
    signal.addEventListener("abort", () => reject(signal.reason), { once: true });
  });
  const handle = f.launch();
  await until(() => f.calls.execute.length === 1);
  upstream.abort(new Error("parent flow cancelled"));
  await handle.promise;
  assert.equal(f.calls.execute.length, 1);
  assert.equal(handle.abortController.signal.aborted, true);
  assert.equal(isAsyncExecutionActive(f.flowId), false);
});

test("retryable=false returns final failure on attempt one despite configured retries", async (t) => {
  const f = fixture(t);
  f.control.execute = async () => ({ status: "failed", error: "unsettled underlying execution", retryable: false });
  await f.launch().promise;
  assert.equal(f.calls.execute.length, 1);
  assert.equal(f.state.nodeStates.work.attempts, 1);
  assert.equal(f.state.nodeStates.work.status, "failed");
  assert.equal(f.state.auditLog.some((entry) => entry.action === "retry"), false);
});

test("ordinary executor failures still retry using the existing policy", async (t) => {
  const f = fixture(t);
  f.control.execute = async () => f.calls.execute.length < 3
    ? { status: "failed", error: "transient" }
    : { status: "succeeded", result: { recovered: true } };
  await f.launch().promise;
  assert.equal(f.calls.execute.length, 3);
  assert.equal(f.state.nodeStates.work.status, "succeeded");
});

test("cancellation interrupts retry backoff instead of starting the next attempt", async (t) => {
  const f = fixture(t);
  f.workflow.nodes[0].retry!.backoffMs = 60_000;
  f.control.execute = async () => ({ status: "failed", error: "transient" });
  const handle = f.launch();
  await until(() => f.state.auditLog.some((entry) => entry.action === "retry"));
  abortAsyncExecutionForFlow(f.flowId);
  await handle.promise;
  assert.equal(f.calls.execute.length, 1);
});

test("initial launch persists the global fallback once when no workflow override is present", async (t) => {
  const f = fixture(t);
  delete f.workflow.flowTimeoutMinutes;
  f.app.execution.flowTimeoutMinutes = 2;
  f.hold();
  await handleRun(f.deps, { workflowId: f.workflow.id, params: {}, executionMode: "private", startAsync: true });
  const persisted = JSON.parse(f.records.get(f.flowId)!.stateJson as string);
  const handle = f.launch(persisted);
  assert.equal(handle.timeoutPolicy?.timeoutMs, 120_000);
  assert.equal(persisted.flowTimeoutPolicy.deadlineAt, persisted.flowEvents[0].time + 120_000);
  f.app.execution.flowTimeoutMinutes = 600;
  assert.equal(handle.timeoutPolicy?.timeoutMs, 120_000);
});

test("per-flow abort leaves a sibling using the same session and executor factory alive", async (t) => {
  const f = fixture(t);
  const gate = f.hold();
  const first = f.launch();
  const siblingId = `${f.flowId}-sibling`;
  const siblingState = structuredClone(f.state);
  f.records.set(siblingId, { ...f.records.get(f.flowId)!, flowId: siblingId, stateJson: JSON.stringify(siblingState) });
  f.rows.set(siblingId, { ...f.rows.get(f.flowId)!, flow_id: siblingId });
  const { __launchAsyncExecutionForTest } = await import("../../src/controller.js");
  const sibling = __launchAsyncExecutionForTest(f.deps, f.workflow, siblingState, siblingId, 1);
  f.handles.push(sibling);
  await until(() => f.calls.execute.length === 2);
  assert.notEqual(first.abortController.signal, sibling.abortController.signal);
  abortAsyncExecutionForFlow(f.flowId);
  assert.equal(first.abortController.signal.aborted, true);
  assert.equal(sibling.abortController.signal.aborted, false);
  gate.resolve({ status: "succeeded", result: { ok: true } });
  await Promise.all([first.promise, sibling.promise]);
  assert.equal(f.records.get(f.flowId)!.status, "failed");
  assert.equal(f.records.get(siblingId)!.status, "completed");
});

test("cancellation after a persisted node uses the owned flow's current revision for closeout", async (t) => {
  const f = fixture(t);
  const gate = f.hold();
  f.workflow.nodes[0].executor = { type: "action", action: "first" };
  f.state.nodeStates.work.executor = "action";
  f.workflow.nodes.push({ id: "next", title: "Next", phase: "P1", dependsOn: ["work"], executor: { type: "done" } });
  f.state.nodeStates.next = { status: "pending", phase: "P1", executor: "done" };
  f.records.get(f.flowId)!.stateJson = JSON.stringify(f.state);
  f.control.execute = async () => f.calls.execute.length === 1
    ? { status: "succeeded", result: { first: true } }
    : gate.promise;
  const handle = f.launch();
  await until(() => f.calls.execute.length === 2);
  const currentRevision = f.records.get(f.flowId)!.revision;
  assert.ok(Number(currentRevision) > 1, "first node must have persisted a new revision");
  abortAsyncExecutionForFlow(f.flowId);
  gate.resolve({ status: "succeeded", result: { late: true } });
  await handle.promise;
  assert.equal(f.calls.execute.length, 2);
  assert.equal(f.calls.fail.at(-1)!.expectedRevision, currentRevision);
  assert.equal(f.records.get(f.flowId)!.status, "failed");
  assert.equal(f.calls.completion.length, 1);
});
