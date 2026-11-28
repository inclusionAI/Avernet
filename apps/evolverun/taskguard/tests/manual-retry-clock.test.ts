import assert from 'node:assert/strict';
import { test } from 'node:test';
import { SqliteDatabase } from '../src/db/sqlite-database.js';
import { FlowRunRepository } from '../src/db/repositories/flow-run-repository.js';
import { admitManualRetry } from '../src/recovery/manual-retry.js';
import { loadRunningSnapshot } from '../src/recovery/scan.js';
import { reapTimedOutRuns } from '../src/recovery/timeout-sweep.js';
import { setCachedConfig } from '../src/config/config-cache.js';
import { defaults } from '../src/config/types.js';
setCachedConfig({ database: { type: 'sqlite', sqlitePath: ':memory:' }, app: structuredClone(defaults) });
const controller = await import('../src/controller.js');
const workflow = { id: 'retry-clock', title: 'Retry clock', version: 1, nodes: [] };
function state() { return { workflowId: workflow.id, workflowVersion: 1, workflowSnapshot: workflow,
  retryEpoch: 0, effectiveFlowTimeoutMinutes: 1, params: {}, executionMode: 'private', businessStatus: 'INIT',
  currentPhase: 'P2', activeNodes: [], nodeStates: {}, workflowData: {}, actionOutputs: {}, flowHooks: {}, auditLog: [] }; }
