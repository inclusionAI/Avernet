// @vitest-environment node
import { afterEach, describe, expect, it, vi } from 'vitest';
import express from 'express';
import Database from 'better-sqlite3';
import type { Server } from 'node:http';
import { createHash } from 'node:crypto';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { ordinaryRunRequestDdl } from '@avernet/clawweb-shared/server/schema-ordinary-run-request';
import { FlowRunRepository } from '../../../repositories/flow-run-repository.js';
import { createInternalRunsRouter } from '../runs.js';
import { OrdinaryRunRequestService } from '../../../services/ordinary-run-request.js';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import * as internalLogger from '../../internal-logger.js';

const cleanup: Array<() => Promise<void>> = [];
afterEach(async () => { for (const f of cleanup.splice(0)) await f(); });
const start = { type: 'ordinary-run-request.register.v1', flow_id: 'child', workflow_id: 'child-wf', status: 'pending', ordinary_request_key: 'key_A',
  ordinary_parent_flow_id: 'parent', params_json: '{"private":"secret"}', input_json: '{"n":1}', origin_bot_id: 'bot:owner' };
async function fixture() {
  const raw = new Database(':memory:'); const db = new SqliteDatabase(raw);
  raw.exec(`CREATE TABLE flow_runs (id INTEGER PRIMARY KEY, flow_id TEXT UNIQUE, workflow_id TEXT NOT NULL, workflow_title TEXT,
    status TEXT NOT NULL, state_json TEXT, params_json TEXT, input_json TEXT, node_count INTEGER, triggered_by TEXT,
    identity_key TEXT, current_phase TEXT, started_at INTEGER, credentials_json TEXT, origin_session_key TEXT,
    origin_session_id TEXT, origin_bot_id TEXT, user_id TEXT, plugin_version TEXT, engine TEXT, workflow_version INTEGER,
    workflow_deploy_number INTEGER, gmt_create INTEGER, gmt_modified INTEGER, ordinary_run_marker VARCHAR(64));
    CREATE TABLE workflow_repair_release_records (record_kind TEXT, record_id TEXT, body_json TEXT);
    INSERT INTO flow_runs (flow_id,workflow_id,status,started_at) VALUES ('parent','parent-wf','running',1),('other-parent','other-wf','running',1);`);
  await db.exec(db.dialect.renderDdl(ordinaryRunRequestDdl));
  const authenticate = vi.fn(async () => ({ botId: 'verified-bot', ownerId: 'verified-owner' }));
  const authorize = vi.fn(async (_principal: unknown, _relation: unknown, tx: unknown) => { expect(tx).not.toBe(db); return true; });
  const app = express(); app.use(express.json());
  app.use('/runs', createInternalRunsRouter(new FlowRunRepository(db), null, undefined, {}, { authenticate, authorize }));
  const server = await new Promise<Server>(resolve => { const s = app.listen(0, '127.0.0.1', () => resolve(s)); });
  cleanup.push(() => new Promise<void>(resolve => server.close(() => resolve())), () => db.close());
  const base = `http://127.0.0.1:${(server.address() as { port: number }).port}/runs`;
  const postWire = async (path: string, body: unknown, method = 'POST') => {
    const r = await fetch(base + path, { method, headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
    return { status: r.status, data: await r.json(), cache: r.headers.get('cache-control') };
  };
  // Existing cases express run-field overrides compactly; the actual wire body is nested.
  const post = (path: string, body: unknown) => {
    if (path.startsWith('/request-association/register') && body && typeof body === 'object') {
      const { type, ordinary_request_key, ordinary_parent_flow_id, ...run } = body as Record<string, unknown>;
      return postWire(path, { type, ordinary_request_key, ordinary_parent_flow_id, run });
    }
    return postWire(path, body);
  };
  return { raw, db, authenticate, authorize, post, postWire };
}
const lookup = { type: 'ordinary-run-request.lookup.v1', ordinary_request_key: 'key_A' };
describe('ordinary run request association', () => {
  it('rejects initial fanout child claims on the nested ordinary run before any association or insert', async () => {
    const f = await fixture();
    for (const token of ['claim-secret', '', null, false, { forged: true }]) {
      const state_json = JSON.stringify({ subworkflowFanouts: { first: { children: [{}] }, second: { children: [{ dispatchClaimToken: token }] } } });
      const response = await f.post('/request-association/register', { ...start, state_json });
      expect(response.status).toBe(400);
      expect(JSON.stringify(response.data)).not.toContain('claim-secret');
    }
    expect(f.raw.prepare('SELECT count(*) AS n FROM ordinary_run_requests').get()).toEqual({ n: 0 });
    expect(f.raw.prepare("SELECT count(*) AS n FROM flow_runs WHERE flow_id='child'").get()).toEqual({ n: 0 });
    const state_json = JSON.stringify({ subworkflowFanouts: { first: { children: [{ item: { dispatchClaimToken: 'business' } }] } },
      workflowData: { dispatchClaimToken: 'business' } });
    expect((await f.post('/request-association/register', { ...start, state_json })).status).toBe(201);
    expect(f.raw.prepare("SELECT state_json FROM flow_runs WHERE flow_id='child'").get()).toEqual({ state_json });
  });
  it('atomically registers and returns the original run for exact retries and authenticated lookup, never params', async () => {
    const f = await fixture();
    expect(await f.post('/request-association/register', start)).toMatchObject({ status: 201, data: { success: true, data: { created: true, parent_flow_id: 'parent', flow_id: 'child', workflow_id: 'child-wf' } } });
    expect(await f.post('/request-association/register', { ...start, flow_id: 'proposed-again' })).toMatchObject({ status: 200, data: { success: true, data: { created: false, parent_flow_id: 'parent', flow_id: 'child', workflow_id: 'child-wf' } } });
    const result = await f.post('/request-association/lookup', lookup);
    expect(result).toMatchObject({ status: 200, data: { success: true, data: { parent_flow_id: 'parent', flow_id: 'child', workflow_id: 'child-wf' } }, cache: 'no-store' });
    expect(JSON.stringify(result)).not.toContain('secret');
    expect(f.raw.prepare("SELECT count(*) AS n FROM flow_runs WHERE workflow_id='child-wf'").get()).toEqual({ n: 1 });
    expect(f.authorize.mock.calls.every(call => (call[1] as any).requestKey === 'key_A')).toBe(true);
    expect(f.authorize.mock.calls.at(-1)?.[1]).toMatchObject({ parent: { flowId: 'parent', workflowId: 'parent-wf' }, child: { flowId: 'child', workflowId: 'child-wf' }, requestKey: 'key_A' });
  });
  it('registers only pending runs and rejects malformed initial JSON without writing', async () => {
    const f = await fixture();
    for (const status of ['running', 'succeeded', 'failed', 'cancelled']) {
      expect((await f.post('/request-association/register', { ...start, status })).status).toBe(400);
    }
    for (const field of ['params_json', 'input_json', 'credentials_json']) {
      for (const value of ['', '{broken', 'null', '[]', '"text"']) {
        expect((await f.post('/request-association/register', { ...start, status: 'pending', [field]: value })).status).toBe(400);
      }
    }
    for (const stage of [null, 1, '', 'production']) {
      expect((await f.post('/request-association/register', { ...start, status: 'pending', credentials_json: JSON.stringify({ STAGE: stage }) })).status).toBe(400);
    }
    expect(f.raw.prepare('SELECT count(*) AS n FROM ordinary_run_requests').get()).toEqual({ n: 0 });
    expect(f.raw.prepare("SELECT count(*) AS n FROM flow_runs WHERE workflow_id='child-wf'").get()).toEqual({ n: 0 });
    expect((await f.post('/request-association/register', { ...start, status: 'pending', credentials_json: '{"STAGE":"draft"}' })).status).toBe(201);
  });
  it('accepts personal bot credentials with an empty service stage', async () => {
    const f = await fixture();
    const response = await f.post('/request-association/register', {
      ...start, credentials_json: JSON.stringify({ BOT_TYPE: 'personal', STAGE: '' }),
    });
    expect(response.status).toBe(201);
  });
  it('persists current_phase and freezes it for same-key retries', async () => {
    const f = await fixture();
    const registration = { ...start, current_phase: 'preparing' };
    expect((await f.post('/request-association/register', registration)).status).toBe(201);
    expect(f.raw.prepare("SELECT current_phase FROM flow_runs WHERE flow_id='child'").get()).toEqual({ current_phase: 'preparing' });
    expect(await f.post('/request-association/register', { ...registration, flow_id: 'retry' })).toMatchObject({
      status: 200, data: { data: { created: false, flow_id: 'child' } },
    });
    expect((await f.post('/request-association/register', { ...registration, flow_id: 'retry', current_phase: 'running' })).status).toBe(409);
    expect((await f.post('/request-association/register', { ...registration, current_phase: 7 })).status).toBe(400);
    expect(f.raw.prepare("SELECT count(*) AS n FROM flow_runs WHERE workflow_id='child-wf'").get()).toEqual({ n: 1 });
  });
  it('preserves pre-phase fingerprints and treats an omitted phase as null, not empty', async () => {
    const f = await fixture();
    expect((await f.post('/request-association/register', start)).status).toBe(201);
    const previousFields = ['child-wf', null, 'pending', null, start.params_json, start.input_json, 0, null,
      null, null, null, null, start.origin_bot_id, null, null, null, null, null];
    const fingerprint = createHash('sha256').update(JSON.stringify(previousFields)).digest('hex');
    expect(f.raw.prepare('SELECT request_fingerprint FROM ordinary_run_requests').get()).toEqual({ request_fingerprint: fingerprint });
    expect((await f.post('/request-association/register', { ...start, flow_id: 'retry', current_phase: null })).status).toBe(200);
    expect((await f.post('/request-association/register', { ...start, flow_id: 'retry', current_phase: '' })).status).toBe(409);
  });
  it('rejects changed inputs, parent scope, trial identities and malformed keys without additional runs', async () => {
    const f = await fixture(); await f.post('/request-association/register', start);
    for (const fields of [{ input_json: '{"n":2}' }, { ordinary_parent_flow_id: 'other-parent' }, { workflow_id: 'other-wf' }, { origin_bot_id: 'other:owner' }])
      expect((await f.post('/request-association/register', { ...start, ...fields, flow_id: 'other-child' })).status).toBe(409);
    for (const fields of [{ run_request_id: 'trial' }, { run_request_parent_flow_id: 'parent' }, { origin_session_key: 'agent:main:workflow-trial:12345678-1234-1234-1234-123456789012' }])
      expect((await f.post('/request-association/register', { ...start, ...fields })).status).toBe(409);
    expect((await f.post('/request-association/register', { ...start, ordinary_request_key: 'bad/key' })).status).toBe(400);
    expect(f.raw.prepare("SELECT count(*) AS n FROM flow_runs WHERE workflow_id='child-wf'").get()).toEqual({ n: 1 });
  });
  it('denies trial parents and legacy trial state metadata', async () => {
    const f = await fixture();
    f.raw.exec("INSERT INTO workflow_repair_release_records VALUES ('trial_run','parent','{}'), ('trial_run','trial-parent','{}')");
    expect((await f.post('/request-association/register', start)).status).toBe(404);
    f.raw.exec("DELETE FROM workflow_repair_release_records WHERE record_id='parent'");
    expect((await f.post('/request-association/register', { ...start, state_json: JSON.stringify({ subworkflowMeta: { parentFlowId: 'trial-parent' } }) })).status).toBe(409);
  });
  it('rechecks trusted authorization on retries and lookup; strict body and no key in URL', async () => {
    const f = await fixture(); await f.post('/request-association/register', start); f.authorize.mockResolvedValue(false);
    expect((await f.post('/request-association/register', { ...start, flow_id: 'retry' })).status).toBe(404);
    expect((await f.post('/request-association/lookup', lookup)).status).toBe(404);
    f.authorize.mockResolvedValue(true);
    expect((await f.post('/request-association/lookup', { ...lookup, extra: 1 })).status).toBe(400);
    expect((await f.post('/request-association/lookup', { ...lookup, type: 'wrong' })).status).toBe(400);
    expect((await f.post('/request-association/lookup?unsigned=1', lookup)).status).toBe(400);
    expect((await f.post('/request-association/register', { ...start, type: 'wrong' })).status).toBe(400);
    expect((await f.post('/request-association/register', { ...start, extra: 'ignored-input' })).status).toBe(400);
    f.authenticate.mockResolvedValue(null as any);
    expect((await f.post('/request-association/lookup', lookup)).status).toBe(401);
    expect((await f.postWire('/request-association/register', { arbitrary: 'invalid' })).status).toBe(401);
  });
  it('rolls back association on run insert failure; reports DB failures instead of not found', async () => {
    const f = await fixture();
    f.raw.exec("CREATE TRIGGER fail_child BEFORE INSERT ON flow_runs WHEN NEW.flow_id='child' BEGIN SELECT RAISE(ABORT, 'failure'); END");
    expect((await f.post('/request-association/register', start)).status).toBe(503);
    expect(f.raw.prepare('SELECT count(*) AS n FROM ordinary_run_requests').get()).toEqual({ n: 0 });
    f.raw.exec('DROP TABLE ordinary_run_requests');
    expect((await f.post('/request-association/lookup', lookup)).status).toBe(503);
  });
  it('serializes concurrent same-key starts without duplicate runs', async () => {
    const f = await fixture();
    const results = await Promise.all(Array.from({ length: 8 }, (_, n) => f.post('/request-association/register', { ...start, flow_id: `child-${n}` })));
    expect(results.filter(r => r.status === 201)).toHaveLength(1);
    expect(results.filter(r => r.status === 200)).toHaveLength(7);
    expect(new Set(results.map(r => r.data.data.flow_id)).size).toBe(1);
    expect(f.raw.prepare('SELECT count(*) AS n FROM ordinary_run_requests').get()).toEqual({ n: 1 });
  });
  it('refuses external SQLite schemas without a full unique flow ID constraint', async () => {
    const f = await fixture();
    f.raw.exec('ALTER TABLE flow_runs RENAME TO old_runs; CREATE TABLE flow_runs AS SELECT * FROM old_runs; DROP TABLE old_runs');
    expect((await f.post('/request-association/register', start)).status).toBe(503);
    expect(f.raw.prepare('SELECT count(*) AS n FROM ordinary_run_requests').get()).toEqual({ n: 0 });
  });
  it('does not claim absent when an association points to a missing child or authorizer fails', async () => {
    const f = await fixture(); await f.post('/request-association/register', start);
    f.raw.exec("DELETE FROM flow_runs WHERE flow_id='child'");
    expect((await f.post('/request-association/lookup', lookup)).status).toBe(503);
    f.authorize.mockRejectedValueOnce(new Error('private auth failure'));
    const response = await f.post('/request-association/lookup', lookup);
    expect(response.status).toBe(503); expect(JSON.stringify(response)).not.toContain('private auth failure');
  });
  it('rejects legacy trial child registration without creating an ordinary run', async () => {
    const f = await fixture();
    f.raw.exec(`INSERT INTO workflow_repair_release_records VALUES ('trial_run','trial-root','{"trialId":"trial"}');
      INSERT INTO workflow_repair_release_records VALUES ('trial','trial','{"id":"trial","mode":"exact"}')`);
    const params_json = JSON.stringify({ _clawmind_state: JSON.stringify({ subworkflowMeta: { parentFlowId: 'trial-root' } }) });
    expect((await f.post('/request-association/register', { ...start, params_json })).status).toBe(409);
    expect((await f.post('/request-association/register', { ...start, params_json, state_json: '{}' })).status).toBe(409);
    expect(f.raw.prepare('SELECT count(*) AS n FROM ordinary_run_requests').get()).toEqual({ n: 0 });
  });

  it('retries a competing unique-key winner in a fresh transaction', async () => {
    const f = await fixture(); let first = true; let candidate: unknown[] = [];
    const wrapper: IDatabase = {
      dbType: f.db.dbType, dialect: f.db.dialect, query: f.db.query.bind(f.db), exec: f.db.exec.bind(f.db), close: async () => {},
      transaction: async fn => {
        try {
          return await f.db.transaction(async tx => {
            const proxy: IDatabase = { dbType: tx.dbType, dialect: tx.dialect, query: tx.query.bind(tx), close: async () => {},
              transaction: inner => inner(proxy),
              exec: async (sql, params) => {
                if (first && sql.includes('INSERT INTO ordinary_run_requests')) {
                  first = false; candidate = params!;
                  throw Object.assign(new Error('racing insert'), { code: 'SQLITE_CONSTRAINT_PRIMARYKEY' });
                }
                return tx.exec(sql, params);
              } };
            return fn(proxy);
          });
        } catch (error) {
          if (candidate.length) {
            f.raw.prepare("UPDATE flow_runs SET ordinary_run_marker=? WHERE flow_id=?").run(candidate[5], candidate[1]);
            f.raw.prepare("INSERT INTO flow_runs (flow_id,workflow_id,status,started_at,ordinary_run_marker) VALUES (?,?,?,?,?)").run(candidate[2], candidate[3], 'pending', 1, candidate[6]);
            f.raw.prepare('INSERT INTO ordinary_run_requests (request_key_hash,parent_flow_id,child_flow_id,child_workflow_id,request_fingerprint,parent_run_marker,child_run_marker) VALUES (?,?,?,?,?,?,?)').run(...candidate);
            candidate = [];
          }
          throw error;
        }
      },
    };
    const service = new OrdinaryRunRequestService(wrapper, async () => { throw new Error('loser inserted a run'); });
    const result = await service.create(start, { flowId: 'child', workflowId: 'child-wf', status: 'pending',
      startedAt: 1, paramsJson: start.params_json, inputJson: start.input_json, originBotId: start.origin_bot_id },
      { botId: 'verified-bot', ownerId: 'verified-owner' }, { authenticate: f.authenticate, authorize: f.authorize });
    expect(result).toEqual({ created: false, parent_flow_id: 'parent', flow_id: 'child', workflow_id: 'child-wf' });
  });

  it('never routes ordinary registration through legacy POST /runs, even when auth is configured', async () => {
    const f = await fixture();
    expect((await f.post('', start)).status).toBe(503);
    expect(f.authenticate).not.toHaveBeenCalled();
    expect(f.raw.prepare('SELECT count(*) AS n FROM ordinary_run_requests').get()).toEqual({ n: 0 });
  });

  it('accepts only the nested signed register envelope and cannot replay it to status', async () => {
    const f = await fixture();
    const wire = { type: 'ordinary-run-request.register.v1', ordinary_request_key: 'key_A',
      ordinary_parent_flow_id: 'parent', run: { flow_id: 'child', workflow_id: 'child-wf', status: 'pending' } };
    expect((await f.postWire('/request-association/register', wire)).status).toBe(201);
    expect((await f.postWire('/request-association/register', { ...wire, status: 'failed' })).status).toBe(400);
    expect((await f.postWire('/parent/status', wire, 'PUT')).status).toBe(400);
    expect(f.raw.prepare("SELECT status FROM flow_runs WHERE flow_id='parent'").get()).toEqual({ status: 'running' });
  });
  it('does not put unvalidated ordinary request fields or objects into logs', async () => {
    const f = await fixture(); const log = vi.spyOn(internalLogger, 'apiLog');
    try {
      const wire = { type: 'ordinary-run-request.register.v1', ordinary_request_key: 'key_A',
        ordinary_parent_flow_id: 'parent', run: { flow_id: { password: 'DO_NOT_LOG' }, workflow_id: 'child-wf', status: 'running' } };
      expect((await f.postWire('/request-association/register', wire)).status).toBe(400);
      expect(JSON.stringify(log.mock.calls)).not.toContain('DO_NOT_LOG');
    } finally { log.mockRestore(); }
  });

  it('rejects trial context independently in either native state representation', async () => {
    const f = await fixture();
    const trialState = JSON.stringify({ trialId: 'trial-id', subworkflowMeta: { parentFlowId: 'parent' } });
    const cleanState = JSON.stringify({ subworkflowMeta: { parentFlowId: 'parent' } });
    const params_json = JSON.stringify({ _clawmind_state: trialState });
    expect((await f.post('/request-association/register', { ...start, state_json: trialState })).status).toBe(409);
    expect((await f.post('/request-association/register', { ...start, state_json: cleanState, params_json })).status).toBe(409);
    expect((await f.post('/request-association/register', { ...start, state_json: trialState,
      params_json: JSON.stringify({ _clawmind_state: cleanState }) })).status).toBe(409);
    expect((await f.post('/request-association/register', { ...start, state_json: JSON.stringify({ subworkflowMeta: {
      parentFlowId: 'parent', originSessionKey: 'agent:main:workflow-trial:trial-id' } }) })).status).toBe(409);
    expect((await f.post('/request-association/register', { ...start, state_json: cleanState,
      params_json: JSON.stringify({ _clawmind_state: JSON.stringify({ execution: { trialContext: { id: 'trial' } } }) }) })).status).toBe(409);
    expect(f.raw.prepare('SELECT count(*) AS n FROM ordinary_run_requests').get()).toEqual({ n: 0 });
  });

  it('authorizes immutable proposed bot, owner, user and session identities inside the transaction', async () => {
    const f = await fixture();
    const expected = { originBotId: 'bot:owner', userId: 'user', originSessionKey: 'session-key', originSessionId: 'session-id' };
    f.authorize.mockImplementation(async (principal: any, relation: any, tx: unknown) => {
      expect(tx).not.toBe(f.db);
      expect(Object.isFrozen(relation)).toBe(true); expect(Object.isFrozen(relation.child.identity)).toBe(true);
      return principal.botId === 'verified-bot' && principal.ownerId === 'verified-owner'
        && JSON.stringify(relation.child.identity) === JSON.stringify(expected);
    });
    const good = { ...start, user_id: 'user', origin_session_key: 'session-key', origin_session_id: 'session-id' };
    for (const [i, fields] of [{ origin_bot_id: 'other:owner' }, { origin_bot_id: 'bot:other' }, { user_id: 'other' },
      { origin_session_key: 'other-session' }, { origin_session_id: 'other-id' }].entries()) {
      expect((await f.post('/request-association/register', { ...good, ...fields, ordinary_request_key: `bad-${i}` })).status).toBe(404);
    }
    expect((await f.post('/request-association/register', good)).status).toBe(201);
    expect(f.authorize.mock.calls.at(-1)?.[1]).toMatchObject({ child: { identity: expected } });
    expect(f.raw.prepare('SELECT count(*) AS n FROM ordinary_run_requests').get()).toEqual({ n: 1 });
  });
  it('reauthorizes retries and lookup using the stored child identity, not request claims', async () => {
    const f = await fixture();
    const good = { ...start, user_id: 'user', origin_session_key: 'session-key', origin_session_id: 'session-id' };
    const expected = { originBotId: 'bot:owner', userId: 'user', originSessionKey: 'session-key', originSessionId: 'session-id' };
    f.authorize.mockImplementation(async (_principal: any, relation: any) => JSON.stringify(relation.child.identity) === JSON.stringify(expected));
    expect((await f.post('/request-association/register', good)).status).toBe(201);
    expect((await f.post('/request-association/register', { ...good, user_id: 'forged' })).status).toBe(409);
    expect(f.authorize.mock.calls.at(-1)?.[1]).toMatchObject({ child: { identity: expected } });
    expect((await f.post('/request-association/lookup', lookup)).status).toBe(200);
    for (const [column, bad] of [['origin_bot_id','bot:other'], ['user_id','other'], ['origin_session_key','other'], ['origin_session_id','other']] as const) {
      f.raw.prepare(`UPDATE flow_runs SET ${column}=? WHERE flow_id='child'`).run(bad);
      expect((await f.post('/request-association/lookup', lookup)).status).toBe(404);
      expect((await f.post('/request-association/register', good)).status).toBe(404);
      const original = { origin_bot_id: 'bot:owner', user_id: 'user', origin_session_key: 'session-key', origin_session_id: 'session-id' }[column];
      f.raw.prepare(`UPDATE flow_runs SET ${column}=? WHERE flow_id='child'`).run(original);
    }
    f.authenticate.mockResolvedValue({ botId: 'different', ownerId: 'different' });
    f.authorize.mockImplementation(async (principal: any) => principal.botId === 'verified-bot' && principal.ownerId === 'verified-owner');
    expect((await f.post('/request-association/lookup', lookup)).status).toBe(404);
    expect((await f.post('/request-association/register', good)).status).toBe(404);
  });
  it('allows the host to deny missing identities and never returns authorized identity or session fields', async () => {
    const f = await fixture();
    f.authorize.mockImplementation(async (_principal: any, relation: any) => {
      const identity = relation.child.identity;
      return ['originBotId', 'userId', 'originSessionKey', 'originSessionId']
        .every(field => typeof identity[field] === 'string' && identity[field].length > 0);
    });
    expect((await f.post('/request-association/register', start)).status).toBe(404);
    expect(f.raw.prepare('SELECT count(*) AS n FROM ordinary_run_requests').get()).toEqual({ n: 0 });
    const full = { ...start, user_id: 'private-user', origin_session_key: 'private-session-key', origin_session_id: 'private-session-id' };
    const created = await f.post('/request-association/register', full);
    expect(created.status).toBe(201);
    const found = await f.post('/request-association/lookup', lookup);
    expect(found.status).toBe(200);
    for (const secret of ['bot:owner', 'private-user', 'private-session-key', 'private-session-id']) {
      expect(JSON.stringify(created.data)).not.toContain(secret);
      expect(JSON.stringify(found.data)).not.toContain(secret);
    }
    f.raw.prepare("UPDATE flow_runs SET origin_session_id=NULL WHERE flow_id='child'").run();
    expect((await f.post('/request-association/lookup', lookup)).status).toBe(404);
    expect((await f.post('/request-association/register', full)).status).toBe(404);
  });
  it('rejects an explicitly empty params_json rather than treating it as omitted', async () => {
    const f = await fixture();
    expect((await f.post('/request-association/register', { ...start, params_json: '' })).status).toBe(400);
    expect(f.raw.prepare('SELECT count(*) AS n FROM ordinary_run_requests').get()).toEqual({ n: 0 });
  });

  it('persists the detached identity even if the caller mutates its original object during authorization', async () => {
    const f = await fixture();
    const body = { ...start, user_id: 'user', origin_session_key: 'session', origin_session_id: 'session-id' };
    const run = { flowId: 'child', workflowId: 'child-wf', status: 'pending', startedAt: 1,
      paramsJson: start.params_json, inputJson: start.input_json, originBotId: 'bot:owner',
      userId: 'user', originSessionKey: 'session', originSessionId: 'session-id' };
    const service = new OrdinaryRunRequestService(f.db, async (tx, value) => {
      if (!await new FlowRunRepository(tx).insert(value)) throw Error('insert failed');
    });
    const auth = { authenticate: f.authenticate, authorize: async (_principal: any, relation: any) => {
      expect(relation.child.identity).toEqual({ originBotId: 'bot:owner', userId: 'user', originSessionKey: 'session', originSessionId: 'session-id' });
      expect(Object.isFrozen(relation.child)).toBe(true);
      expect(() => { relation.child.identity.userId = 'forged'; }).toThrow();
      run.userId = 'forged'; run.originBotId = 'forged:owner'; body.origin_session_key = 'forged-session';
      return true;
    } };
    await expect(service.create(body, run, { botId: 'verified-bot', ownerId: 'verified-owner' }, auth))
      .resolves.toMatchObject({ created: true });
    expect(f.raw.prepare("SELECT origin_bot_id, user_id, origin_session_key FROM flow_runs WHERE flow_id='child'").get())
      .toEqual({ origin_bot_id: 'bot:owner', user_id: 'user', origin_session_key: 'session' });
  });

});
