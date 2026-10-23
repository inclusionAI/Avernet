import { workflowConfigurationDigest } from '@avernet/clawweb-shared/server/services/workflow-snapshot';
import { afterEach, describe, expect, it, vi } from 'vitest';
import Database from 'better-sqlite3';
import express from 'express';
import { SqliteDatabase, runMigrations } from '@avernet/clawweb-shared/server/db';
import { createWorkflowRepairRuntime } from '../repair-workbench-runtime.js';
import { RepairReleaseRepository } from '@avernet/workflow/server/repositories/repair-release-repository';
import { WorkflowAutomationRepository } from '@avernet/workflow/server/repositories/workflow-automation-repository';
import { AppConfigRepository } from '@avernet/workflow/server/repositories/app-config-repository';
import { createAutoAnalysisSettings } from '@avernet/workflow/server/services/task-guard/auto-analysis-settings';
import type { RepairDeployment } from '@avernet/workflow/server/contracts/repair-release';
import type { RepairDispatchRequest } from '@avernet/workflow/server/contracts/repair-workbench';

const cleanup: Array<() => Promise<unknown>> = [];
afterEach(async () => { for (const close of cleanup.splice(0).reverse()) await close(); });
async function fixture(dispatch?: (input: RepairDispatchRequest) => Promise<{ jobId: string }>, bound = true, environment?: string) {
  const raw = new Database(':memory:');
  const db = new SqliteDatabase(raw);
  cleanup.push(() => db.close());
  await runMigrations(db, 'sqlite');
  await db.exec("ALTER TABLE workflow_healing_suggestions ADD COLUMN proposal_json TEXT");
  // Existing analysis tables are provisioned separately from Shared migrations,
  // matching the legacy evolution HTTP fixtures; this is not a new migration.
  await db.exec(`CREATE TABLE workflow_evolution_analysis_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_id TEXT, status TEXT, scope_type TEXT,
    analysis_id TEXT, flow_id TEXT, scope_json TEXT, result_json TEXT,
    requested_at_ms INTEGER, completed_at_ms INTEGER, state_version INTEGER NOT NULL DEFAULT 0
  )`);
  await db.exec("INSERT INTO workflow_specs (workflow_id, spec_json) VALUES ('wf', '{}')");
  const spec = { id: 'wf', nodes: [] }, configDigest = workflowConfigurationDigest(spec);
  if (bound) await db.exec("INSERT INTO workflow_deploy_history (pack_id,workflow_id,deploy_number,version,tag_name,spec_json,is_active,action) VALUES ('pack','wf',1,1,'deploy/wf/#1',?,1,'deploy')", [JSON.stringify(spec)]);
  for (let index = 0; index < 39; index++) {
    if (bound) await db.exec("INSERT INTO flow_runs (flow_id,workflow_id,status,started_at,workflow_version,workflow_deploy_number) VALUES (?,'wf','completed',1,1,1)", [`run-${index}`]);
    await db.exec('INSERT INTO workflow_healing_suggestions (workflow_id,failure_signature,fix_spec,status,proposal_json,impact_run_ids) VALUES (?,?,?,?,?,?)',
      ['wf', `timeout-${index}`, `修复 ${index}`, index === 0 ? 'applied_unverified' : 'pending', bound ? JSON.stringify({schemaVersion:'workflow-patch/v1',workflowId:'wf',baseSpecDigest:configDigest,summary:`修复 ${index}`,operations:[{op:'replace',nodeId:`fetch-${index}`,path:'/executor/timeoutMs',value:90}]}) : null, bound ? JSON.stringify([`run-${index}`]) : null]);
  }
  const runtime = createWorkflowRepairRuntime(db, { environment, principal: async () => ({ actorId: 'editor', isAdmin: true }),
    ...(dispatch ? { execution: {
      dispatch,
      async resolveBaseline() { return { workflowId: 'wf', packId: 'pack', repoId: 'managed', specPath: 'workflows/wf.yaml',
        packCommit: 'a'.repeat(40), packDigest: 'b'.repeat(40), specDigest: 'c'.repeat(64), proposalSpecDigest: configDigest, releaseRevision: 1, activeDeployNumber: 1 }; },
      async status() { return { status: 'running' as const, rawStatus: 'running', errorMessage: null }; },
      async readResult() { return { status: 'failed' as const, error: { code: 'not-ready' } }; },
    } } : {}),
  });
  const app = express(); app.use(express.json()); app.use('/api/workflow-repairs', runtime.router);
  const server = await new Promise<ReturnType<typeof app.listen>>(resolve => { const value = app.listen(0, '127.0.0.1', () => resolve(value)); });
  cleanup.push(() => new Promise<void>(resolve => server.close(() => resolve())));
  const base = `http://127.0.0.1:${(server.address() as { port: number }).port}/api/workflow-repairs`;
  return { db, runtime, base };
}

