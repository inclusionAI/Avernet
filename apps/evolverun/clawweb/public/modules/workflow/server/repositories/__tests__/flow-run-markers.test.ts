// @vitest-environment node
import Database from 'better-sqlite3';
import express from 'express';
import type { Server } from 'node:http';
import { afterEach, describe, expect, it } from 'vitest';
import { SqliteDatabase, runMigrations } from '@avernet/clawweb-shared/server/db';
import { FlowRunRepository, type FlowRunInsert } from '../flow-run-repository.js';
import { NodeExecutionRepository } from '../node-execution-repository.js';
import { RunReadRepository } from '../run-read-repository.js';
import { RunSubworkflowsRepository } from '../run-subworkflows-repository.js';
import { RunSubworkflowsService } from '../../services/run-subworkflows-service.js';
import { normalizeFlowRunMarkers } from '../../services/flow-run-markers.js';
import { createInternalRunsRouter } from '../../routes/internal/runs.js';
import { createRunReadRouter } from '../../services/run-read-http.js';
import { parentRelationFixture } from '../../routes/internal/__tests__/parent-relation-fixture.js';

const cleanups: Array<() => Promise<void>> = [];
afterEach(async () => { for (const close of cleanups.splice(0).reverse()) await close(); });
const baseRun: FlowRunInsert = { flowId: 'one', workflowId: 'workflow', status: 'running', startedAt: 1, originBotId: 'bot:owner' };
const legacyState = '{ "workflowId": "workflow", "nodeStates": { }, "escaped": "\\u4e2d" }';
const markerCases = [
  { label: 'omitted', input: {}, expected: { env: 'prod', tag: '' } },
  { label: 'explicit null', input: { env: null, tag: null }, expected: { env: null, tag: null } },
  { label: 'env null only', input: { env: null }, expected: { env: null, tag: '' } },
  { label: 'tag null only', input: { tag: null }, expected: { env: 'prod', tag: null } },
  { label: 'custom', input: { env: ' pre ', tag: '回归 / release-1' }, expected: { env: ' pre ', tag: '回归 / release-1' } },
  { label: 'empty tag', input: { env: 'test', tag: '' }, expected: { env: 'test', tag: '' } },
  { label: 'max chars', input: { env: '测'.repeat(32), tag: '试'.repeat(255) }, expected: { env: '测'.repeat(32), tag: '试'.repeat(255) } },
  { label: 'unicode code points', input: { env: '🚀'.repeat(32), tag: '🚀'.repeat(255) }, expected: { env: '🚀'.repeat(32), tag: '🚀'.repeat(255) } },
];
const invalidMarkers = [
  { env: '' }, { env: ' \n\t' }, { env: 'x'.repeat(33) }, { env: false }, { env: 1 }, { env: {} }, { env: [] },
  { tag: 'x'.repeat(256) }, { tag: false }, { tag: 1 }, { tag: {} }, { tag: [] },
];

