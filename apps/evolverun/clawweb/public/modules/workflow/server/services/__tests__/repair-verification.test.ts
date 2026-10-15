// @vitest-environment node
import { afterEach, expect, it } from 'vitest';
import { repairFixture } from './repair-batch-fixtures.js';
import { repairReleaseMigrations } from '@avernet/clawweb-shared/server/schema-repair-release';
import { createRepairVerificationService } from '../repair-verification-service.js';
import { RepairReleaseRepository } from '../../repositories/repair-release-repository.js';
import type { RepairDeployment } from '../../contracts/repair-release.js';

const fixtures: Awaited<ReturnType<typeof repairFixture>>[] = [];
afterEach(async () => { for (const fixture of fixtures.splice(0)) await fixture.db.close(); });
async function fixture() {
  const f = await repairFixture(); fixtures.push(f);
  for (const m of repairReleaseMigrations) for (const sql of m.sql) await f.db.exec(f.db.dialect.renderDdl(sql));
  await f.db.exec('CREATE TABLE flow_runs (flow_id TEXT PRIMARY KEY, workflow_id TEXT, workflow_deploy_number INTEGER, status TEXT, started_at INTEGER, completed_at INTEGER, total_duration_ms INTEGER, total_token_usage INTEGER)');
  await f.db.exec('CREATE TABLE workflow_healing_diagnoses (flow_id TEXT, workflow_id TEXT, failure_signature TEXT, failure_mode TEXT)');
  await f.db.exec('CREATE TABLE node_executions (flow_id TEXT, node_id TEXT, status TEXT, started_at INTEGER)');
  const d = { id: 'deployment', workflowId: 'wf-1', taskId: 'task', revision: 1, stateVersion: 1, requestId: 'publish', createdAtMs: Date.now(),
    baselineDeployNumber: 7, candidateCommit: 'a'.repeat(40), status: 'succeeded', receipt: { deployNumber: 8 } } as RepairDeployment;
  await new RepairReleaseRepository(f.db).insert('deployment', { requestId: 'publish' }, d);
  await f.db.exec(`INSERT INTO workflow_repair_items (item_id, workflow_id, episode_key, group_key, proposal_key, content_revision, content_json,
    source_refs_json, state, state_version, active_task_id, active_revision, updated_at_ms, identity_digest)
    VALUES ('item', 'wf-1', 'e', 'g', 'p', 1, '{}', '[]', 'awaiting_verification', 3, 'task', 1, 0, 'identity')`);
  for (const [id, number, status, completed] of [['old', 7, 'succeeded', 2], ['unknown', null, 'succeeded', 2], ['new', 8, 'succeeded', 2], ['running', 8, 'running', null]] as const) {
    await f.db.exec('INSERT INTO flow_runs VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)', [id, 'wf-1', number, status, 1, completed]);
  }
  return { ...f, service: createRepairVerificationService(f.db) };
}
it('uses exact deployment identity and never closes an item just because a run succeeded', async () => {
  const f = await fixture(); const evidence = await f.service.evidence('task', 'deployment');
  expect(evidence.samples.map(row => row.flowId).sort()).toEqual(['new', 'running']);
  expect(evidence.baseline.total).toBe(1); expect(evidence.status).toBe('ready');
  expect(evidence.excludedUnknownVersions).toBe(1);
  expect(evidence.metrics.window).toEqual({ from: 1, to: 1 });
  expect(evidence.metrics.averageDurationMs).toBeNull();
  expect(evidence.metrics.averageTokens).toBeNull();
  expect((await f.db.query<{ state: string }>('SELECT state FROM workflow_repair_items'))[0].state).toBe('awaiting_verification');
});
it('rejects old, unknown and unfinished runs as effect evidence; records attributable idempotent conclusions', async () => {
  const f = await fixture(); const request = { requestId: 'result', itemId: 'item', expectedStateVersion: 3,
    result: 'verified' as const, reason: '核对原错误相关节点，没有复现，并核对业务结果', flowIds: ['old'] };
  for (const id of ['old', 'unknown', 'running']) await expect(f.service.conclude('reviewer', 'task', 'deployment', { ...request, flowIds: [id] })).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
  const value = await f.service.conclude('reviewer', 'task', 'deployment', { ...request, flowIds: ['new'] });
  expect(value).toMatchObject({ deployNumber: 8, actorId: 'reviewer', flowIds: ['new'] });
  expect(await f.service.conclude('reviewer', 'task', 'deployment', { ...request, flowIds: ['new'] })).toEqual(value);
  expect((await f.db.query<{ state: string }>('SELECT state FROM workflow_repair_items'))[0].state).toBe('verified');
});
it('retains failed-effect history and reopens an item explicitly for a fresh-baseline task', async () => {
  const f = await fixture();
  await f.service.conclude('reviewer', 'task', 'deployment', { requestId: 'ineffective', itemId: 'item', expectedStateVersion: 3,
    result: 'ineffective', reason: '原问题在新版本仍然复现', flowIds: ['new'] });
  const request = { requestId: 'reopen', itemId: 'item', expectedStateVersion: 4 };
  const reopened = await f.service.reopen('reviewer', 'task', 'deployment', request);
  expect(await f.service.reopen('reviewer', 'task', 'deployment', request)).toEqual(reopened);
  expect((await f.db.query('SELECT state, active_task_id FROM workflow_repair_items'))[0]).toMatchObject({ state: 'pending', active_task_id: null });
  expect(await f.service.conclusions('task')).toHaveLength(1);
});
