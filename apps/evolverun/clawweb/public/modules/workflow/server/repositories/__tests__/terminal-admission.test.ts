import { afterEach, beforeEach, expect, it } from 'vitest';
import { FlowRunRepository } from '../flow-run-repository.js';
import { NodeExecutionRepository } from '../node-execution-repository.js';
import { timeoutFixture, NOW_SECONDS } from './timeout-write.fixture.js';

let f: ReturnType<typeof timeoutFixture>;
beforeEach(() => { f = timeoutFixture(); });
afterEach(async () => { await f.close(); });
const finish = (status = 'succeeded', extra = {}) => ({ status, resultJson: 'same settlement', completedAt: 123,
  succeededCount: 1, failedCount: 0, ...extra });

for (const status of ['failed', 'succeeded']) {
  it.each(['succeeded', 'failed', 'cancelled', 'canceled'])(`${status} cannot replace terminal %s even without completed_at`, async terminal => {
    f.seed({}, { status: terminal }); f.raw.exec('UPDATE flow_runs SET completed_at = NULL');
    const before = f.row();
    expect(await f.repo.updateCompletionOutcome('flow', finish(status))).toBe('rejected');
    expect(f.row()).toEqual(before);
  });
  it(`${status} replay is read-only, but a different result or details conflict`, async () => {
    f.seed(); expect(await f.repo.updateCompletionOutcome('flow', finish(status))).toBe('updated');
    const settled = f.row();
    f.raw.exec("CREATE TRIGGER no_rewrite BEFORE UPDATE ON flow_runs BEGIN SELECT RAISE(ABORT, 'rewritten'); END");
    expect(await f.repo.updateCompletionOutcome('flow', finish(status, { completedAt: 999 }))).toBe('duplicate');
    for (const patch of [{ resultJson: 'other' }, { currentPhase: 'other' }, { failedCount: 2 },
      { totalDurationMs: 3 }, { totalTokenUsage: 2 }, { inputJson: 'different input' }]) {
      expect(await f.repo.updateCompletionOutcome('flow', finish(status, patch))).toBe('rejected');
    }
    expect(f.row()).toEqual(settled);
  });
  it.each([undefined, 0, 1])(`${status} rejects missing/stale epoch %s when current is 2`, async retryEpoch => {
    f.seed({}, { epoch: 2 }); const before = f.row();
    expect(await f.repo.updateCompletionOutcome('flow', finish(status, { retryEpoch }))).toBe('rejected');
    expect(f.row()).toEqual(before);
  });
  it(`${status} accepts matching positive epoch and only its matching replay`, async () => {
    f.seed({}, { epoch: 2 });
    expect(await f.repo.updateCompletionOutcome('flow', finish(status, { retryEpoch: 2 }))).toBe('updated');
    expect(await f.repo.updateCompletionOutcome('flow', finish(status, { retryEpoch: 2 }))).toBe('duplicate');
    expect(await f.repo.updateCompletionOutcome('flow', finish(status))).toBe('rejected');
  });
  it.each(['state_json', 'started_at', 'retry_epoch', 'completed_at', 'current_phase'])(`${status} loses a selected %s race`, async column => {
    f.seed();
    const change = column === 'state_json' ? "'new state'" : column === 'current_phase' ? "'new phase'" : '5';
    const race = f.afterPolicyRead(() => f.raw.exec(`UPDATE flow_runs SET ${column} = ${change}`));
    expect(await new FlowRunRepository(race.db).updateCompletionOutcome('flow', finish(status))).toBe('rejected');
    expect(race.hit()).toBe(true);
    expect(f.row().status).toBe('running');
  });
  it.each([undefined, null, 'P2'])(`${status} phase %s preserves undefined and clears null`, async currentPhase => {
    f.seed(); f.raw.exec("UPDATE flow_runs SET current_phase = 'P1'");
    expect(await f.repo.updateCompletionOutcome('flow', finish(status, { currentPhase }))).toBe('updated');
    expect(f.row().current_phase).toBe(currentPhase === undefined ? 'P1' : currentPhase);
  });
}
it('different timeout reports are not the same settlement', async () => {
  f.seed();
  expect(await f.repo.updateCompletionOutcome('flow', finish('failed', { resultJson: '{"reason":"timeout","detail":"first"}' }))).toBe('updated');
  expect(await f.repo.updateCompletionOutcome('flow', finish('failed', { resultJson: '{"reason":"timeout","detail":"other"}' }))).toBe('rejected');
});
it.each(['waiting', 'blocked'])('watchdog is strictly running, not %s', async status => {
  f.seed({}, { status }); const before = f.row();
  expect(await f.repo.markFailedIfRunning('flow', { reason: 'engine decision', currentPhase: 'timeout', completedAt: 1 })).toBe(false);
  expect(f.row()).toEqual(before);
});
it.each([undefined, 0, 1])('watchdog enforces implicit-zero legacy epoch %s against positive epoch', async retryEpoch => {
  f.seed({}, { epoch: 2 });
  expect(await f.repo.markFailedIfRunning('flow', { reason: 'engine decision', currentPhase: 'timeout', completedAt: 1, retryEpoch })).toBe(false);
});
it.each([undefined, 0, 1])('truly absent epoch column admits only epoch zero: %s', async retryEpoch => {
  f.seed(); f.raw.exec('ALTER TABLE flow_runs DROP COLUMN retry_epoch');
  expect(await f.repo.updateCompletionOutcome('flow', finish('succeeded', { retryEpoch }))).toBe(retryEpoch === 1 ? 'rejected' : 'updated');
});

