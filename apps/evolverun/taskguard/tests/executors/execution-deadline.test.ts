import assert from "node:assert/strict";
import { getEventListeners } from "node:events";
import { test } from "node:test";
import { EXECUTION_ABORT_GRACE_MS, withExecutionDeadline, type ExecutionAttempt } from "../../src/executors/execution-deadline.js";

const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

for (const lateOutcome of ["resolve", "reject"] as const) {
  test(`ignoring abort returns within deadline + grace; late ${lateOutcome} is consumed`, async (t) => {
    t.mock.timers.enable({ apis: ["setTimeout", "Date"], now: 100 });
    const parent = new AbortController();
    const pending = deferred<{ status: "succeeded" }>();
    let attempt!: ExecutionAttempt;
    let returned = false;
    const resultPromise = withExecutionDeadline(1_000, parent.signal, async (scope) => {
      attempt = scope;
      return pending.promise;
    }).then((result) => { returned = true; return result; });
    t.mock.timers.tick(1_000);
    await flush();
    assert.equal(attempt.signal.aborted, true);
    assert.equal(attempt.isActive(), false);
    assert.equal(parent.signal.aborted, false, "attempt cancellation must not abort the parent");
    t.mock.timers.tick(EXECUTION_ABORT_GRACE_MS - 1);
    await flush();
    assert.equal(returned, false);
    t.mock.timers.tick(1);
    const result = await resultPromise;
    assert.equal(result.status, "failed");
    assert.equal(result.retryable, false);
    assert.match(result.error!, /deadline.*did not settle/);
    assert.equal(getEventListeners(parent.signal, "abort").length, 0);
    if (lateOutcome === "resolve") pending.resolve({ status: "succeeded" });
    else pending.reject(new Error("late runtime rejection"));
    await flush();
    assert.equal(result.status, "failed");
  });
}

for (const cooperativeOutcome of ["resolve", "reject"] as const) {
  test(`cooperative abort (${cooperativeOutcome}) is confirmed settled before allowing normal retry policy`, async (t) => {
    t.mock.timers.enable({ apis: ["setTimeout", "Date"], now: 100 });
    const resultPromise = withExecutionDeadline(1_000, undefined, ({ signal }) => new Promise((resolve, reject) => {
      signal.addEventListener("abort", () => {
        if (cooperativeOutcome === "resolve") resolve({ status: "succeeded" });
        else reject(signal.reason);
      }, { once: true });
    }));
    t.mock.timers.tick(1_000);
    const result = await resultPromise;
    assert.equal(result.status, "failed", "even a successful abort response cannot beat expiry");
    assert.equal(result.retryable, undefined);
  });
}

test("already-aborted parent never starts work and always forbids retry", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "Date"] });
  const parent = new AbortController();
  parent.abort(new Error("cancelled"));
  let calls = 0;
  const result = await withExecutionDeadline(1_000, parent.signal, async () => {
    calls++;
    return { status: "succeeded" };
  });
  assert.equal(calls, 0);
  assert.equal(result.status, "failed");
  assert.equal(result.retryable, false);
  assert.equal(getEventListeners(parent.signal, "abort").length, 0);
});

test("parent abort during timeout grace remains non-retryable even after settlement", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "Date"] });
  const parent = new AbortController();
  const pending = deferred<{ status: "succeeded" }>();
  const resultPromise = withExecutionDeadline(1_000, parent.signal, () => pending.promise);
  t.mock.timers.tick(1_000);
  parent.abort();
  pending.resolve({ status: "succeeded" });
  const result = await resultPromise;
  assert.equal(result.status, "failed");
  assert.equal(result.retryable, false);
  assert.match(result.error!, /parent signal/);
});

test("parent abort bounds uncooperative work and gates callbacks before abort listeners run", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "Date"] });
  const parent = new AbortController();
  let scope!: ExecutionAttempt;
  let callbacks = 0;
  const resultPromise = withExecutionDeadline(60_000, parent.signal, async (attempt) => {
    scope = attempt;
    const guarded = attempt.guard(() => { callbacks++; });
    attempt.signal.addEventListener("abort", () => { void guarded!(); }, { once: true });
    return new Promise(() => {});
  });
  parent.abort();
  await flush();
  assert.equal(callbacks, 0);
  assert.equal(scope.signal.aborted, true);
  t.mock.timers.tick(EXECUTION_ABORT_GRACE_MS);
  const result = await resultPromise;
  assert.equal(result.status, "failed");
  assert.equal(result.retryable, false);
});

