import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import express from 'express';
import type { Server } from 'node:http';
import { NodeExecutionRepository } from '../../../repositories/node-execution-repository.js';
import { createInternalNodeExecutionsRouter } from '../node-executions.js';
import { timeoutFixture } from '../../../repositories/__tests__/timeout-write.fixture.js';

let f: ReturnType<typeof timeoutFixture>;
let server: Server | undefined;
beforeEach(() => { f = timeoutFixture(); });
afterEach(async () => {
  if (server) await new Promise<void>(resolve => { server!.closeAllConnections(); server!.close(() => resolve()); });
  server = undefined; await f.close(); vi.restoreAllMocks();
});

it.each([undefined, null, 'P2'])('completion wire current_phase=%s implements preserve/clear/set (F1)', async phase => {
  f.seed(); f.raw.exec("UPDATE flow_runs SET current_phase = 'P1'");
  const put = await f.http();
  const payload = { status: 'succeeded', result_json: '{"success":true}', current_phase: phase };
  expect((await put('completion', payload)).status).toBe(200);
  expect(f.row().current_phase).toBe(phase === undefined ? 'P1' : phase);
  const settled = f.row();
  f.raw.exec("CREATE TRIGGER no_rewrite BEFORE UPDATE ON flow_runs BEGIN SELECT RAISE(ABORT, 'replayed UPDATE'); END");
  expect((await put('completion', payload)).status).toBe(200);
  expect(f.row()).toEqual(settled);
});
it('same failed settlement never rewrites or renotifies; contradictory intent conflicts', async () => {
  f.seed(); const put = await f.http();
  const payload = { status: 'failed', result_json: '{"reason":"timeout","detail":"first"}' };
  expect((await put('completion', payload)).status).toBe(200);
  const settled = f.row();
  f.raw.exec("CREATE TRIGGER no_rewrite BEFORE UPDATE ON flow_runs BEGIN SELECT RAISE(ABORT, 'replayed UPDATE'); END");
  expect((await put('completion', payload)).status).toBe(200);
  expect((await put('completion', { ...payload, result_json: '{"reason":"timeout","detail":"other"}' })).status).toBe(409);
  expect((await put('completion', { ...payload, status: 'succeeded' })).status).toBe(409);
  expect(f.onTerminal).toHaveBeenCalledTimes(1);
  expect(f.row()).toEqual(settled);
});
it.each([false, 0, {}, []])('invalid current_phase %j is a 400, not silently discarded', async phase => {
  f.seed(); const before = f.row(); const put = await f.http();
  expect((await put('completion', { status: 'failed', current_phase: phase })).status).toBe(400);
  expect(f.row()).toEqual(before); expect(f.onTerminal).not.toHaveBeenCalled();
});

