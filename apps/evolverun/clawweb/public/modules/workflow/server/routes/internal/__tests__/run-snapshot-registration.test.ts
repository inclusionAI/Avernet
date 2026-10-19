// @vitest-environment node
import { afterEach, expect, it } from 'vitest';
import Database from 'better-sqlite3';
import express from 'express';
import type { Server } from 'node:http';
import { SqliteDatabase, runMigrations, type IDatabase } from '@avernet/clawweb-shared/server/db';
import { FlowRunRepository } from '../../../repositories/flow-run-repository.js';
import { createInternalRunsRouter } from '../runs.js';

const cleanup: Array<() => Promise<void>> = [];
afterEach(async () => { for (const close of cleanup.splice(0).reverse()) await close(); });
const body = { flow_id: 'ordinary-run', workflow_id: 'wf', status: 'running', started_at: 100,
  workflow_version: 39, workflow_deploy_number: 34, state_json: JSON.stringify({ workflowId: 'wf', nodeStates: {} }) };
const run = { flowId: body.flow_id, workflowId: body.workflow_id, status: body.status, startedAt: body.started_at,
  workflowVersion: body.workflow_version, workflowDeployNumber: body.workflow_deploy_number, stateJson: body.state_json };
const failUpdate = `CREATE TRIGGER reject_snapshot BEFORE UPDATE OF workflow_snapshot_json ON flow_runs
  BEGIN SELECT RAISE(FAIL, 'snapshot association write failed'); END`;
async function fixture() {
  const db = new SqliteDatabase(new Database(':memory:'));
  cleanup.push(() => db.close());
  await runMigrations(db, 'sqlite');
  await db.exec("INSERT INTO workflow_deploy_history (pack_id,workflow_id,version,deploy_number,tag_name,spec_json,is_active,action) VALUES ('pack','wf',39,34,'deploy/wf/#34',?,1,'deploy')", [JSON.stringify({ id: 'wf', nodes: [] })]);
  const app = express(); app.use(express.json()); app.use('/runs', createInternalRunsRouter(new FlowRunRepository(db)));
  const server = await new Promise<Server>(resolve => { const listener = app.listen(0, '127.0.0.1', () => resolve(listener)); });
  cleanup.push(() => new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve())));
  const url = `http://127.0.0.1:${(server.address() as { port: number }).port}/runs`;
  const register = () => fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  return { db, register };
}

it.each(['lookup', 'update'] as const)('rolls back ordinary registration on snapshot %s failure and allows the same flow ID to retry', async failure => {
  const { db, register } = await fixture();
  if (failure === 'lookup') await db.exec('ALTER TABLE workflow_deploy_history RENAME TO unavailable_deploy_history');
  else await db.exec(failUpdate);
  expect((await register()).status).toBe(500);
  expect(await db.query('SELECT flow_id FROM flow_runs WHERE flow_id = ?', [body.flow_id])).toEqual([]);

  if (failure === 'lookup') await db.exec('ALTER TABLE unavailable_deploy_history RENAME TO workflow_deploy_history');
  else await db.exec('DROP TRIGGER reject_snapshot');
  const response = await register();
  expect(response.status).toBe(201);
  const stored = (await response.json()).data;
  expect(stored.state_json).toBe(body.state_json);
  const rows = await db.query<{ workflow_snapshot_json: string }>('SELECT workflow_snapshot_json FROM flow_runs WHERE flow_id = ?', [body.flow_id]);
  expect(rows).toHaveLength(1);
  expect(JSON.parse(rows[0].workflow_snapshot_json)).toMatchObject({ status: 'resolved', snapshot: { workflowVersion: 39, deployNumber: 34 } });
});

it('keeps an old schema without the optional association column compatible', async () => {
  const { db, register } = await fixture();
  await db.exec('ALTER TABLE flow_runs DROP COLUMN workflow_snapshot_json');
  expect((await register()).status).toBe(201);
  expect(await db.query('SELECT workflow_version,workflow_deploy_number FROM flow_runs')).toEqual([{ workflow_version: 39, workflow_deploy_number: 34 }]);
});

it('registers a legitimate run when its snapshot is unavailable, preserving an unresolved association', async () => {
  const { db, register } = await fixture();
  await db.exec('DELETE FROM workflow_deploy_history');
  expect((await register()).status).toBe(201);
  const [row] = await db.query<{ workflow_snapshot_json: string }>('SELECT workflow_snapshot_json FROM flow_runs');
  expect(JSON.parse(row.workflow_snapshot_json)).toEqual({ status: 'unresolved', reason: 'SNAPSHOT_MISSING' });
});

it('preserves the outer registration transaction when purpose metadata and the run are written together', async () => {
  const { db } = await fixture();
  await db.exec('CREATE TABLE registration_marker (id TEXT PRIMARY KEY)');
  const register = (tx: IDatabase) => new FlowRunRepository(tx).insert(run);
  const transaction = () => db.transaction(async tx => {
    await tx.exec("INSERT INTO registration_marker VALUES ('request')");
    if (!await register(tx)) throw new Error('Run persistence failed');
  });
  await db.exec(failUpdate);
  await expect(transaction()).rejects.toThrow('Run persistence failed');
  expect(await db.query('SELECT * FROM registration_marker')).toEqual([]);
  expect(await db.query('SELECT flow_id FROM flow_runs')).toEqual([]);
  await db.exec('DROP TRIGGER reject_snapshot');
  await transaction();
  expect(await db.query('SELECT * FROM registration_marker')).toEqual([{ id: 'request' }]);
  expect(await db.query('SELECT flow_id FROM flow_runs')).toEqual([{ flow_id: body.flow_id }]);
});
