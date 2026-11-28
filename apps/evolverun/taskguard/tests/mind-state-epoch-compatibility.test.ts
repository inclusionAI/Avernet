import assert from 'node:assert/strict';
import { test, type TestContext } from 'node:test';
import { SqliteDatabase } from '../src/db/sqlite-database.js';
import { FlowRunRepository } from '../src/db/repositories/flow-run-repository.js';
import { validateRetryCheckpoint } from '../src/db/repositories/retry-checkpoint.js';
import { captureStateAttempt } from '../src/recovery/attempt.js';
import { admitManualRetry } from '../src/recovery/manual-retry.js';
import { loadRunningSnapshot } from '../src/recovery/scan.js';
import { reapTimedOutRuns } from '../src/recovery/timeout-sweep.js';
import { DatabaseTaskFlowAdapter } from '../src/platform/database-taskflow.js';
import type { FlowState } from '../src/types.js';

// Mind's shared execution generation is recoveryEpoch; epoch is a LOCAL clock counter.
const mindState = (epoch = 2) => ({ workflowId: 'mind', recoveryEpoch: epoch,
  recoveryPolicy: 'checkpoint-v1', effectiveFlowTimeoutMinutes: 1,
  currentPhase: 'P2', nodeStates: { node: { status: 'pending' } } });
async function fixture(t: TestContext, state: Record<string, unknown>, epoch = 2) {
  const db = new SqliteDatabase({ path: ':memory:' }); await db.connect(); t.after(() => db.close());
  await db.exec(`CREATE TABLE flow_runs (
    flow_id TEXT PRIMARY KEY, workflow_id TEXT, status TEXT, retry_epoch INTEGER,
    state_json TEXT, params_json TEXT, current_phase TEXT, result_json TEXT,
    started_at INTEGER, completed_at INTEGER, gmt_modified INTEGER,
    total_duration_ms INTEGER, total_token_usage INTEGER,
    succeeded_count INTEGER DEFAULT 0, failed_count INTEGER DEFAULT 0
  )`);
  await db.exec(`INSERT INTO flow_runs (flow_id, workflow_id, status, retry_epoch, state_json, params_json, started_at)
    VALUES ('mind', 'mind', 'running', ?, ?, ?, ?)`, [epoch, JSON.stringify(state),
    JSON.stringify({ input: 'keep', _clawmind_state: JSON.stringify(state) }), Math.floor(Date.now() / 1000) - 600]);
  const runs = new FlowRunRepository(db);
  const row = () => runs.findByFlowId('mind');
  return { db, runs, row };
}
async function sweep(runs: FlowRunRepository, state?: Record<string, unknown>) {
  let effects = 0;
  const claims = await reapTimedOutRuns({ repository: runs, globalTimeoutMs: 60_000, limit: 100,
    ...(state ? { getState: async () => state as unknown as FlowState } : {}),
    onClaim: async () => { effects++; } });
  return { claims, effects };
}

