import assert from "node:assert/strict";
import { getEventListeners } from "node:events";
import { afterEach, beforeEach, test } from "node:test";
import { acquireLlmPermit, getLlmSemaphoreStats, resetLlmSemaphore } from "../../src/executors/llm-semaphore.js";

const originalMax = process.env.MAX_CONCURRENT_LLM_CALLS;
beforeEach(() => {
  process.env.MAX_CONCURRENT_LLM_CALLS = "1";
  resetLlmSemaphore();
});
afterEach(() => {
  if (originalMax === undefined) delete process.env.MAX_CONCURRENT_LLM_CALLS;
  else process.env.MAX_CONCURRENT_LLM_CALLS = originalMax;
  resetLlmSemaphore();
});

test("queued cancellation removes the waiter/listener and never grants it later", async () => {
  const release = await acquireLlmPermit({ nodeId: "running" });
  const parent = new AbortController();
  let lateGrant = false;
  const pending = acquireLlmPermit({ nodeId: "cancelled" }, parent.signal).then((release) => {
    lateGrant = true;
    release();
  });
  const rejected = assert.rejects(pending, /cancelled/);
  assert.equal(getLlmSemaphoreStats().waiting, 1);
  parent.abort(new Error("cancelled"));
  await rejected;
  assert.equal(getLlmSemaphoreStats().waiting, 0);
  assert.equal(getEventListeners(parent.signal, "abort").length, 0);
  release();
  release();
  await Promise.resolve();
  assert.equal(lateGrant, false);
  assert.equal(getLlmSemaphoreStats().active, 0);
  assert.equal(getLlmSemaphoreStats().totalAcquired, 1);
});

test("pre-aborted acquire neither queues nor consumes a permit", async () => {
  const parent = new AbortController();
  parent.abort(new Error("cancelled"));
  await assert.rejects(acquireLlmPermit({}, parent.signal), /cancelled/);
  assert.equal(getLlmSemaphoreStats().active, 0);
  assert.equal(getLlmSemaphoreStats().waiting, 0);
  assert.equal(getLlmSemaphoreStats().totalAcquired, 0);
});

test("abort immediately after a grant releases it before returning to the caller", async () => {
  const parent = new AbortController();
  const pending = acquireLlmPermit({}, parent.signal);
  parent.abort(new Error("cancelled"));
  await assert.rejects(pending, /cancelled/);
  assert.equal(getLlmSemaphoreStats().active, 0);
  assert.equal(getLlmSemaphoreStats().waiting, 0);
});

test("TTL warns without over-dispatching; only true completion releases capacity", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "Date"], now: 100 });
  const warn = t.mock.method(console, "error", () => {});
  const release = await acquireLlmPermit({ nodeId: "ignores-abort" });
  let nextStarted = false;
  const next = acquireLlmPermit({ nodeId: "next" }).then((releaseNext) => {
    nextStarted = true;
    return releaseNext;
  });
  t.mock.timers.tick(120_001);
  for (let i = 0; i < 10; i++) await Promise.resolve();
  assert.equal(nextStarted, false);
  assert.equal(getLlmSemaphoreStats().active, 1);
  assert.equal(getLlmSemaphoreStats().waiting, 1);
  assert.equal(warn.mock.calls.length, 1);
  assert.match(String(warn.mock.calls[0].arguments[0]), /retaining permit/);
  release();
  const releaseNext = await next;
  assert.equal(nextStarted, true);
  assert.equal(getLlmSemaphoreStats().active, 1);
  assert.equal(getLlmSemaphoreStats().waiting, 0);
  releaseNext();
  assert.equal(getLlmSemaphoreStats().active, 0);
  assert.equal(getLlmSemaphoreStats().totalAcquired, 2);
});

test("configuration changes cannot replace a semaphore while requests remain alive", async () => {
  const release = await acquireLlmPermit({});
  process.env.MAX_CONCURRENT_LLM_CALLS = "2";
  assert.equal(getLlmSemaphoreStats().active, 1);
  assert.equal(getLlmSemaphoreStats().maxConcurrent, 1);
  release();
  assert.equal(getLlmSemaphoreStats().maxConcurrent, 2);
});
