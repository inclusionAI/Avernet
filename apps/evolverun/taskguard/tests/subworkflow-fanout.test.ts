import assert from "node:assert/strict";
import { test } from "node:test";
import { setTimeout as delay } from "node:timers/promises";
import { executeSubworkflowFanout } from "../src/executors/subworkflow-fanout.js";
import { FanoutRuntimeError } from "../src/executors/subworkflow-fanout-types.js";
import type { FanoutChildCompletion, FanoutChildRequest, FanoutConfig, FanoutResult, FanoutRuntime, FanoutState } from "../src/executors/subworkflow-fanout-types.js";
import type { ExecutorResult, FlowState, WorkflowNode, WorkflowSpec } from "../src/types.js";
import type { TemplateContext } from "../src/runner.js";

const copy = <T>(value: T): T => structuredClone(value);
const plan = Array.from({ length: 5 }, (_, i) => ({ scene_code: `scene-${i}`, partition: "p", n: i }));
function config(overrides: Partial<FanoutConfig> = {}): FanoutConfig {
  return { schemaVersion: "fanout.v1", items: "{{nodeOutput.plan.items}}", itemIdFrom: ["scene_code", "partition"],
    concurrency: 2, successPolicy: "all_done", finishMode: "wait_all", resultPolicy: "collect_all",
    groupTimeoutSeconds: 5, cancelGraceSeconds: 0.03, ...overrides };
}
function node(overrides: Partial<FanoutConfig> = {}): WorkflowNode {
  return { id: "fanout", phase: "P5", dependsOn: [], executor: {
    type: "subworkflow", workflowId: "child", fanout: config(overrides),
    params: { scene: "{{item.scene_code}}", n: "{{item.n}}", shared: "{{params.shared}}" } },
  } as WorkflowNode;
}
function workflow(): WorkflowSpec {
  return { id: "child", title: "child v1", version: 1, nodes: [], outputs: { result: { from: "nodeOutput.finalize" } } };
}
function parent(): FlowState {
  return { workflowId: "parent", workflowVersion: 7, params: { shared: "original" },
    executionMode: "standalone", businessStatus: "RUNNING", currentPhase: "P5", activeNodes: [],
    nodeStates: {}, workflowData: {}, actionOutputs: {}, flowHooks: {}, auditLog: [],
    subworkflowDependencies: { child: { workflowId: "child", publishedVersion: 7, releaseId: "release-child-7",
      digest: `sha256:${"a".repeat(64)}`, spec: workflow() } },
  } as FlowState;
}
function context(items: unknown = copy(plan)): TemplateContext {
  return { skillRoot: "", nodeOutput: { plan: { items } }, params: { shared: "original" } };
}
const ok = (r: FanoutChildRequest): FanoutChildCompletion => ({ status: "succeeded",
  outputs: { result: { experimentId: `exp-${r.itemIndex}`, reportId: `rep-${r.itemIndex}` } } });

