import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { timeoutFixture } from './timeout-write.fixture.js';

let f: ReturnType<typeof timeoutFixture>;
beforeEach(() => { f = timeoutFixture(); });
afterEach(async () => { await f.close(); vi.restoreAllMocks(); });
for (const method of ['resetStartedAt', 'updateResultJson'] as const) {
  const call = () => method === 'resetStartedAt' ? f.repo.resetStartedAt('flow', 999) : f.repo.updateResultJson('flow', 'old-node', { value: 'late' });
  for (const status of ['running', 'waiting', 'succeeded', 'failed', 'completed', 'cancelled']) {
    for (const epoch of [0, 2]) {
      it(`${method} is read-only for existing ${status} epoch ${epoch}`, async () => {
        f.seed({}, { status, epoch }); const before = f.row(); const exec = vi.spyOn(f.db, 'exec');
        expect(await call()).toBe(false); expect(exec).not.toHaveBeenCalled(); expect(f.row()).toEqual(before);
        expect(f.onTerminal).not.toHaveBeenCalled();
      });
    }
  }
  it(`${method} preserves the direct boolean missing contract without writes`, async () => {
    const exec = vi.spyOn(f.db, 'exec'); expect(await call()).toBe(false); expect(exec).not.toHaveBeenCalled();
  });
  it(`${method} propagates read errors rather than reporting a harmless false`, async () => {
    f.seed(); const before = f.row(); vi.spyOn(f.db, 'query').mockRejectedValueOnce(new Error('read unavailable'));
    await expect(call()).rejects.toThrow('read unavailable'); expect(f.row()).toEqual(before);
  });
}