function nodes() {
  f.raw.exec(`ALTER TABLE node_executions ADD COLUMN error_text TEXT;
    ALTER TABLE node_executions ADD COLUMN completed_at INTEGER;
    ALTER TABLE node_executions ADD COLUMN gmt_modified INTEGER;
    INSERT INTO node_executions (flow_id, status) VALUES ('flow', 'running');`);
  return new NodeExecutionRepository(f.db);
}
it.each(['running', 'waiting', 'blocked', 'succeeded'])('reconcile rejects fake failed parent %s without mutation', async status => {
  f.seed({}, { status }); const repo = nodes();
  const before = f.raw.prepare('SELECT * FROM node_executions').all();
  await expect(repo.reconcileStaleRunning('flow', 'failed')).rejects.toThrow(/conflict/i);
  expect(f.raw.prepare('SELECT * FROM node_executions').all()).toEqual(before);
});
it.each([undefined, 0, 1])('reconcile rejects stale/missing epoch %s against positive parent', async retryEpoch => {
  f.seed({}, { status: 'failed', epoch: 2 }); const repo = nodes();
  await expect(repo.reconcileStaleRunning('flow', 'failed', undefined, retryEpoch)).rejects.toThrow(/conflict/i);
  expect(f.raw.prepare('SELECT status FROM node_executions').get()).toEqual({ status: 'running' });
});
it('reconcile preserves third customErrorText, accepts fourth epoch, and valid zero', async () => {
  f.seed({}, { status: 'failed', epoch: 2 }); const repo = nodes();
  expect(await repo.reconcileStaleRunning('flow', 'failed', 'custom abort reason', 2)).toBe(1);
  expect(f.raw.prepare('SELECT status, error_text, completed_at FROM node_executions').get())
    .toEqual({ status: 'failed', error_text: 'custom abort reason', completed_at: NOW_SECONDS });
  expect(await repo.reconcileStaleRunning('flow', 'failed', undefined, 2)).toBe(0);
});
it('reconcile write failures throw instead of reporting valid zero', async () => {
  f.seed({}, { status: 'failed' }); const repo = nodes();
  f.raw.exec("CREATE TRIGGER reject_node_write BEFORE UPDATE ON node_executions BEGIN SELECT RAISE(ABORT, 'write unavailable'); END");
  await expect(repo.reconcileStaleRunning('flow', 'failed')).rejects.toThrow('write unavailable');
});
it('legacy status cannot zero a positive epoch or reopen its terminal result', async () => {
  f.seed({}, { status: 'failed', epoch: 2 }); const before = f.row();
  expect(await f.repo.updateStatus('flow', 'running')).toBe(false);
  expect(await f.repo.updateStatus('flow', 'running', 2)).toBe(false);
  expect(f.row()).toEqual(before);
});
it.each(['succeeded', 'failed'])('status cannot bypass %s settlement', async status => {
  f.seed(); const before = f.row();
  expect(await f.repo.updateStatus('flow', status)).toBe(false);
  expect(f.row()).toEqual(before);
});
it.each(['succeeded', 'failed', 'cancelled', 'canceled'])('status cannot reopen terminal %s even in epoch0 or absent schema', async status => {
  f.seed({}, { status });
  for (const absent of [false, true]) {
    if (absent) f.raw.exec('ALTER TABLE flow_runs DROP COLUMN retry_epoch');
    const before = f.row();
    for (const retryEpoch of [undefined, 0]) expect(await f.repo.updateStatus('flow', 'running', retryEpoch)).toBe(false);
    expect(f.row()).toEqual(before);
  }
});
it.each([undefined, 0, 1])('no-epoch schema reconciliation admits only implicit zero %s', async retryEpoch => {
  f.seed({}, { status: 'failed' }); const repo = nodes();
  f.raw.exec('ALTER TABLE flow_runs DROP COLUMN retry_epoch');
  if (retryEpoch === 1) await expect(repo.reconcileStaleRunning('flow', 'failed', undefined, retryEpoch)).rejects.toThrow(/conflict/i);
  else expect(await repo.reconcileStaleRunning('flow', 'failed', undefined, retryEpoch)).toBe(1);
});
it.each(['succeeded', 'cancelled', 'canceled'])('reconciliation of matched %s skips stale nodes', async status => {
  f.seed({}, { status }); const repo = nodes();
  expect(await repo.reconcileStaleRunning('flow', status, 'custom message', 0)).toBe(1);
  expect(f.raw.prepare('SELECT status, error_text FROM node_executions').get()).toEqual({ status: 'skipped', error_text: 'custom message' });
});
it.each([undefined, null, 'P2'])('watchdog phase %s preserves undefined and clears null', async currentPhase => {
  f.seed(); f.raw.exec("UPDATE flow_runs SET current_phase = 'P1'");
  expect(await f.repo.markFailedIfRunning('flow', { reason: 'timeout', currentPhase, completedAt: 1 })).toBe(true);
  expect(f.row().current_phase).toBe(currentPhase === undefined ? 'P1' : currentPhase);
});
it('historical completed without timestamp is terminal, not an open attempt', async () => {
  f.seed({}, { status: 'completed' }); const before = f.row();
  expect(await f.repo.updateStatus('flow', 'running')).toBe(false);
  expect(await f.repo.updateCompletionOutcome('flow', finish('failed'))).toBe('rejected');
  expect(f.row()).toEqual(before);
});
it('status mirror cannot manufacture a completed terminal without settlement', async () => {
  f.seed(); const before = f.row(); expect(await f.repo.updateStatus('flow', 'completed')).toBe(false); expect(f.row()).toEqual(before);
});