/** Test-only CAS store and idempotent child host, never a production fallback. */
class TestRuntime implements FanoutRuntime {
  state: FanoutState | null = null;
  history: FanoutState[] = [];
  calls: FanoutChildRequest[] = [];
  cancelCalls: FanoutChildRequest[] = [];
  instances = new Map<string, { request: FanoutChildRequest; completion: Promise<FanoutChildCompletion> }>();
  active = 0; peak = 0; parent = "running";
  beforeSave?: (s: FanoutState, expected: number | null) => void | Promise<void>;
  run: (r: FanoutChildRequest, s: AbortSignal) => Promise<FanoutChildCompletion> = async r => ok(r);
  async load(): Promise<FanoutState | null> { return copy(this.state); }
  async save(s: FanoutState, expected: number | null): Promise<void> {
    await this.beforeSave?.(s, expected);
    assert.equal(expected, this.state?.revision ?? null, "CAS conflict");
    assert.equal(s.revision, expected === null ? 0 : expected + 1);
    if (s.status === "succeeded" && this.parent !== "running") throw new Error("parent seal barrier");
    this.state = copy(s); this.history.push(copy(s));
  }
  async parentStatus(): Promise<string> { return this.parent; }
  async startOrGetChild(r: FanoutChildRequest, signal: AbortSignal): Promise<FanoutChildCompletion> {
    const intent = this.state?.children.find(c => c.childFlowId === r.childFlowId);
    assert.ok(intent && intent.status !== "queued", "start must follow durable intent");
    this.calls.push(copy(r));
    const existing = this.instances.get(r.childFlowId);
    if (existing) { assert.deepEqual(r, existing.request, "rejoin must reuse exact request"); return existing.completion; }
    if (signal.aborted) return { status: "cancelled" };
    this.active++; this.peak = Math.max(this.peak, this.active);
    const completion = Promise.resolve().then(() => this.run(r, signal)).finally(() => this.active--);
    this.instances.set(r.childFlowId, { request: copy(r), completion });
    return completion;
  }
  async cancelChild(r: FanoutChildRequest): Promise<void> { this.cancelCalls.push(copy(r)); }
}
function execute(rt: FanoutRuntime, n = node(), ctx = context(), child = workflow(), signal?: AbortSignal) {
  return executeSubworkflowFanout(n, ctx, parent(), "parent-1", child, rt, { signal });
}
function result(r: ExecutorResult): FanoutResult { assert.ok(r.result, "executor must expose a structured result"); return r.result as FanoutResult; }
function conserved(r: FanoutResult) {
  assert.equal(r.total, r.successCount + r.failedCount + r.cancelledCount + r.timedOutCount + r.rejectedCount + r.pendingCount);
  assert.equal(r.items.length, r.total);
}

