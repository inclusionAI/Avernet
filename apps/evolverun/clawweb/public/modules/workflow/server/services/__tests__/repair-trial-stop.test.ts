// @vitest-environment node
import { expect, it } from 'vitest';
import { repairReleaseMigrations } from '@avernet/clawweb-shared/server/schema-repair-release';
import { repairFixture } from './repair-batch-fixtures.js';
import { createRepairWorkbenchService } from '../repair-batch-service.js';
import { createRepairTrialService } from '../repair-trial-service.js';
import { registerRunRequest } from '../run-request-registration.js';
import { digestRepairJson } from '../../contracts/repair-batch.js';
import express from 'express';
import { createRepairBatchesRouter } from '../../routes/repair-batches.js';

async function fixture() {
  const f = await repairFixture();
  f.raw.exec('CREATE TABLE flow_runs (flow_id TEXT PRIMARY KEY, workflow_id TEXT, origin_bot_id TEXT, status TEXT, result_json TEXT, total_duration_ms INTEGER)');
  for (const m of repairReleaseMigrations) for (const sql of m.sql) await f.db.exec(f.db.dialect.renderDdl(sql));
  const workbench = createRepairWorkbenchService(f.db, f.sourcePort, f.executionPort);
  const inbox = await workbench.candidates('wf-1');
  const rev = await workbench.create('actor', { workflowId: 'wf-1', itemIds: ['item-0'], inputDigest: inbox.inputDigest, requestId: 'gen', instructions: '' });
  const draft = { candidateCommit: 'd'.repeat(40), packDigest: 'e'.repeat(40), parentCandidateCommit: 'b'.repeat(40), itemResults: [{ itemId: 'item-0', status: 'changed', reason: 'fixed' }] };
  await workbench.report({ workflowId: 'wf-1', taskId: rev.taskId, revision: 1, expectedStateVersion: 0, status: 'succeeded', draft,
    checks: { schemaVersion: 'workflow-repair-checks/v1', candidateCommit: draft.candidateCommit, packDigest: draft.packDigest, candidateDigest: digestRepairJson(draft), static: { status: 'passed' }, mock: { status: 'not_covered' }, coverage: [{ itemId: 'item-0', mode: 'not_covered' }], uncoveredChecks: [] } });
  let dispatches = 0;
  const service = createRepairTrialService({ db: f.db, workbench, port: {
    targets: async () => [],
    inspect: async () => ({ packId: 'pack', candidateRef: `refs/heads/snapshots/${draft.candidateCommit}`, inputSchema: null }),
    prepare: async () => ({ id: 'bot', botId: 'bot', ownerId: 'owner', environment: 'prod', label: 'Bot', executionMode: 'pinned', capabilityId: 'capability' }),
    save: async () => {}, dispatch: async () => { dispatches++; return { status: 'accepted' }; },
  } });
  const request = { revision: 1, expectedStateVersion: 1, requestId: 'first', targetId: 'bot', input: { topic: 'redis' }, inputSchemaDigest: digestRepairJson(null) };
  const trial = await service.start('actor', rev.taskId, request);
  const body = { flow_id: 'native-flow', workflow_id: 'wf-1', workflow_version: -1, workflow_deploy_number: null,
    origin_bot_id: 'bot:owner', run_request_id: trial.id, run_request_bot_id: 'bot', run_request_owner_id: 'owner',
    candidate_commit: trial.candidateCommit, pack_digest: trial.packDigest, input_digest: trial.inputDigest };
  const insert = async (tx: typeof f.db) => { await tx.exec("INSERT INTO flow_runs VALUES ('native-flow', 'wf-1', 'bot:owner', 'running', NULL, NULL)"); };
  return { ...f, service, workbench, trial, request, body, insert, dispatches: () => dispatches };
}

