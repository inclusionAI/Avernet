// @vitest-environment node
import Database from 'better-sqlite3';
import express from 'express';
import type { Server } from 'node:http';
import { afterEach, beforeEach, expect, it } from 'vitest';
import { SqliteDatabase, runMigrations } from '@avernet/clawweb-shared/server/db';
import { group } from '../../../repositories/__tests__/fanout-state-fixture.js';
import { FlowRunRepository } from '../../../repositories/flow-run-repository.js';
import { createInternalRunsRouter } from '../runs.js';

let db: SqliteDatabase;
let server: Server;
let base: string;

beforeEach(async () => {
  db = new SqliteDatabase(new Database(':memory:'));
  await runMigrations(db, 'sqlite');
  const app = express();
  app.use(express.json());
  app.use('/runs', createInternalRunsRouter(new FlowRunRepository(db)));
  await new Promise<void>(resolve => { server = app.listen(0, '127.0.0.1', resolve); });
  base = `http://127.0.0.1:${(server.address() as { port: number }).port}/runs`;
});

afterEach(async () => {
  if (server) await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  if (db) await db.close();
});

async function request(path: string, method = 'GET', body?: unknown) {
  const response = await fetch(`${base}${path}`, body === undefined ? { method } : {
    method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  return { status: response.status, body: await response.json() };
}

const parent = { flow_id: 'parent', workflow_id: 'parent-wf', status: 'running', started_at: 1,
  state_json: JSON.stringify({ workflowId: 'parent-wf', nodeStates: {} }) };
const child = { flow_id: 'native-child-123', workflow_id: 'child-wf', status: 'running', started_at: 2,
  state_json: JSON.stringify({ workflowId: 'child-wf', nodeStates: {} }) };
const groupPath = '/parent/fanouts/fanout';
const queued = () => { const value = group(); value.children[0].status = 'queued'; return value; };
const claim = (value: ReturnType<typeof group>) => {
  const next = structuredClone(value);
  next.revision = 1;
  next.children[0].status = 'dispatch_pending';
  next.children[0].dispatchClaimToken = 'claim-token';
  next.cleanupPendingChildIds = [next.children[0].childFlowId];
  return next;
};
const save = (value: ReturnType<typeof group>, expectedRevision: number | null) =>
  request(groupPath, 'PUT', { group: value, expectedRevision });

it('round trips a claimed fanout and native child registered through legacy POST /runs', async () => {
  expect(await request('', 'POST', parent)).toMatchObject({ status: 201, body: { success: true, data: { flow_id: 'parent' } } });
  const initial = queued();
  expect(await request(groupPath)).toMatchObject({ status: 200, body: { success: true, data: { group: null, parentStatus: 'running' } } });
  expect(await save(initial, null)).toMatchObject({ status: 200, body: { success: true, data: { group: initial } } });
  const claimed = claim(initial);
  expect(await save(claimed, 0)).toMatchObject({ status: 200, body: { success: true, data: { group: claimed } } });
  expect(await request('', 'POST', child)).toMatchObject({ status: 201, body: { success: true, data: { flow_id: child.flow_id } } });
  const associated = structuredClone(claimed);
  associated.revision = 2;
  associated.children[0].status = 'running';
  associated.children[0].nativeFlowId = child.flow_id;
  expect(await save(associated, 1)).toMatchObject({ status: 200, body: { success: true, data: { group: associated } } });
  expect(await request(groupPath)).toEqual({ status: 200, body: { success: true, data: { group: associated, parentStatus: 'running' } } });
  expect(await request(`/${child.flow_id}`)).toMatchObject({ status: 200, body: { success: true, data: { flow_id: child.flow_id, workflow_id: child.workflow_id } } });
  const rows = await db.query<{ flow_id: string }>('SELECT flow_id FROM flow_runs ORDER BY flow_id');
  expect(rows.map(row => row.flow_id)).toEqual([child.flow_id, parent.flow_id]);
  expect(associated.children[0].nativeFlowId).not.toBe(associated.children[0].childFlowId);
});

it('returns conflicts for stale claims and a stopped parent without changing the durable group', async () => {
  expect((await request('', 'POST', parent)).status).toBe(201);
  const initial = queued();
  expect((await save(initial, null)).status).toBe(200);
  const claimed = claim(initial);
  expect((await save(claimed, 0)).status).toBe(200);
  expect(await save(claim(initial), 0)).toMatchObject({ status: 409, body: { success: false } });
  await db.exec('UPDATE flow_runs SET status = ? WHERE flow_id = ?', ['cancelled', parent.flow_id]);
  const progress = structuredClone(claimed); progress.revision = 2; progress.children[0].status = 'running';
  progress.children[0].nativeFlowId = child.flow_id;
  expect(await save(progress, 1)).toMatchObject({ status: 409, body: { success: false } });
  expect(await request(groupPath)).toEqual({ status: 200, body: { success: true, data: { group: claimed, parentStatus: 'cancelled' } } });
  expect(await request('/missing/fanouts/fanout')).toMatchObject({ status: 404, body: { success: false } });
});

it('reports failed group and legacy child writes and leaves neither write persisted', async () => {
  expect((await request('', 'POST', parent)).status).toBe(201);
  const initial = queued();
  expect((await save(initial, null)).status).toBe(200);
  const claimed = claim(initial);
  expect((await save(claimed, 0)).status).toBe(200);
  await db.exec("CREATE TRIGGER reject_child BEFORE INSERT ON flow_runs WHEN NEW.flow_id = 'native-child-123' BEGIN SELECT RAISE(ABORT, 'write failed'); END");
  expect(await request('', 'POST', child)).toMatchObject({ status: 500, body: { success: false } });
  expect(await db.query('SELECT flow_id FROM flow_runs WHERE flow_id = ?', [child.flow_id])).toEqual([]);
  await db.exec("CREATE TRIGGER reject_group BEFORE UPDATE OF state_json ON flow_runs BEGIN SELECT RAISE(ABORT, 'write failed'); END");
  const progress = structuredClone(claimed); progress.revision = 2; progress.children[0].status = 'running';
  progress.children[0].nativeFlowId = child.flow_id;
  expect(await save(progress, 1)).toMatchObject({ status: 500, body: { success: false } });
  expect(await request(groupPath)).toEqual({ status: 200, body: { success: true, data: { group: claimed, parentStatus: 'running' } } });
});