test("five items run with two bounded children, a single structured evaluation and ordered results", async () => {
  const rt = new TestRuntime(); let reads = 0;
  const items = copy(plan); const ctx = context(); const child = workflow();
  Object.defineProperty(ctx.nodeOutput.plan, "items", { get: () => { reads++; return items; } });
  rt.run = async r => {
    if (r.itemIndex === 0) { items[4].scene_code = "changed"; child.title = "changed"; (ctx.params as Record<string, string>).shared = "changed"; }
    await delay(r.itemIndex === 0 ? 25 : 3); return ok(r);
  };
  const executed = await execute(rt, node(), ctx, child);
  assert.equal(executed.status, "succeeded"); const r = result(executed);
  assert.equal(reads, 1); assert.equal(rt.peak, 2); assert.equal(rt.instances.size, 5);
  assert.deepEqual(r.items.map(i => i.itemId), ["scene-0/p", "scene-1/p", "scene-2/p", "scene-3/p", "scene-4/p"]);
  assert.deepEqual(r.items.map(i => i.result?.experimentId), ["exp-0", "exp-1", "exp-2", "exp-3", "exp-4"]);
  assert.equal(r.items[0].result?.result, undefined); assert.equal(r.businessOutcome, "all_success");
  assert.equal(r.successCount, 5); assert.equal(r.complete, true); conserved(r);
  for (const field of ["childFlowId", "sessionKey", "stateDirRef"] as const) assert.equal(new Set(rt.calls.map(c => c[field])).size, 5);
  for (const request of rt.calls) {
    assert.equal(request.childWorkflow.title, "child v1"); assert.equal(request.childWorkflow.version, 1);
    assert.equal(request.params.shared, "original"); assert.equal(typeof request.params.n, "number");
    assert.equal(request.groupExecutionId, r.groupExecutionId);
  }
  assert.deepEqual(rt.state?.result, r); assert.equal(rt.state?.revision, r.groupRevision);
});
test("empty array durably seals a complete empty result without creating a child", async () => {
  const rt = new TestRuntime(); const executed = await execute(rt, node(), context([]));
  assert.equal(executed.status, "succeeded"); const r = result(executed);
  assert.equal(r.businessOutcome, "empty"); assert.equal(r.total, 0); assert.equal(r.complete, true);
  assert.equal(rt.calls.length, 0); assert.equal(rt.state?.status, "succeeded"); conserved(r);
});
for (const [label, input, overrides] of [
  ["non-array", "[]", {}], ["null", null, {}], ["duplicate composite keys", [plan[0], plan[0]], {}],
  ["missing identity", [{ scene_code: "s" }], {}], ["blank identity", [{ scene_code: " ", partition: "p" }], {}],
  ["non-string identity", [{ scene_code: 1, partition: "p" }], {}], ["primitive item", [1], {}], ["array item", [[]], {}],
  ["zero concurrency", plan, { concurrency: 0 }], ["fractional concurrency", plan, { concurrency: 1.5 }],
  ["string concurrency", plan, { concurrency: "2" }], ["negative timeout", plan, { groupTimeoutSeconds: -1 }],
  ["infinite timeout", plan, { groupTimeoutSeconds: Infinity }], ["negative grace", plan, { cancelGraceSeconds: -1 }],
  ["unknown policy", plan, { successPolicy: "any_success" }], ["replica count", plan, { count: 3 }],
  ["empty key list", plan, { itemIdFrom: [] }],
] as [string, unknown, Partial<FanoutConfig>][]) {
  test(`invalid ${label} fails before any dispatch`, async () => {
    const rt = new TestRuntime(); const r = await execute(rt, node(overrides), context(input));
    assert.equal(r.status, "failed"); assert.ok(r.error); assert.equal(rt.calls.length, 0);
  });
}
test("composite identity percent-encodes delimiters without collisions", async () => {
  const rt = new TestRuntime(); const r = result(await execute(rt, node(), context([
    { scene_code: "a/b", partition: "c", n: 0 }, { scene_code: "a", partition: "b/c", n: 1 }, { scene_code: "a", partition: "p!", n: 2 },
  ])));
  assert.deepEqual(r.items.map(i => i.itemId), ["a%2Fb/c", "a/b%2Fc", "a/p%21"]);
});
for (const category of ["execution", "business"] as const) {
  test(`all_done retains one confirmed ${category} failure with four successes`, async () => {
    const rt = new TestRuntime(); rt.run = async r => r.itemIndex === 1 ? {
      status: "failed", error: { category, code: "ACTION_FAILED", message: "confirmed failure", sourceNodeId: "launch-one" },
    } : ok(r);
    const executed = await execute(rt); assert.equal(executed.status, "succeeded"); const r = result(executed);
    assert.equal(r.successCount, 4); assert.equal(r.failedCount, 1); assert.equal(r.businessOutcome, "partial_failure");
    assert.equal(r.complete, true); assert.equal(r.items[1].result, null); assert.equal(r.items[1].error?.code, "ACTION_FAILED"); conserved(r);
  });
}
for (const outputs of [undefined, {}, { result: null }, { result: "text" }]) {
  test(`successful child with invalid explicit export ${JSON.stringify(outputs)} fails contract`, async () => {
    const rt = new TestRuntime(); rt.run = async () => ({ status: "succeeded", outputs });
    const executed = await execute(rt, node({ concurrency: 1 })); assert.equal(executed.status, "failed"); const r = result(executed);
    assert.equal(r.error?.code, "CHILD_EXPORT_INVALID"); assert.equal(r.items[0].error?.category, "contract");
    assert.equal(rt.calls.length, 1); conserved(r);
  });
}
test("a workflow lacking declared outputs.result fails before child creation", async () => {
  const rt = new TestRuntime(); const child = workflow(); delete child.outputs;
  assert.equal((await execute(rt, node(), context(), child)).status, "failed"); assert.equal(rt.calls.length, 0);
});
for (const failAt of ["create", "intent", "completion", "seal"]) {
  test(`persistence failure at ${failAt} propagates and never returns memory-only success`, async () => {
    const rt = new TestRuntime(); const failure = new Error(`disk-${failAt}`);
    rt.beforeSave = (s, expected) => {
      if ((failAt === "create" && expected === null) || (failAt === "intent" && s.children.some(c => c.status === "dispatch_pending"))
        || (failAt === "completion" && s.children.some(c => c.status === "succeeded")) || (failAt === "seal" && s.status === "succeeded")) throw failure;
    };
    await assert.rejects(execute(rt, node({ concurrency: 1 }), context([plan[0]])), failure);
    assert.notEqual(rt.state?.status, "succeeded"); if (failAt === "create" || failAt === "intent") assert.equal(rt.calls.length, 0);
  });
}
test("resume skips persisted completions and preserves identities, params and child version", async () => {
  const rt = new TestRuntime(); let crash = true;
  rt.beforeSave = s => { if (crash && s.children[1]?.status === "dispatch_pending") throw new Error("restart"); };
  await assert.rejects(execute(rt, node({ concurrency: 1 })), /restart/);
  const saved = copy(rt.state!); assert.equal(saved.children[0].status, "succeeded"); crash = false;
  const changed = workflow(); changed.version = 99;
  const r = result(await executeSubworkflowFanout(node(), context("broken on resume"), parent(), "parent-1", changed, rt));
  assert.equal(r.status, "succeeded"); assert.equal(r.groupExecutionId, saved.groupExecutionId);
  assert.deepEqual(rt.state!.children.map(c => c.childFlowId), saved.children.map(c => c.childFlowId));
  assert.equal(rt.calls.filter(c => c.itemIndex === 0).length, 1); assert.ok(rt.calls.every(c => c.childWorkflow.version === 1));
  const calls = rt.calls.length; assert.deepEqual(result(await execute(rt)), r); assert.equal(rt.calls.length, calls);
});
test("unknown accepted start remains pending, throws, and rejoins the same child on recovery", async () => {
  const rt = new TestRuntime(); const start = rt.startOrGetChild.bind(rt); let lost = true;
  rt.startOrGetChild = async (r, signal) => {
    const completion = await start(r, signal);
    if (lost) { lost = false; throw new FanoutRuntimeError({ category: "orchestration", code: "START_UNKNOWN", message: "response lost" }); }
    return completion;
  };
  await assert.rejects(execute(rt, node({ concurrency: 1 })), FanoutRuntimeError);
  assert.equal(rt.state?.children[0].status, "dispatch_unknown"); assert.equal(rt.state?.children[0].result, null); assert.equal(rt.instances.size, 1);
  const id = rt.state?.children[0].childFlowId; const r = result(await execute(rt));
  assert.equal(r.successCount, 5); assert.equal(rt.instances.size, 5); assert.equal(rt.calls[1].childFlowId, id);
});
test("ordinary start errors and parent-status read failures fail closed", async () => {
  const rt = new TestRuntime(); rt.run = async () => { throw new Error("permission service unreachable"); };
  await assert.rejects(execute(rt, node({ concurrency: 1 })), /permission service unreachable/);
  assert.notEqual(rt.state?.status, "succeeded"); assert.equal(rt.calls.length, 1);
  const other = new TestRuntime(); other.parentStatus = async () => { throw new Error("cannot read parent"); };
  await assert.rejects(execute(other), /cannot read parent/); assert.equal(other.calls.length, 0);
});
test("confirmed start rejection is not a tolerable execution failure", async () => {
  const rt = new TestRuntime(); rt.run = async () => { throw new FanoutRuntimeError({ category: "authorization", code: "DENIED", message: "not authorized" }, "rejected"); };
  const executed = await execute(rt, node({ concurrency: 1 })); assert.equal(executed.status, "failed"); const r = result(executed);
  assert.equal(r.rejectedCount, 1); assert.equal(r.failedCount, 0); assert.equal(r.error?.code, "DENIED"); assert.equal(rt.calls.length, 1);
});
function cancellationAware(_r: FanoutChildRequest, signal: AbortSignal): Promise<FanoutChildCompletion> {
  return new Promise(resolve => {
    if (signal.aborted) resolve({ status: "cancelled" });
    else signal.addEventListener("abort", () => resolve({ status: "cancelled" }), { once: true });
  });
}
test("cancellation retains succeeded, failed, active and never-dispatched items", async () => {
  const rt = new TestRuntime(); const abort = new AbortController();
  rt.run = async (r, signal) => {
    if (r.itemIndex === 0) return ok(r);
    if (r.itemIndex === 1) return { status: "failed", error: { category: "execution", code: "FAILED", message: "confirmed" } };
    setTimeout(() => abort.abort(), 5); return cancellationAware(r, signal);
  };
  const executed = await execute(rt, node({ concurrency: 1 }), context(), workflow(), abort.signal);
  assert.equal(executed.status, "failed"); const r = result(executed);
  assert.equal(r.status, "cancelled"); assert.deepEqual(r.items.map(i => i.status), ["succeeded", "failed", "cancelled", "cancelled", "cancelled"]);
  assert.equal(r.cancelledCount, 3); assert.equal(r.complete, true); assert.equal(rt.calls.length, 3); assert.equal(rt.cancelCalls.length, 1); conserved(r);
});
test("parent cancellation wins over the last successful child completion", async () => {
  const rt = new TestRuntime(); rt.run = async r => { rt.parent = "cancelled"; return ok(r); };
  const r = result(await execute(rt, node(), context([plan[0]])));
  assert.equal(r.status, "cancelled"); assert.equal(r.successCount, 1); assert.equal(r.items[0].status, "succeeded");
});
test("atomic parent seal barrier propagates a cancellation race instead of success", async () => {
  const rt = new TestRuntime(); rt.beforeSave = s => { if (s.status === "succeeded") rt.parent = "cancelled"; };
  await assert.rejects(execute(rt, node(), context([])), /parent seal barrier/); assert.notEqual(rt.state?.status, "succeeded");
});
test("group deadline preserves unconfirmed cleanup responsibility and ignores late completion", async () => {
  const rt = new TestRuntime(); let finish!: (c: FanoutChildCompletion) => void;
  rt.run = () => new Promise(resolve => { finish = resolve; });
  const r = result(await execute(rt, node({ concurrency: 1, groupTimeoutSeconds: 0.04 })));
  assert.equal(r.status, "timed_out"); assert.equal(r.complete, false); assert.equal(r.pendingCount, 1); assert.equal(r.cancelledCount, 4);
  assert.equal(r.items[0].status, "cancel_requested"); assert.deepEqual(rt.state?.cleanupPendingChildIds, [rt.calls[0].childFlowId]);
  assert.equal(rt.cancelCalls.length, 1); const sealed = copy(rt.state); finish(ok(rt.calls[0])); await delay(5); assert.deepEqual(rt.state, sealed); conserved(r);
});
test("cancellation grace expiry never invents a cancelled child", async () => {
  const rt = new TestRuntime(); const abort = new AbortController(); rt.run = () => { abort.abort(); return new Promise(() => {}); };
  const r = result(await execute(rt, node({ concurrency: 1 }), context(), workflow(), abort.signal));
  assert.equal(r.status, "timed_out"); assert.equal(r.error?.code, "CANCEL_CONFIRMATION_TIMEOUT"); assert.equal(r.pendingCount, 1); assert.equal(r.complete, false); conserved(r);
});
test("expired persisted deadline does not reset or dispatch queued children on resume", async () => {
  const rt = new TestRuntime(); rt.beforeSave = (_s, expected) => { if (expected !== null) throw new Error("crash after creation"); };
  await assert.rejects(execute(rt, node({ groupTimeoutSeconds: 0.02 })), /crash/); rt.beforeSave = undefined; await delay(25);
  const r = result(await execute(rt)); assert.equal(r.status, "timed_out"); assert.equal(rt.calls.length, 0);
});
test("concurrent executors compete through CAS and never duplicate child instances", async () => {
  const rt = new TestRuntime(); rt.run = async r => { await delay(2); return ok(r); };
  const runs = await Promise.allSettled([execute(rt), execute(rt)]);
  assert.equal(runs.filter(r => r.status === "fulfilled").length, 1); assert.equal(runs.filter(r => r.status === "rejected").length, 1);
  assert.equal(rt.instances.size, 5); assert.ok(rt.peak <= 2);
});