test("normal success clears timers/listeners and ignores all subsequent callbacks", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "Date"], now: 100 });
  const clear = t.mock.method(globalThis, "clearTimeout");
  const parent = new AbortController();
  let scope!: ExecutionAttempt;
  let callbacks = 0;
  let guarded!: () => Promise<void>;
  const result = await withExecutionDeadline(1_000, parent.signal, async (attempt) => {
    scope = attempt;
    guarded = attempt.guard(() => { callbacks++; })!;
    await guarded();
    return { status: "succeeded", result: { output: "ok" } };
  });
  assert.equal(result.status, "succeeded");
  assert.equal(callbacks, 1);
  assert.ok(clear.mock.calls.some((call) => call.arguments[0] !== undefined));
  assert.equal(getEventListeners(parent.signal, "abort").length, 0);
  await guarded();
  t.mock.timers.tick(10_000);
  parent.abort();
  assert.equal(scope.signal.aborted, false);
  assert.equal(callbacks, 1);
});

test("callback rejections are observed even when the runtime discards their promises", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "Date"] });
  const result = await withExecutionDeadline(1_000, undefined, async (attempt) => {
    const callback = attempt.guard(async () => { throw new Error("callback failure"); });
    void callback!();
    await flush();
    return { status: "succeeded" };
  });
  assert.equal(result.status, "succeeded");
});

test("abortable retry/stagger delay removes its timer and listener", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "Date"] });
  const parent = new AbortController();
  let signal!: AbortSignal;
  const resultPromise = withExecutionDeadline(60_000, parent.signal, async (attempt) => {
    signal = attempt.signal;
    await attempt.delay(30_000);
    assert.fail("cancelled delay must not continue");
  });
  parent.abort();
  const result = await resultPromise;
  assert.equal(result.retryable, false);
  assert.equal(getEventListeners(signal, "abort").length, 0);
});


test("wall-clock expiry rejects a result even before the timeout callback gets CPU", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "Date"], now: 100 });
  const result = await withExecutionDeadline(1_000, undefined, async () => {
    t.mock.timers.setTime(1_100);
    return { status: "succeeded" };
  });
  assert.equal(result.status, "failed");
});

test("separate attempts own separate controllers and never abort the caller's controller", async () => {
  const parent = new AbortController();
  const signals: AbortSignal[] = [];
  for (let i = 0; i < 2; i++) {
    await withExecutionDeadline(1_000, parent.signal, async (attempt) => {
      signals.push(attempt.signal);
      return { status: "succeeded" };
    });
  }
  assert.notEqual(signals[0], signals[1]);
  assert.notEqual(signals[0], parent.signal);
  assert.equal(parent.signal.aborted, false);
  assert.equal(getEventListeners(parent.signal, "abort").length, 0);
});

for (const callbackOutcome of ["resolve", "reject"] as const) {
  test(`native abort settlement cannot permit retry while an unawaited callback is pending (${callbackOutcome})`, async (t) => {
    t.mock.timers.enable({ apis: ["setTimeout", "Date"], now: 100 });
    const gate = deferred<void>();
    let callbackStarted = false;
    let nativeSettled = false;
    let sideEffects = 0;
    let returned = false;
    const resultPromise = withExecutionDeadline(1_000, undefined, (attempt) => {
      const callback = attempt.guard(async () => {
        callbackStarted = true;
        await gate.promise;
        sideEffects++;
      });
      void callback!(); // A native runtime may deliberately not await callbacks.
      return new Promise((resolve) => {
        attempt.signal.addEventListener("abort", () => {
          nativeSettled = true;
          resolve({ status: "failed" });
        }, { once: true });
      });
    }).then((result) => { returned = true; return result; });
    assert.equal(callbackStarted, true);
    t.mock.timers.tick(1_000);
    await flush();
    assert.equal(nativeSettled, true);
    assert.equal(returned, false, "native settlement alone must not end callback grace");
    t.mock.timers.tick(EXECUTION_ABORT_GRACE_MS - 1);
    await flush();
    assert.equal(returned, false);
    t.mock.timers.tick(1);
    const result = await resultPromise;
    assert.equal(result.status, "failed");
    assert.equal(result.retryable, false);
    if (callbackOutcome === "resolve") gate.resolve();
    else gate.reject(new Error("late callback failure"));
    await flush();
    // Invalidation cannot interrupt a callback's own await. Its late work must
    // not be mistaken for permission to retry the previously failed attempt.
    assert.equal(sideEffects, callbackOutcome === "resolve" ? 1 : 0);
    assert.equal(result.retryable, false);
  });
}

