import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { startRepairBackgroundJob, type RepairScanCache } from "../background-job.js";

function cacheFixture() {
  const values = new Map<string, { value: string; expires: number }>();
  let owner = 0;
  const cache: RepairScanCache = {
    get: vi.fn(async key => {
      const entry = values.get(key);
      if (!entry || entry.expires <= Date.now()) { values.delete(key); return null; }
      return entry.value;
    }),
    set: vi.fn(async (key, value, ttl) => { values.set(key, { value, expires: Date.now() + ttl * 1000 }); }),
    acquireLock: vi.fn(async (key, ttl) => {
      const current = values.get(key);
      if (current && current.expires > Date.now()) return null;
      const identity = `owner-${++owner}`;
      values.set(key, { value: identity, expires: Date.now() + ttl * 1000 }); return identity;
    }),
    renewLock: vi.fn(async (key, token, ttl) => {
      const current = values.get(key);
      if (!current || current.value !== token || current.expires <= Date.now()) return false;
      current.expires = Date.now() + ttl * 1000; return true;
    }),
    releaseLock: vi.fn(async (key, token) => { if (values.get(key)?.value === token) values.delete(key); }),
  };
  return { cache, values };
}

let stops: Array<() => Promise<void>>;
beforeEach(() => { stops = []; vi.useFakeTimers(); vi.setSystemTime(new Date("2026-01-01T00:00:00Z")); });
afterEach(async () => { await Promise.all(stops.map(stop => stop())); vi.useRealTimers(); vi.restoreAllMocks(); });
const scope = { controlPlaneEnvironment: "pre" as const, job: "agent_execution" as const };

it("runs one scan across competing instances and throttles staggered timers", async () => {
  const { cache } = cacheFixture(), run = vi.fn(async () => {});
  stops.push(startRepairBackgroundJob({ ...scope, cache, run }));
  await vi.advanceTimersByTimeAsync(5000);
  stops.push(startRepairBackgroundJob({ ...scope, cache, run }), startRepairBackgroundJob({ ...scope, cache, run }));
  await vi.advanceTimersByTimeAsync(20000);
  expect(run).toHaveBeenCalledOnce();
  await vi.advanceTimersByTimeAsync(20000);
  expect(run).toHaveBeenCalledTimes(2);
});

it("isolates jobs and control-plane environments", async () => {
  const { cache } = cacheFixture(), run = vi.fn(async () => {});
  for (const other of [scope, { ...scope, controlPlaneEnvironment: "prod" as const }, { ...scope, job: "recovery_reporting" as const }]) {
    stops.push(startRepairBackgroundJob({ ...other, cache, run }));
  }
  await vi.advanceTimersByTimeAsync(20000);
  expect(run).toHaveBeenCalledTimes(3);
});

it("renews a long scan without blocking event-loop turns or admitting a competing owner", async () => {
  const { cache } = cacheFixture();
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  const run = vi.fn(async () => { await gate; });
  stops.push(startRepairBackgroundJob({ ...scope, cache, run }), startRepairBackgroundJob({ ...scope, cache, run }));
  try {
    await vi.advanceTimersByTimeAsync(20000);
    let responsive = false;
    setImmediate(() => { responsive = true; });
    await vi.advanceTimersByTimeAsync(120001);
    expect(responsive).toBe(true);
    expect(run).toHaveBeenCalledOnce();
    expect(cache.renewLock).toHaveBeenCalledTimes(6);
  } finally { release(); await vi.advanceTimersByTimeAsync(1); }
});

it("stops work after takeover and preserves the successor's lock", async () => {
  const { cache, values } = cacheFixture();
  let release!: () => void, active!: () => Promise<boolean>;
  const gate = new Promise<void>(resolve => { release = resolve; });
  stops.push(startRepairBackgroundJob({ ...scope, cache, run: async isActive => { active = isActive; await gate; } }));
  try {
    await vi.advanceTimersByTimeAsync(20000);
    const key = "lock:clawweb:repair:pre:agent_execution";
    values.get(key)!.expires = Date.now() - 1;
    const successor = await cache.acquireLock(key, 60);
    expect(successor).toBeTruthy();
    expect(await active()).toBe(false);
    release(); await vi.advanceTimersByTimeAsync(1);
    expect(await cache.get(key)).toBe(successor);
  } finally { release(); }
});

it.each(["acquireLock", "set"] as const)("skips the round when cache %s fails", async method => {
  const { cache } = cacheFixture(), run = vi.fn(async () => {});
  vi.mocked(cache[method]).mockRejectedValue(new Error("cache unavailable"));
  vi.spyOn(console, "error").mockImplementation(() => {});
  stops.push(startRepairBackgroundJob({ ...scope, cache, run }));
  await vi.advanceTimersByTimeAsync(40000);
  expect(run).not.toHaveBeenCalled();
});

it("stops admitting work after a renewal failure or shutdown", async () => {
  const { cache } = cacheFixture();
  vi.mocked(cache.renewLock).mockRejectedValue(new Error("cache unavailable"));
  vi.spyOn(console, "error").mockImplementation(() => {});
  let release!: () => void, active!: () => Promise<boolean>;
  const gate = new Promise<void>(resolve => { release = resolve; });
  const stop = startRepairBackgroundJob({ ...scope, cache, run: async isActive => { active = isActive; await gate; } });
  stops.push(stop);
  try {
    await vi.advanceTimersByTimeAsync(20000);
    expect(await active()).toBe(true);
    await vi.advanceTimersByTimeAsync(20000);
    expect(await active()).toBe(false);
    const stopping = stop();
    release(); await stopping;
  } finally { release(); }
});

it("keeps the existing local in-flight guard when no shared cache is injected", async () => {
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  const run = vi.fn(async () => { await gate; });
  stops.push(startRepairBackgroundJob({ ...scope, run }));
  try {
    await vi.advanceTimersByTimeAsync(80000);
    expect(run).toHaveBeenCalledOnce();
  } finally { release(); await vi.advanceTimersByTimeAsync(1); }
});
