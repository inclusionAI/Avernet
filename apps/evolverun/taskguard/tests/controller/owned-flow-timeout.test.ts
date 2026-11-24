import assert from "node:assert/strict";
import { test } from "node:test";
import { reapStaleRunningFlows } from "../../src/controller.js";
import { freezeFlowTimeoutPolicy, readFrozenFlowTimeoutPolicy } from "../../src/runtime/owned-flow-timeout.js";
import { deferred, fixture, until } from "./fixtures.js";

test("short workflow deadline is reaped despite a 120-minute global cutoff and foreign oldest rows", async (t) => {
  const f = fixture(t, 1);
  const gate = f.hold();
  const handle = f.launch();
  await until(() => f.calls.execute.length === 1);
  f.calls.lookups.length = 0;
  f.rows.set("foreign-oldest", { ...f.rows.get(f.flowId)!, flow_id: "foreign-oldest", started_at: 1 });
  let latestLookups = 0;
  const reaped = await reapStaleRunningFlows(120 * 60_000, 1, undefined, undefined, () => { latestLookups += 1; return 0; });
  assert.equal(reaped, 1);
  assert.deepEqual(f.calls.lookups, [f.flowId]);
  assert.equal(latestLookups, 0, "must not use a changed workflow/catalog policy");
  assert.equal(handle.abortController.signal.aborted, true);
  assert.equal(f.rows.get("foreign-oldest")!.status, "running");
  assert.equal(f.calls.fail.length, 1);
  assert.equal(f.calls.completion.length, 1);
  const result = JSON.parse(f.calls.completion[0].value.resultJson as string);
  assert.equal(JSON.parse(result.message).timeoutSecs, 60);
  gate.resolve({ status: "succeeded", result: { late: true } });
  await handle.promise;
  assert.equal(f.calls.completion.length, 1, "late executor result cannot overwrite timeout");
  assert.equal(f.rows.get(f.flowId)!.status, "failed");
});

test("initial launch freezes the global fallback instead of using a later larger or disabled global", async (t) => {
  const f = fixture(t, undefined);
  f.state.flowTimeoutPolicy = freezeFlowTimeoutPolicy(Date.now() - 70_000, undefined, 1);
  f.records.get(f.flowId)!.stateJson = JSON.stringify(f.state);
  f.hold();
  f.launch();
  await until(() => f.calls.execute.length === 1);
  f.app.execution.flowTimeoutMinutes = 0;
  assert.equal(await reapStaleRunningFlows(0), 1);
  assert.equal(f.calls.fail.length, 1);
});

test("workflow timeout 0 remains disabled even when a changed global timeout would be exceeded", async (t) => {
  const f = fixture(t, 0);
  f.hold();
  const handle = f.launch();
  await until(() => f.calls.execute.length === 1);
  f.calls.lookups.length = 0;
  assert.equal(handle.timeoutPolicy?.deadlineAt, null);
  assert.equal(await reapStaleRunningFlows(1), 0);
  assert.equal(f.calls.lookups.length, 0);
  assert.equal(f.calls.fail.length, 0);
  assert.equal(f.calls.completion.length, 0);
});

test("long per-workflow timeout does not inherit a shorter current global cutoff", async (t) => {
  const f = fixture(t, 240);
  f.hold();
  f.launch();
  await until(() => f.calls.execute.length === 1);
  f.calls.lookups.length = 0;
  assert.equal(await reapStaleRunningFlows(1), 0);
  assert.equal(f.calls.lookups.length, 0);
  assert.equal(f.calls.fail.length, 0);
});

test("legacy/recovered state without a trusted policy is not guessed from today's config", async (t) => {
  const f = fixture(t, 1);
  delete f.state.flowTimeoutPolicy;
  f.records.get(f.flowId)!.stateJson = JSON.stringify(f.state);
  f.hold();
  f.launch();
  await until(() => f.calls.execute.length === 1);
  f.calls.lookups.length = 0;
  assert.equal(await reapStaleRunningFlows(1), 0);
  assert.deepEqual(f.calls.lookups, []);
  assert.equal(f.calls.fail.length, 0);
});

test("restored execution preserves its first-event absolute deadline, not the resume time", async (t) => {
  const f = fixture(t, 1);
  const originalPolicy = structuredClone(f.state.flowTimeoutPolicy);
  f.hold();
  // Simulate persisted JSON restored by a later controller invocation.
  const restored = JSON.parse(f.records.get(f.flowId)!.stateJson as string);
  f.workflow.flowTimeoutMinutes = 500;
  f.app.execution.flowTimeoutMinutes = 600;
  const handle = f.launch(restored);
  assert.deepEqual(handle.timeoutPolicy, originalPolicy);
  await until(() => f.calls.execute.length === 1);
  assert.equal(await reapStaleRunningFlows(600 * 60_000), 1);
});

