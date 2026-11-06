// @vitest-environment node
import { afterEach, describe, expect, it, vi } from 'vitest';
import Database from 'better-sqlite3';
import express from 'express';
import type { Server } from 'node:http';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db';
import { FlowRunRepository } from '../../repositories/flow-run-repository.js';
import type { NodeExecutionRepository } from '../../repositories/node-execution-repository.js';
import type { FlowEventRepository } from '../../repositories/event-repository.js';
import { createRunsRouter } from '../runs.js';
import { RunSubworkflowsService } from '../../services/run-subworkflows-service.js';
import { RunSubworkflowsRepository } from '../../repositories/run-subworkflows-repository.js';

const clientFetch = globalThis.fetch;
vi.mock('@avernet/clawweb-shared/server/db', async original => ({
  ...await original<typeof import('@avernet/clawweb-shared/server/db')>(),
  resolveBaasConfig: () => ({ apiKey: 'test-key', baseUrl: 'https://example.test', iamtoken: '' }),
}));
const send = vi.hoisted(() => vi.fn());
vi.mock('@avernet/clawevolve/server/services/baas-intervention', () => ({ sendIntervention: send }));
const cleanup: Array<() => Promise<void>> = [];
afterEach(async () => { for (const close of cleanup.splice(0)) await close(); vi.restoreAllMocks(); vi.clearAllMocks(); });

type Path = 'session' | 'intervene' | 'rerun' | 'abort' | 'chat' | 'poll-message' | '';
const cases = [
  ['rerun', {}], ['abort', {}], ['chat', { message: 'hello' }],
  ['poll-message', { messageId: 'msg' }], ['', {}],
] as const;
const allCases = [['session', { originSessionKey: 'changed' }], ['intervene', { action: 'retry' }], ...cases] as const;
async function fixture(state: string | null) {
  const raw = new Database(':memory:');
  const db = new SqliteDatabase(raw);
  raw.exec(`CREATE TABLE flow_runs (flow_id TEXT, workflow_id TEXT, status TEXT, state_json TEXT,
    workflow_title TEXT, input_json TEXT, result_json TEXT, credentials_json TEXT,
    origin_bot_id TEXT, origin_session_key TEXT, origin_session_id TEXT, completed_at INTEGER, gmt_modified INTEGER);
    INSERT INTO flow_runs VALUES ('run', 'wf', 'running', NULL, NULL, NULL, NULL, NULL, 'bot', 'session', NULL, NULL, NULL);`);
  raw.prepare('UPDATE flow_runs SET state_json = ?').run(state);
  for (const column of ['params_json', 'node_count', 'succeeded_count', 'failed_count', 'total_duration_ms', 'total_token_usage', 'started_at', 'triggered_by', 'current_phase', 'workflow_version', 'workflow_deploy_number', 'id', 'identity_key', 'user_id', 'plugin_version', 'engine', 'gmt_create']) raw.exec(`ALTER TABLE flow_runs ADD COLUMN ${column} TEXT`);
  const repo = new FlowRunRepository(db);
  const reader = new RunSubworkflowsService(new RunSubworkflowsRepository(db), {} as never);
  const abort = vi.spyOn(repo, 'abort'); const remove = vi.spyOn(repo, 'deleteByFlowId');
  const update = vi.spyOn(repo, 'updateSessionInfo');
  const nodes = { reconcileStaleRunning: vi.fn().mockResolvedValue(0), deleteByFlowId: vi.fn().mockResolvedValue(0) };
  const events = { insert: vi.fn().mockResolvedValue(undefined), deleteByFlowId: vi.fn().mockResolvedValue(0) };
  const externalFetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ data: { status: 'DONE' } }), { status: 200 }));
  const app = express(); app.use(express.json());
  app.use((req, _res, next) => { req.isAdmin = req.headers['x-admin'] === 'true'; next(); });
  app.use('/runs', createRunsRouter(repo, nodes as unknown as NodeExecutionRepository, events as unknown as FlowEventRepository, null, null, null, null, null, null, null, reader));
  const server = await new Promise<Server>(resolve => { const s = app.listen(0, '127.0.0.1', () => resolve(s)); });
  cleanup.push(() => new Promise<void>(resolve => server.close(() => resolve())), () => db.close());
  const url = `http://127.0.0.1:${(server.address() as { port: number }).port}/runs/run`;
  const request = (path: Path, body: unknown, admin = true) => clientFetch(path ? `${url}/${path}` : url, {
    method: path === 'session' ? 'PATCH' : path ? 'POST' : 'DELETE', headers: { 'content-type': 'application/json', 'x-admin': String(admin), 'x-user-id': 'admin' },
    body: JSON.stringify(body),
  });
  send.mockResolvedValue({ ok: true, messageId: 'message', sessionId: 'new-id' });
  return { raw, repo, abort, remove, update, nodes, events, externalFetch, request };
}

