// @vitest-environment node
import Database from 'better-sqlite3';
import type { Request, Response } from 'express';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SqliteDatabase, type IDatabase } from '@avernet/clawweb-shared/server/db';
import { dialectFor } from '@avernet/clawweb-shared/server/db/dialect';
import { FlowRunRepository, type RunOriginScope } from '../../repositories/flow-run-repository.js';
import { createRunBaasRouter } from '../run-baas-routes.js';

vi.mock('@avernet/clawevolve/server/services/baas-intervention', () => ({ sendIntervention: vi.fn() }));

const scope = { workflowId: 'Workflow', originBotId: 'Bot:owner' };
const close: Array<() => Promise<void>> = [];
afterEach(async () => {
  for (const cleanup of close.splice(0)) await cleanup();
  vi.restoreAllMocks();
});

function fixture(origin: RunOriginScope = scope) {
  const raw = new Database(':memory:');
  const db = new SqliteDatabase(raw);
  close.push(() => db.close());
  // Deliberately case-insensitive columns: an ordinary SQL equality would match a different identity.
  raw.exec(`CREATE TABLE flow_runs (
    id INTEGER PRIMARY KEY, flow_id TEXT UNIQUE, workflow_id TEXT COLLATE NOCASE, workflow_title TEXT,
    parent_flow_id VARCHAR(255), root_flow_id VARCHAR(255),
    status TEXT, node_count INTEGER, succeeded_count INTEGER, failed_count INTEGER,
    total_duration_ms INTEGER, total_token_usage INTEGER, started_at INTEGER, completed_at INTEGER,
    triggered_by TEXT, identity_key TEXT, current_phase TEXT, origin_session_key TEXT, origin_session_id TEXT,
    origin_bot_id TEXT COLLATE NOCASE, user_id TEXT, plugin_version TEXT, engine TEXT, env TEXT, tag TEXT,
    workflow_version INTEGER, workflow_deploy_number INTEGER, gmt_create INTEGER, gmt_modified INTEGER,
    evolution_analysis_status TEXT
  );`);
  raw.prepare('INSERT INTO flow_runs (flow_id, workflow_id, origin_bot_id, origin_session_key, origin_session_id, gmt_modified) VALUES (?, ?, ?, ?, ?, ?)')
    .run('run', origin.workflowId, origin.originBotId, 'old-key', 'old-session', 1);
  const router = createRunBaasRouter(new FlowRunRepository(db), null, null);
  // Call the router directly so the tests do not need a listening socket.
  const patch = (body: Record<string, unknown>, authorizedRunOrigin?: RunOriginScope) =>
    new Promise<{ status: number; body: unknown }>((resolve, reject) => {
      let status = 200;
      const req = { method: 'PATCH', url: '/run/session', body } as Request;
      const res = {
        locals: { authorizedRunOrigin },
        status(code: number) { status = code; return this; },
        json(payload: unknown) { resolve({ status, body: payload }); },
      } as unknown as Response;
      router(req, res, error => reject(error ?? new Error('Session route did not respond')));
    });
  const row = () => raw.prepare('SELECT workflow_id, origin_bot_id, origin_session_key, origin_session_id, gmt_modified FROM flow_runs').get();
  return { raw, db, patch, row };
}