test("failure observed while reserving the next item prevents its launch", async () => {
  const rt = new TestRuntime();
  let failSecond!: (c: FanoutChildCompletion) => void;
  rt.run = async r => {
    if (r.itemIndex === 0) { await delay(4); return ok(r); }
    if (r.itemIndex === 1) return new Promise(resolve => { failSecond = resolve; });
    return ok(r);
  };
  let injected = false;
  rt.beforeSave = async s => {
    if (!injected && s.children[2]?.status === "dispatch_pending") {
      injected = true;
      failSecond({ status: "failed", error: { category: "authorization", code: "REVOKED", message: "authorization revoked" } });
      await delay(1);
    }
  };
  const executed = await execute(rt);
  assert.equal(executed.status, "failed");
  assert.ok([...rt.instances.values()].every(c => c.request.itemIndex < 2), "known fatal observation must prevent another start");
});

test("abort-rejecting observation yields to cancellation confirmation instead of busy-looping", async () => {
  const rt = new TestRuntime(); const abort = new AbortController(); let confirmed = false;
  rt.startOrGetChild = async request => {
    rt.calls.push(copy(request));
    if (confirmed) return { status: "cancelled" };
    if (rt.calls.length === 1) {
      abort.abort();
      setTimeout(() => { confirmed = true; }, 5);
    }
    throw new Error("observation aborted, remote state unknown");
  };
  const r = result(await execute(rt, node({ concurrency: 1, cancelGraceSeconds: 0.1 }), context(), workflow(), abort.signal));
  assert.equal(r.status, "cancelled"); assert.equal(r.pendingCount, 0);
  assert.ok(rt.calls.length >= 2 && rt.calls.length <= 4, "bounded re-observation must yield to timers/I/O");
});

