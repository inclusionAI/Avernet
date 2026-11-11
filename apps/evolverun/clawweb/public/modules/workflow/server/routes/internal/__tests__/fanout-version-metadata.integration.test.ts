// @vitest-environment node
import Database from 'better-sqlite3';
import express from 'express';
import type { Server } from 'node:http';
import { afterEach, beforeEach, expect, it } from 'vitest';
import { SqliteDatabase, runMigrations } from '@avernet/clawweb-shared/server/db';
import { normalizeWorkflowSpec } from '../../../../shared/workflow.js';
import { group } from '../../../repositories/__tests__/fanout-state-fixture.js';
import { FanoutStateRepository } from '../../../repositories/fanout-state-repository.js';
import { FlowRunRepository } from '../../../repositories/flow-run-repository.js';
import { createInternalRunsRouter } from '../runs.js';

let db: SqliteDatabase;
let repo: FanoutStateRepository;
let server: Server;
let base: string;

beforeEach(async () => {
  db = new SqliteDatabase(new Database(':memory:'));
  await runMigrations(db, 'sqlite');
  repo = new FanoutStateRepository(db);
  const app = express();
  app.use(express.json());
  app.use('/runs', createInternalRunsRouter(new FlowRunRepository(db)));
  await new Promise<void>(resolve => { server = app.listen(0, '127.0.0.1', resolve); });
  base = `http://127.0.0.1:${(server.address() as { port: number }).port}/runs`;
  expect((await request('', 'POST', { flow_id: 'parent', workflow_id: 'parent-wf', status: 'running', started_at: 1 })).status).toBe(201);
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

function snapshot(version: unknown) {
  const spec = group().childWorkflow;
  if (version === undefined) delete spec.version; else spec.version = version;
  return spec;
}

function resolvedGroup(spec: Record<string, unknown>, nodeId = 'fanout') {
  const value = group(nodeId);
  delete value.childPublishedVersion;
  value.children[0].status = 'queued';
  value.childWorkflow = structuredClone(spec);
  value.childResolved = { id: spec.id, spec: structuredClone(spec), digest: 'db:7:12',
    absolutePath: '', source: { kind: 'deploy-history' },
    pack: { id: '', version: '', root: '', digest: '' }, workflowVersion: 7, deployNumber: 12 };
  return value;
}

function claim(value: ReturnType<typeof group>) {
  const next = structuredClone(value);
  next.revision = 1;
  next.children[0].status = 'dispatch_pending';
  next.children[0].dispatchClaimToken = 'metadata-claim';
  next.cleanupPendingChildIds = [next.children[0].childFlowId];
  return next;
}

const cases = [
  { name: "string '1'", input: '1', normalize: false, expected: '1' },
  { name: 'semver', input: '1.2.3', normalize: false, expected: '1.2.3' },
  { name: 'numeric', input: 7, normalize: false, expected: 7 },
  { name: 'omitted', input: undefined, normalize: false, expected: undefined },
  { name: 'null', input: null, normalize: false, expected: null },
  { name: 'production-normalized numeric', input: 7, normalize: true, expected: '7' },
  { name: 'production-normalized default', input: undefined, normalize: true, expected: '1' },
];

it.each(cases)('preserves $name metadata through repository and real HTTP save/claim/read', async testCase => {
  const raw = snapshot(testCase.input);
  const spec = testCase.normalize ? normalizeWorkflowSpec(raw) : raw;
  expect(spec.version).toBe(testCase.expected);
  const original = structuredClone(spec);
  const initial = resolvedGroup(spec);
  expect((await repo.saveGroup('parent', 'fanout', { group: initial, expectedRevision: null })).group).toStrictEqual(initial);
  expect((await repo.getGroup('parent', 'fanout')).group).toStrictEqual(initial);
  const claimed = claim(initial);
  expect((await repo.saveGroup('parent', 'fanout', { group: claimed, expectedRevision: 0 })).group).toStrictEqual(claimed);
  expect((await repo.getGroup('parent', 'fanout')).group).toStrictEqual(claimed);

  const httpInitial = resolvedGroup(spec, 'http-fanout');
  const path = '/parent/fanouts/http-fanout';
  const response = (value: ReturnType<typeof group>) => ({ status: 200,
    body: { success: true, data: { group: value, parentStatus: 'running' } } });
  expect(await request(path, 'PUT', { group: httpInitial, expectedRevision: null })).toStrictEqual(response(httpInitial));
  expect(await request(path)).toStrictEqual(response(httpInitial));
  const httpClaimed = claim(httpInitial);
  expect(await request(path, 'PUT', { group: httpClaimed, expectedRevision: 0 })).toStrictEqual(response(httpClaimed));
  expect(await request(path)).toStrictEqual(response(httpClaimed));
  const [row] = await db.query<{ state_json: string }>('SELECT state_json FROM flow_runs WHERE flow_id = ?', ['parent']);
  const saved = JSON.parse(row.state_json).subworkflowFanouts;
  expect(saved.fanout).toStrictEqual(claimed);
  expect(saved['http-fanout']).toStrictEqual(httpClaimed);
  expect(saved['http-fanout'].childWorkflow).toStrictEqual(original);
  expect(saved['http-fanout'].childResolved.spec).toStrictEqual(original);
  expect(saved['http-fanout']).not.toHaveProperty('childPublishedVersion');
  expect(spec).toStrictEqual(original);
});

it('retains full snapshot consistency, immutable metadata and revision CAS over HTTP', async () => {
  const path = '/parent/fanouts/fanout';
  const initial = resolvedGroup(snapshot('1'));
  expect((await request(path, 'PUT', { group: initial, expectedRevision: null })).status).toBe(200);
  const claimed = claim(initial);
  expect((await request(path, 'PUT', { group: claimed, expectedRevision: 0 })).status).toBe(200);
  expect(await request(path, 'PUT', { group: claimed, expectedRevision: 0 })).toMatchObject({ status: 409,
    body: { message: 'Group revision conflict' } });
  const mismatched = structuredClone(claimed); mismatched.revision = 2;
  mismatched.childResolved.spec.version = 1;
  expect(await request(path, 'PUT', { group: mismatched, expectedRevision: 1 })).toMatchObject({ status: 400,
    body: { message: 'Invalid resolved child snapshot' } });
  for (const version of [1, '2.0.0', null, undefined]) {
    const changed = structuredClone(claimed); changed.revision = 2;
    if (version === undefined) delete changed.childWorkflow.version; else changed.childWorkflow.version = version;
    changed.childResolved.spec = structuredClone(changed.childWorkflow);
    expect(await request(path, 'PUT', { group: changed, expectedRevision: 1 })).toMatchObject({ status: 409,
      body: { message: 'Group launch identity conflict' } });
  }
  expect(await request(path)).toStrictEqual({ status: 200,
    body: { success: true, data: { group: claimed, parentStatus: 'running' } } });
});