async function reconcileHttp() {
  f.raw.exec(`ALTER TABLE node_executions ADD COLUMN error_text TEXT;
    ALTER TABLE node_executions ADD COLUMN completed_at INTEGER;
    ALTER TABLE node_executions ADD COLUMN gmt_modified INTEGER;
    INSERT INTO node_executions (flow_id, status) VALUES ('flow', 'running');`);
  const repo = new NodeExecutionRepository(f.db);
  const app = express(); app.use(express.json()); app.use('/nodes', createInternalNodeExecutionsRouter(repo));
  server = await new Promise<Server>(resolve => { const s = app.listen(0, '127.0.0.1', () => resolve(s)); });
  const base = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
  return { repo, put: async (body: unknown) => {
    const response = await fetch(`${base}/nodes/flow/reconcile-stale-running`, {
      method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
    });
    return { status: response.status, body: await response.json() };
  } };
}
it.each(['running', 'waiting', 'succeeded'])('reconcile rejects failed claim for parent %s with HTTP409', async status => {
  f.seed({}, { status }); const { put } = await reconcileHttp();
  const before = f.raw.prepare('SELECT * FROM node_executions').all();
  expect((await put({ flow_status: 'failed' })).status).toBe(409);
  expect(f.raw.prepare('SELECT * FROM node_executions').all()).toEqual(before);
});
it.each([undefined, 0, 1])('reconcile rejects wrong/missing retry_epoch %s with HTTP409', async retryEpoch => {
  f.seed({}, { status: 'failed', epoch: 2 }); const { put } = await reconcileHttp();
  expect((await put({ flow_status: 'failed', retry_epoch: retryEpoch })).status).toBe(409);
  expect(f.raw.prepare('SELECT status FROM node_executions').get()).toEqual({ status: 'running' });
});
it('matching reconcile returns a real count, then a valid zero, without touching another flow', async () => {
  f.seed({}, { status: 'failed', epoch: 2 }); const { put } = await reconcileHttp();
  f.raw.exec("INSERT INTO node_executions (flow_id, status) VALUES ('other', 'running')");
  expect(await put({ flow_status: 'failed', retry_epoch: 2 })).toMatchObject({ status: 200, body: { data: { reconciled: 1 } } });
  expect(await put({ flow_status: 'failed', retry_epoch: 2 })).toMatchObject({ status: 200, body: { data: { reconciled: 0 } } });
  expect(f.raw.prepare("SELECT status FROM node_executions WHERE flow_id = 'other'").get()).toEqual({ status: 'running' });
});
it('reconcile database write failure is HTTP500, never HTTP200 count0', async () => {
  f.seed({}, { status: 'failed' }); const { put } = await reconcileHttp();
  f.raw.exec("CREATE TRIGGER reject_write BEFORE UPDATE ON node_executions BEGIN SELECT RAISE(ABORT, 'db unavailable'); END");
  expect((await put({ flow_status: 'failed' })).status).toBe(500);
  expect(f.raw.prepare('SELECT status FROM node_executions').get()).toEqual({ status: 'running' });
});
it.each([NaN, -1, '0', null, undefined])('reconcile invalid count %j is HTTP500', async count => {
  f.seed({}, { status: 'failed' }); const { repo, put } = await reconcileHttp();
  vi.spyOn(repo, 'reconcileStaleRunning').mockResolvedValueOnce(count as number);
  expect((await put({ flow_status: 'failed' })).status).toBe(500);
});
it.each([-1, 0.5, '2', null])('reconcile malformed epoch %j is HTTP400', async epoch => {
  f.seed({}, { status: 'failed' }); const { put } = await reconcileHttp();
  expect((await put({ flow_status: 'failed', retry_epoch: epoch })).status).toBe(400);
});
for (const epoch of [null, 0, 2]) {
  it.each(['succeeded', 'failed', 'cancelled', 'canceled'])(`status cannot reopen %s epoch ${epoch} before a stale timeout`, async status => {
    f.seed({}, { status, epoch }); const before = f.row(); const put = await f.http();
    expect((await put('status', { status: 'running', retry_epoch: epoch ?? 0 })).status).toBe(409);
    expect((await put('completion', { status: 'failed', result_json: '{"reason":"timeout"}', retry_epoch: epoch ?? 0 })).status).toBe(409);
    expect((await put('fail-if-running', { reason: 'late timeout', retry_epoch: epoch ?? 0 })).body.data.claimed).toBe(false);
    expect(f.row()).toEqual(before); expect(f.onTerminal).not.toHaveBeenCalled();
  });
}
it.each([undefined, null, 'P2'])('watchdog wire current_phase=%s implements preserve/clear/set', async currentPhase => {
  f.seed(); f.raw.exec("UPDATE flow_runs SET current_phase = 'P1'"); const put = await f.http();
  expect(await put('fail-if-running', { reason: 'timeout', current_phase: currentPhase })).toMatchObject({ status: 200, body: { data: { claimed: true } } });
  expect(f.row().current_phase).toBe(currentPhase === undefined ? 'P1' : currentPhase);
});
it.each([false, 0, {}, []])('watchdog rejects invalid current_phase %j before settlement', async currentPhase => {
  f.seed(); const before = f.row(); const put = await f.http();
  expect((await put('fail-if-running', { reason: 'timeout', current_phase: currentPhase })).status).toBe(400);
  expect(f.row()).toEqual(before); expect(f.onTerminal).not.toHaveBeenCalled();
});
it('historical completed/null cannot be reopened or overwritten over HTTP', async () => {
  f.seed({}, { status: 'completed' }); const before = f.row(); const put = await f.http();
  expect((await put('status', { status: 'running', retry_epoch: 0 })).status).toBe(409);
  expect((await put('completion', { status: 'failed', result_json: 'late', retry_epoch: 0 })).status).toBe(409);
  expect(f.row()).toEqual(before); expect(f.onTerminal).not.toHaveBeenCalled();
});

for (const epoch of [undefined, 2]) {
  it(`reconcile preserves wire custom_error_text in third position and epoch ${epoch} in fourth`, async () => {
    f.seed({}, { status: 'failed', epoch: epoch ?? 0 }); const { repo, put } = await reconcileHttp();
    const reconcile = vi.spyOn(repo, 'reconcileStaleRunning'); const custom = 'Captured attempt closeout — caller text';
    expect(await put({ flow_status: 'failed', custom_error_text: custom, retry_epoch: epoch }))
      .toMatchObject({ status: 200, body: { data: { reconciled: 1 } } });
    expect(reconcile).toHaveBeenCalledWith('flow', 'failed', custom, epoch);
    expect(f.raw.prepare('SELECT status, error_text FROM node_executions').get()).toEqual({ status: 'failed', error_text: custom });
  });
}
it('reconcile keeps an explicitly empty custom string instead of default text', async () => {
  f.seed({}, { status: 'failed' }); const { put } = await reconcileHttp();
  expect((await put({ flow_status: 'failed', custom_error_text: '' })).status).toBe(200);
  expect(f.raw.prepare('SELECT error_text FROM node_executions').get()).toEqual({ error_text: '' });
});
it.each([null, false, 0, {}, []])('reconcile rejects non-string custom_error_text %j before repository or SQL', async custom => {
  f.seed({}, { status: 'failed' }); const { repo, put } = await reconcileHttp();
  const reconcile = vi.spyOn(repo, 'reconcileStaleRunning'); const before = f.raw.prepare('SELECT * FROM node_executions').all();
  expect((await put({ flow_status: 'failed', custom_error_text: custom })).status).toBe(400);
  expect(reconcile).not.toHaveBeenCalled(); expect(f.raw.prepare('SELECT * FROM node_executions').all()).toEqual(before);
});
