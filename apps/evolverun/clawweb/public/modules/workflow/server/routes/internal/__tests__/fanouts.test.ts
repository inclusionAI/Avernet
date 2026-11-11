// @vitest-environment node
import express from 'express';
import type { Server } from 'node:http';
import { afterEach, beforeEach, expect, it } from 'vitest';
import { FlowRunRepository } from '../../../repositories/flow-run-repository.js';
import { childInput, fixture, group } from '../../../repositories/__tests__/fanout-state-fixture.js';
import { createInternalRunsRouter } from '../runs.js';

let f: ReturnType<typeof fixture>;
let server: Server;
let base: string;
beforeEach(async () => {
  f = fixture();
  const app = express(); app.use(express.json());
  app.use('/api/internal/runs', createInternalRunsRouter(new FlowRunRepository(f.db)));
  app.use('/unavailable', createInternalRunsRouter(null));
  const config = { enabled: false, queueGuard: { enabled: false, maxQueueSize: 1, maxEnqueuePerFlow: 1, activeStatuses: ['waiting'] },
    perWorkflow: { 'child-wf': { maxConcurrent: 1 } } };
  app.use('/limited', createInternalRunsRouter(new FlowRunRepository(f.db), null, config));
  await new Promise<void>(resolve => { server = app.listen(0, '127.0.0.1', resolve); });
  base = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
});
afterEach(async () => { await new Promise<void>(resolve => server.close(() => resolve())); await f.db.close(); });
const groupUrl = '/api/internal/runs/parent/fanouts/fanout';
const childUrl = '/api/internal/runs/parent/fanout-children/child-fanout';
async function request(path: string, body?: unknown) {
  const res = await fetch(`${base}${path}`, body === undefined ? {} : { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  return { status: res.status, body: await res.json() };
}
const save = () => request(groupUrl, { group: group(), expectedRevision: null, parentState: { workflowId: 'parent-wf', nodeStates: {} } });

it('mounts strict group reads and writes with revision conflict status', async () => {
  expect(await request(groupUrl)).toEqual({ status: 200, body: { success: true, data: { group: null, parentStatus: 'running' } } });
  expect(await save()).toMatchObject({ status: 200, body: { success: true, data: { group: { revision: 0 }, parentStatus: 'running' } } });
  expect(await save()).toMatchObject({ status: 200, body: { success: true } });
  const changed = group(); changed.children[0].status = 'running';
  expect(await request(groupUrl, { group: changed, expectedRevision: null })).toMatchObject({ status: 409, body: { success: false } });
  const rows = f.raw.prepare("SELECT message FROM run_logs WHERE flow_id='parent' AND node_id='fanout' ORDER BY id").all() as Array<{ message: string }>;
  expect(rows.map(row => JSON.parse(row.message).recovery)).toEqual(['reloaded_equivalent_state', 'conflict_retained']);
  expect(JSON.parse(rows[1].message)).toMatchObject({ httpStatus: 409, code: 'FANOUT_REVISION_CONFLICT', stage: 'save_group' });
  expect(await request(groupUrl, { group: group() })).toMatchObject({ status: 400, body: { success: false } });
});

it('exposes start-or-get created/run and PUT checkpoint contracts without credentials', async () => {
  await save();
  expect(await request(childUrl)).toMatchObject({ status: 404, body: { success: false } });
  const first = await request(childUrl, childInput());
  expect(first).toMatchObject({ status: 201, body: { success: true, data: { created: true, run: { revision: 1 } } } });
  expect(JSON.stringify(first)).not.toContain('private-value');
  expect(await request(childUrl, childInput())).toMatchObject({ status: 200, body: { data: { created: false } } });
  const stateJson = first.body.data.run.stateJson;
  expect(typeof stateJson).toBe('string');
  expect(JSON.parse(stateJson)._fanoutRevision).toBe(1);
  expect(await request(`${childUrl}/state`, { expectedRevision: 1, stateJson, status: 'succeeded', currentStep: 'done' }))
    .toMatchObject({ status: 200, body: { data: { revision: 2, status: 'succeeded' } } });
  expect(await request(childUrl)).toMatchObject({ status: 200, body: { data: { revision: 2, status: 'succeeded' } } });
  expect(await request(`${childUrl}/state`, { expectedRevision: 1, stateJson, status: 'running' })).toMatchObject({ status: 409 });
});

it('does not turn database write/read errors into empty successful responses or disclose SQL errors', async () => {
  f.raw.exec(`CREATE TRIGGER unavailable BEFORE UPDATE ON flow_runs BEGIN SELECT RAISE(ABORT, 'credentials=secret-sql-detail'); END`);
  const failed = await save();
  expect(failed).toMatchObject({ status: 500, body: { success: false } });
  expect(JSON.stringify(failed)).not.toContain('secret-sql-detail');
  f.raw.exec('DROP TABLE flow_runs');
  expect(await request(groupUrl)).toMatchObject({ status: 500, body: { success: false } });
  expect(await request(childUrl)).toMatchObject({ status: 500, body: { success: false } });
});

it('fails closed without a database and validates bodies before querying children', async () => {
  expect(await request('/unavailable/parent/fanouts/fanout')).toMatchObject({ status: 503 });
  expect(await request('/unavailable/parent/fanout-children/child-fanout', childInput())).toMatchObject({ status: 503 });
  expect(await request(childUrl, {})).toMatchObject({ status: 400 });
  expect(await request(`${childUrl}/state`, { expectedRevision: 0, stateJson: {}, status: 'running' })).toMatchObject({ status: 400 });
});

it('maps legacy mirror CAS conflicts to 409 instead of a false success', async () => {
  await save(); await request(childUrl, childInput());
  expect(await request('/api/internal/runs/child-fanout/state', { state_json: '{}' })).toMatchObject({ status: 409, body: { success: false } });
});

it('accepts the existing API client nodeId and JSON-string state without ambiguity', async () => {
  await save();
  const { parentNodeId, ...input } = childInput();
  const response = await request(childUrl, { ...input, nodeId: parentNodeId, stateJson: JSON.stringify(input.stateJson) });
  expect(response).toMatchObject({ status: 201, body: { data: { created: true } } });
  expect(typeof response.body.data.run.stateJson).toBe('string');
  expect(await request(childUrl, { ...childInput(), nodeId: 'other' })).toMatchObject({ status: 400 });
  expect(await request(childUrl, { ...input, nodeId: parentNodeId, stateJson: JSON.stringify('encoded twice') })).toMatchObject({ status: 400 });
});

it('round trips the internal fanout HTTP contract through SQLite', async () => {
  const g = group();
  expect(await save()).toMatchObject({ status: 200 });
  expect(await request(groupUrl)).toMatchObject({ status: 200, body: { data: { group: g } } });
  const child = { ...g.children[0], childWorkflow: g.childWorkflow };
  const initial = childInput(g).stateJson;
  const created = await request(childUrl, {
    ...childInput(g), nodeId: child.parentNodeId, stateJson: initial,
  });
  expect(created).toMatchObject({ status: 201, body: { data: { created: true, run: { revision: 1 } } } });
  const state = JSON.parse(created.body.data.run.stateJson); state.workflowData = { verified: true };
  const updated = await request(`${childUrl}/state`, { expectedRevision: 1, stateJson: state, status: 'running', currentStep: 'work' });
  expect(updated).toMatchObject({ status: 200, body: { data: { revision: 2, status: 'running' } } });
  expect(await request(childUrl, { ...childInput(g), nodeId: child.parentNodeId, stateJson: initial }))
    .toMatchObject({ status: 200, body: { data: { created: false, run: { revision: 2 } } } });
  expect(await request(childUrl)).toMatchObject({ status: 200, body: { data: { revision: 2 } } });
  expect(await request(`${childUrl}/state`, { expectedRevision: 1, stateJson: state, status: 'succeeded' }))
    .toMatchObject({ status: 409 });
});

it('forwards host admission config and exposes CAPACITY_BUSY without creating the losing child', async () => {
  await save();
  expect(await request('/limited/parent/fanout-children/child-fanout', childInput())).toMatchObject({ status: 201 });
  const next = group('next');
  await request('/api/internal/runs/parent/fanouts/next', { group: next, expectedRevision: null });
  expect(await request('/limited/parent/fanout-children/child-next', childInput(next)))
    .toMatchObject({ status: 409, body: { success: false, error: 'CAPACITY_BUSY', code: 'CAPACITY_BUSY' } });
  expect(await request('/limited/parent/fanout-children/child-next')).toMatchObject({ status: 404 });
  expect(await request('/limited/parent/fanout-children/child-fanout', childInput()))
    .toMatchObject({ status: 200, body: { data: { created: false } } });
});
