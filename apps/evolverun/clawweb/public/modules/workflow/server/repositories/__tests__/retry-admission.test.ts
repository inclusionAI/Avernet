import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { timeoutFixture } from './timeout-write.fixture.js';

let f: ReturnType<typeof timeoutFixture>;
beforeEach(() => { f = timeoutFixture(); });
afterEach(async () => { await f.close(); vi.restoreAllMocks(); });

for (const epoch of ['absent', null, 0, 2] as const) {
  it.each(['failed', 'succeeded', 'running', 'waiting', 'cancelled', 'canceled'])(`legacy retry cannot reopen %s at epoch ${epoch}`, async status => {
    f.seed({}, { status, epoch: epoch === 'absent' ? 0 : epoch });
    if (epoch === 'absent') f.raw.exec('ALTER TABLE flow_runs DROP COLUMN retry_epoch');
    f.raw.exec("INSERT INTO node_executions VALUES ('flow', 'failed')");
    const before = f.row(); const beforeNodes = f.raw.prepare('SELECT * FROM node_executions').all();
    const reset = vi.fn(async () => 1);
    const exec = vi.spyOn(f.db, 'exec'); const transaction = vi.spyOn(f.db, 'transaction');
    expect(await f.repo.resetFailedForRetry('flow', reset)).toEqual({ status: 'conflict' });
    expect(reset).not.toHaveBeenCalled(); expect(exec).not.toHaveBeenCalled(); expect(transaction).not.toHaveBeenCalled();
    expect(f.row()).toEqual(before); expect(f.raw.prepare('SELECT * FROM node_executions').all()).toEqual(beforeNodes);
  });
}
it('legacy retry preserves not_found without invoking a reset callback', async () => {
  const reset = vi.fn(async () => 1);
  expect(await f.repo.resetFailedForRetry('missing', reset)).toEqual({ status: 'not_found' });
  expect(reset).not.toHaveBeenCalled();
});
it('legacy retry propagates read failures instead of reporting a conflict or missing run', async () => {
  const reset = vi.fn(async () => 1); vi.spyOn(f.db, 'query').mockRejectedValueOnce(new Error('read unavailable'));
  await expect(f.repo.resetFailedForRetry('flow', reset)).rejects.toThrow('read unavailable');
  expect(reset).not.toHaveBeenCalled();
});
it.each(['cancelled', 'canceled', ' CANCELLED ', 'Canceled', ' canceled '])('explicit retry cannot reopen cancellation %s', async status => {
  f.seed({}, { status, epoch: 2 }); const before = f.row();
  expect(await f.repo.resumeForRetry('flow', 3)).toBe('conflict');
  expect(f.row()).toEqual(before);
});
it.each([undefined, null, 0, -1, 0.5, NaN, Infinity, '1', Number.MAX_SAFE_INTEGER + 1])('invalid next epoch %s is rejected before SQL', async epoch => {
  f.seed({}, { status: 'failed' }); const exec = vi.spyOn(f.db, 'exec');
  await expect(f.repo.resumeForRetry('flow', epoch as number)).rejects.toThrow(/epoch/i);
  expect(exec).not.toHaveBeenCalled();
});
it.each(['failed', 'succeeded', 'waiting', 'blocked'])('coordinated manual retry of %s advances once and fences all old writers', async status => {
  f.seed({}, { status, epoch: 2 }); const startedAt = f.row().started_at;
  f.raw.exec(`CREATE TABLE retry_audit (changed INTEGER);
    CREATE TRIGGER retry_audit AFTER UPDATE ON flow_runs BEGIN INSERT INTO retry_audit VALUES (1); END;`);
  expect(await f.repo.resumeForRetry('flow', 4)).toBe('conflict');
  expect(await f.repo.resumeForRetry('flow', 3)).toBe('resumed');
  expect(await f.repo.resumeForRetry('flow', 3)).toBe('resumed');
  expect(await f.repo.resumeForRetry('flow', 2)).toBe('conflict');
  const resumed = f.row();
  expect(resumed).toMatchObject({ status: 'running', started_at: startedAt, completed_at: null, retry_epoch: 3 });
  expect(await f.repo.updateStatus('flow', 'running', 2)).toBe(false);
  expect(await f.repo.updateCompletion('flow', { status: 'failed', completedAt: 10, resultJson: 'old', retryEpoch: 2 })).toBe(false);
  expect(await f.repo.resetFailedForRetry('flow', async () => 1)).toEqual({ status: 'conflict' });
  expect(f.row()).toEqual(resumed);
  expect(f.raw.prepare('SELECT COUNT(*) AS n FROM retry_audit').get()).toEqual({ n: 1 });
});
it('absent epoch schema cannot authorize generation-advancing retry', async () => {
  f.seed({}, { status: 'failed' }); f.raw.exec('ALTER TABLE flow_runs DROP COLUMN retry_epoch');
  const before = f.row();
  await expect(f.repo.resumeForRetry('flow', 1)).rejects.toThrow(/retry_epoch/);
  expect(f.row()).toEqual(before);
});