it('ends an old unregistered request, retains history and parameters, and allows an explicit new trial', async () => {
  const f = await fixture();
  try {
    const old = { ...JSON.parse((f.raw.prepare('SELECT body_json FROM workflow_repair_release_records WHERE record_id = ?').get(f.trial.id) as { body_json: string }).body_json), updatedAtMs: Date.now() - 180_000 };
    f.raw.prepare('UPDATE workflow_repair_release_records SET body_json = ? WHERE record_id = ?').run(JSON.stringify(old), f.trial.id);
    expect((await f.service.list(f.trial.taskId))[0]).toMatchObject({ status: 'unknown', flowId: null });
    await expect(f.service.start('actor', f.trial.taskId, { ...f.request, requestId: 'second' })).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
    const stopped = await f.service.stop(f.trial.taskId, f.trial.id);
    expect(stopped).toMatchObject({ id: f.trial.id, status: 'failed', flowId: null, input: { topic: 'redis' } });
    expect(f.dispatches()).toBe(1);
    expect(await f.service.stop(f.trial.taskId, f.trial.id)).toEqual(stopped);
    expect(await f.service.start('actor', f.trial.taskId, f.request)).toEqual(stopped);
    expect((await f.service.list(f.trial.taskId))[0]).toMatchObject({ status: 'failed', error: expect.stringContaining('已结束') });
    // Delayed old commands cannot insert a run after the user closes their request.
    await expect(registerRunRequest(f.db, f.body, f.insert)).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
    expect(f.raw.prepare('SELECT COUNT(*) AS count FROM flow_runs').get()).toEqual({ count: 0 });
    const next = await f.service.start('actor', f.trial.taskId, { ...f.request, requestId: 'second' });
    expect(next.id).not.toBe(f.trial.id);
    expect(next.input).toEqual({ topic: 'redis' });
    expect(f.dispatches()).toBe(2);
  } finally { await f.db.close(); }
});

it('does not claim to close an unfenced legacy saved-mode request', async () => {
  const f = await fixture();
  try {
    const row = f.raw.prepare('SELECT body_json FROM workflow_repair_release_records WHERE record_id = ?').get(f.trial.id) as { body_json: string };
    const legacy = { ...JSON.parse(row.body_json), mode: 'saved' };
    f.raw.prepare('UPDATE workflow_repair_release_records SET body_json = ? WHERE record_id = ?').run(JSON.stringify(legacy), f.trial.id);
    await expect(f.service.stop(f.trial.taskId, f.trial.id)).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
    expect((await f.service.list(f.trial.taskId))[0]).toMatchObject({ mode: 'saved', status: 'awaiting_run' });
  } finally { await f.db.close(); }
});

it('requires workflow edit access to end the matching task request through HTTP', async () => {
  const f = await fixture();
  const app = express(); app.use(express.json());
  app.use('/api/workflow-repairs', createRepairBatchesRouter({ service: f.workbench, trials: f.service,
    authorize: async req => ({ actorId: 'actor', canEdit: req.headers['x-test-edit'] === 'yes' }) }));
  const server = app.listen(0, '127.0.0.1'); await new Promise<void>(resolve => server.once('listening', resolve));
  try {
    const url = `http://127.0.0.1:${(server.address() as { port: number }).port}/api/workflow-repairs/${f.trial.taskId}/trials/${f.trial.id}/stop`;
    const post = (edit: string) => fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Test-Edit': edit }, body: '{}' });
    expect((await post('no')).status).toBe(403);
    expect((await f.service.list(f.trial.taskId))[0].status).toBe('awaiting_run');
    const response = await post('yes');
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({ id: f.trial.id, status: 'failed', flowId: null });
    expect(f.dispatches()).toBe(1);
  } finally {
    await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
    await f.db.close();
  }
});

it('cannot end a request whose native run registered first or a request from another task', async () => {
  const f = await fixture();
  try {
    await expect(f.service.stop('other-task', f.trial.id)).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
    expect(await registerRunRequest(f.db, f.body, f.insert)).toBe('created');
    await expect(f.service.stop(f.trial.taskId, f.trial.id)).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
    expect((await f.service.list(f.trial.taskId))[0]).toMatchObject({ status: 'running', flowId: 'native-flow' });
    expect(f.dispatches()).toBe(1);
  } finally { await f.db.close(); }
});
