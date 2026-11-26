import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { lockFixture, lockOptions } from '../../__tests__/distributed-lock-fixture.js';
import { DistributedLockService, DistributedLeaseLostError, type DistributedLease } from '../distributed-lock.js';

const fixtures: Array<Awaited<ReturnType<typeof lockFixture>>> = [];
beforeEach(() => { vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'performance'] }); });
afterEach(async () => { vi.useRealTimers(); vi.restoreAllMocks(); for (const f of fixtures.splice(0)) await f.close(); });
function deferred<T = void>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}
async function fixture() {
  const f = await lockFixture(); fixtures.push(f);
  return { ...f, service: new DistributedLockService(f.a) };
}
const options = { ...lockOptions, renewEveryMs: 20_000 };
async function advance(f: { clock: { now: number } }, ms: number) {
  for (let remaining = ms; remaining > 0;) {
    const step = Math.min(10_000, remaining); remaining -= step;
    f.clock.now += step; await vi.advanceTimersByTimeAsync(step);
  }
}

it('skips the callback and starts no renewal timer when another holder owns the lease', async () => {
  const f = await fixture(), run = vi.fn();
  await f.b.claim(lockOptions);
  expect(await f.service.withLease(options, run)).toEqual({ acquired: false });
  expect(run).not.toHaveBeenCalled(); expect(vi.getTimerCount()).toBe(0);
});

it('renews a slow callback beyond the original expiry then releases the latest revision', async () => {
  const f = await fixture(), done = deferred(), started = vi.fn();
  const running = f.service.withLease(options, async lease => { started(); await done.promise; lease.assertHeld(); return 'result'; });
  await vi.advanceTimersByTimeAsync(0);
  expect(started).toHaveBeenCalledOnce();
  await advance(f, 140_000);
  expect(await f.b.claim(lockOptions)).toBeNull();
  done.resolve();
  expect(await running).toEqual({ acquired: true, value: 'result' });
  expect((await f.db.query<{ owner_token: null }>('SELECT owner_token FROM clawweb_distributed_locks'))[0].owner_token).toBeNull();
  expect(vi.getTimerCount()).toBe(0);
});

it('releases ownership and preserves the business failure', async () => {
  const f = await fixture(), failure = new Error('business failed');
  await expect(f.service.withLease(options, async () => { throw failure; })).rejects.toBe(failure);
  expect(await f.b.claim(lockOptions)).not.toBeNull();
  expect(vi.getTimerCount()).toBe(0);
});

it('stops admitting later work when renewal storage fails', async () => {
  const f = await fixture(), done = deferred(), effect = vi.fn(), failure = new Error('renewal unavailable');
  vi.spyOn(f.a, 'renew').mockRejectedValue(failure);
  const running = expect(f.service.withLease(options, async lease => {
    await done.promise; lease.assertHeld(); effect();
  })).rejects.toBe(failure);
  await vi.advanceTimersByTimeAsync(0);
  await advance(f, 20_000);
  done.resolve(); await running;
  expect(effect).not.toHaveBeenCalled();
  expect(vi.getTimerCount()).toBe(0);
});

it('does not remove a newer owner after expiry and takeover', async () => {
  const f = await fixture(), done = deferred(), effect = vi.fn();
  let handle!: DistributedLease;
  const running = expect(f.service.withLease(options, async lease => {
    handle = lease; await done.promise; lease.assertHeld(); effect();
  })).rejects.toBeInstanceOf(DistributedLeaseLostError);
  await vi.advanceTimersByTimeAsync(0);
  // The old event loop stalls; database time advances and another connection takes over.
  f.clock.now += 120_001;
  const next = (await f.b.claim(lockOptions))!;
  await vi.advanceTimersByTimeAsync(120_001);
  expect(() => handle.assertHeld()).toThrow(DistributedLeaseLostError);
  done.resolve(); await running;
  expect(effect).not.toHaveBeenCalled();
  expect(await f.db.query('SELECT owner_token FROM clawweb_distributed_locks')).toEqual([{ owner_token: next.ownerToken }]);
});

