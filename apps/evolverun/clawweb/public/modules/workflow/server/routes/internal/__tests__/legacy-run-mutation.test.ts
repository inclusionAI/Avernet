import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import express from 'express';
import type { Server } from 'node:http';
import { timeoutFixture } from '../../../repositories/__tests__/timeout-write.fixture.js';
import { createInternalRunsRouter } from '../runs.js';

let f: ReturnType<typeof timeoutFixture>; let server: Server | undefined;
beforeEach(() => { f = timeoutFixture(); });
afterEach(async () => {
  if (server) await new Promise<void>(resolve => { server!.closeAllConnections(); server!.close(() => resolve()); });
  server = undefined; await f.close(); vi.restoreAllMocks();
});
for (const path of ['started-at', 'result-json']) {
  const body = () => ({ retry_epoch: 0, ...(path === 'started-at' ? { started_at: 999 } : { result_json: '{"nodeId":"old-node","value":"late"}' }) });
  it.each(['running', 'waiting', 'succeeded', 'failed', 'completed', 'cancelled'])(`${path} refuses stale epoch-zero mutation of %s positive parent without UPDATE or observer`, async status => {
    f.seed({}, { status, epoch: 2 }); const before = f.row(); const put = await f.http(); const exec = vi.spyOn(f.db, 'exec');
    const response = await put(path, body()); expect(response.status).toBe(409);
    expect(response.body).toMatchObject({ success: false, error: 'Conflict', message: expect.stringMatching(/deprecated/i) });
    expect(exec).not.toHaveBeenCalled(); expect(f.row()).toEqual(before); expect(f.onTerminal).not.toHaveBeenCalled();
  });
  it.each(['failed', 'succeeded'])(`${path} cannot invalidate the original %s completion replay`, async status => {
    f.seed({}, { epoch: 2 }); const put = await f.http();
    const intent = { status, result_json: '{"nodeId":"final-node","value":"accepted"}', retry_epoch: 2, succeeded_count: 1, failed_count: 0 };
    expect((await put('completion', intent)).status).toBe(200); const settled = f.row(); const observers = f.onTerminal.mock.calls.length;
    f.raw.exec("CREATE TRIGGER no_rewrite BEFORE UPDATE ON flow_runs BEGIN SELECT RAISE(ABORT,'terminal rewrite'); END");
    expect((await put(path, body())).status).toBe(409);
    expect((await put('completion', intent)).status).toBe(200); expect(f.row()).toEqual(settled); expect(f.onTerminal).toHaveBeenCalledTimes(observers);
  });
  it(`${path} missing run is404`, async () => {
    const put = await f.http(); expect((await put(path, body())).status).toBe(404);
  });
  it(`${path} database read failure is500 without changes`, async () => {
    f.seed(); const before = f.row(); const put = await f.http(); vi.spyOn(f.db, 'query').mockRejectedValueOnce(new Error('read unavailable'));
    expect((await put(path, body())).status).toBe(500); expect(f.row()).toEqual(before);
  });
  it(`${path} database unavailable is503`, async () => {
    const app = express(); app.use(express.json()); app.use('/runs', createInternalRunsRouter(null));
    server = await new Promise<Server>(resolve => { const s = app.listen(0, '127.0.0.1', () => resolve(s)); });
    const response = await fetch(`http://127.0.0.1:${(server.address() as { port: number }).port}/runs/flow/${path}`,
      { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body()) });
    expect(response.status).toBe(503);
  });
}