test('capture recognizes original Mind recoveryEpoch, canonicalizes locally, never local clock epoch', () => {
  const state = { ...mindState(), epoch: 99, executionEpoch: 88 };
  assert.equal(captureStateAttempt({ retry_epoch: 2 }, state), 2);
  assert.equal((state as typeof state & { retryEpoch: number }).retryEpoch, 2);
  assert.equal(state.epoch, 99); assert.equal(state.recoveryEpoch, 2);
  assert.throws(() => captureStateAttempt({ retry_epoch: 3 }, state), /mismatch/);
});
test('missing original state epoch is only legacy zero, not authority from a current row or local clock', () => {
  const legacy = { epoch: 2, executionEpoch: 2 };
  assert.throws(() => captureStateAttempt({ retry_epoch: 2 }, legacy), /mismatch/);
  assert.equal(captureStateAttempt({ retry_epoch: 0 }, legacy), 0);
  assert.throws(() => captureStateAttempt({ retry_epoch: undefined }, { retryEpoch: 0 }), /epoch/i);
});
for (const aliases of [
  { recoveryEpoch: 2 }, { retryEpoch: 2 }, { recoveryEpoch: 2, retryEpoch: 2 },
]) {
  test(`checkpoint validates original execution alias ${JSON.stringify(aliases)}`, () => {
    const state = { workflowId: 'mind', ...aliases };
    assert.deepEqual(validateRetryCheckpoint(JSON.stringify(state), 2, 'mind'), state);
  });
}
for (const bad of [undefined, null, '2', -1, 2.5, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1]) {
  test(`present invalid recoveryEpoch ${String(bad)} cannot fall back to valid retryEpoch`, () => {
    const state = { retryEpoch: 2, recoveryEpoch: bad };
    assert.throws(() => captureStateAttempt({ retry_epoch: 2 }, state as any), /epoch|mismatch/i);
    if (bad !== undefined) assert.throws(() => validateRetryCheckpoint(JSON.stringify(state), 2), /epoch|attempt/i);
    assert.throws(() => captureStateAttempt({ retry_epoch: 2 }, { recoveryEpoch: 2, retryEpoch: bad } as any), /epoch|mismatch/i);
  });
}
test('conflicting valid aliases are not permission to pick the newer field', () => {
  const state = { retryEpoch: 2, recoveryEpoch: 1 };
  assert.throws(() => captureStateAttempt({ retry_epoch: 2 }, state), /epoch|mismatch/i);
  assert.throws(() => validateRetryCheckpoint(JSON.stringify(state), 2), /epoch|attempt/i);
});

