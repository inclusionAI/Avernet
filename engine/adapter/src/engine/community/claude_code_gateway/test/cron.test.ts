import { strict as assert } from 'node:assert';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { CronStore } from '../src/cron/store.js';
import { CronScheduler, computeNextRunAtMs } from '../src/cron/scheduler.js';
import {
  handleCronAdd,
  handleCronGet,
  handleCronList,
  handleCronRemove,
  handleCronRuns,
  handleCronStatus,
  handleCronUpdate,
} from '../src/cron/handlers.js';
import type { CronJobRecord, CronSchedule } from '../src/cron/types.js';
import type { OrchestratorFinal, RunningOrchestration } from '../src/chat-orchestrator.js';

/**
 * Build a fake `RunningOrchestration` that completes with the given result
 * after a short microtask. Records the params it was invoked with so tests
 * can assert the scheduler handed them off correctly.
 */
function makeFakeRun(result: Partial<OrchestratorFinal> = {}): {
  runner: (params: { cwd: string; message: string; model?: string; mode?: string }) => RunningOrchestration;
  invocations: Array<{ cwd: string; message: string; model?: string; mode?: string }>;
} {
  const invocations: Array<{ cwd: string; message: string; model?: string; mode?: string }> = [];
  const runner = (params: { cwd: string; message: string; model?: string; mode?: string }): RunningOrchestration => {
    invocations.push(params);
    let aborted = false;
    const completed = new Promise<OrchestratorFinal>(resolve => {
      setImmediate(() => resolve({
        ok: true,
        text: result.text ?? 'ok',
        toolUses: [],
        aborted,
        ...result,
      }));
    });
    return {
      subscribe(listener) {
        // Fire a single textDelta so the scheduler sees a summary.
        setImmediate(() => listener({ kind: 'textDelta', fullText: result.text ?? 'ok', delta: result.text ?? 'ok' }));
      },
      abort() { aborted = true; },
      completed,
    };
  };
  return { runner, invocations };
}

function tmpJobsPath(): string {
  return path.join(os.tmpdir(), `teamclaw-relay-cron-test-${Date.now()}-${Math.random().toString(36).slice(2, 6)}.json`);
}

function cleanupStore(p: string) {
  for (const file of [ p, path.join(path.dirname(p), 'cron-runs.json') ]) {
    if (fs.existsSync(file)) {
      try { fs.unlinkSync(file); } catch { /* ignore */ }
    }
  }
}

// ---- CronStore ----

