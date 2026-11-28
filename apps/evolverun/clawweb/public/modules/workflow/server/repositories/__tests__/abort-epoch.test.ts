import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { FlowRunRepository } from '../flow-run-repository.js';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { timeoutFixture } from './timeout-write.fixture.js';

let f: ReturnType<typeof timeoutFixture>;
beforeEach(() => { f = timeoutFixture(); f.raw.exec("ALTER TABLE flow_runs ADD COLUMN workflow_id TEXT DEFAULT 'wf'"); });
afterEach(async () => { await f.close(); vi.restoreAllMocks(); });

it('reads the original abort generation without a lightweight-list epoch omission', async () => {
  f.seed({}, { epoch: 2 });
  expect(await f.repo.findForAbort('flow')).toEqual({ workflow_id: 'wf', status: 'running', completed_at: null, retry_epoch: 2 });
});
it('propagates abort snapshot read errors', async () => {
  vi.spyOn(f.db, 'query').mockRejectedValueOnce(new Error('read unavailable'));
  await expect(f.repo.findForAbort('flow')).rejects.toThrow('read unavailable');
});
it('admits an explicit matching positive epoch and preserves started_at', async () => {
  f.seed({}, { epoch: 2 }); const before = f.row();
  expect(await f.repo.abort('flow', 'operator stop', 2)).toBe(true);
  expect(f.row()).toMatchObject({ status: 'cancelled', retry_epoch: 2, started_at: before.started_at });
  expect(JSON.parse(String(f.row().result_json))).toMatchObject({ aborted: true, reason: 'operator stop' });
});
it.each([undefined, 0, 1, 3, -1, 1.5, NaN])('rejects an old or malformed abort epoch %s without writes', async epoch => {
  f.seed({}, { epoch: 2 }); const before = f.row(); const exec = vi.spyOn(f.db, 'exec');
  expect(await f.repo.abort('flow', 'old operator', epoch)).toBe(false);
  expect(exec).not.toHaveBeenCalled(); expect(f.row()).toEqual(before);
});
it.each(['running', 'waiting', 'blocked', 'pending', 'queued', 'postActionsRunning'])('allows active %s in its original epoch', async status => {
  f.seed({}, { status, epoch: 2 }); expect(await f.repo.abort('flow', 'stop', 2)).toBe(true);
});
it.each(['succeeded', 'failed', 'completed', 'cancelled', 'canceled'])('never overwrites saved terminal %s', async status => {
  f.seed({}, { status, epoch: 2 }); const before = f.row();
  expect(await f.repo.abort('flow', 'late abort', 2)).toBe(false); expect(f.row()).toEqual(before);
});
it('rejects an active spelling with a saved completion timestamp', async () => {
  f.seed({}, { epoch: 2 }); f.raw.exec('UPDATE flow_runs SET completed_at=123'); const before = f.row();
  expect(await f.repo.abort('flow', 'late abort', 2)).toBe(false); expect(f.row()).toEqual(before);
});
it.each([0, null, 'absent'] as const)('retains genuine legacy epoch-zero abort (%s)', async epoch => {
  f.seed({}, { epoch: epoch === 'absent' ? 0 : epoch });
  if (epoch === 'absent') f.raw.exec('ALTER TABLE flow_runs DROP COLUMN retry_epoch');
  expect(await f.repo.abort('flow', 'legacy stop')).toBe(true);
});
it('rejects positive abort on a genuinely epochless schema', async () => {
  f.seed(); f.raw.exec('ALTER TABLE flow_runs DROP COLUMN retry_epoch'); const before = f.row();
  expect(await f.repo.abort('flow', 'not a legacy writer', 2)).toBe(false); expect(f.row()).toEqual(before);
});
it.each(['status', 'completion', 'epoch', 'raw-null-epoch'] as const)('CAS rejects %s changing after the real admission SELECT', async change => {
  f.seed({}, { epoch: change === 'raw-null-epoch' ? null : 2 }); let hit = false;
  const racing: IDatabase = new Proxy(f.db, { get(target, key) {
    if (key === 'query') return async <T>(sql: string, params?: unknown[]) => {
      const rows = await target.query<T>(sql, params);
      if (!hit && /^\s*SELECT\b/i.test(sql) && /FROM flow_runs/i.test(sql)) {
        hit = true;
        const mutation = { status: "status='waiting'", completion: "status='succeeded', completed_at=123, result_json='winner'",
          epoch: 'retry_epoch=3', 'raw-null-epoch': 'retry_epoch=0' }[change];
        f.raw.exec(`UPDATE flow_runs SET ${mutation}`);
      }
      return rows;
    };
    const member = Reflect.get(target, key); return typeof member === 'function' ? member.bind(target) : member;
  } });
  expect(await new FlowRunRepository(racing).abort('flow', 'stale', change === 'raw-null-epoch' ? 0 : 2)).toBe(false);
  expect(hit).toBe(true); expect(f.row().status).not.toBe('cancelled');
});
it('propagates an abort write failure instead of returning false', async () => {
  f.seed({}, { epoch: 2 }); const before = f.row();
  f.raw.exec("CREATE TRIGGER fail_abort BEFORE UPDATE ON flow_runs BEGIN SELECT RAISE(ABORT, 'write unavailable'); END");
  await expect(f.repo.abort('flow', 'stop', 2)).rejects.toThrow('write unavailable'); expect(f.row()).toEqual(before);
});
it('missing abort snapshot and mutation remain absent', async () => {
  expect(await f.repo.findForAbort('flow')).toBeNull(); expect(await f.repo.abort('flow', 'stop')).toBe(false);
});