test("cancel port failures propagate with durable cleanup obligations", async () => {
  const rt = new TestRuntime(); const abort = new AbortController();
  rt.run = () => { abort.abort(); return new Promise(() => {}); };
  rt.cancelChild = async () => { throw new Error("cancel permission denied"); };
  await assert.rejects(execute(rt, node({ concurrency: 1 }), context(), workflow(), abort.signal), /cancel permission denied/);
  assert.equal(rt.state?.status, "cancelling");
  assert.equal(rt.state?.cleanupPendingChildIds.length, 1);
});

test("lost completion write rejoins an already completed child without recreating it", async () => {
  const rt = new TestRuntime();
  rt.beforeSave = s => { if (s.children[0].status === "succeeded") throw new Error("completion commit lost"); };
  await assert.rejects(execute(rt, node({ concurrency: 1 }), context([plan[0]])), /completion commit lost/);
  assert.equal(rt.instances.size, 1);
  const saved = copy(rt.state!); rt.beforeSave = undefined;
  const r = result(await execute(rt));
  assert.equal(r.status, "succeeded"); assert.equal(rt.instances.size, 1);
  assert.equal(r.items[0].childFlowId, saved.children[0].childFlowId);
  assert.equal(rt.calls.length, 2);
});

test("committed-but-unacknowledged seal is replayed without resealing", async () => {
  const rt = new TestRuntime(); const save = rt.save.bind(rt); let loseResponse = true;
  rt.save = async (s, expected) => {
    await save(s, expected);
    if (s.status === "succeeded" && loseResponse) { loseResponse = false; throw new Error("seal response lost"); }
  };
  await assert.rejects(execute(rt), /seal response lost/);
  const saved = copy(rt.state!); const count = rt.history.length;
  assert.deepEqual(result(await execute(rt)), saved.result);
  assert.equal(rt.history.length, count); assert.equal(rt.calls.length, 5);
});