describe('CronStore', () => {
  let storePath: string;
  let store: CronStore;

  beforeEach(() => {
    storePath = tmpJobsPath();
    store = new CronStore(storePath, { writeDebounceMs: 0, maxRunsPerJob: 3 });
  });

  afterEach(async () => {
    await store.flush();
    cleanupStore(storePath);
  });

  function makeJob(overrides: Partial<CronJobRecord> = {}): CronJobRecord {
    const now = Date.now();
    return {
      id: overrides.id ?? `job-${Math.random().toString(36).slice(2, 8)}`,
      name: 'test-job',
      enabled: true,
      schedule: { kind: 'every', everyMs: 60_000 },
      payload: { kind: 'agentTurn', message: 'hi' },
      sessionTarget: 'isolated',
      state: {},
      createdAtMs: now,
      updatedAtMs: now,
      ...overrides,
    };
  }

  it('put/get/delete roundtrips a job', () => {
    const job = makeJob({ id: 'j1' });
    store.put(job);
    assert.deepEqual(store.get('j1'), job);
    assert.equal(store.delete('j1'), true);
    assert.equal(store.get('j1'), undefined);
    assert.equal(store.delete('j1'), false);
  });

  it('list sorts jobs newest-first by createdAtMs', () => {
    store.put(makeJob({ id: 'a', createdAtMs: 100 }));
    store.put(makeJob({ id: 'b', createdAtMs: 200 }));
    store.put(makeJob({ id: 'c', createdAtMs: 150 }));
    const list = store.list();
    assert.deepEqual(list.map(j => j.id), [ 'b', 'c', 'a' ]);
  });

  it('appendRun caps run history at maxRunsPerJob', () => {
    store.put(makeJob({ id: 'j' }));
    for (let i = 0; i < 5; i++) {
      store.appendRun({
        jobId: 'j', runId: `r${i}`, runAtMs: i, ts: i + 1,
        status: 'ok', durationMs: 1,
      });
    }
    const runs = store.getRuns('j', 10);
    assert.equal(runs.length, 3); // capped
    // Newest-first: r4, r3, r2
    assert.deepEqual(runs.map(r => r.runId), [ 'r4', 'r3', 'r2' ]);
  });

  it('delete drops associated run history', () => {
    store.put(makeJob({ id: 'j' }));
    store.appendRun({ jobId: 'j', runId: 'r1', runAtMs: 0, ts: 1, status: 'ok', durationMs: 1 });
    store.delete('j');
    assert.deepEqual(store.getRuns('j', 10), []);
  });

  it('flush waits for concurrent jobs and runs writes', async () => {
    const runsPath = path.join(path.dirname(storePath), `cron-runs-gated-${Date.now()}-${Math.random().toString(36).slice(2, 6)}.json`);
    const persistent = new CronStore(storePath, { writeDebounceMs: 0, runsPath, maxRunsPerJob: 3 });
    const writable = persistent as unknown as {
      writeAtomic: (filePath: string, contents: string) => Promise<void>;
    };
    const originalWriteAtomic = writable.writeAtomic.bind(persistent);
    let releaseJobsWrite!: () => void;
    const jobsWriteGate = new Promise<void>(resolve => { releaseJobsWrite = resolve; });
    writable.writeAtomic = async (filePath, contents) => {
      if (filePath === storePath) await jobsWriteGate;
      await originalWriteAtomic(filePath, contents);
    };

    try {
      persistent.put(makeJob({ id: 'persist' }));
      persistent.appendRun({ jobId: 'persist', runId: 'r1', runAtMs: 0, ts: 1, status: 'ok', durationMs: 1 });

      let flushSettled = false;
      const flushPromise = persistent.flush().then(() => { flushSettled = true; });
      await new Promise<void>(resolve => setImmediate(resolve));
      assert.equal(flushSettled, false, 'flush returned before the blocked jobs write completed');

      releaseJobsWrite();
      await flushPromise;

      const reopened = new CronStore(storePath, { writeDebounceMs: 0, runsPath, maxRunsPerJob: 3 });
      assert.equal(reopened.get('persist')?.id, 'persist');
      assert.equal(reopened.getRuns('persist', 10).length, 1);
      await reopened.flush();
    } finally {
      releaseJobsWrite();
      await persistent.flush();
      if (fs.existsSync(runsPath)) {
        try { fs.unlinkSync(runsPath); } catch { /* ignore */ }
      }
    }
  });

  it('flush persists and a new store reloads state', async () => {
    const runsPath = path.join(path.dirname(storePath), `cron-runs-${Date.now()}-${Math.random().toString(36).slice(2, 6)}.json`);
    const persistent = new CronStore(storePath, { writeDebounceMs: 0, runsPath, maxRunsPerJob: 3 });
    try {
      persistent.put(makeJob({ id: 'persist' }));
      persistent.appendRun({ jobId: 'persist', runId: 'r1', runAtMs: 0, ts: 1, status: 'ok', durationMs: 1 });
      await persistent.flush();

      const reopened = new CronStore(storePath, { writeDebounceMs: 0, runsPath, maxRunsPerJob: 3 });
      assert.equal(reopened.get('persist')?.id, 'persist');
      assert.equal(reopened.getRuns('persist', 10).length, 1);
      await reopened.flush();
    } finally {
      if (fs.existsSync(runsPath)) {
        try { fs.unlinkSync(runsPath); } catch { /* ignore */ }
      }
    }
  });

  it('updateState merges patch and bumps updatedAtMs', () => {
    const t0 = Date.now() - 10_000;
    store.put(makeJob({ id: 'j', updatedAtMs: t0, state: { a: 1 } }));
    const updated = store.updateState('j', { b: 2 });
    assert(updated);
    assert.deepEqual(updated!.state, { a: 1, b: 2 });
    assert(updated!.updatedAtMs > t0);
  });
});

