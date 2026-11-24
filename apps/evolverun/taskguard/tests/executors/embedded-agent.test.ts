import assert from "node:assert/strict";
import { getEventListeners } from "node:events";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, test, type TestContext } from "node:test";
import { executeEmbeddedAgent, type EmbeddedAgentApi, type ExecuteEmbeddedAgentOptions } from "../../src/executors/embedded-agent.js";
import { clearEmbeddedContextCaches } from "../../src/executors/embedded-context.js";
import { EXECUTION_ABORT_GRACE_MS } from "../../src/executors/execution-deadline.js";
import { acquireLlmPermit, getLlmSemaphoreStats, resetLlmSemaphore } from "../../src/executors/llm-semaphore.js";
import { clearCachedConfig, setCachedConfig } from "../../src/config/config-cache.js";
import { defaults } from "../../src/config/types.js";
import { defaultWorkflowLogDir } from "../../src/workflow-log.js";
import type { TemplateContext } from "../../src/runner.js";
import type { WorkflowNode } from "../../src/types.js";

const originalEnv = { HOME: process.env.HOME, WORKFLOW_LOG_DIR: process.env.WORKFLOW_LOG_DIR, MAX_CONCURRENT_LLM_CALLS: process.env.MAX_CONCURRENT_LLM_CALLS };
let home: string;
const flush = async () => { for (let i = 0; i < 20; i++) await new Promise<void>((resolve) => setImmediate(resolve)); };
const flushMicrotasks = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
async function until(predicate: () => boolean) {
  for (let i = 0; i < 1_000; i++) {
    if (predicate()) return;
    await new Promise<void>((resolve) => setImmediate(resolve));
  }
  assert.fail("expected executor phase was not reached");
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

beforeEach(async () => {
  home = await mkdtemp(join(tmpdir(), "taskguard-executor-test-"));
  process.env.HOME = home;
  process.env.WORKFLOW_LOG_DIR = join(home, "logs");
  process.env.MAX_CONCURRENT_LLM_CALLS = "1";
  assert.equal(homedir(), home);
  assert.ok(defaultWorkflowLogDir().startsWith(home));
  clearEmbeddedContextCaches();
  resetLlmSemaphore();
  setCachedConfig({ database: { type: "sqlite", sqlitePath: ":memory:" }, app: structuredClone(defaults) });
  await writeFile(join(home, "session.jsonl"), "");
});
afterEach(async () => {
  await flush(); // Drain fire-and-forget diagnostic writes before removing HOME.
  await rm(home, { recursive: true, force: true, maxRetries: 3 });
  for (const [key, value] of Object.entries(originalEnv)) {
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
  clearEmbeddedContextCaches();
  clearCachedConfig();
  resetLlmSemaphore();
});

function fixture(t: TestContext, run: EmbeddedAgentApi["runtime"]["agent"]["runEmbeddedPiAgent"], options: ExecuteEmbeddedAgentOptions = {}) {
  t.mock.method(console, "log", () => {});
  t.mock.method(console, "warn", () => {});
  t.mock.method(console, "error", () => {});
  // Hash("d") % 20 === 0: no real/fake stagger delay is needed in these tests.
  const node = {
    id: "d", title: "Deadline test", phase: "execute", dependsOn: [],
    executor: { type: "embedded-agent", prompt: "test only", timeoutSeconds: 1, contextPolicy: { history: "inherit" } },
  } as WorkflowNode;
  const runtime = t.mock.fn(run);
  const ensure = t.mock.fn(async () => {});
  const api: EmbeddedAgentApi = { runtime: { agent: { runEmbeddedPiAgent: runtime, ensureAgentWorkspace: ensure } } };
  const events: string[] = [];
  const outputs: string[] = [];
  const executionOptions: ExecuteEmbeddedAgentOptions = {
    sessionKey: "agent:test:deadline",
    toolCtx: { sessionId: "test-session", sessionFile: join(home, "session.jsonl"), workspaceDir: home, agentDir: home },
    agentEvent: (event) => { events.push(event.event); },
    finalOutput: (output) => { outputs.push(output); },
    ...options,
  };
  const execute = () => executeEmbeddedAgent(node, { params: {}, nodeOutput: {}, skillRoot: home } as TemplateContext, api, executionOptions);
  return { execute, runtime, ensure, api, node, events, outputs };
}

test("native runtime ignoring abort returns failed/no-retry while retaining the permit; late callbacks/resolve are ignored", async (t) => {
  const pending = deferred<{ output: string }>();
  const parent = new AbortController();
  const f = fixture(t, () => pending.promise, { abortSignal: parent.signal });
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval", "Date"], now: 100 });
  let returned = false;
  const execution = f.execute().then((result) => { returned = true; return result; });
  await until(() => f.runtime.mock.calls.length === 1);
  const params = f.runtime.mock.calls[0].arguments[0];
  const signal = params.abortSignal as AbortSignal;
  assert.notEqual(signal, parent.signal, "each attempt must own its signal");
  assert.equal(getLlmSemaphoreStats().active, 1);
  const eventsAtDeadline = f.events.length;
  t.mock.timers.tick(1_000);
  await flushMicrotasks();
  assert.equal(signal.aborted, true);
  assert.equal(parent.signal.aborted, false);
  assert.equal(returned, false);
  for (const [name, payload] of [
    ["onAssistantMessageStart", undefined], ["onReasoningStream", { text: "late" }],
    ["onReasoningEnd", undefined], ["onPartialReply", { text: "late" }],
    ["onToolResult", {}], ["onAgentEvent", { stream: "lifecycle", data: { phase: "end" } }],
  ] as const) await (params[name] as (payload?: unknown) => Promise<void>)(payload);
  assert.equal(f.events.length, eventsAtDeadline);
  t.mock.timers.tick(EXECUTION_ABORT_GRACE_MS);
  const result = await execution;
  assert.equal(result.status, "failed");
  assert.equal(result.retryable, false);
  assert.equal(f.runtime.mock.calls.length, 1);
  assert.equal(getEventListeners(parent.signal, "abort").length, 0);
  // TTL must not manufacture capacity for another request while this runtime lives.
  const queuedAbort = new AbortController();
  const queued = acquireLlmPermit({}, queuedAbort.signal);
  const cancelled = assert.rejects(queued);
  t.mock.timers.tick(120_000);
  await flushMicrotasks();
  assert.equal(getLlmSemaphoreStats().active, 1);
  assert.equal(getLlmSemaphoreStats().waiting, 1);
  queuedAbort.abort();
  await cancelled;
  pending.resolve({ output: "late success" });
  await until(() => getLlmSemaphoreStats().active === 0);
  assert.equal(result.status, "failed");
  assert.deepEqual(f.outputs, []);
  assert.equal(f.events.length, eventsAtDeadline);
  assert.equal(f.runtime.mock.calls.length, 1);
});

test("a runtime that never settles still returns failed/no-retry at the caller boundary", async (t) => {
  const f = fixture(t, () => new Promise(() => {}));
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval", "Date"] });
  const execution = f.execute();
  await until(() => f.runtime.mock.calls.length === 1);
  t.mock.timers.tick(1_000);
  await flushMicrotasks();
  t.mock.timers.tick(EXECUTION_ABORT_GRACE_MS);
  const result = await execution;
  assert.equal(result.status, "failed");
  assert.equal(result.retryable, false);
  assert.equal(getLlmSemaphoreStats().active, 1, "unsettled work keeps its permit");
  assert.equal(getLlmSemaphoreStats().waiting, 0);
  assert.equal(f.runtime.mock.calls.length, 1);
});

test("late native rejection is consumed and frees the real permit only on settlement", async (t) => {
  const pending = deferred<{ output: string }>();
  const f = fixture(t, () => pending.promise);
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval", "Date"] });
  const execution = f.execute();
  await until(() => f.runtime.mock.calls.length === 1);
  t.mock.timers.tick(1_000);
  await flushMicrotasks();
  t.mock.timers.tick(EXECUTION_ABORT_GRACE_MS);
  const result = await execution;
  assert.equal(result.retryable, false);
  pending.reject(new Error("late native failure"));
  await until(() => getLlmSemaphoreStats().active === 0);
  assert.equal(f.runtime.mock.calls.length, 1);
});

test("cooperative runtime abort settles and returns a retry-eligible timeout failure", async (t) => {
  const f = fixture(t, (params) => new Promise((_resolve, reject) => {
    const signal = params.abortSignal as AbortSignal;
    signal.addEventListener("abort", () => reject(signal.reason), { once: true });
  }));
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval", "Date"] });
  const execution = f.execute();
  await until(() => f.runtime.mock.calls.length === 1);
  t.mock.timers.tick(1_000);
  const result = await execution;
  assert.equal(result.status, "failed");
  assert.equal(result.retryable, undefined);
  assert.equal(getLlmSemaphoreStats().active, 0);
  assert.equal(f.runtime.mock.calls.length, 1);
});

test("parent cancellation is never retryable even when the native runtime cooperates", async (t) => {
  const parent = new AbortController();
  const f = fixture(t, (params) => new Promise((_resolve, reject) => {
    const signal = params.abortSignal as AbortSignal;
    signal.addEventListener("abort", () => reject(signal.reason), { once: true });
  }), { abortSignal: parent.signal });
  const execution = f.execute();
  await until(() => f.runtime.mock.calls.length === 1);
  parent.abort();
  const result = await execution;
  assert.equal(result.status, "failed");
  assert.equal(result.retryable, false);
  assert.equal(getLlmSemaphoreStats().active, 0);
  assert.equal(f.runtime.mock.calls.length, 1);
});

test("upstream already aborted skips preparation and runtime entirely", async (t) => {
  const parent = new AbortController();
  parent.abort();
  const f = fixture(t, async () => ({ output: "must not start" }), { abortSignal: parent.signal });
  const result = await f.execute();
  assert.equal(result.status, "failed");
  assert.equal(result.retryable, false);
  assert.equal(f.ensure.mock.calls.length, 0);
  assert.equal(f.runtime.mock.calls.length, 0);
  assert.deepEqual(f.events, []);
});

test("abort while waiting for a permit removes the waiter and prevents late runtime start", async (t) => {
  const parent = new AbortController();
  const f = fixture(t, async () => ({ output: "must not start" }), { abortSignal: parent.signal });
  const release = await acquireLlmPermit({});
  const execution = f.execute();
  await until(() => getLlmSemaphoreStats().waiting === 1);
  parent.abort();
  const result = await execution;
  assert.equal(result.retryable, false);
  assert.equal(getLlmSemaphoreStats().waiting, 0);
  release();
  await flush();
  assert.equal(f.runtime.mock.calls.length, 0);
  assert.equal(getLlmSemaphoreStats().active, 0);
});

test("deadline includes semaphore queueing and cannot send a request after release", async (t) => {
  const f = fixture(t, async () => ({ output: "must not start" }));
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval", "Date"] });
  const release = await acquireLlmPermit({});
  const execution = f.execute();
  await until(() => getLlmSemaphoreStats().waiting === 1);
  t.mock.timers.tick(1_000);
  const result = await execution;
  assert.equal(result.status, "failed");
  assert.equal(getLlmSemaphoreStats().waiting, 0);
  release();
  await flush();
  assert.equal(f.runtime.mock.calls.length, 0);
});

test("deadline covers hung preparation; resuming it later cannot launch native work", async (t) => {
  const pending = deferred<void>();
  const f = fixture(t, async () => ({ output: "must not start" }));
  f.api.runtime.agent.ensureAgentWorkspace = f.ensure = t.mock.fn(() => pending.promise);
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval", "Date"] });
  const execution = f.execute();
  await until(() => f.ensure.mock.calls.length === 1);
  t.mock.timers.tick(1_000);
  await flushMicrotasks();
  t.mock.timers.tick(EXECUTION_ABORT_GRACE_MS);
  const result = await execution;
  assert.equal(result.status, "failed");
  assert.equal(result.retryable, false);
  pending.resolve();
  await flush();
  assert.equal(f.runtime.mock.calls.length, 0);
  assert.deepEqual(f.events, []);
  assert.equal(getLlmSemaphoreStats().active, 0);
});

test("deadline also bounds hung result processing; late completion cannot become success", async (t) => {
  const pending = deferred<void>();
  let finalStarted = false;
  const f = fixture(t, async () => ({ output: "runtime done" }), { finalOutput: () => { finalStarted = true; return pending.promise; } });
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval", "Date"] });
  const execution = f.execute();
  await until(() => finalStarted);
  t.mock.timers.tick(1_000);
  await flushMicrotasks();
  t.mock.timers.tick(EXECUTION_ABORT_GRACE_MS);
  const result = await execution;
  assert.equal(result.status, "failed");
  assert.equal(result.retryable, false);
  pending.resolve();
  await until(() => getLlmSemaphoreStats().active === 0);
  assert.equal(result.status, "failed");
  assert.equal(f.runtime.mock.calls.length, 1);
});

test("normal success clears deadline/heartbeat/listeners and ignores post-success callbacks", async (t) => {
  const parent = new AbortController();
  const f = fixture(t, async () => ({ output: "ok" }), { abortSignal: parent.signal });
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval", "Date"] });
  const result = await f.execute();
  assert.equal(result.status, "succeeded");
  assert.deepEqual(f.outputs, ["ok"]);
  assert.equal(getLlmSemaphoreStats().active, 0);
  assert.equal(getEventListeners(parent.signal, "abort").length, 0);
  const params = f.runtime.mock.calls[0].arguments[0];
  assert.equal(getEventListeners(params.abortSignal as AbortSignal, "abort").length, 0);
  const events = f.events.length;
  await (params.onToolResult as () => Promise<void>)();
  t.mock.timers.tick(200_000);
  assert.equal((params.abortSignal as AbortSignal).aborted, false);
  assert.equal(f.events.length, events);
  assert.equal(f.runtime.mock.calls.length, 1);
});

test("confirmed settled rate-limit responses retain the original retry policy", async (t) => {
  let calls = 0;
  const f = fixture(t, async () => ++calls === 1 ? { error: "429 rate limit" } : { output: "recovered" });
  f.node.executor = { ...f.node.executor, timeoutSeconds: 20 } as WorkflowNode["executor"];
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval", "Date"] });
  const execution = f.execute();
  await until(() => f.events.includes("agent_event"));
  await flushMicrotasks();
  t.mock.timers.tick(5_000);
  const result = await execution;
  assert.equal(result.status, "succeeded");
  assert.equal(calls, 2);
  assert.deepEqual(f.outputs, ["recovered"]);
});

test("native abort settling cannot enable retry while its unawaited nested event callback is still alive", async (t) => {
  const gate = deferred<void>();
  let callbackStarted = false;
  let nativeSettled = false;
  let lateSideEffects = 0;
  let returned = false;
  const f = fixture(t, (params) => {
    void (params.onToolResult as (payload: Record<string, unknown>) => Promise<void>)({ text: "tool result" });
    return new Promise((_resolve, reject) => {
      const signal = params.abortSignal as AbortSignal;
      signal.addEventListener("abort", () => {
        nativeSettled = true;
        reject(signal.reason);
      }, { once: true });
    });
  }, {
    agentEvent: async (event) => {
      if (event.event !== "tool_result") return;
      callbackStarted = true;
      await gate.promise;
      lateSideEffects++;
    },
  });
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval", "Date"] });
  const execution = f.execute().then((result) => { returned = true; return result; });
  await until(() => callbackStarted);
  t.mock.timers.tick(1_000);
  await until(() => nativeSettled && getLlmSemaphoreStats().active === 0);
  await flush();
  assert.equal(returned, false, "the native runtime has settled, but its callback has not");
  t.mock.timers.tick(EXECUTION_ABORT_GRACE_MS - 1);
  await flushMicrotasks();
  assert.equal(returned, false);
  t.mock.timers.tick(1);
  const result = await execution;
  assert.equal(result.status, "failed");
  assert.equal(result.retryable, false);
  gate.resolve();
  await until(() => lateSideEffects === 1);
  assert.equal(result.retryable, false, "a callback's own await cannot be interrupted, so retry must stay forbidden");
  assert.equal(f.runtime.mock.calls.length, 1);
});

test("native success waits for a fire-and-forget event callback to finish before executor success", async (t) => {
  const gate = deferred<void>();
  let callbackStarted = false;
  let callbackExited = false;
  let returned = false;
  const f = fixture(t, async (params) => {
    void (params.onToolResult as (payload: Record<string, unknown>) => Promise<void>)({ text: "tool result" });
    return { output: "ok" };
  }, {
    agentEvent: async (event) => {
      if (event.event !== "tool_result") return;
      callbackStarted = true;
      await gate.promise;
      callbackExited = true;
    },
  });
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval", "Date"] });
  const execution = f.execute().then((result) => { returned = true; return result; });
  await until(() => callbackStarted && f.outputs.length === 1 && getLlmSemaphoreStats().active === 0);
  await flush();
  assert.equal(returned, false);
  gate.resolve();
  const result = await execution;
  assert.equal(callbackExited, true);
  assert.equal(result.status, "succeeded");
  assert.equal(f.runtime.mock.calls.length, 1);
});

test("native success with a stuck fire-and-forget callback fails boundedly instead of leaving retryable side effects", async (t) => {
  const gate = deferred<void>();
  let callbackStarted = false;
  let callbackExited = false;
  let returned = false;
  const f = fixture(t, async (params) => {
    void (params.onToolResult as (payload: Record<string, unknown>) => Promise<void>)({ text: "tool result" });
    return { output: "ok" };
  }, {
    agentEvent: async (event) => {
      if (event.event !== "tool_result") return;
      callbackStarted = true;
      await gate.promise;
      callbackExited = true;
    },
  });
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval", "Date"] });
  const execution = f.execute().then((result) => { returned = true; return result; });
  await until(() => callbackStarted && f.outputs.length === 1 && getLlmSemaphoreStats().active === 0);
  await flush();
  assert.equal(returned, false);
  t.mock.timers.tick(1_000);
  await flushMicrotasks();
  assert.equal(returned, false);
  t.mock.timers.tick(EXECUTION_ABORT_GRACE_MS);
  const result = await execution;
  assert.equal(result.status, "failed");
  assert.equal(result.retryable, false);
  gate.resolve();
  await until(() => callbackExited);
  assert.equal(result.status, "failed");
  assert.equal(f.runtime.mock.calls.length, 1);
});