test("a callback exiting within timeout grace allows retry only after its side effects finish", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "Date"] });
  const gate = deferred<void>();
  let callbackExited = false;
  let returned = false;
  const resultPromise = withExecutionDeadline(1_000, undefined, (attempt) => {
    void attempt.guard(async () => { await gate.promise; callbackExited = true; })!();
    return new Promise((resolve) => {
      attempt.signal.addEventListener("abort", () => resolve({ status: "failed" }), { once: true });
    });
  }).then((result) => { returned = true; return result; });
  t.mock.timers.tick(1_000);
  await flush();
  assert.equal(returned, false);
  t.mock.timers.tick(4_000);
  gate.resolve();
  const result = await resultPromise;
  assert.equal(callbackExited, true);
  assert.equal(result.status, "failed");
  assert.equal(result.retryable, undefined);
});

test("normal native success waits for its unawaited callback before returning success", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "Date"] });
  const gate = deferred<void>();
  let callbackExited = false;
  let returned = false;
  const resultPromise = withExecutionDeadline(1_000, undefined, async (attempt) => {
    void attempt.guard(async () => { await gate.promise; callbackExited = true; })!();
    return { status: "succeeded" };
  }).then((result) => { returned = true; return result; });
  await flush();
  assert.equal(returned, false);
  t.mock.timers.tick(250);
  gate.resolve();
  const result = await resultPromise;
  assert.equal(callbackExited, true);
  assert.equal(result.status, "succeeded");
});

test("normal native success with a hung callback still has a bounded, non-retryable deadline", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "Date"] });
  const gate = deferred<void>();
  let returned = false;
  let signal!: AbortSignal;
  const resultPromise = withExecutionDeadline(1_000, undefined, async (attempt) => {
    signal = attempt.signal;
    void attempt.guard(() => gate.promise)!();
    return { status: "succeeded" };
  }).then((result) => { returned = true; return result; });
  await flush();
  assert.equal(returned, false);
  t.mock.timers.tick(1_000);
  await flush();
  assert.equal(signal.aborted, true);
  assert.equal(returned, false);
  t.mock.timers.tick(EXECUTION_ABORT_GRACE_MS);
  const result = await resultPromise;
  assert.equal(result.status, "failed");
  assert.equal(result.retryable, false);
  gate.resolve();
  await flush();
  assert.equal(result.status, "failed");
});

for (const awaitInner of [true, false]) {
  test(`nested guarded callbacks drain without waiting on their own pending set (awaitInner=${awaitInner})`, async (t) => {
    t.mock.timers.enable({ apis: ["setTimeout", "Date"] });
    const outerGate = deferred<void>();
    const innerGate = deferred<void>();
    let innerExited = false;
    let returned = false;
    const resultPromise = withExecutionDeadline(1_000, undefined, async (attempt) => {
      const inner = attempt.guard(async () => { await innerGate.promise; innerExited = true; });
      const outer = attempt.guard(async () => {
        await outerGate.promise;
        const pending = inner!();
        if (awaitInner) await pending;
      });
      void outer!();
      return { status: "succeeded" };
    }).then((result) => { returned = true; return result; });
    await flush();
    assert.equal(returned, false);
    outerGate.resolve(); // Inner callback starts after operation draining began.
    await flush();
    assert.equal(returned, false);
    innerGate.resolve();
    const result = await resultPromise;
    assert.equal(innerExited, true);
    assert.equal(result.status, "succeeded");
  });
}

test("an awaited guarded rejection still propagates the original error to the operation", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "Date"] });
  const error = new Error("awaited callback failure");
  const result = await withExecutionDeadline(1_000, undefined, async (attempt) => {
    await attempt.guard(async () => { throw error; })!();
    return { status: "succeeded" };
  });
  assert.equal(result.status, "failed");
  assert.equal(result.rawError, error);
});
