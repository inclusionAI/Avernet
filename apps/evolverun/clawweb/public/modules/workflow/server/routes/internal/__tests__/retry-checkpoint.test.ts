import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { retryCheckpointFixture, nextRetryState, priorRetryState } from '../../../repositories/__tests__/retry-checkpoint.fixture.js';

let f: ReturnType<typeof retryCheckpointFixture>;
beforeEach(() => { f = retryCheckpointFixture(); });
afterEach(async () => { await f.close(); vi.restoreAllMocks(); });
it('retry-resume accepts state_json and atomically persists canonical and embedded state with epoch+1', async () => {
  f.seed(); const put = await f.http(); const stateJson = JSON.stringify(nextRetryState);
  expect((await put({ retry_epoch: 3, state_json: stateJson })).status).toBe(200);
  const row = f.row(); expect(row).toMatchObject({ status: 'running', retry_epoch: 3, state_json: stateJson, started_at: 123, completed_at: null });
  expect(JSON.parse(JSON.parse(row.params_json as string)._clawmind_state)).toEqual(nextRetryState);
  expect(f.raw.prepare('SELECT COUNT(*) AS n FROM retry_audit').get()).toEqual({ n: 1 });
  expect(f.onTerminal).not.toHaveBeenCalled();
});
it.each([null, 0, {}, [], '', '{', 'null', '[]', '{}', '{"retryEpoch":2}', '{"retryEpoch":"3"}'])(
  'malformed state_json %j is HTTP400 without partially advancing epoch', async invalid => {
    f.seed(); const before = f.row(); const put = await f.http();
    expect((await put({ retry_epoch: 3, state_json: invalid })).status).toBe(400);
    expect(f.row()).toEqual(before); expect(f.raw.prepare('SELECT * FROM retry_audit').all()).toEqual([]);
  });
it('wrong checkpoint identity is HTTP400 and never changes the run', async () => {
  f.seed(); const before = f.row(); const put = await f.http();
  expect((await put({ retry_epoch: 3, state_json: JSON.stringify({ ...nextRetryState, workflowId: 'other' }) })).status).toBe(400);
  expect(f.row()).toEqual(before);
});
it('contradictory historical embedded checkpoint is HTTP409 with no mutation', async () => {
  f.seed(); f.raw.prepare('UPDATE flow_runs SET params_json = ?').run(JSON.stringify({ _clawmind_state: { ...priorRetryState, checkpoint: 'other' } }));
  f.raw.exec('DELETE FROM retry_audit'); const before = f.row(); const put = await f.http();
  expect((await put({ retry_epoch: 3, state_json: JSON.stringify(nextRetryState) })).status).toBe(409);
  expect(f.row()).toEqual(before); expect(f.raw.prepare('SELECT * FROM retry_audit').all()).toEqual([]);
});
it('checkpoint persistence database failure is HTTP500, not an accepted retry with missing state', async () => {
  f.seed(); const before = f.row(); const put = await f.http();
  f.raw.exec("CREATE TRIGGER reject_checkpoint BEFORE UPDATE ON flow_runs BEGIN SELECT RAISE(ABORT, 'checkpoint unavailable'); END");
  expect((await put({ retry_epoch: 3, state_json: JSON.stringify(nextRetryState) })).status).toBe(500);
  expect(f.row()).toEqual(before); expect(f.raw.prepare('SELECT * FROM retry_audit').all()).toEqual([]);
});
it('replayed retry remains read-only after the running checkpoint advances', async () => {
  f.seed(); const put = await f.http(); const body = { retry_epoch: 3, state_json: JSON.stringify(nextRetryState) };
  expect((await put(body)).status).toBe(200);
  f.raw.prepare('UPDATE flow_runs SET state_json = ?').run(JSON.stringify({ ...nextRetryState, checkpoint: 'progressed' }));
  f.raw.exec('DELETE FROM retry_audit'); const before = f.row();
  expect((await put(body)).status).toBe(200); expect(f.row()).toEqual(before);
  expect(f.raw.prepare('SELECT * FROM retry_audit').all()).toEqual([]);
});
it('checkpoint retry preserves missing404', async () => {
  f.seed(); const put = await f.http(); expect((await put({ retry_epoch: 3, state_json: JSON.stringify(nextRetryState) }, 'missing')).status).toBe(404);
});
it('unavailable repository is503 even for a checkpoint-bearing request', async () => {
  const put = await f.http(null); expect((await put({ retry_epoch: 3, state_json: JSON.stringify(nextRetryState) })).status).toBe(503);
});

it('Mind recoveryEpoch checkpoint is accepted and persisted without inventing retryEpoch', async () => {
  f.seed(); const prior = JSON.stringify({ workflowId: 'wf', recoveryEpoch: 2 });
  f.raw.prepare('UPDATE flow_runs SET state_json=?, params_json=?').run(prior, JSON.stringify({ _clawmind_state: prior }));
  const put = await f.http(); const next = JSON.stringify({ workflowId: 'wf', recoveryEpoch: 3 });
  expect((await put({ retry_epoch: 3, state_json: next })).status).toBe(200);
  expect(f.row()).toMatchObject({ retry_epoch: 3, state_json: next });
  expect(JSON.parse(f.row().params_json as string)._clawmind_state).toBe(next);
});
it.each([
  { recoveryEpoch: '3' }, { recoveryEpoch: null }, { recoveryEpoch: 3, retryEpoch: 2 }, { retryEpoch: 3, recoveryEpoch: null },
])('invalid Mind or conflicting checkpoint aliases %j are HTTP400 before admission', async aliases => {
  f.seed(); const before = f.row(); const put = await f.http();
  expect((await put({ retry_epoch: 3, state_json: JSON.stringify(aliases) })).status).toBe(400); expect(f.row()).toEqual(before);
});
it('HTTP retry cannot replace a managed child checkpoint', async () => {
  f.seed(); const prior = JSON.stringify({ ...priorRetryState, _fanoutIdentity: { parentFlowId: 'parent' } });
  f.raw.prepare('UPDATE flow_runs SET state_json=?, params_json=?').run(prior, JSON.stringify({ _clawmind_state: prior }));
  const before = f.row(); const put = await f.http();
  expect((await put({ retry_epoch: 3, state_json: JSON.stringify(nextRetryState) })).status).toBe(409); expect(f.row()).toEqual(before);
});