test("overlapping sweeps share one owner lookup and one successful TaskFlow CAS", async (t) => {
  const f = fixture(t, 1);
  f.hold();
  f.launch();
  await until(() => f.calls.execute.length === 1);
  f.calls.lookups.length = 0;
  const lookupGate = deferred<void>();
  f.control.lookup = async (id) => { await lookupGate.promise; return f.rows.get(id) ?? null; };
  const first = reapStaleRunningFlows(7_200_000);
  const second = reapStaleRunningFlows(7_200_000);
  assert.equal(first, second);
  lookupGate.resolve();
  assert.deepEqual(await Promise.all([first, second]), [1, 1]);
  assert.deepEqual(f.calls.lookups, [f.flowId]);
  assert.equal(f.calls.fail.length, 1);
  assert.equal(f.calls.completion.length, 1);
});

for (const condition of ["query failure", "missing row", "waiting row", "terminal row", "CAS not applied", "CAS throws", "TaskFlow not running", "policy replaced"]) {
  test(`watchdog performs no terminal write for ${condition}`, async (t) => {
    const f = fixture(t, 1);
    f.hold();
    const handle = f.launch();
    await until(() => f.calls.execute.length === 1);
    if (condition === "query failure") f.control.lookup = async () => { throw new Error("unavailable"); };
    if (condition === "missing row") f.rows.delete(f.flowId);
    if (condition === "waiting row") f.rows.get(f.flowId)!.status = "waiting";
    if (condition === "terminal row") f.rows.get(f.flowId)!.status = "succeeded";
    if (condition === "CAS not applied") f.control.failApplied = false;
    if (condition === "CAS throws") f.deps.boundTaskFlow.fail = async () => { throw new Error("CAS unavailable"); };
    if (condition === "TaskFlow not running") f.records.get(f.flowId)!.status = "completed";
    if (condition === "policy replaced") {
      const changed = { ...f.state, flowTimeoutPolicy: freezeFlowTimeoutPolicy(Date.now(), 10, 120) };
      f.records.get(f.flowId)!.stateJson = JSON.stringify(changed);
    }
    assert.equal(await reapStaleRunningFlows(1), 0);
    assert.equal(handle.abortController.signal.aborted, false);
    assert.equal(f.calls.completion.length, 0);
    assert.equal(f.calls.fail.length, condition === "CAS not applied" ? 1 : 0);
  });
}

test("a running row without an owned handle is never queried or finalized", async (t) => {
  const f = fixture(t, 1);
  assert.equal(await reapStaleRunningFlows(1), 0);
  assert.deepEqual(f.calls.lookups, []);
  assert.equal(f.calls.fail.length, 0);
  assert.equal(f.calls.completion.length, 0);
});

test("frozen timeout policies reject invalid restored data and preserve explicit zero", () => {
  assert.deepEqual(freezeFlowTimeoutPolicy(1_000, 0, 120), { startedAt: 1_000, timeoutMs: 0, deadlineAt: null });
  assert.equal(readFrozenFlowTimeoutPolicy({ startedAt: 1_000, timeoutMs: 60_000, deadlineAt: 1_000 }), undefined);
  assert.equal(readFrozenFlowTimeoutPolicy({ startedAt: 0, timeoutMs: 60_000, deadlineAt: 60_000 }), undefined);
  assert.equal(readFrozenFlowTimeoutPolicy({ startedAt: 1_000, timeoutMs: -1, deadlineAt: 999 }), undefined);
  assert.equal(readFrozenFlowTimeoutPolicy({ startedAt: 1_000, timeoutMs: Infinity, deadlineAt: Infinity }), undefined);
  assert.throws(() => freezeFlowTimeoutPolicy(1_000, -1, 120), /Invalid flow timeout policy/);
});

test("ownership cancelled while an overdue row lookup is pending is not reaped", async (t) => {
  const f = fixture(t, 1);
  f.hold();
  const handle = f.launch();
  await until(() => f.calls.execute.length === 1);
  const lookupGate = deferred<void>();
  f.control.lookup = async (id) => { await lookupGate.promise; return f.rows.get(id) ?? null; };
  const sweep = reapStaleRunningFlows(1);
  handle.abortController.abort(new Error("stop won the race"));
  lookupGate.resolve();
  assert.equal(await sweep, 0);
  assert.equal(f.calls.fail.length, 0);
  assert.equal(f.calls.completion.length, 0);
});

test("an executor failure while a watchdog CAS is pending still performs its own cleanup if that CAS loses", async (t) => {
  const f = fixture(t, 1);
  const gate = f.hold();
  const handle = f.launch();
  await until(() => f.calls.execute.length === 1);
  const casGate = deferred<void>();
  const fail = f.deps.boundTaskFlow.fail;
  let reaperPending = false;
  f.deps.boundTaskFlow.fail = async (params) => {
    if (String(params.blockedSummary).includes("工作流执行超时")) {
      reaperPending = true;
      await casGate.promise;
      return { applied: false };
    }
    return fail(params);
  };
  const sweep = reapStaleRunningFlows(1);
  await until(() => reaperPending);
  gate.reject(new Error("executor crashed while timeout CAS pending"));
  await handle.promise;
  assert.equal(f.records.get(f.flowId)!.status, "failed");
  assert.equal(f.calls.completion.length, 1);
  casGate.resolve();
  assert.equal(await sweep, 0);
  assert.equal(f.calls.completion.length, 1);
  assert.doesNotMatch(f.calls.completion[0].value.resultJson as string, /flow_timeout/);
});