it('waits for an in-flight renewal before releasing the current database version', async () => {
  const f = await fixture(), done = deferred(), renewed = deferred(), renew = f.a.renew.bind(f.a);
  vi.spyOn(f.a, 'renew').mockImplementation(async record => { await renewed.promise; return renew(record); });
  const release = vi.spyOn(f.a, 'release');
  const running = f.service.withLease(options, async () => { await done.promise; });
  await vi.advanceTimersByTimeAsync(0);
  await advance(f, 20_000);
  done.resolve(); await vi.advanceTimersByTimeAsync(0);
  expect(release).not.toHaveBeenCalled();
  renewed.resolve(); await running;
  expect(release).toHaveBeenCalledOnce();
  expect(await f.b.claim(lockOptions)).not.toBeNull();
});

it.each([false, true])('never revives an abandoned callback on a delayed renewal receipt, with takeover=%s', async takeover => {
  const f = await fixture(), done = deferred(), receipt = deferred(), renew = f.a.renew.bind(f.a);
  const release = vi.spyOn(f.a, 'release');
  let handle!: DistributedLease;
  vi.spyOn(f.a, 'renew').mockImplementation(async record => {
    const result = await renew(record); await receipt.promise; return result;
  });
  const running = expect(f.service.withLease(options, async lease => {
    handle = lease; await done.promise; lease.assertHeld();
  })).rejects.toBeInstanceOf(DistributedLeaseLostError);
  await vi.advanceTimersByTimeAsync(0);
  await advance(f, 20_000);
  await advance(f, 100_001);
  expect(() => handle.assertHeld()).toThrow(DistributedLeaseLostError);
  let nextOwner: string | null = null;
  if (takeover) {
    f.clock.now += 20_000;
    nextOwner = (await f.b.claim(lockOptions))!.ownerToken;
  }
  done.resolve(); receipt.resolve(); await running;
  expect(() => handle.assertHeld()).toThrow(DistributedLeaseLostError);
  expect(release).toHaveBeenCalledOnce();
  await expect(release.mock.results[0].value).resolves.toBe(!takeover);
  expect(await f.db.query('SELECT owner_token FROM clawweb_distributed_locks')).toEqual([{ owner_token: nextOwner }]);
  if (!takeover) expect(await f.b.claim(lockOptions)).not.toBeNull();
});

it('propagates acquisition errors without executing the callback', async () => {
  const f = await fixture(), run = vi.fn(), failure = new Error('database unavailable');
  vi.spyOn(f.a, 'claim').mockRejectedValue(failure);
  await expect(f.service.withLease(options, run)).rejects.toBe(failure);
  expect(run).not.toHaveBeenCalled();
});

it('reports a release write failure rather than claiming success', async () => {
  const f = await fixture(), failure = new Error('release unavailable');
  vi.spyOn(f.a, 'release').mockRejectedValue(failure);
  await expect(f.service.withLease(options, async () => 'done')).rejects.toBe(failure);
  expect(vi.getTimerCount()).toBe(0);
});

it('preserves both errors when business work and release fail', async () => {
  const f = await fixture(), business = new Error('business failed'), release = new Error('release failed');
  vi.spyOn(f.a, 'release').mockRejectedValue(release);
  await expect(f.service.withLease(options, async () => { throw business; })).rejects.toMatchObject({ errors: [business, release] });
});

it.each([0, -1, 120_000, 120_001])('rejects an unsafe renewal interval of %s before acquisition', async renewEveryMs => {
  const f = await fixture(), claim = vi.spyOn(f.a, 'claim'), run = vi.fn();
  await expect(f.service.withLease({ ...options, renewEveryMs }, run)).rejects.toThrow('renewal interval');
  expect(claim).not.toHaveBeenCalled(); expect(run).not.toHaveBeenCalled();
});