async function fixture(authorizedReads = true) {
  const raw = new Database(':memory:'); const db = new SqliteDatabase(raw);
  await runMigrations(db, 'sqlite');
  const repo = new FlowRunRepository(db);
  const permissions = {
    resolveRunViewScope: async () => 'all' as const,
    listRunViewScopes: async () => new Map([['workflow', 'all' as const]]),
  };
  const reader = new RunSubworkflowsService(new RunSubworkflowsRepository(db), permissions);
  const app = express(); app.use(express.json());
  app.use((req, _res, next) => { req.isAdmin = true; next(); });
  app.use('/internal/runs', createInternalRunsRouter(repo, null, undefined, {}, {
    authenticate: async () => ({ botId: 'bot', ownerId: 'owner' }), authorize: async () => true,
  }));
  // This is the first read router mounted by createRunsRouter, including its production reader.
  app.use('/runs', createRunReadRouter(repo, new NodeExecutionRepository(db), null, null, null, null, authorizedReads ? reader : null));
  const server = await new Promise<Server>(resolve => { const s = app.listen(0, '127.0.0.1', () => resolve(s)); });
  cleanups.push(async () => { await new Promise<void>(resolve => { server.closeAllConnections(); server.close(() => resolve()); }); await db.close(); });
  const base = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
  const post = async (body: unknown, path = '/internal/runs') => {
    const response = await fetch(base + path, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
    return { status: response.status, body: await response.json() };
  };
  const read = async (path: string) => {
    const response = await fetch(base + path); expect(response.status).toBe(200); return response.json();
  };
  return { raw, db, repo, post, read, scoped: new RunReadRepository(db, permissions) };
}

describe('flow run markers', () => {
  it.each(markerCases)('normalizes $label without trimming, truncating or replacing null', ({ input, expected }) => {
    expect(normalizeFlowRunMarkers(input)).toEqual(expected);
  });
  it.each(invalidMarkers)('rejects invalid markers %j', input => { expect(() => normalizeFlowRunMarkers(input)).toThrow(); });

  it('round-trips defaults, custom values and null through INSERT and every repository read', async () => {
    const f = await fixture();
    for (const [index, { input, expected }] of markerCases.entries()) {
      const flowId = `run-${index}`;
      expect(await f.repo.insert({ ...baseRun, ...input, flowId, stateJson: legacyState })).toBe(true);
      expect(await f.repo.findByFlowId(flowId)).toMatchObject(expected);
      expect(await f.repo.findFullByFlowId(flowId)).toMatchObject({ ...expected, state_json: legacyState });
      expect(await f.repo.findRuns({ query: flowId })).toMatchObject([expected]);
      expect(f.raw.prepare('SELECT env, tag FROM flow_runs WHERE flow_id = ?').get(flowId)).toEqual(expected);
    }
    const scoped = await f.scoped.listRuns({ botId: 'bot', ownerId: 'owner', limit: 50 });
    expect(scoped.items).toHaveLength(markerCases.length);
    for (const [index, { expected }] of markerCases.entries()) {
      expect(scoped.items.find(row => row.flow_id === `run-${index}`)).toMatchObject(expected);
    }
    for (const invalid of invalidMarkers) {
      expect(await f.repo.insert({ ...baseRun, ...invalid } as FlowRunInsert)).toBe(false);
    }
    expect(await f.repo.countRuns()).toBe(markerCases.length);
  });

  it.each([false, true])('preserves nullable raw fields on the active public list/detail path (authorized reader: %s)', async authorized => {
    const f = await fixture(authorized);
    for (const [index, { input, expected }] of markerCases.entries()) {
      const flow_id = `http-${index}`;
      const response = await f.post({ flow_id, workflow_id: 'workflow', status: 'running', state_json: legacyState, ...input });
      expect(response).toMatchObject({ status: 201, body: { success: true, data: { ...expected, state_json: legacyState } } });
      expect((await f.read(`/runs/${flow_id}`)).run).toMatchObject({ ...expected, state_json: legacyState });
    }
    const listed = await f.read('/runs');
    expect(listed.total).toBe(markerCases.length);
    for (const [index, { expected }] of markerCases.entries()) {
      expect(listed.runs.find((row: { flow_id: string }) => row.flow_id === `http-${index}`)).toMatchObject(expected);
    }
  });

  it('rejects invalid HTTP input before inserting instead of relying on VARCHAR truncation', async () => {
    const f = await fixture();
    for (const invalid of invalidMarkers) {
      expect(await f.post({ flow_id: 'invalid', workflow_id: 'workflow', status: 'running', ...invalid }))
        .toMatchObject({ status: 400, body: { success: false, error: 'Bad Request' } });
    }
    expect(await f.repo.countRuns()).toBe(0);
  });

  it('accepts markers on ordinary idempotent requests without changing identity or overwriting the winner', async () => {
    const f = await fixture();
    expect(await f.repo.insert({ ...baseRun, flowId: 'parent' })).toBe(true);
    const request = {
      type: 'ordinary-run-request.register.v1', ordinary_request_key: 'key', ordinary_parent_flow_id: 'parent',
      run: { flow_id: 'child', workflow_id: 'workflow', status: 'pending', state_json: legacyState, env: null, tag: 'first' },
    };
    const path = '/internal/runs/request-association/register';
    expect((await f.post(request, path)).status).toBe(201);
    const before = f.raw.prepare('SELECT request_fingerprint FROM ordinary_run_requests').get();
    const { env: _env, tag: _tag, ...withoutMarkers } = request.run;
    for (const run of [withoutMarkers, { ...request.run, env: 'test', tag: 'later' }, request.run]) {
      expect((await f.post({ ...request, run }, path)).status).toBe(200);
    }
    expect(f.raw.prepare('SELECT request_fingerprint FROM ordinary_run_requests').get()).toEqual(before);
    expect(await f.repo.findFullByFlowId('child')).toMatchObject({ env: null, tag: 'first', state_json: legacyState });
    expect((await f.post({ ...request, run: { ...request.run, env: '' } }, path)).status).toBe(400);
    expect(await f.repo.countRuns()).toBe(2);
  });

  it.each([false, true])('passes markers through the separate parent INSERT and retains labels on retry (fanout: %s)', async fanout => {
    const f = await parentRelationFixture(fanout); cleanups.push(f.close);
    const state = '{ "workflowId": "child-wf", "nodeStates": {} }';
    const input = { ...f.body, env: null, tag: 'child-test', state_json: state };
    expect(await f.post(input)).toMatchObject({ status: 201, body: { data: { env: null, tag: 'child-test', state_json: state } } });
    expect(await f.post({ ...input, env: 'prod', tag: 'retry' })).toMatchObject({ status: 200, body: { data: { env: null, tag: 'child-test' } } });
    expect(f.child()).toMatchObject({ env: null, tag: 'child-test', state_json: state });
    expect(f.links()).toHaveLength(1);
  });
});
