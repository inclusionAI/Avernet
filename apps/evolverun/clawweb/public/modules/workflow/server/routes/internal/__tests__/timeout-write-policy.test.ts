import { afterEach, beforeEach, expect, it } from 'vitest';
import { NOW_SECONDS, structuredTimeout, timeoutEvidence, timeoutFixture } from '../../../repositories/__tests__/timeout-write.fixture.js';

let f: ReturnType<typeof timeoutFixture>;
beforeEach(() => { f = timeoutFixture(); });
afterEach(async () => { await f.close(); });

it.each([1440, 0, null, 'invalid'])('HTTP records an executor timeout regardless of stored budget %s', async budget => {
  f.seed({ effectiveFlowTimeoutMinutes: budget, workflowSnapshot: { flowTimeoutMinutes: 120 } });
  const request = await f.http();
  const payload = { status: 'failed', result_json: timeoutEvidence() };
  expect((await request('completion', payload)).status).toBe(200);
  expect(f.row()).toMatchObject({ status: 'failed', result_json: payload.result_json, completed_at: NOW_SECONDS });
  expect((await request('completion', payload)).status).toBe(200);
  expect(f.onTerminal).toHaveBeenCalledExactlyOnceWith({ flowId: 'flow', status: 'failed' });
});
it('bare /status cannot settle a run; the executor can settle it through completion', async () => {
  f.seed({ effectiveFlowTimeoutMinutes: 1440 });
  const request = await f.http();
  const before = f.row();
  expect((await request('status', { status: 'failed' })).status).toBe(409);
  expect(f.row()).toEqual(before);
  expect(f.onTerminal).not.toHaveBeenCalled();
  expect((await request('completion', { status: 'failed', result_json: structuredTimeout() })).status).toBe(200);
  expect(f.row().status).toBe('failed');
  expect(f.onTerminal).toHaveBeenCalledTimes(1);
});
it.each(['succeeded', 'cancelled', 'canceled'])('HTTP protects native/shared terminal %s from unlabelled failure', async status => {
  f.seed(undefined, { status, epoch: 2 });
  const request = await f.http();
  const before = f.row();
  expect((await request('status', { status: 'failed' })).status).toBe(409);
  expect((await request('completion', { status: 'failed' })).status).toBe(409);
  expect((await request('completion', { status: 'failed', result_json: structuredTimeout() })).status).toBe(409);
  expect((await request('fail-if-running', { reason: timeoutEvidence() })).body.data.claimed).toBe(false);
  expect(f.row()).toEqual(before);
  expect(f.onTerminal).not.toHaveBeenCalled();
});
it('nested timeout with different executor and wall clocks records the server settlement timestamp', async () => {
  f.seed(undefined, { elapsed: 7000 });
  const request = await f.http();
  expect((await request('completion', { status: 'failed', result_json: structuredTimeout(), completed_at: NOW_SECONDS + 99_999 })).status).toBe(200);
  expect(f.row()).toMatchObject({ status: 'failed', completed_at: NOW_SECONDS });
  expect(f.onTerminal).toHaveBeenCalledTimes(1);
});
it('HTTP watchdog accepts a nonempty reason without requiring a duration proof', async () => {
  f.seed({ effectiveFlowTimeoutMinutes: 1440 });
  const request = await f.http();
  const response = await request('fail-if-running', { reason: 'legacy watchdog decision' });
  expect(response).toEqual({ status: 200, body: { success: true, data: { claimed: true } } });
  expect(f.row()).toMatchObject({ status: 'failed', result_json: 'legacy watchdog decision' });
  expect(f.onTerminal).toHaveBeenCalledTimes(1);
});
it.each(['completion', 'fail-if-running'])('one true timeout via %s, generic duplicate acknowledged without another observer', async first => {
  f.seed();
  const request = await f.http();
  const completeBody = { status: 'failed', result_json: timeoutEvidence(), completed_at: 1, total_duration_ms: 7_300_000 };
  const claimBody = { reason: timeoutEvidence(), completed_at: 1, total_duration_ms: 7_300_000 };
  const settled = await request(first, first === 'completion' ? completeBody : claimBody);
  expect(settled.status).toBe(200);
  if (first === 'fail-if-running') expect(settled.body.data.claimed).toBe(true);
  const saved = f.row();
  expect(saved).toMatchObject({ status: 'failed', completed_at: NOW_SECONDS });
  expect((await request('completion', completeBody)).status).toBe(200);
  expect((await request('fail-if-running', claimBody)).body.data.claimed).toBe(false);
  expect(f.row()).toEqual(saved);
  expect(f.onTerminal).toHaveBeenCalledExactlyOnceWith({ flowId: 'flow', status: 'failed' });
});
it('simultaneous generic and watchdog completions yield one transition and one observer', async () => {
  f.seed();
  const request = await f.http();
  const responses = await Promise.all([
    request('completion', { status: 'failed', result_json: timeoutEvidence() }),
    request('completion', { status: 'failed', result_json: timeoutEvidence() }),
    request('fail-if-running', { reason: timeoutEvidence() }),
  ]);
  expect(responses.map(r => r.status)).toEqual([200, 200, 200]);
  expect(f.onTerminal).toHaveBeenCalledTimes(1);
  expect(f.row().completed_at).toBe(NOW_SECONDS);
});
it('does not acknowledge a timeout as duplicate of an unrelated failure', async () => {
  f.seed(undefined, { status: 'failed' });
  const request = await f.http();
  const before = f.row();
  expect((await request('completion', { status: 'failed', result_json: timeoutEvidence() })).status).toBe(409);
  expect(f.row()).toEqual(before);
  expect(f.onTerminal).not.toHaveBeenCalled();
});
it('HTTP loses an exact CAS race after the snapshot SELECT without notifying', async () => {
  f.seed();
  const racing = f.afterPolicyRead(() => f.raw.exec('UPDATE flow_runs SET started_at = started_at + 1'));
  const request = await f.http(racing.db);
  expect((await request('completion', { status: 'failed', result_json: structuredTimeout() })).status).toBe(409);
  expect(racing.hit()).toBe(true);
  expect(f.row().status).toBe('running');
  expect(f.onTerminal).not.toHaveBeenCalled();
});
it('normal controller failure settles once and rejects different failed details', async () => {
  f.seed({}, { epoch: 2 });
  const request = await f.http();
  expect((await request('status', { status: 'failed' })).status).toBe(409);
  expect((await request('completion', { status: 'failed', retry_epoch: 2, result_json: '{"message":"real TaskFlow.fail"}' })).status).toBe(200);
  expect(f.row().status).toBe('failed');
  expect((await request('completion', { status: 'failed', retry_epoch: 2, result_json: '{"message":"phase-sync details"}' })).status).toBe(409);
});
it('does not turn an actual timeout persistence failure into success or observer notification', async () => {
  f.seed();
  f.raw.exec("CREATE TRIGGER reject_timeout BEFORE UPDATE ON flow_runs BEGIN SELECT RAISE(ABORT, 'timeout write rejected'); END;");
  const before = f.row();
  const request = await f.http();
  expect((await request('completion', { status: 'failed', result_json: timeoutEvidence() })).status).toBe(500);
  expect(f.row()).toEqual(before);
  expect(f.onTerminal).not.toHaveBeenCalled();
});
it('generic retry rejects a different result despite a saved watchdog timeout phase', async () => {
  f.seed();
  const request = await f.http();
  expect((await request('fail-if-running', { reason: timeoutEvidence(7200, 7395, 'other') })).body.data.claimed).toBe(true);
  const saved = f.row();
  expect((await request('completion', { status: 'failed', result_json: structuredTimeout() })).status).toBe(409);
  expect(f.row()).toEqual(saved);
  expect(f.onTerminal).toHaveBeenCalledTimes(1);
});