it('serves suggestions and reconciles tasks without starting an AI aggregation worker', async () => {
  const { db, runtime } = await fixture(async () => ({ jobId: 'controlled-job' }), true, 'prod');
  const query = vi.spyOn(db, 'query');
  try {
    const page = await runtime.service.candidates('wf', { pageSize: 100 });
    expect(page.items).toHaveLength(39);
    await runtime.monitor.reconcile();
    expect(query.mock.calls.some(([sql]) => sql.includes("record_kind = 'proposal_scan'"))).toBe(false);
    expect(await db.query("SELECT * FROM workflow_repair_release_records WHERE record_kind IN ('proposal_scan','proposal_group')")).toHaveLength(0);
  } finally { query.mockRestore(); }
});

it('drains an unrelated manual completion even if background result reconciliation throws', async () => {
  const { db } = await fixture();
  const notify = vi.fn(async () => ({ status: 'sent' as const, receipt: 'accepted-by-provider' }));
  const runtime = createWorkflowRepairRuntime(db, { environment: 'prod', principal: async () => ({ actorId: 'editor', isAdmin: true }),
    notifications: { settings: createAutoAnalysisSettings({ repo: new AppConfigRepository(db), environmentDefault: '' }),
      ports: { owner: async () => 'editor', canEdit: async () => true, notify } } });
  const repo = new WorkflowAutomationRepository(db);
  const notice = await repo.ensure('wf', 'auto_notice', 'manual-no-change', { kind: 'completion', taskId: 'finished-task', revision: 2,
    title: '优化任务已完成，无需修改', text: '已完成', path: '/task/finished-task' }, 0);
  const broken = vi.spyOn(runtime.service, 'reconcile').mockRejectedValue(new Error('background baseline unavailable'));
  try {
    await expect(runtime.monitor.reconcile()).rejects.toThrow('background baseline unavailable');
    expect((await repo.get(notice.id))?.status).toBe('done');
    expect(notify).toHaveBeenCalledTimes(1);
    expect(notify).toHaveBeenCalledWith(expect.objectContaining({ title: '优化任务已完成，无需修改', recipientId: 'editor' }));
  } finally { broken.mockRestore(); }
});
it('limits the fast result poll to unfinished generations and notifications, not planning or historical Git scans', async () => {
  const { db } = await fixture();
  const notify = vi.fn(async () => ({ status: 'sent' as const }));
  const runtime = createWorkflowRepairRuntime(db, { principal: async () => ({ actorId: 'editor', isAdmin: true }),
    automation: { settings: createAutoAnalysisSettings({ repo: new AppConfigRepository(db), environmentDefault: '' }),
      ports: { owner: async () => 'editor', canEdit: async () => true, baseline: async () => { throw new Error('must not fetch'); },
        analysis: { lookup: async () => ({ status: 'absent' }), start: async () => ({ status: 'running' }) }, notify } } });
  const tick = vi.spyOn(runtime.automation!, 'tick').mockResolvedValue(undefined);
  const query = vi.spyOn(db, 'query');
  try {
    await runtime.monitor.reconcile();
    expect(tick).not.toHaveBeenCalled();
    expect(query.mock.calls.some(([sql]) => sql.includes("record_kind = 'deployment'"))).toBe(false);
    expect(query.mock.calls.some(([sql]) => sql.includes('workflow_healing_outcomes'))).toBe(false);
  } finally { tick.mockRestore(); query.mockRestore(); }
});

