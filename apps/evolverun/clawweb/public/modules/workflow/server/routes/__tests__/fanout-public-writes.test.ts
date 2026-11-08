// @vitest-environment node
import { afterEach, describe, expect, it, vi } from 'vitest';
import Database from 'better-sqlite3';
import express from 'express';
import type { Server } from 'node:http';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db';
import { FlowRunRepository } from '../../repositories/flow-run-repository.js';
import { createRunsRouter } from '../runs.js';
import { RunSubworkflowsService } from '../../services/run-subworkflows-service.js';
import { RunSubworkflowsRepository } from '../../repositories/run-subworkflows-repository.js';

const send = vi.hoisted(() => vi.fn());
vi.mock('@avernet/clawevolve/server/services/baas-intervention', () => ({ sendIntervention: send }));
const cleanup: Array<() => Promise<void>> = [];
afterEach(async () => { for (const close of cleanup.splice(0)) await close(); vi.clearAllMocks(); });

async function fixture(state: string | null) {
  const raw = new Database(':memory:');
  const db = new SqliteDatabase(raw);
  raw.exec(`CREATE TABLE flow_runs (id INTEGER PRIMARY KEY, flow_id TEXT, workflow_id TEXT, workflow_title TEXT,
    status TEXT, state_json TEXT, node_count INTEGER, succeeded_count INTEGER, failed_count INTEGER,
    total_duration_ms INTEGER, total_token_usage INTEGER, started_at INTEGER, completed_at INTEGER,
    triggered_by TEXT, identity_key TEXT, current_phase TEXT, origin_session_key TEXT, origin_session_id TEXT,
    origin_bot_id TEXT, user_id TEXT, plugin_version TEXT, engine TEXT, workflow_version INTEGER,
    workflow_deploy_number INTEGER, gmt_create INTEGER, gmt_modified INTEGER, evolution_analysis_status TEXT);`);
  raw.prepare(`INSERT INTO flow_runs (flow_id, workflow_id, status, started_at, state_json,
    origin_bot_id, origin_session_key, origin_session_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)`)
    .run('run', 'wf', 'failed', 1, state, 'bot', 'original-key', 'original-id');
  for (const column of ['params_json', 'input_json', 'result_json']) raw.exec(`ALTER TABLE flow_runs ADD COLUMN ${column} TEXT`);
  const repo = new FlowRunRepository(db);
  const reader = new RunSubworkflowsService(new RunSubworkflowsRepository(db), {} as never);
  const update = vi.spyOn(repo, 'updateSessionInfo');
  const app = express(); app.use(express.json());
  app.use((req, _res, next) => { req.isAdmin = req.headers['x-admin'] === 'true'; next(); });
  app.use('/runs', createRunsRouter(repo, null, null, null, null, null, null, null, null, null, reader));
  const server = await new Promise<Server>(resolve => { const s = app.listen(0, '127.0.0.1', () => resolve(s)); });
  cleanup.push(() => new Promise<void>(resolve => server.close(() => resolve())), () => db.close());
  const url = `http://127.0.0.1:${(server.address() as { port: number }).port}/runs/run`;
  const request = (path: 'session' | 'intervene', body: unknown, headers: Record<string, string> = {}) => fetch(`${url}/${path}`, {
    method: path === 'session' ? 'PATCH' : 'POST',
    headers: { 'content-type': 'application/json', ...headers }, body: JSON.stringify(body),
  });
  send.mockResolvedValue({ ok: true, messageId: 'message', sessionId: 'new-id' });
  return { raw, update, request };
}

describe('temporary public fanout child write guard', () => {
  it.each([{}, null, [], 'bad'])('denies persisted fanoutOrigin %j regardless of forged caller or body', async origin => {
    const f = await fixture(JSON.stringify({ fanoutOrigin: origin }));
    const headers = { 'x-user-id': 'admin', 'x-admin': 'true', authorization: 'Bearer forged', 'x-signature': 'forged' };
    expect((await f.request('session', { originSessionKey: 'attacker', state_json: '{}' }, headers)).status).toBe(403);
    expect((await f.request('intervene', { action: 'retry' }, headers)).status).toBe(403);
    expect(f.update).not.toHaveBeenCalled(); expect(send).not.toHaveBeenCalled();
    expect(f.raw.prepare('SELECT origin_session_key, origin_session_id FROM flow_runs').get())
      .toEqual({ origin_session_key: 'original-key', origin_session_id: 'original-id' });
  });

  it('denies an anonymous caller without relying on headers or body', async () => {
    const f = await fixture(JSON.stringify({ fanoutOrigin: {} }));
    expect((await f.request('session', { originSessionKey: 'new-key' })).status).toBe(403);
    expect((await f.request('intervene', { action: 'retry' })).status).toBe(403);
    expect(f.update).not.toHaveBeenCalled(); expect(send).not.toHaveBeenCalled();
  });

  it.each([null, '', '{}', '{"other":"fanoutOrigin"}'])('keeps ordinary operations working with state %j', async state => {
    const f = await fixture(state);
    expect((await f.request('session', { originSessionKey: 'new-key', fanoutOrigin: {} })).status).toBe(200);
    expect((await f.request('intervene', { action: 'retry', fanoutOrigin: {} })).status).toBe(200);
    expect(send).toHaveBeenCalledWith(expect.objectContaining({ botId: 'bot', sessionKey: 'new-key' }));
    expect(f.raw.prepare('SELECT origin_session_key, origin_session_id FROM flow_runs').get())
      .toEqual({ origin_session_key: 'new-key', origin_session_id: 'new-id' });
  });

  it('fails closed on malformed durable state', async () => {
    const f = await fixture('{"fanoutOrigin":');
    expect((await f.request('session', { originSessionKey: 'new-key' })).status).toBe(503);
    expect((await f.request('intervene', { action: 'retry' })).status).toBe(503);
    expect(f.update).not.toHaveBeenCalled(); expect(send).not.toHaveBeenCalled();
  });

  it('fails closed when the real persistent read fails', async () => {
    const f = await fixture(JSON.stringify({ fanoutOrigin: {} }));
    f.raw.exec('DROP TABLE flow_runs');
    expect((await f.request('session', { originSessionKey: 'new-key' })).status).toBe(503);
    expect((await f.request('intervene', { action: 'retry' })).status).toBe(503);
    expect(f.update).not.toHaveBeenCalled(); expect(send).not.toHaveBeenCalled();
  });

  it('fails closed when the persisted state column is unavailable', async () => {
    const f = await fixture(JSON.stringify({ fanoutOrigin: {} }));
    f.raw.exec('ALTER TABLE flow_runs DROP COLUMN state_json');
    expect((await f.request('session', { originSessionKey: 'new-key' })).status).toBe(503);
    expect((await f.request('intervene', { action: 'retry' })).status).toBe(503);
    expect(f.update).not.toHaveBeenCalled(); expect(send).not.toHaveBeenCalled();
  });

  it('retains existing validation and missing-run behavior', async () => {
    const f = await fixture('{}');
    expect((await f.request('session', {})).status).toBe(400);
    expect((await f.request('intervene', { action: 'invalid' })).status).toBe(400);
    f.raw.exec('DELETE FROM flow_runs');
    expect((await f.request('session', { originSessionKey: 'key' })).status).toBe(404);
    expect((await f.request('intervene', { action: 'retry' })).status).toBe(404);
    expect(f.update).not.toHaveBeenCalled(); expect(send).not.toHaveBeenCalled();
  });
});
