import { afterEach, describe, expect, it } from 'vitest';
import Database from 'better-sqlite3';
import express from 'express';
import { SqliteDatabase, runMigrations } from '@avernet/clawweb-shared/server/db';
import { createWorkflowRepairRuntime } from '../repair-workbench-runtime.js';
import type { RepairDispatchRequest } from '@avernet/workflow/server/contracts/repair-workbench';

const cleanup: Array<() => Promise<unknown>> = [];
afterEach(async () => { for (const close of cleanup.splice(0).reverse()) await close(); });
async function fixture(dispatch?: (input: RepairDispatchRequest) => Promise<{ jobId: string }>) {
  const raw = new Database(':memory:');
  const db = new SqliteDatabase(raw);
  cleanup.push(() => db.close());
  await runMigrations(db, 'sqlite');
  // Existing analysis tables are provisioned separately from Shared migrations,
  // matching the legacy evolution HTTP fixtures; this is not a new migration.
  await db.exec(`CREATE TABLE workflow_evolution_analysis_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_id TEXT, status TEXT, scope_type TEXT
  )`);
  await db.exec("INSERT INTO workflow_specs (workflow_id, spec_json) VALUES ('wf', '{}')");
  for (let index = 0; index < 39; index++) await db.exec(
    'INSERT INTO workflow_healing_suggestions (workflow_id, failure_signature, fix_spec, status) VALUES (?, ?, ?, ?)',
    ['wf', `timeout-${index}`, `修复 ${index}`, index === 0 ? 'applied_unverified' : 'pending']);
  const runtime = createWorkflowRepairRuntime(db, { principal: async () => ({ actorId: 'editor', isAdmin: true }),
    ...(dispatch ? { execution: {
      dispatch,
      async resolveBaseline() { return { workflowId: 'wf', packId: 'pack', repoId: 'managed', specPath: 'workflows/wf.yaml',
        packCommit: 'a'.repeat(40), packDigest: 'b'.repeat(40), specDigest: 'c'.repeat(64), releaseRevision: 0, activeDeployNumber: null }; },
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

describe('real repair Host composition', () => {
  it('reads real legacy repositories through HTTP without AIS and preserves all 39 items', async () => {
    const { base, runtime } = await fixture();
    expect((await runtime.service.candidates('wf')).items).toHaveLength(39);
    const response = await fetch(`${base}/candidates?workflowId=wf`);
    const body = await response.json();
    expect(response.status).toBe(200);
    expect(body.items).toHaveLength(39);
    expect(body.items.filter((item: { state: string }) => item.state === 'pending')).toHaveLength(38);
    expect(body.items.find((item: { state: string }) => item.state === 'awaiting_verification')).toMatchObject({ sourceAvailable: false });
    expect(body).toMatchObject({ canEdit: true, capabilities: { generation: false, publication: false } });
  });
  it('connects selection, frozen task dispatch, task reading and cancellation through the real Host', async () => {
    const calls: RepairDispatchRequest[] = [];
    const { base, db } = await fixture(async input => { calls.push(input); return { jobId: 'job-host-1' }; });
    const candidates = await (await fetch(`${base}/candidates?workflowId=wf`)).json();
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
    const after = await (await fetch(`${base}/candidates?workflowId=wf`)).json();
    expect(after.items.filter((item: { state: string }) => item.state === 'pending')).toHaveLength(38);
    expect(after.tasks[0].phase).toBe('cancelled');
  });
});