describe('real repair Host composition', () => {
  it('queues manual AIS completion through the real Host even without automatic-analysis configuration', async () => {
    const { base, db, runtime } = await fixture(async () => ({ jobId: 'controlled-ais' }));
    const candidates = await (await fetch(`${base}/candidates?workflowId=wf&pageSize=100`)).json();
    const itemId = candidates.items.find((item: { state: string }) => item.state === 'pending').itemId;
    const created = await fetch(base, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ workflowId: 'wf', itemIds: [itemId], inputDigest: candidates.inputDigest, instructions: '', requestId: 'manual-completion-host' }) });
    expect(created.status).toBe(202);
    const task = await created.json();
    await runtime.service.report({ workflowId: 'wf', taskId: task.taskId, revision: 1, expectedStateVersion: 0, status: 'failed', error: { code: 'AIS_FAILED' } });
    const notices = await db.query<{ body_json: string }>("SELECT body_json FROM workflow_repair_release_records WHERE record_kind = 'auto_notice'");
    expect(notices).toHaveLength(1);
    expect(JSON.parse(notices[0].body_json)).toMatchObject({ status: 'pending', data: { taskId: task.taskId, revision: 1, title: '优化任务生成失败' } });
    expect((await (await fetch(`${base}/${task.taskId}`)).json()).latestAttempt.phase).toBe('failed');
  });
  it('reuses read projections across pages and invalidates them when sources change', async () => {
    const { db, runtime } = await fixture();
    const query = vi.spyOn(db, 'query');
    const sourcesRead = () => query.mock.calls.filter(([sql]) => sql.includes('SELECT * FROM workflow_healing_suggestions')).length;
    const first = await runtime.service.candidates('wf', { state: 'all', page: 1, pageSize: 20 });
    const reads = sourcesRead();
    expect(reads).toBeGreaterThan(0);
    const second = await runtime.service.candidates('wf', { state: 'all', page: 2, pageSize: 20 });
    expect(second.page.total).toBe(39);
    expect(sourcesRead()).toBe(reads);
    await runtime.service.item('wf', first.items[0].itemId);
    expect(sourcesRead()).toBe(reads);
    await db.exec("INSERT INTO workflow_healing_suggestions (workflow_id, failure_signature, fix_spec, status) VALUES ('wf', 'new', '新建议', 'pending')");
    const updated = await runtime.service.candidates('wf', { state: 'all', page: 1, pageSize: 100, versionScope: 'all' });
    expect(updated.page.total).toBe(40);
    expect(updated.inputDigest).not.toBe(first.inputDigest);
    expect(sourcesRead()).toBeGreaterThan(reads);
  });
  it('reads real legacy repositories through HTTP without AIS and preserves all 39 items', async () => {
    const { base, runtime } = await fixture(undefined, false);
    expect((await runtime.service.candidates('wf')).items).toHaveLength(0);
    expect((await runtime.service.candidates('wf', { state: 'all', page: 1, pageSize: 100, versionScope: 'historical' })).items).toHaveLength(39);
    const response = await fetch(`${base}/candidates?workflowId=wf&versionScope=historical`);
    const body = await response.json();
    expect(response.status).toBe(200);
    expect(body.items).toHaveLength(20);
    expect(body.page).toMatchObject({ page: 1, pageSize: 20, total: 39, totalPages: 2 });
    expect(body.counts).toMatchObject({ pending: 38, awaiting_verification: 1, all: 39 });
    expect(body).toMatchObject({ canEdit: true, capabilities: { generation: false, publication: false } });
  });
  it('bounds inactive suggestion history while retaining old active lifecycle rows', async () => {
    const { db, runtime } = await fixture();
    await db.exec("UPDATE workflow_healing_suggestions SET gmt_modified = 0 WHERE failure_signature IN ('timeout-0', 'timeout-1')");
    const result = await runtime.service.candidates('wf', { state: 'all', page: 1, pageSize: 100 });
    expect(result.items.some(item => item.instruction === '修复 0')).toBe(true);
    expect(result.items.some(item => item.instruction === '修复 1')).toBe(false);
  });
  it('connects selection, frozen task dispatch, task reading and cancellation through the real Host', async () => {
    const calls: RepairDispatchRequest[] = [];
    const { base, db } = await fixture(async input => { calls.push(input); return { jobId: 'job-host-1' }; });
    const candidates = await (await fetch(`${base}/candidates?workflowId=wf&pageSize=100`)).json();
    const ids = candidates.items.filter((item: { state: string }) => item.state === 'pending').map((item: { itemId: string }) => item.itemId);
    const response = await fetch(base, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ workflowId: 'wf', itemIds: ids, inputDigest: candidates.inputDigest, instructions: '一次修复', requestId: 'browser-selection-1' }) });
    expect(response.status).toBe(202);
    const task = await response.json();
    expect(calls).toHaveLength(1); expect(calls[0].input.items).toHaveLength(38);
    expect(calls[0].input.items[0].context).toHaveProperty('suggestions');
    const detail = await (await fetch(`${base}/${task.taskId}`)).json();
    expect(detail).toMatchObject({ latestAttempt: { phase: 'drafting' }, execution: { status: 'dispatched' } });
    expect((await db.query('SELECT task_type FROM ce_tasks'))[0]).toMatchObject({ task_type: 'workflow_repair' });
    const cancelled = await fetch(`${base}/${task.taskId}/cancel`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ expectedRevision: 1 }) });
    expect(cancelled.status).toBe(200);
    const after = await (await fetch(`${base}/candidates?workflowId=wf&pageSize=100`)).json();
    expect(after.counts.pending).toBe(38);
    expect(after.tasks[0].phase).toBe('cancelled');
  });
});