// ---- computeNextRunAtMs ----

describe('computeNextRunAtMs', () => {
  function stateWith(lastFiredAtMs?: number) {
    return { state: lastFiredAtMs == null ? {} : { last_fired_at_ms: lastFiredAtMs } };
  }

  it('once: returns atMs before fire, null after', () => {
    const s: CronSchedule = { kind: 'once', atMs: 5000 };
    assert.equal(computeNextRunAtMs(s, stateWith(), 1000), 5000);
    assert.equal(computeNextRunAtMs(s, stateWith(5000), 6000), null);
  });

  it('every: first run uses firstRunAtMs; subsequent uses last + step', () => {
    const s: CronSchedule = { kind: 'every', everyMs: 1000, firstRunAtMs: 500 };
    // Not yet fired — fire-asap at firstRunAtMs.
    assert.equal(computeNextRunAtMs(s, stateWith(), 0), 500);
    // Fired at 500 — next is 500 + 1000 = 1500.
    assert.equal(computeNextRunAtMs(s, stateWith(500), 1000), 1500);
  });

  it('every: catches up past many missed slots in one jump', () => {
    // Step = 1000ms; fired at 0; now = 5500 -> next = 6000 (6 steps in).
    const s: CronSchedule = { kind: 'every', everyMs: 1000, firstRunAtMs: 0 };
    assert.equal(computeNextRunAtMs(s, stateWith(0), 5500), 6000);
  });

  it('every: defaults firstRunAtMs to now + step when omitted', () => {
    const s: CronSchedule = { kind: 'every', everyMs: 2000 };
    assert.equal(computeNextRunAtMs(s, stateWith(), 10000), 12000);
  });

  it('cron: returns current slot (prev) when never fired', () => {
    const s: CronSchedule = { kind: 'cron', expr: '* * * * *' };
    const now = new Date('2026-01-01T12:00:30Z').getTime();
    const next = computeNextRunAtMs(s, stateWith(), now);
    // 12:00:00 slot not yet fired → return it (fire asap)
    assert.equal(next, new Date('2026-01-01T12:00:00Z').getTime());
  });

  it('cron: returns next slot when current slot already fired', () => {
    const s: CronSchedule = { kind: 'cron', expr: '* * * * *' };
    const now = new Date('2026-01-01T12:00:30Z').getTime();
    const firedAt = new Date('2026-01-01T12:00:05Z').getTime();
    const next = computeNextRunAtMs(s, stateWith(firedAt), now);
    // 12:00:00 slot already fired → next is 12:01:00
    assert.equal(next, new Date('2026-01-01T12:01:00Z').getTime());
  });

  it('cron: does NOT catch up stale prev slot (daily job, startup)', () => {
    // Daily job "0 19 * * *" in Asia/Shanghai, now is same day 18:23 CST.
    // prev() = yesterday 19:00 CST (23h23m ago, way beyond 60s window).
    // Must NOT catch up — should return today 19:00 CST (next slot).
    const s: CronSchedule = { kind: 'cron', expr: '0 19 * * *', tz: 'Asia/Shanghai' };
    // 2026-01-02 18:23 CST = 2026-01-02 10:23 UTC
    const now = new Date('2026-01-02T10:23:00Z').getTime();
    const next = computeNextRunAtMs(s, stateWith(), now);
    // Today 19:00 CST = 2026-01-02 11:00 UTC
    assert.equal(next, new Date('2026-01-02T11:00:00Z').getTime());
  });

  it('cron: invalid expression returns null (soft failure)', () => {
    const s: CronSchedule = { kind: 'cron', expr: 'definitely not a cron' };
    assert.equal(computeNextRunAtMs(s, stateWith(), 0), null);
  });
});

// ---- Handlers ----

