import { afterEach, beforeEach, expect, it } from 'vitest';
import express from 'express';
import type { Server } from 'node:http';
import { timeoutFixture } from './timeout-write.fixture.js';
import { createInternalRunsRouter } from '../../routes/internal/runs.js';

let f: ReturnType<typeof timeoutFixture>; let server: Server | undefined;
beforeEach(() => {
  f = timeoutFixture();
  f.raw.exec("ALTER TABLE flow_runs ADD COLUMN workflow_id TEXT DEFAULT 'wf'; ALTER TABLE flow_runs ADD COLUMN origin_session_key TEXT; ALTER TABLE flow_runs ADD COLUMN params_json TEXT");
});
afterEach(async () => {
  if (server) await new Promise<void>(resolve => { server!.closeAllConnections(); server!.close(() => resolve()); });
  server = undefined; await f.close();
});
async function http() {
  const app = express(); app.use(express.json()); app.use('/runs', createInternalRunsRouter(f.repo));
  server = await new Promise<Server>(resolve => { const s = app.listen(0, '127.0.0.1', () => resolve(s)); });
  return async (path: string, body: unknown) => fetch(`http://127.0.0.1:${(server!.address() as { port: number }).port}/runs/flow/${path}`,
    { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
}
for (const path of ['state', 'phase']) {
  for (const epoch of [undefined, 0, 1]) {
    it(`${path} cannot let stale/omitted epoch ${epoch} mutate a positive attempt`, async () => {
      f.seed({}, { epoch: 2 }); const before = f.row(); const put = await http();
      const body = path === 'state' ? { state_json: '{"retryEpoch":1}' } : { phase: 'old-P2' };
      expect((await put(path, { ...body, retry_epoch: epoch })).status).toBe(409); expect(f.row()).toEqual(before);
    });
  }
  it(`${path} admits a matching-epoch nonterminal mirror`, async () => {
    f.seed({}, { epoch: 2 }); const put = await http();
    const body = path === 'state' ? { state_json: '{"retryEpoch":2,"currentPhase":"P2"}' } : { phase: 'P2' };
    expect((await put(path, { ...body, retry_epoch: 2 })).status).toBe(200);
    expect(path === 'state' ? JSON.parse(f.row().state_json as string).currentPhase : f.row().current_phase).toBe('P2');
  });
  for (const status of ['succeeded', 'failed', 'completed', 'cancelled']) {
    it(`${path} cannot modify ${status} settlement even at the matching epoch`, async () => {
      f.seed({}, { epoch: 2, status }); const before = f.row(); const put = await http();
      const body = path === 'state' ? { state_json: '{"retryEpoch":2,"checkpoint":"late"}' } : { phase: 'late-P2' };
      expect((await put(path, { ...body, retry_epoch: 2 })).status).toBe(409); expect(f.row()).toEqual(before);
    });
  }
  it.each([null, -1, '2', 0.5])(`${path} invalid epoch %j is 400 without writes`, async epoch => {
    f.seed(); const before = f.row(); const put = await http();
    const body = path === 'state' ? { state_json: '{}' } : { phase: 'P2' };
    expect((await put(path, { ...body, retry_epoch: epoch })).status).toBe(400); expect(f.row()).toEqual(before);
  });
  it(`${path} database failure is HTTP500, not false404 or count0`, async () => {
    f.seed(); const before = f.row(); const put = await http();
    f.raw.exec("CREATE TRIGGER reject_mirror BEFORE UPDATE ON flow_runs BEGIN SELECT RAISE(ABORT,'mirror unavailable'); END");
    const body = path === 'state' ? { state_json: '{"checkpoint":"next"}' } : { phase: 'P2' };
    expect((await put(path, body)).status).toBe(500); expect(f.row()).toEqual(before);
  });
}
it('terminal same-value mirrors can acknowledge read-only but cannot authorize a stale generation', async () => {
  f.seed({}, { epoch: 2, status: 'succeeded' }); f.raw.exec("UPDATE flow_runs SET current_phase='P1'");
  const before = f.row(); const put = await http();
  f.raw.exec("CREATE TRIGGER no_rewrite BEFORE UPDATE ON flow_runs BEGIN SELECT RAISE(ABORT,'terminal rewrite'); END");
  expect((await put('state', { state_json: before.state_json, retry_epoch: 2 })).status).toBe(200);
  expect((await put('phase', { phase: 'P1', retry_epoch: 2 })).status).toBe(200);
  expect((await put('phase', { phase: 'P1', retry_epoch: 1 })).status).toBe(409); expect(f.row()).toEqual(before);
});
it('legacy missing-column schema admits only epoch-zero mirror inside transaction', async () => {
  f.seed(); f.raw.exec('ALTER TABLE flow_runs DROP COLUMN retry_epoch');
  expect(await f.repo.updateCurrentPhase('flow', 'P1', 0)).toBe(true);
  expect(await f.repo.updateStateJson('flow', '{"checkpoint":"legacy"}', 0)).toBe(true);
  const before = f.row(); expect(await f.repo.updateCurrentPhase('flow', 'P2', 1)).toBe(false);
  expect(await f.repo.updateStateJson('flow', '{}', 1)).toBe(false); expect(f.row()).toEqual(before);
});

it.each([
  {}, { retryEpoch: 1 }, { recoveryEpoch: 1 }, { retryEpoch: 2, recoveryEpoch: 1 },
  { retryEpoch: 2, recoveryEpoch: null }, { recoveryEpoch: 2, retryEpoch: '2' },
])('matching wire epoch cannot persist an absent/contradictory positive-attempt checkpoint %j', async state => {
  f.seed({}, { epoch: 2 }); const before = f.row(); const put = await http();
  expect((await put('state', { state_json: JSON.stringify(state), retry_epoch: 2 })).status).toBe(409); expect(f.row()).toEqual(before);
});
it('legacy epoch-zero mirror without checkpoint aliases still works', async () => {
  f.seed(); const put = await http();
  expect((await put('state', { state_json: '{"currentPhase":"P2"}' })).status).toBe(200);
  expect((await put('phase', { phase: 'P2' })).status).toBe(200);
});
it('matching Mind recoveryEpoch mirror works without adding a TaskGuard alias', async () => {
  f.seed({}, { epoch: 2 }); const put = await http();
  expect((await put('state', { state_json: '{"recoveryEpoch":2,"currentPhase":"P2"}', retry_epoch: 2 })).status).toBe(200);
  expect(JSON.parse(f.row().state_json as string)).toEqual({ recoveryEpoch: 2, currentPhase: 'P2' });
});

it.each(['string', 'object'])('progress mirrors synchronize existing embedded %s state so a second retry remains possible', async kind => {
  f.seed(); const state0 = { workflowId: 'wf', recoveryEpoch: 0, currentPhase: 'P1' };
  f.raw.prepare('UPDATE flow_runs SET state_json=?, params_json=?').run(JSON.stringify(state0), JSON.stringify({ keep: 7, _clawmind_state: kind === 'string' ? JSON.stringify(state0) : state0 }));
  const next1 = { ...state0, recoveryEpoch: 1 };
  expect(await f.repo.resumeForRetry('flow', 1, JSON.stringify(next1))).toBe('resumed');
  const progress1 = { ...next1, currentPhase: 'P2' };
  expect(await f.repo.updateStateJson('flow', JSON.stringify(progress1), 1)).toBe(true);
  const embedded = JSON.parse(f.row().params_json as string); expect(embedded.keep).toBe(7);
  expect(typeof embedded._clawmind_state).toBe(kind); expect(kind === 'string' ? JSON.parse(embedded._clawmind_state) : embedded._clawmind_state).toEqual(progress1);
  expect(await f.repo.updateCompletion('flow', { status: 'failed', retryEpoch: 1, completedAt: 123, succeededCount: 0, failedCount: 0 })).toBe(true);
  expect(await f.repo.resumeForRetry('flow', 2, JSON.stringify({ ...progress1, recoveryEpoch: 2 }))).toBe('resumed');
  expect(f.row().retry_epoch).toBe(2);
});
it('embedded mirror SQL failure rolls canonical progress back with the parent transaction', async () => {
  f.seed(); f.raw.prepare('UPDATE flow_runs SET params_json=?').run(JSON.stringify({ _clawmind_state: '{}' }));
  const before = f.row(); f.raw.exec("CREATE TRIGGER refuse_embedded BEFORE UPDATE OF params_json ON flow_runs BEGIN SELECT RAISE(ABORT,'embedded unavailable'); END");
  await expect(f.repo.updateStateJson('flow', '{"progress":true}', 0)).rejects.toThrow('embedded unavailable'); expect(f.row()).toEqual(before);
});