it('does not poll pending or settled publication history in the fast result consumer', async () => {
  const { db, runtime } = await fixture();
  const records = new RepairReleaseRepository(db);
  const make = (id: string, status: RepairDeployment['status']) => ({ id, workflowId: 'wf', taskId: 'task', revision: 1,
    stateVersion: 0, createdAtMs: 1, actorId: 'editor', status } as RepairDeployment);
  for (let i = 0; i < 250; i++) {
    const id = `settled-${i}`;
    await records.insert('deployment', { requestId: id }, make(id, i % 2 ? 'succeeded' : 'failed'));
  }
  const statuses = ['dispatching', 'running', 'uncertain'] as const;
  for (let i = 0; i < 101; i++) {
    const id = `pending-${String(i).padStart(3, '0')}`;
    await records.insert('deployment', { requestId: id }, make(id, statuses[i % 3]));
  }
  const reconcile = vi.spyOn(runtime.release, 'reconcile').mockImplementation(async (_actor, _task, id) => make(id, 'running'));
  const query = vi.spyOn(db, 'query');
  await runtime.monitor.reconcile();
  expect(reconcile).not.toHaveBeenCalled();
  const pages = await Promise.all(query.mock.calls.flatMap(([sql], i) =>
    sql.includes('SELECT record_id, body_json FROM workflow_repair_release_records') ? [query.mock.results[i].value] : []));
  expect(pages).toEqual([]);
  reconcile.mockClear();
  await runtime.monitor.reconcile();
  expect(reconcile).not.toHaveBeenCalled();
  expect(await db.query("SELECT record_id FROM workflow_repair_release_records WHERE record_kind = 'deployment'")).toHaveLength(351);
});