test('real SQLite accepts Mind epoch2 snapshot but online-clock retry is not a wall-clock timeout candidate', async t => {
  const original = mindState(); const f = await fixture(t, original);
  const snapshot = await loadRunningSnapshot(f.runs, 'mind');
  assert.equal(snapshot?.retryEpoch, 2); assert.equal(snapshot?.state?.recoveryEpoch, 2);
  assert.deepEqual(JSON.parse((await f.row())!.state_json!), original, 'loading must not rewrite source checkpoint');
  const before = await f.row(); assert.deepEqual(await sweep(f.runs), { claims: 0, effects: 0 });
  assert.deepEqual(await f.row(), before, 'ten minutes wall time cannot kill a fresh online attempt');
});
test('Mind epoch0 waiting/downtime remains isolated even though legacy epoch fence matches', async t => {
  const f = await fixture(t, mindState(0), 0); const before = await f.row();
  assert.deepEqual(await sweep(f.runs), { claims: 0, effects: 0 }); assert.deepEqual(await f.row(), before);
});
for (const source of ['canonical', 'embedded', 'taskflow']) {
  test(`online clock evidence in ${source} cannot be hidden by a lagging wall-clock mirror`, async t => {
    const wall = { workflowId: 'mind', retryEpoch: 2, effectiveFlowTimeoutMinutes: 1 };
    const f = await fixture(t, source === 'canonical' ? { ...mindState(), retryEpoch: 2 } : wall);
    if (source === 'embedded') await f.db.exec('UPDATE flow_runs SET params_json = ?', [JSON.stringify({ _clawmind_state: { ...mindState(), retryEpoch: 2 } })]);
    assert.deepEqual(await sweep(f.runs, source === 'taskflow' ? { ...mindState(), retryEpoch: 2 } : wall), { claims: 0, effects: 0 });
    assert.equal((await f.row())?.status, 'running');
  });
}
for (const state of [{ recoveryEpoch: 1 }, {}, { retryEpoch: 2, recoveryEpoch: 1 }, { retryEpoch: 2, recoveryEpoch: 'bad' }]) {
  test(`real SQLite row2 rejects stale/malformed original snapshot ${JSON.stringify(state)}`, async t => {
    const f = await fixture(t, { workflowId: 'mind', effectiveFlowTimeoutMinutes: 1, ...state });
    const before = await f.row();
    await assert.rejects(loadRunningSnapshot(f.runs, 'mind'), /epoch|attempt/i);
    assert.deepEqual(await sweep(f.runs), { claims: 0, effects: 0 }); assert.deepEqual(await f.row(), before);
  });
}
test('a stale TaskFlow or embedded alias cannot borrow a matching canonical epoch2', async t => {
  const f = await fixture(t, mindState());
  await assert.rejects(loadRunningSnapshot(f.runs, 'mind', async () => mindState(1) as any), /attempt|epoch/i);
  await f.db.exec('UPDATE flow_runs SET params_json = ?', [JSON.stringify({ _clawmind_state: mindState(1) })]);
  await assert.rejects(loadRunningSnapshot(f.runs, 'mind'), /attempt|epoch/i);
  assert.equal((await f.row())?.status, 'running');
});
test('TaskGuard wall-clock retryEpoch state still has exactly one real SQLite timeout winner', async t => {
  const f = await fixture(t, { workflowId: 'mind', retryEpoch: 2, effectiveFlowTimeoutMinutes: 1 });
  assert.deepEqual(await sweep(f.runs), { claims: 1, effects: 1 });
  assert.deepEqual(await sweep(f.runs), { claims: 0, effects: 0 });
  assert.equal((await f.row())?.status, 'failed');
});
for (const supplied of [true, false]) {
  test(`SQL retry imports Mind checkpoint with${supplied ? '' : 'out'} captured next state, synchronizing aliases`, async t => {
    const f = await fixture(t, mindState()); await f.db.exec("UPDATE flow_runs SET status = 'failed', completed_at = 1");
    const next = { ...mindState(3), retryEpoch: 3 };
    assert.equal(await f.runs.resumeForRetry('mind', 3, supplied ? JSON.stringify(next) : undefined), 'resumed');
    const row = (await f.row())!; const saved = JSON.parse(row.state_json!);
    assert.equal(saved.retryEpoch, 3); assert.equal(saved.recoveryEpoch, 3);
    assert.deepEqual(JSON.parse(JSON.parse(row.params_json!)._clawmind_state), saved);
    assert.equal((await loadRunningSnapshot(f.runs, 'mind'))?.retryEpoch, 3);
  });
}
test('manual TaskGuard retry advances canonical retryEpoch and imported alias only after admission', async () => {
  const state = mindState(); const before = structuredClone(state);
  await assert.rejects(admitManualRetry({ resumeForRetry: async () => 'conflict' } as any, 'mind', state));
  assert.deepEqual(state, before);
  const repo = { resumeForRetry: async (_id: string, epoch: number, json: string) => {
    assert.equal(epoch, 3); assert.equal(JSON.parse(json).retryEpoch, 3); assert.equal(JSON.parse(json).recoveryEpoch, 3);
    assert.deepEqual(state, before); return 'resumed';
  } };
  assert.equal(await admitManualRetry(repo as any, 'mind', state), 3);
  assert.equal((state as any).retryEpoch, 3); assert.equal(state.recoveryEpoch, 3);
});
test('TaskFlow phase/status mirrors carry imported original state epoch, without querying a newer row', async () => {
  const epochs: number[] = [];
  const repo = { insert: async () => true,
    findByFlowId: async () => { throw new Error('must not borrow current row epoch99'); },
    updateStatus: async (_id: string, _status: string, epoch: number) => { epochs.push(epoch); return true; },
    updateCurrentPhase: async (_id: string, _phase: string, epoch: number) => { epochs.push(epoch); return true; } };
  const adapter = new DatabaseTaskFlowAdapter('mind-import-mirror', repo as any);
  const flow = await adapter.createManaged({ stateJson: JSON.stringify(mindState()) });
  const waiting = await adapter.setWaiting({ flowId: flow.flowId, expectedRevision: 1, currentStep: 'waiting' });
  assert.equal(waiting.applied, true);
  assert.equal((await adapter.resume({ flowId: flow.flowId, expectedRevision: 2, currentStep: 'resumed' })).applied, true);
  assert.deepEqual(epochs, [2, 2, 2, 2]);
});