describe('temporary fanout child guard on other public operations', () => {
  it('retains the real registered origin after a mirror update and denies all seven public operations', async () => {
    const origin = { parentFlowId: 'parent', itemId: 'item', attemptNumber: 1 };
    const f = await fixture(JSON.stringify({ fanoutOrigin: origin, currentPhase: 'before' }));
    expect(await f.repo.updateStateJson('run', JSON.stringify({ currentPhase: 'after' }))).toBe(true);
    const persisted = f.raw.prepare('SELECT state_json FROM flow_runs').get() as { state_json: string };
    expect(JSON.parse(persisted.state_json)).toEqual({ fanoutOrigin: origin, currentPhase: 'after' });
    for (const [path, body] of allCases) expect((await f.request(path, body)).status, path).toBe(403);
    expect(f.abort).not.toHaveBeenCalled(); expect(f.remove).not.toHaveBeenCalled(); expect(f.update).not.toHaveBeenCalled();
    expect(f.nodes.reconcileStaleRunning).not.toHaveBeenCalled(); expect(f.nodes.deleteByFlowId).not.toHaveBeenCalled();
    expect(f.events.insert).not.toHaveBeenCalled(); expect(f.events.deleteByFlowId).not.toHaveBeenCalled();
    expect(send).not.toHaveBeenCalled(); expect(f.externalFetch).not.toHaveBeenCalled();
  });

  it('accepts identical origin but rejects replacement, retaining persisted state', async () => {
    const origin = { parentFlowId: 'parent', itemId: 'item' };
    const f = await fixture(JSON.stringify({ fanoutOrigin: origin }));
    expect(await f.repo.updateStateJson('run', JSON.stringify({ fanoutOrigin: { itemId: 'item', parentFlowId: 'parent' }, phase: 2 }))).toBe(true);
    const before = (f.raw.prepare('SELECT state_json FROM flow_runs').get() as { state_json: string }).state_json;
    for (const altered of [null, {}, { ...origin, parentFlowId: 'other' }]) {
      await expect(f.repo.updateStateJson('run', JSON.stringify({ fanoutOrigin: altered }))).rejects.toMatchObject({ statusCode: 409 });
      expect((f.raw.prepare('SELECT state_json FROM flow_runs').get() as { state_json: string }).state_json).toBe(before);
    }
  });

  it.each(allCases)('does not newly block legacy managed child operation %s', async (path, body) => {
    const f = await fixture(JSON.stringify({ _fanoutIdentity: { parentFlowId: 'parent' }, _fanoutRevision: 1 }));
    if (path === 'intervene') f.raw.exec("UPDATE flow_runs SET status = 'failed'");
    expect((await f.request(path, body)).status).toBe(200);
  });

  it.each(cases)('denies %s before side effects, including for admin', async (path, body) => {
    const f = await fixture(JSON.stringify({ fanoutOrigin: {} }));
    expect((await f.request(path, body)).status).toBe(403);
    expect(f.abort).not.toHaveBeenCalled(); expect(f.remove).not.toHaveBeenCalled(); expect(f.update).not.toHaveBeenCalled();
    expect(f.nodes.reconcileStaleRunning).not.toHaveBeenCalled(); expect(f.nodes.deleteByFlowId).not.toHaveBeenCalled();
    expect(f.events.deleteByFlowId).not.toHaveBeenCalled(); expect(send).not.toHaveBeenCalled(); expect(f.externalFetch).not.toHaveBeenCalled();
    expect(f.raw.prepare('SELECT status FROM flow_runs').get()).toEqual({ status: 'running' });
  });
  it.each(cases)('fails closed on persistent read error for %s', async (path, body) => {
    const f = await fixture(JSON.stringify({ fanoutOrigin: {} })); f.raw.exec('DROP TABLE flow_runs');
    expect((await f.request(path, body)).status).toBe(503);
    expect(f.abort).not.toHaveBeenCalled(); expect(f.remove).not.toHaveBeenCalled(); expect(f.update).not.toHaveBeenCalled();
    expect(f.nodes.deleteByFlowId).not.toHaveBeenCalled(); expect(send).not.toHaveBeenCalled(); expect(f.externalFetch).not.toHaveBeenCalled();
  });
  it.each(cases)('fails closed on malformed persisted state for %s', async (path, body) => {
    const f = await fixture('{"fanoutOrigin":');
    expect((await f.request(path, body)).status).toBe(503);
    expect(f.abort).not.toHaveBeenCalled(); expect(f.remove).not.toHaveBeenCalled(); expect(f.update).not.toHaveBeenCalled();
    expect(f.nodes.deleteByFlowId).not.toHaveBeenCalled(); expect(send).not.toHaveBeenCalled(); expect(f.externalFetch).not.toHaveBeenCalled();
  });
  it('preserves ordinary delete authorization', async () => {
    const f = await fixture('{}');
    expect((await f.request('', {}, false)).status).toBe(403);
    expect(f.remove).not.toHaveBeenCalled();
  });
  it('keeps ordinary rerun, chat, polling, abort and admin delete working', async () => {
    const f = await fixture('{}');
    expect((await f.request('rerun', {})).status).toBe(200);
    expect((await f.request('chat', { message: 'hello' })).status).toBe(200);
    expect((await f.request('poll-message', { messageId: 'msg' })).status).toBe(200);
    expect(f.externalFetch).toHaveBeenCalledTimes(1);
    expect((await f.request('abort', {})).status).toBe(200);
    expect(f.raw.prepare('SELECT status FROM flow_runs').get()).toEqual({ status: 'cancelled' });
    expect((await f.request('', {})).status).toBe(200);
    expect(f.raw.prepare('SELECT count(*) AS n FROM flow_runs').get()).toEqual({ n: 0 });
  });
});
