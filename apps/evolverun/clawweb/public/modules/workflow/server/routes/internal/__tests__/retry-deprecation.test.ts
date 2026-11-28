import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import express from 'express';
import type { Server } from 'node:http';
import { timeoutFixture } from '../../../repositories/__tests__/timeout-write.fixture.js';
import { createInternalRunsRouter } from '../runs.js';

let f: ReturnType<typeof timeoutFixture>;
let server: Server | undefined;
beforeEach(() => { f = timeoutFixture(); });
afterEach(async () => {
  if (server) await new Promise<void>(resolve => { server!.closeAllConnections(); server!.close(() => resolve()); });
  server = undefined; await f.close(); vi.restoreAllMocks();
});
it.each(['failed', 'succeeded', 'running', 'waiting', 'cancelled'])('deprecated retry-failed returns actionable 409 without changing %s', async status => {
  f.seed({}, { status, epoch: 2 }); const before = f.row(); const put = await f.http();
  const exec = vi.spyOn(f.db, 'exec');
  const response = await put('retry-failed', { retry_epoch: 3 });
  expect(response.status).toBe(409);
  expect(response.body).toMatchObject({ success: false, error: 'Conflict', message: expect.stringMatching(/deprecated.*retry-resume/i) });
  expect(exec).not.toHaveBeenCalled(); expect(f.row()).toEqual(before); expect(f.onTerminal).not.toHaveBeenCalled();
});
it('deprecated retry-failed preserves missing-run 404', async () => {
  const put = await f.http(); expect((await put('retry-failed', {})).status).toBe(404);
});
it('deprecated retry-failed reports database read failure as 500', async () => {
  f.seed(); const put = await f.http(); vi.spyOn(f.db, 'query').mockRejectedValueOnce(new Error('read unavailable'));
  expect((await put('retry-failed', {})).status).toBe(500);
});
it('deprecated retry-failed preserves unavailable repository 503', async () => {
  const app = express(); app.use(express.json()); app.use('/runs', createInternalRunsRouter(null));
  server = await new Promise<Server>(resolve => { const s = app.listen(0, '127.0.0.1', () => resolve(s)); });
  const response = await fetch(`http://127.0.0.1:${(server.address() as { port: number }).port}/runs/flow/retry-failed`, {
    method: 'PUT', headers: { 'content-type': 'application/json' }, body: '{}',
  });
  expect(response.status).toBe(503);
});
it.each([' CANCELLED ', 'Canceled', ' canceled '])('retry-resume cancellation %s is 409 without mutation', async status => {
  f.seed({}, { status, epoch: 2 }); const before = f.row(); const put = await f.http();
  expect((await put('retry-resume', { retry_epoch: 3 })).status).toBe(409);
  expect(f.row()).toEqual(before);
});