describe('cron handlers', () => {
  let storePath: string;
  let store: CronStore;
  let scheduler: CronScheduler;

  beforeEach(() => {
    storePath = tmpJobsPath();
    store = new CronStore(storePath, { writeDebounceMs: 0 });
    // Scheduler isn't ticking here — handlers don't need a started scheduler.
    scheduler = new CronScheduler(store, { chatRunner: makeFakeRun().runner });
  });

  afterEach(async () => {
    await scheduler.stop();
    await store.flush();
    cleanupStore(storePath);
  });

  it('cron.add validates schedule.kind', () => {
    const r = handleCronAdd(store, scheduler, {
      name: 'x',
      schedule: { kind: 'bogus' },
      payload: { kind: 'agentTurn', message: 'hi' },
    });
    assert.equal(r.ok, false);
    if (!r.ok) assert.equal(r.error.code, 'INVALID_SCHEDULE');
  });

  it('cron.add rejects agentTurn payload without message', () => {
    const r = handleCronAdd(store, scheduler, {
      name: 'x',
      schedule: { kind: 'once', atMs: 1 },
      payload: { kind: 'agentTurn' },
    });
    assert.equal(r.ok, false);
    if (!r.ok) assert.equal(r.error.code, 'INVALID_PAYLOAD');
  });

  it('cron.add creates a job with an id and createdAtMs', () => {
    const r = handleCronAdd(store, scheduler, {
      name: 'morning',
      schedule: { kind: 'cron', expr: '0 8 * * *', tz: 'Asia/Shanghai' },
      payload: { kind: 'agentTurn', message: 'write a summary' },
    });
    assert.equal(r.ok, true);
    if (r.ok) {
      assert(r.payload.id);
      assert.equal(r.payload.name, 'morning');
      assert(r.payload.createdAtMs > 0);
      assert.equal(r.payload.enabled, true);
    }
  });

  it('cron.update returns NOT_FOUND for unknown id', () => {
    const r = handleCronUpdate(store, scheduler, { id: 'nope', patch: { enabled: false } });
    assert.equal(r.ok, false);
    if (!r.ok) assert.equal(r.error.code, 'NOT_FOUND');
  });

  it('cron.update patches name/enabled and bumps updatedAtMs', async () => {
    const added = handleCronAdd(store, scheduler, {
      name: 'a',
      schedule: { kind: 'every', everyMs: 1000 },
      payload: { kind: 'agentTurn', message: 'hi' },
    });
    assert(added.ok);
    const id = added.ok ? added.payload.id : '';
    const before = added.ok ? added.payload.updatedAtMs : 0;
    await new Promise(r => setTimeout(r, 2));

    const r = handleCronUpdate(store, scheduler, {
      id,
      patch: { name: 'renamed', enabled: false },
    });
    assert.equal(r.ok, true);
    if (r.ok) {
      assert.equal(r.payload.name, 'renamed');
      assert.equal(r.payload.enabled, false);
      assert(r.payload.updatedAtMs >= before);
    }
  });

  it('cron.remove returns removed: true then false on re-remove', () => {
    const added = handleCronAdd(store, scheduler, {
      name: 'a',
      schedule: { kind: 'once', atMs: 1 },
      payload: { kind: 'agentTurn', message: 'hi' },
    });
    assert(added.ok);
    const id = added.ok ? added.payload.id : '';

    const r1 = handleCronRemove(store, scheduler, { id });
    assert.equal(r1.ok, true);
    if (r1.ok) assert.equal(r1.payload.removed, true);

    const r2 = handleCronRemove(store, scheduler, { id });
    assert.equal(r2.ok, true);
    if (r2.ok) assert.equal(r2.payload.removed, false);
  });

  it('cron.list respects includeDisabled', () => {
    const a = handleCronAdd(store, scheduler, {
      name: 'a', schedule: { kind: 'once', atMs: 1 },
      payload: { kind: 'agentTurn', message: 'x' },
    });
    const b = handleCronAdd(store, scheduler, {
      name: 'b', schedule: { kind: 'once', atMs: 1 },
      payload: { kind: 'agentTurn', message: 'x' }, enabled: false,
    });
    assert(a.ok && b.ok);

    const all = handleCronList(store, scheduler, { includeDisabled: true });
    assert.equal(all.ok, true);
    if (all.ok) assert.equal(all.payload.length, 2);

    const enabled = handleCronList(store, scheduler, { includeDisabled: false });
    assert.equal(enabled.ok, true);
    if (enabled.ok) {
      assert.equal(enabled.payload.length, 1);
      assert.equal(enabled.payload[0].name, 'a');
    }
  });

  it('cron.status reports job_count/enabled_count/nextRunAtMs', () => {
    handleCronAdd(store, scheduler, {
      name: 'a', schedule: { kind: 'once', atMs: Date.now() + 60_000 },
      payload: { kind: 'agentTurn', message: 'x' },
    });
    handleCronAdd(store, scheduler, {
      name: 'b', schedule: { kind: 'once', atMs: 1 },
      payload: { kind: 'agentTurn', message: 'x' }, enabled: false,
    });
    const r = handleCronStatus(store, scheduler, {});
    assert.equal(r.ok, true);
    if (r.ok) {
      assert.equal(r.payload.jobCount, 2);
      assert.equal(r.payload.enabledCount, 1);
      assert(r.payload.nextRunAtMs! > 0);
    }
  });

  it('cron.get returns the job by id and null when missing', () => {
    const added = handleCronAdd(store, scheduler, {
      name: 'a', schedule: { kind: 'once', atMs: 1 },
      payload: { kind: 'agentTurn', message: 'x' },
    });
    assert(added.ok);
    const id = added.ok ? added.payload.id : '';
    const hit = handleCronGet(store, scheduler, { jobId: id });
    assert.equal(hit.ok, true);
    if (hit.ok) assert.equal(hit.payload?.id, id);

    const miss = handleCronGet(store, scheduler, { jobId: 'nope' });
    assert.equal(miss.ok, true);
    if (miss.ok) assert.equal(miss.payload, null);
  });

  it('cron.runs returns empty for a job that has never run', () => {
    const added = handleCronAdd(store, scheduler, {
      name: 'a', schedule: { kind: 'once', atMs: 1 },
      payload: { kind: 'agentTurn', message: 'x' },
    });
    assert(added.ok);
    const id = added.ok ? added.payload.id : '';
    const r = handleCronRuns(store, scheduler, { id, limit: 10 });
    assert.equal(r.ok, true);
    if (r.ok) assert.deepEqual(r.payload.entries, []);
  });
});