describe('BaaS run session PATCH expected origin scope', () => {
  it.each([scope.originBotId, null])('updates a matching origin (%s) and honors explicit null fields', async originBotId => {
    const expected = { ...scope, originBotId };
    const f = fixture(expected);
    expect(await f.patch({ originBotId: 'new-bot', originSessionId: null }, expected)).toEqual({
      status: 200, body: { ok: true, flowId: 'run', originBotId: 'new-bot', originSessionKey: 'old-key', originSessionId: null },
    });
    expect(f.row()).toMatchObject({ origin_bot_id: 'new-bot', origin_session_id: null });
  });

  it.each([
    [scope, { ...scope, originBotId: 'bot:owner' }],
    [scope, { ...scope, workflowId: 'workflow' }],
    [scope, { ...scope, originBotId: null }],
    [{ ...scope, originBotId: null }, { ...scope, originBotId: '' }],
  ])('denies a changed origin between the scoped read and write (%j → %j)', async (expected, changed) => {
    const f = fixture(expected);
    const exec = f.db.exec.bind(f.db);
    const update = vi.spyOn(f.db, 'exec').mockImplementationOnce(async (sql, params) => {
      f.raw.prepare('UPDATE flow_runs SET workflow_id = ?, origin_bot_id = ?').run(changed.workflowId, changed.originBotId);
      return exec(sql, params);
    });
    const response = await f.patch({ originSessionKey: 'new-key' }, expected);
    expect(update).toHaveBeenCalledOnce();
    expect(response).toMatchObject({ status: 403, body: { error: 'Forbidden' } });
    expect(response.body).not.toHaveProperty('originSessionKey');
    expect(f.row()).toEqual({ workflow_id: changed.workflowId, origin_bot_id: changed.originBotId,
      origin_session_key: 'old-key', origin_session_id: 'old-session', gmt_modified: 1 });
  });

  it('does not serialize a different origin that takes over after the update', async () => {
    const f = fixture();
    const exec = f.db.exec.bind(f.db);
    vi.spyOn(f.db, 'exec').mockImplementationOnce(async (sql, params) => {
      const result = await exec(sql, params);
      f.raw.prepare('UPDATE flow_runs SET origin_bot_id = ?, origin_session_id = ?').run('other-bot', 'other-session');
      return result;
    });
    expect(await f.patch({ originSessionKey: 'new-key' }, scope)).toEqual({
      status: 200, body: { ok: true, flowId: 'run', originBotId: scope.originBotId, originSessionKey: 'new-key', originSessionId: 'old-session' },
    });
    expect(f.row()).toMatchObject({ origin_bot_id: 'other-bot', origin_session_id: 'other-session' });
  });

  it.each(['query', 'exec'] as const)('keeps %s failures distinct from a stale scope', async operation => {
    const f = fixture();
    vi.spyOn(f.db, operation).mockRejectedValueOnce(new Error('database failure detail'));
    expect(await f.patch({ originSessionKey: 'new-key' }, scope)).toEqual({
      status: 500, body: { error: 'Internal Server Error', message: 'Failed to update session info' },
    });
    expect(f.row()).toMatchObject({ origin_session_key: 'old-key', gmt_modified: 1 });
  });

  it('preserves unscoped behavior and ignores a scope supplied in the request body', async () => {
    const f = fixture();
    expect(await f.patch({ originSessionId: 'new-id', authorizedRunOrigin: { workflowId: 'unrelated', originBotId: null } })).toEqual({
      status: 200, body: { ok: true, flowId: 'run', originBotId: scope.originBotId, originSessionKey: 'old-key', originSessionId: 'new-id' },
    });
    expect(f.row()).toMatchObject({ origin_session_id: 'new-id' });
  });

  it.each(['mysql', 'zdas'] as const)('uses a binary identity predicate with the %s dialect', async dbType => {
    const dialect = dialectFor(dbType);
    const exec = vi.fn<IDatabase['exec']>().mockResolvedValue({ affectedRows: 0 });
    const db: IDatabase = { dbType, dialect, exec, query: vi.fn(), transaction: vi.fn(), close: vi.fn() };
    const repo = new FlowRunRepository(db);
    expect(await repo.updateSessionInfo('run', { originSessionId: 'new-id' }, scope)).toBe(false);
    expect(exec).toHaveBeenCalledWith(
      'UPDATE flow_runs SET origin_session_id = ?, gmt_modified = ? WHERE flow_id = ? AND BINARY workflow_id = BINARY ? AND BINARY origin_bot_id = BINARY ?',
      ['new-id', expect.any(String), 'run', scope.workflowId, scope.originBotId],
    );
  });
});