test("multiple parent runs with equal item keys have isolated child and group identities", async () => {
  const first = new TestRuntime(); const second = new TestRuntime();
  const [a, b] = await Promise.all([execute(first), executeSubworkflowFanout(node(), context(), parent(), "parent-2", workflow(), second)]);
  assert.notEqual(result(a).groupExecutionId, result(b).groupExecutionId);
  for (const key of ["childFlowId", "sessionKey", "stateDirRef"] as const) {
    assert.equal(new Set([...first.calls, ...second.calls].map(c => c[key])).size, 10);
  }
});

test("a stalled parent-status read cannot disable the persisted group deadline", async () => {
  const rt = new TestRuntime(); let reads = 0;
  rt.parentStatus = async () => { reads++; return rt.calls.length === 0 ? "running" : new Promise(() => {}); };
  rt.run = () => new Promise(() => {});
  const execution = execute(rt, node({ concurrency: 1, groupTimeoutSeconds: 0.02 }));
  const completed = await Promise.race([execution, delay(150).then(() => null)]);
  assert.ok(completed, "deadline must interrupt the stalled parent read");
  const r = result(completed); assert.equal(r.status, "timed_out");
  assert.equal(r.pendingCount, 1); assert.equal(rt.state?.cleanupPendingChildIds.length, 1);
});