async function fixture(t: any) {
  const db = new SqliteDatabase({ path: ':memory:' }); await db.connect(); t.after(() => db.close());
  await db.exec(`CREATE TABLE flow_runs (flow_id TEXT PRIMARY KEY, workflow_id TEXT, status TEXT, retry_epoch INTEGER,
    started_at INTEGER, completed_at INTEGER, current_phase TEXT, state_json TEXT, params_json TEXT, result_json TEXT,
    gmt_modified INTEGER, total_duration_ms INTEGER, total_token_usage INTEGER,
    succeeded_count INTEGER DEFAULT 0, failed_count INTEGER DEFAULT 0)`);
  const prior = state(); const sharedStart = Math.floor(Date.now() / 1000) - 600;
  await db.exec(`INSERT INTO flow_runs (flow_id, workflow_id, status, retry_epoch, started_at, completed_at, state_json, params_json)
    VALUES ('retry-clock', 'retry-clock', 'failed', 0, ?, 1, ?, ?)`, [sharedStart, JSON.stringify(prior), JSON.stringify({ _clawmind_state: JSON.stringify(prior) })]);
  const runs = new FlowRunRepository(db);
  return { db, runs, prior, sharedStart };
}
const sweep = (runs: FlowRunRepository) => reapTimedOutRuns({ repository: runs, globalTimeoutMs: 60_000, limit: 100, onClaim: async () => {} });
test('real TaskGuard manual retry checkpoints a fresh timeout anchor without deprecated started-at write', async t => {
  const f = await fixture(t); const before = Date.now();
  await admitManualRetry(f.runs, 'retry-clock', f.prior);
  const row = (await f.runs.findByFlowId('retry-clock'))!; const saved = JSON.parse(row.state_json!);
  assert.equal(row.started_at, f.sharedStart); assert.equal(row.retry_epoch, 1);
  assert.ok(saved.retryStartedAtMs >= before && saved.retryStartedAtMs <= Date.now());
  assert.deepEqual(JSON.parse(JSON.parse(row.params_json!)._clawmind_state), saved);
  assert.equal(await sweep(f.runs), 0, 'old shared start cannot immediately expire the retry');
  const expired = { ...saved, retryStartedAtMs: Date.now() - 61_000 };
  await f.db.exec('UPDATE flow_runs SET state_json = ?, params_json = ?', [JSON.stringify(expired), JSON.stringify({ _clawmind_state: expired })]);
  assert.equal(await sweep(f.runs), 1, 'same retry expires against its own saved anchor');
  assert.equal((await f.runs.findByFlowId('retry-clock'))?.status, 'failed');
});
test('retry refusal cannot publish/reset a new local timeout anchor', async () => {
  const original = { ...state(), retryStartedAtMs: 123 }; const before = structuredClone(original);
  await assert.rejects(admitManualRetry({ resumeForRetry: async () => 'conflict', resetStartedAt: async () => { throw new Error('deprecated'); } } as any, 'retry-clock', original));
  assert.deepEqual(original, before);
});
for (const bad of [null, '1', -1, 0, Infinity, Date.now() + 86400000]) {
  test(`corrupt retry clock anchor ${String(bad)} never falls back to an older shared start`, async t => {
    const f = await fixture(t); await admitManualRetry(f.runs, 'retry-clock', f.prior);
    const original = JSON.parse((await f.runs.findByFlowId('retry-clock'))!.state_json!);
    await f.db.exec('UPDATE flow_runs SET state_json = ?, params_json = ?', [JSON.stringify({ ...original, retryStartedAtMs: bad }), null]);
    assert.equal(await sweep(f.runs), 0); assert.equal((await f.runs.findByFlowId('retry-clock'))?.status, 'running');
  });
}
test('restart recovery uses the same new retry anchor rather than killing it at the shared original start', async t => {
  const f = await fixture(t); await admitManualRetry(f.runs, 'retry-clock', f.prior);
  const snapshot = await loadRunningSnapshot(f.runs, 'retry-clock');
  let claims = 0; const realClaim = f.runs.markFailedIfRunning.bind(f.runs);
  f.runs.markFailedIfRunning = async (...args) => { claims++; return realClaim(...args); };
  f.runs.findRunningByOrigin = async () => [{ flow_id: 'retry-clock' }] as any;
  controller.setFlowRunRepository(f.runs); t.after(() => controller.setFlowRunRepository(null));
  await controller.recoverOrphanedFlows({ actionRegistry: {}, sessionKey: 'retry-clock', skillRoot: '/tmp', chatInject: async () => {},
    boundTaskFlow: { get: async () => ({ flowId: 'retry-clock', status: 'running', retry_epoch: 1, revision: 1, stateJson: JSON.stringify(snapshot?.state) }),
      finish: async () => ({ applied: true }), fail: async () => ({ applied: true }) },
  } as any, 'bot', 'engine');
  assert.equal(claims, 0); assert.equal((await f.runs.findByFlowId('retry-clock'))?.status, 'succeeded');
});
test('real handleRetry sends its new clock only through atomic retry-resume before native execution', async t => {
  const f = await fixture(t); const calls: string[] = [];
  const workflowWithNode = { ...workflow, nodes: [{ id: 'task', title: 'Task', phase: 'P2', executor: { type: 'shell', command: 'unused' } }] };
  const prior = { ...f.prior, workflowSnapshot: workflowWithNode, nodeStates: { task: { status: 'failed', error: 'old failure' } } };
  await f.db.exec('UPDATE flow_runs SET state_json = ?, params_json = ?', [JSON.stringify(prior), JSON.stringify({ _clawmind_state: JSON.stringify(prior) })]);
  f.runs.resetStartedAt = async () => { throw new Error('deprecated started-at must not be called'); };
  const admit = f.runs.resumeForRetry.bind(f.runs);
  f.runs.resumeForRetry = async (...args) => { calls.push('atomic-retry'); assert.ok(JSON.parse(args[2]!).retryStartedAtMs > 0); return admit(...args); };
  const app = structuredClone(defaults); app.execution.asyncRun = false;
  setCachedConfig({ database: { type: 'sqlite', sqlitePath: ':memory:' }, app });
  controller.setFlowRunRepository(f.runs); t.after(() => controller.setFlowRunRepository(null));
  await assert.rejects(controller.handleRetry({ sessionKey: 'retry-clock', skillRoot: '/tmp', chatInject: async () => {},
    boundTaskFlow: { get: async () => ({ flowId: 'retry-clock', retry_epoch: 0, revision: 1, status: 'failed', stateJson: JSON.stringify(prior) }),
      resume: async (params: Record<string, unknown>) => {
        calls.push('native-resume');
        const row = (await f.runs.findByFlowId('retry-clock'))!; const next = JSON.parse(String(params.stateJson));
        assert.equal(row.retry_epoch, 1); assert.equal(row.started_at, f.sharedStart);
        assert.equal(next.retryStartedAtMs, JSON.parse(row.state_json!).retryStartedAtMs);
        assert.deepEqual(JSON.parse(JSON.parse(row.params_json!)._clawmind_state), JSON.parse(row.state_json!));
        assert.equal(await sweep(f.runs), 0);
        throw new Error('stop test after verified native resume boundary');
      } },
  } as any, { flowId: 'retry-clock', nodeId: 'task' }), /stop test after verified native resume boundary/);
  assert.deepEqual(calls, ['atomic-retry', 'native-resume']);
});
test('corrupt or conflicting clock evidence in an original TaskFlow/embedded snapshot cannot be hidden', async t => {
  const f = await fixture(t); await admitManualRetry(f.runs, 'retry-clock', f.prior);
  const current = JSON.parse((await f.runs.findByFlowId('retry-clock'))!.state_json!);
  await assert.rejects(loadRunningSnapshot(f.runs, 'retry-clock', async () => ({ ...current, retryStartedAtMs: 'bad' })), /clock anchor/);
  await f.db.exec('UPDATE flow_runs SET params_json = ?', [JSON.stringify({ _clawmind_state: { ...current, retryStartedAtMs: current.retryStartedAtMs - 1 } })]);
  await assert.rejects(loadRunningSnapshot(f.runs, 'retry-clock'), /anchor mismatch/);
  assert.equal(await sweep(f.runs), 0); assert.equal((await f.runs.findByFlowId('retry-clock'))?.status, 'running');
});