// ---- Scheduler integration ----

describe('CronScheduler', () => {
  let storePath: string;
  let store: CronStore;

  beforeEach(() => {
    storePath = tmpJobsPath();
    store = new CronStore(storePath, { writeDebounceMs: 0, maxRunsPerJob: 10 });
  });

  afterEach(async () => {
    cleanupStore(storePath);
  });

  it('once job fires, records ok, and auto-disables', async () => {
    const fake = makeFakeRun({ text: 'done' });
    const scheduler = new CronScheduler(store, { chatRunner: fake.runner });
    store.put({
      id: 'j',
      name: 'a',
      enabled: true,
      schedule: { kind: 'once', atMs: 0 },
      payload: { kind: 'agentTurn', message: 'hello' },
      sessionTarget: 'isolated',
      state: {},
      createdAtMs: 0,
      updatedAtMs: 0,
    });

    const rec = await scheduler.runJobNow('j', 'force');
    assert(rec);
    assert.equal(rec!.status, 'ok');
    assert.equal(rec!.jobId, 'j');
    assert(rec!.summary);
    // Runner was called once with the payload message.
    assert.equal(fake.invocations.length, 1);
    assert.equal(fake.invocations[0].message, 'hello');

    const job = store.get('j');
    assert(job);
    assert.equal(job!.enabled, false); // auto-disabled after once fire
    assert.equal(job!.state.last_status, 'ok');

    const runs = store.getRuns('j', 10);
    assert.equal(runs.length, 1);
    assert.equal(runs[0].runId, rec!.runId);

    await scheduler.stop();
  });

  it('every job fires and stays enabled with next run advanced', async () => {
    const fake = makeFakeRun();
    const scheduler = new CronScheduler(store, {
      chatRunner: fake.runner,
      now: () => 10_000,
    });
    store.put({
      id: 'e',
      name: 'every',
      enabled: true,
      schedule: { kind: 'every', everyMs: 1000, firstRunAtMs: 0 },
      payload: { kind: 'agentTurn', message: 'tick' },
      sessionTarget: 'isolated',
      state: {},
      createdAtMs: 0,
      updatedAtMs: 0,
    });

    await scheduler.runJobNow('e', 'force');
    const job = store.get('e');
    assert(job);
    assert.equal(job!.enabled, true); // still armed
    assert.equal(job!.state.last_status, 'ok');

    await scheduler.stop();
  });

  it('non-agentTurn payload records a skipped run', async () => {
    const fake = makeFakeRun();
    const scheduler = new CronScheduler(store, { chatRunner: fake.runner });
    store.put({
      id: 's',
      name: 'opaque',
      enabled: true,
      schedule: { kind: 'once', atMs: 0 },
      payload: { kind: 'systemEvent', text: 'noop' },
      sessionTarget: 'isolated',
      state: {},
      createdAtMs: 0,
      updatedAtMs: 0,
    });
    const rec = await scheduler.runJobNow('s', 'force');
    assert(rec);
    assert.equal(rec!.status, 'skipped');
    assert.match(rec!.error ?? '', /not supported/);
    assert.equal(fake.invocations.length, 0); // chatRunner was never called
    await scheduler.stop();
  });

  it('does not double-fire a job that is already running', async () => {
    // Runner that never completes on its own — scheduler.stop() aborts it.
    let resolveRun: (result: OrchestratorFinal) => void = () => {};
    const running: RunningOrchestration = {
      subscribe() { /* noop */ },
      abort() { resolveRun({ ok: false, text: '', toolUses: [], aborted: true, error: 'aborted' }); },
      completed: new Promise<OrchestratorFinal>(resolve => { resolveRun = resolve; }),
    };
    let callCount = 0;
    const scheduler = new CronScheduler(store, {
      chatRunner: () => { callCount++; return running; },
    });
    store.put({
      id: 'slow',
      name: 'slow',
      enabled: true,
      schedule: { kind: 'once', atMs: 0 },
      payload: { kind: 'agentTurn', message: 'hang' },
      sessionTarget: 'isolated',
      state: {},
      createdAtMs: 0,
      updatedAtMs: 0,
    });

    const p1 = scheduler.runJobNow('slow', 'force');
    // Second call returns null because the first is still running.
    const p2 = scheduler.runJobNow('slow', 'force');
    assert.equal(await p2, null);
    assert.equal(callCount, 1);

    await scheduler.stop(); // aborts the hanging run
    await p1;
  });

  it('nextRunAtMs reports the earliest enabled job and ignores running ones', () => {
    const scheduler = new CronScheduler(store, {
      chatRunner: makeFakeRun().runner,
      now: () => 1000,
    });
    store.put({
      id: 'a', name: 'a', enabled: true,
      schedule: { kind: 'once', atMs: 5000 },
      payload: { kind: 'agentTurn', message: '.' },
      sessionTarget: 'isolated', state: {}, createdAtMs: 0, updatedAtMs: 0,
    });
    store.put({
      id: 'b', name: 'b', enabled: true,
      schedule: { kind: 'once', atMs: 3000 },
      payload: { kind: 'agentTurn', message: '.' },
      sessionTarget: 'isolated', state: {}, createdAtMs: 0, updatedAtMs: 0,
    });
    store.put({
      id: 'c', name: 'c', enabled: false,
      schedule: { kind: 'once', atMs: 2000 },
      payload: { kind: 'agentTurn', message: '.' },
      sessionTarget: 'isolated', state: {}, createdAtMs: 0, updatedAtMs: 0,
    });
    assert.equal(scheduler.nextRunAtMs(), 3000);
  });
});