test("caller abort interrupts a stalled parent read and confirms cancellation", async () => {
  const rt = new TestRuntime(); const abort = new AbortController(); let reads = 0;
  rt.parentStatus = async () => { reads++; return rt.calls.length === 0 ? "running" : new Promise(() => {}); };
  rt.run = cancellationAware;
  const execution = execute(rt, node({ concurrency: 1 }), context(), workflow(), abort.signal);
  setTimeout(() => abort.abort(), 5);
  const completed = await Promise.race([execution, delay(150).then(() => null)]);
  assert.ok(completed, "abort must interrupt the stalled parent read");
  assert.equal(result(completed).status, "cancelled");
  assert.equal(result(completed).pendingCount, 0);
});

for (const mode of ["abort", "deadline", "parent"] as const) {
  test(`${mode} during completion persistence prevents the reserved child's launch`, async () => {
    const rt = new TestRuntime(); const abort = new AbortController();
    let completeFirst!: (c: FanoutChildCompletion) => void;
    rt.run = async (request, signal) => request.itemIndex === 0
      ? new Promise(resolve => { completeFirst = resolve; }) : cancellationAware(request, signal);
    let delivered = false; let interrupted = false;
    rt.beforeSave = async s => {
      if (!delivered && s.children[1]?.status === "dispatch_pending") {
        delivered = true; await Promise.resolve(); completeFirst(ok(rt.calls[0])); await delay(1);
      }
      if (delivered && !interrupted && s.children[0].status === "succeeded") {
        interrupted = true;
        if (mode === "abort") abort.abort();
        else if (mode === "parent") rt.parent = "cancelled";
        else await delay(60);
      }
    };
    const r = result(await execute(rt, node({ groupTimeoutSeconds: mode === "deadline" ? 0.05 : 5 }), context(), workflow(), abort.signal));
    assert.notEqual(r.status, "succeeded");
    assert.ok([...rt.instances.values()].every(i => i.request.itemIndex === 0), "must recheck lifecycle after awaited completion persistence");
  });
}

test("zero cancellation grace is rejected consistently with the public schema", async () => {
  const rt = new TestRuntime();
  const executed = await execute(rt, node({ cancelGraceSeconds: 0 }));
  assert.equal(executed.status, "failed"); assert.equal(rt.calls.length, 0);
});

test("published release version comes from the pinned parent dependency, not spec.version", async () => {
  const rt = new TestRuntime(); const p = parent(); const child = workflow(); delete child.version;
  p.subworkflowDependencies.child.publishedVersion = 23;
  rt.beforeSave = s => { if (s.children[1]?.status === "dispatch_pending") throw new Error("restart-version"); };
  await assert.rejects(executeSubworkflowFanout(node({ concurrency: 1 }), context(), p, "parent-1", child, rt), /restart-version/);
  p.subworkflowDependencies.child.publishedVersion = 99; rt.beforeSave = undefined;
  const r = result(await executeSubworkflowFanout(node(), context(), p, "parent-1", { ...child, version: 88 }, rt));
  assert.equal(r.childPublishedVersion, 23);
  assert.ok(rt.calls.every(c => c.childWorkflow.version === undefined));
});

test("a synchronous rejection while rejoining reservations stops subsequent starts", async () => {
  const rt = new TestRuntime();
  rt.beforeSave = (_s, expected) => { if (expected !== null) throw new Error("snapshot ready"); };
  await assert.rejects(execute(rt), /snapshot ready/);
  rt.beforeSave = undefined;
  rt.state!.children[0].status = "dispatch_pending";
  rt.state!.children[1].status = "dispatch_pending";
  const starts: number[] = [];
  rt.startOrGetChild = (request, signal) => {
    if (signal.aborted) return Promise.resolve({ status: "cancelled" });
    starts.push(request.itemIndex);
    if (request.itemIndex === 0) throw new FanoutRuntimeError({ category: "authorization", code: "DENIED", message: "known start rejection" }, "rejected");
    return Promise.resolve(ok(request));
  };
  assert.equal((await execute(rt)).status, "failed");
  assert.deepEqual(starts, [0]);
});
