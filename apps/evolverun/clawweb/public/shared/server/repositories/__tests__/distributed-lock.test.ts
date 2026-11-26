import { afterEach, expect, it, vi } from 'vitest';
import { lockFixture, lockOptions } from '../../__tests__/distributed-lock-fixture.js';
import { DistributedLeaseLostError, DistributedLockRepository } from '../distributed-lock-repository.js';

const fixtures: Array<Awaited<ReturnType<typeof lockFixture>>> = [];
async function fixture() { const f = await lockFixture(); fixtures.push(f); return f; }
afterEach(async () => { vi.restoreAllMocks(); for (const f of fixtures.splice(0)) await f.close(); });

it('arbitrates concurrent first acquisition across independent database connections', async () => {
  const f = await fixture();
  const claims = await Promise.all([f.a.claim(lockOptions), f.b.claim(lockOptions)]);
  expect(claims.filter(Boolean)).toHaveLength(1);
  const rows = await f.db.query<{ id: number; owner_token: string; lease_ms: number }>('SELECT id, owner_token, lease_ms FROM clawweb_distributed_locks');
  expect(rows).toHaveLength(1);
  expect(rows[0].id).toBeGreaterThan(0);
  expect(rows[0].owner_token).toBe(claims.find(Boolean)!.ownerToken);
  expect(rows[0].lease_ms).toBe(120_000);
});

it('reuses the auto-increment row while issuing a fresh token for each acquisition', async () => {
  const f = await fixture(), first = (await f.a.claim(lockOptions))!;
  const before = await f.db.query('SELECT id FROM clawweb_distributed_locks');
  expect(await f.a.release(first)).toBe(true);
  const next = (await f.b.claim(lockOptions))!;
  expect(next.ownerToken).not.toBe(first.ownerToken);
  expect(next.version).toBeGreaterThan(first.version);
  expect(await f.db.query('SELECT id FROM clawweb_distributed_locks')).toEqual(before);
  await f.a.claim({ ...lockOptions, resourceKey: 'other' });
  const rows = await f.db.query<{ id: number }>('SELECT id FROM clawweb_distributed_locks ORDER BY id');
  expect(rows[1].id).toBeGreaterThan(rows[0].id);
});

it.each([{ environment: 'pre' }, { scene: 'other' }, { tenantId: 'tenant-b' }, { resourceKey: 'other' }])(
  'isolates the lock by scope %j', async scope => {
    const f = await fixture();
    expect(await f.a.claim(lockOptions)).not.toBeNull();
    expect(await f.b.claim({ ...lockOptions, ...scope })).not.toBeNull();
    expect(await f.db.query('SELECT id FROM clawweb_distributed_locks')).toHaveLength(2);
  });

it('keeps tuple boundaries unambiguous even when identifiers contain separators', async () => {
  const f = await fixture();
  expect(await f.a.claim({ ...lockOptions, scene: 'a:b', resourceKey: 'c' })).not.toBeNull();
  expect(await f.b.claim({ ...lockOptions, scene: 'a', resourceKey: 'b:c' })).not.toBeNull();
});

it('renews the current acquisition using its persisted custom lease duration', async () => {
  const f = await fixture();
  const lease = (await f.a.claim({ ...lockOptions, leaseMs: 30_000 }))!;
  f.clock.now += 20_000;
  const next = await f.a.renew(lease);
  expect(next.leaseMs).toBe(30_000);
  expect(next.remainingMs).toBe(30_000);
  f.clock.now += 29_000;
  expect(await f.b.claim(lockOptions)).toBeNull();
  expect(await f.a.release(next)).toBe(true);
});

it('allows expired takeover and fences both stale renewal and stale release in SQL', async () => {
  const f = await fixture(), old = (await f.a.claim(lockOptions))!;
  f.clock.now += 120_001;
  const next = (await f.b.claim(lockOptions))!;
  expect(next.ownerToken).not.toBe(old.ownerToken);
  await expect(f.a.renew(old)).rejects.toBeInstanceOf(DistributedLeaseLostError);
  expect(await f.a.release(old)).toBe(false);
  // Even a caller that knows the new revision cannot use the old token.
  await expect(f.a.renew({ ...old, version: next.version })).rejects.toBeInstanceOf(DistributedLeaseLostError);
  expect(await f.a.release({ ...old, version: next.version })).toBe(false);
  expect(await f.db.query('SELECT owner_token FROM clawweb_distributed_locks')).toEqual([{ owner_token: next.ownerToken }]);
});

it('does not revive an expired lease even when nobody else has claimed it yet', async () => {
  const f = await fixture(), old = (await f.a.claim(lockOptions))!;
  f.clock.now += 120_000;
  await expect(f.a.renew(old)).rejects.toBeInstanceOf(DistributedLeaseLostError);
  expect(await f.a.release(old)).toBe(false);
  expect(await f.b.claim(lockOptions)).not.toBeNull();
});

it('retains the start-based cooldown after a quick release across staggered replicas', async () => {
  const f = await fixture(), start = f.clock.now;
  const lease = (await f.a.claim({ ...lockOptions, minIntervalMs: 60_000 }))!;
  f.clock.now += 5_000;
  expect(await f.a.release(lease)).toBe(true);
  f.clock.now = start + 8_000;
  expect(await f.b.claim(lockOptions)).toBeNull();
  f.clock.now = start + 59_999;
  expect(await f.b.claim(lockOptions)).toBeNull();
  f.clock.now = start + 60_000;
  expect(await f.b.claim(lockOptions)).not.toBeNull();
});

it('uses the database clock despite a large application wall-clock jump', async () => {
  const f = await fixture();
  const now = vi.spyOn(Date, 'now').mockReturnValue(0);
  const lease = (await f.a.claim(lockOptions))!;
  now.mockReturnValue(9_000_000_000_000);
  expect(await f.b.claim(lockOptions)).toBeNull();
  expect((await f.a.renew(lease)).remainingMs).toBe(120_000);
});

it('propagates storage failures instead of reporting contention', async () => {
  const f = await fixture();
  const failure = new Error('write unavailable'), exec = f.db.exec.bind(f.db);
  vi.spyOn(f.db, 'exec').mockImplementation((sql, params) => sql.startsWith('UPDATE clawweb_distributed_locks')
    ? Promise.reject(failure) : exec(sql, params));
  await expect(f.a.claim(lockOptions)).rejects.toBe(failure);
});

it('rejects a partial table without an effective unique lock key before any claim', async () => {
  const f = await fixture();
  await f.db.exec('DROP TABLE clawweb_distributed_locks');
  await f.db.exec('CREATE TABLE clawweb_distributed_locks (id INTEGER PRIMARY KEY AUTOINCREMENT, lock_id TEXT)');
  await expect(new DistributedLockRepository(f.db).claim(lockOptions)).rejects.toThrow('Incompatible clawweb_distributed_locks');
});

it.each([{ tenantId: '' }, { scene: 'x'.repeat(65) }, { leaseMs: 0 }, { leaseMs: 1.5 }, { minIntervalMs: -1 }])(
  'rejects invalid scope/duration before database access: %j', async invalid => {
    const f = await fixture(), query = vi.spyOn(f.db, 'query'), exec = vi.spyOn(f.db, 'exec');
    await expect(f.a.claim({ ...lockOptions, ...invalid })).rejects.toThrow('Invalid distributed lock');
    expect(query).not.toHaveBeenCalled(); expect(exec).not.toHaveBeenCalled();
  });