// ---- runId source-split (openclaw parity) ----

describe('CronScheduler runId format', () => {
  let storePath: string;
  let store: CronStore;

  beforeEach(() => {
    storePath = tmpJobsPath();
    store = new CronStore(storePath, { writeDebounceMs: 0, maxRunsPerJob: 10 });
  });

  afterEach(async () => {
    cleanupStore(storePath);
  });

  function putAgentJob(id: string, overrides: Partial<CronJobRecord> = {}): void {
    store.put({
      id,
      name: id,
      enabled: true,
      schedule: { kind: 'once', atMs: 0 },
      payload: { kind: 'agentTurn', message: 'hi' },
      sessionTarget: 'isolated',
      state: {},
      createdAtMs: 0,
      updatedAtMs: 0,
      ...overrides,
    });
  }

  it('manual trigger (runJobNow) produces manual:<jobId>:<startedAt>:<attempt> with startedAt == running_at_ms / runAtMs', async () => {
    const fixedNow = 1_700_000_000_000;
    const scheduler = new CronScheduler(store, {
      chatRunner: makeFakeRun().runner,
      now: () => fixedNow,
    });
    putAgentJob('m1');
    const rec = await scheduler.runJobNow('m1', 'force');
    assert(rec);
    assert.equal(rec!.runId, `manual:m1:${fixedNow}:1`);
    // startedAt segment is the fire marker time, not a second now() read.
    assert.equal(rec!.runAtMs, fixedNow);
    await scheduler.stop();
  });

  it('manual attempt is monotonically increasing across fires in one process', async () => {
    const scheduler = new CronScheduler(store, {
      chatRunner: makeFakeRun().runner,
      now: () => 5_000,
    });
    putAgentJob('a');
    putAgentJob('b');
    const r1 = await scheduler.runJobNow('a', 'force');
    const r2 = await scheduler.runJobNow('b', 'force');
    assert(r1 && r2);
    assert.equal(r1!.runId, 'manual:a:5000:1');
    assert.equal(r2!.runId, 'manual:b:5000:2');
    await scheduler.stop();
  });

  it('fireAsync (cron.run path) produces a manual: runId', () => {
    const scheduler = new CronScheduler(store, {
      chatRunner: makeFakeRun().runner,
      now: () => 9_000,
    });
    putAgentJob('fa');
    const runId = scheduler.fireAsync('fa');
    assert(runId);
    assert.equal(runId, 'manual:fa:9000:1');
  });

  it('scheduled trigger (tick) produces cron:<jobId>:<startedAt>', async () => {
    const fixedNow = 8_888;
    const scheduler = new CronScheduler(store, {
      chatRunner: makeFakeRun().runner,
      now: () => fixedNow,
      tickIntervalMs: 5,
    });
    // Due now (atMs <= now) so the tick fires it.
    putAgentJob('sch', { schedule: { kind: 'once', atMs: 0 } });
    scheduler.start();
    // Let one tick fire and the run settle.
    await new Promise(r => setTimeout(r, 30));
    const runs = store.getRuns('sch', 10);
    assert.equal(runs.length, 1);
    assert.equal(runs[0].runId, `cron:sch:${fixedNow}`);
    assert.equal(runs[0].runAtMs, fixedNow);
    await scheduler.stop();
  });

  it('skipped path shares the same (manual) runId as the normal path', async () => {
    const scheduler = new CronScheduler(store, {
      chatRunner: makeFakeRun().runner,
      now: () => 4_000,
    });
    store.put({
      id: 'skp',
      name: 'skp',
      enabled: true,
      schedule: { kind: 'once', atMs: 0 },
      payload: { kind: 'systemEvent', text: 'noop' },
      sessionTarget: 'isolated',
      state: {},
      createdAtMs: 0,
      updatedAtMs: 0,
    });
    const rec = await scheduler.runJobNow('skp', 'force');
    assert(rec);
    assert.equal(rec!.status, 'skipped');
    assert.equal(rec!.runId, 'manual:skp:4000:1');
    // The runId written to state on fire start matches the recorded one.
    assert.equal(store.get('skp')?.state.last_run_id, rec!.runId);
    await scheduler.stop();
  });
});
