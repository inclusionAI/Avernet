// @vitest-environment node
import { afterEach, describe, expect, it, vi } from 'vitest';
import express from 'express';
import Database from 'better-sqlite3';
import type { Server } from 'node:http';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { createRepairAuthorizer } from '../repair-authorization.js';
import { createSubworkflowObservabilityRouter } from '../subworkflow-observability.js';

describe('subworkflow observability', () => {
  const cleanup: Array<() => Promise<void>> = [];
  afterEach(async () => { for (const close of cleanup.splice(0)) await close(); });

  async function fixture() {
    const raw = new Database(':memory:');
    raw.exec(`CREATE TABLE flow_runs (flow_id TEXT PRIMARY KEY, workflow_id TEXT, status TEXT, started_at INTEGER, completed_at INTEGER, input_json TEXT, result_json TEXT);
      CREATE TABLE flow_run_links (child_flow_id TEXT PRIMARY KEY, parent_flow_id TEXT, parent_node_id TEXT, parent_attempt INTEGER, child_workflow_id TEXT, gmt_create INTEGER DEFAULT 0);
      CREATE TABLE flow_events (id INTEGER PRIMARY KEY, flow_id TEXT, workflow_id TEXT, node_id TEXT, event_type TEXT, attempt INTEGER, time INTEGER, data_json TEXT, error_text TEXT);
      CREATE INDEX event_flow ON flow_events(flow_id);
      CREATE TABLE node_executions (id INTEGER PRIMARY KEY, flow_id TEXT, workflow_id TEXT, node_id TEXT, node_title TEXT, status TEXT, attempt INTEGER, input_json TEXT, output_json TEXT, error_text TEXT);
      CREATE INDEX node_flow ON node_executions(flow_id);
      CREATE TABLE workflow_repair_release_records (record_id TEXT, record_kind TEXT);
      INSERT INTO flow_runs VALUES ('parent','parent-wf','running',10,NULL,'secret-parent','secret-parent-output');`);
    const db = new SqliteDatabase(raw);
    let authenticated = true;
    const permissions = new Map<string, 'all' | 'deny' | { botIds: string[] }>([['parent-wf', 'all'], ['child-wf', 'all']]);
    const authorize = vi.fn(createRepairAuthorizer(async () => authenticated ? { actorId: 'trusted', isAdmin: false } : null, {
      hasEditPermission: async () => false,
      resolveViewScope: async workflowId => permissions.get(workflowId) ?? 'deny',
    }));
    const app = express();
    app.use('/runs', createSubworkflowObservabilityRouter(db, authorize));
    const server = await new Promise<Server>(resolve => { const s = app.listen(0, '127.0.0.1', () => resolve(s)); });
    cleanup.push(() => new Promise<void>(resolve => server.close(() => resolve())), () => db.close());
    const url = `http://127.0.0.1:${(server.address() as { port: number }).port}/runs`;
    async function get(path = '/parent/subworkflows') {
      const res = await fetch(url + path, { headers: { 'x-user-id': 'forged', 'x-admin': 'true' } });
      return { status: res.status, headers: res.headers, body: await res.json() };
    }
    function run(id: string, workflow = 'child-wf') {
      raw.prepare('INSERT INTO flow_runs VALUES (?, ?, ?, ?, ?, ?, ?)').run(id, workflow, 'succeeded', 20, 30, 'secret-input', 'secret-output');
    }
    function event(id: string, kind = 'subworkflow_linked', node = 'parent-node', workflow = 'parent-wf') {
      raw.prepare('INSERT INTO flow_events (flow_id, workflow_id, node_id, event_type, time, data_json, error_text) VALUES (?, ?, ?, ?, ?, ?, ?)')
        .run('parent', workflow, node, kind, 100, JSON.stringify({ childFlowId: id, childWorkflowId: 'untrusted-workflow', secret: 'secret-event' }), 'secret-error');
    }
    function node(flow: string, attempt: number, workflow = 'child-wf') {
      raw.prepare('INSERT INTO node_executions (flow_id, workflow_id, node_id, node_title, status, attempt, input_json, output_json, error_text) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)')
        .run(flow, workflow, 'node-1', 'Safe title', 'succeeded', attempt, 'secret-input', 'secret-output', 'secret-error');
    }
    return { raw, db, get, run, event, node, permissions, authorize, setAuthenticated: (value: boolean) => { authenticated = value; } };
  }

  it('returns only explicitly selected fields, deduplicates retry events, and retains node attempts', async () => {
    const f = await fixture(); f.run('child'); f.event('child'); f.event('child', 'subworkflow_finished'); f.node('child', 1); f.node('child', 2);
    const result = await f.get();
    expect(result.status).toBe(200);
    expect(result.headers.get('cache-control')).toBe('no-store');
    expect(result.body).toEqual({ completeness: 'best_effort', items: [{ parentNodeId: 'parent-node', parentAttempt: null, childFlowId: 'child', childWorkflowId: 'child-wf', status: 'succeeded', startedAt: 20, completedAt: 30,
      nodes: [{ nodeId: 'node-1', nodeTitle: 'Safe title', status: 'succeeded', attempt: 2 }, { nodeId: 'node-1', nodeTitle: 'Safe title', status: 'succeeded', attempt: 1 }] }] });
    expect(JSON.stringify(result.body)).not.toMatch(/secret|untrusted/);
    expect(f.authorize.mock.calls.map(call => [call[1], call[2]])).toEqual([['parent-wf', 'view'], ['child-wf', 'view']]);
  });

  it('returns a valid parent attempt from the association event without guessing absent values', async () => {
    const f = await fixture(); f.run('attempted'); f.run('unknown');
    f.event('attempted'); f.event('unknown');
    f.raw.prepare("UPDATE flow_events SET attempt = ? WHERE json_extract(data_json, '$.childFlowId') = ?").run(2, 'attempted');
    const result = await f.get();
    expect(result.status).toBe(200);
    expect(result.body.items.find((item: { childFlowId: string }) => item.childFlowId === 'attempted').parentAttempt).toBe(2);
    expect(result.body.items.find((item: { childFlowId: string }) => item.childFlowId === 'unknown').parentAttempt).toBeNull();
  });

  it('keeps a linked attempt when a newer finish event omits it for the same parent node', async () => {
    const f = await fixture(); f.run('child'); f.event('child');
    f.raw.prepare("UPDATE flow_events SET attempt = ? WHERE event_type = ?").run(2, 'subworkflow_linked');
    f.event('child', 'subworkflow_finished');
    expect((await f.get()).body.items[0].parentAttempt).toBe(2);
  });

  it('does not borrow an attempt from an event on a different parent node', async () => {
    const f = await fixture(); f.run('child'); f.event('child', 'subworkflow_linked', 'node-a');
    f.raw.prepare("UPDATE flow_events SET attempt = ? WHERE event_type = ?").run(2, 'subworkflow_linked');
    f.event('child', 'subworkflow_finished', 'node-b');
    expect((await f.get()).body.items[0]).toMatchObject({ parentNodeId: 'node-b', parentAttempt: null });
  });

  it('omits missing, trial and independently unauthorized children without exposing their IDs', async () => {
    const f = await fixture(); f.run('allowed'); f.run('denied', 'secret-wf'); f.run('trial');
    f.event('allowed'); f.event('denied'); f.event('missing'); f.event('trial');
    f.raw.exec("INSERT INTO workflow_repair_release_records VALUES ('trial','trial_run')");
    const result = await f.get();
    expect(result.status).toBe(200); expect(result.body.items.map((x: { childFlowId: string }) => x.childFlowId)).toEqual(['allowed']);
    expect(JSON.stringify(result.body)).not.toMatch(/denied|secret-wf|missing|trial/);
    expect(f.authorize.mock.calls.map(call => call[1])).toContain('secret-wf');
  });

  it('rejects forged identities and limited parent view, omits limited child view', async () => {
    const f = await fixture(); f.run('child'); f.event('child');
    f.setAuthenticated(false); expect((await f.get()).status).toBe(404);
    f.setAuthenticated(true); f.permissions.set('parent-wf', { botIds: ['bot'] }); expect((await f.get()).status).toBe(404);
    f.permissions.set('parent-wf', 'all'); f.permissions.set('child-wf', { botIds: ['bot'] });
    expect((await f.get()).body).toEqual({ items: [], completeness: 'best_effort' });
  });

  it('returns 404 for missing or trial parent and 400 for invalid requests', async () => {
    const f = await fixture();
    expect((await f.get('/missing/subworkflows')).status).toBe(404);
    expect((await f.get('/%20/subworkflows')).status).toBe(400);
    expect((await f.get('/parent/subworkflows?limit=100000')).status).toBe(400);
    f.raw.exec("INSERT INTO workflow_repair_release_records VALUES ('parent','trial')");
    expect((await f.get()).status).toBe(404);
  });

  it('gives identical responses for missing, trial, and unauthorized parents without reading events', async () => {
    const f = await fixture();
    const queries = vi.spyOn(f.db, 'query');
    const missing = await f.get('/missing/subworkflows');
    f.permissions.set('parent-wf', 'deny');
    const denied = await f.get();
    f.raw.exec("INSERT INTO workflow_repair_release_records VALUES ('parent','trial_run')");
    const trial = await f.get();
    for (const result of [missing, denied, trial]) {
      expect(result.status).toBe(404);
      expect(result.body).toEqual({ error: 'Run not found', code: 'NOT_FOUND' });
      expect(result.headers.get('cache-control')).toBe('no-store');
    }
    expect(queries.mock.calls.every(([sql]) => !sql.includes('flow_events'))).toBe(true);
  });

  it('bounds distinct child lookups to 20 even for missing or denied runs and duplicate events', async () => {
    const f = await fixture();
    for (let i = 0; i < 25; i++) {
      f.run(`child-${i}`, i >= 23 ? 'secret-wf' : 'child-wf');
      f.event(`child-${i}`);
    }
    f.event('missing-secret');
    f.event('child-24', 'subworkflow_finished'); // duplicate must not consume a second slot
    const queries = vi.spyOn(f.db, 'query');
    const result = await f.get();
    const runQueries = queries.mock.calls.filter(([sql]) => sql.includes('FROM flow_runs'));
    const nodeQueries = queries.mock.calls.filter(([sql]) => sql.includes('FROM node_executions'));
    expect(result.status).toBe(200);
    expect(result.body.completeness).toBe('best_effort');
    expect(runQueries).toHaveLength(21); // parent plus 20 candidates, including missing and denied
    expect(runQueries.slice(1).map(([, params]) => params?.[0])).toEqual([
      'child-24', 'missing-secret', ...Array.from({ length: 18 }, (_, i) => `child-${23 - i}`),
    ]);
    expect(nodeQueries).toHaveLength(17);
    expect(nodeQueries.every(([, params]) => params?.[1] === 'child-wf')).toBe(true);
    expect(JSON.stringify(result.body)).not.toMatch(/child-24|child-23|missing-secret|secret-wf|child-5/);
  });

  it('caches allowed and denied workflow decisions within a request only and reads nodes only after authorization', async () => {
    const f = await fixture();
    for (const [id, workflow] of [['same-1', 'parent-wf'], ['same-2', 'parent-wf'], ['allowed-1', 'child-wf'], ['allowed-2', 'child-wf'], ['denied-1', 'secret-wf'], ['denied-2', 'secret-wf']]) {
      f.run(id, workflow); f.event(id); f.node(id, 1, workflow);
    }
    const queries = vi.spyOn(f.db, 'query');
    const first = await f.get();
    expect(first.body.items).toHaveLength(4);
    expect(JSON.stringify(first.body)).not.toMatch(/denied|secret-wf/);
    expect(f.authorize.mock.calls.map(call => call[1]).sort()).toEqual(['child-wf', 'parent-wf', 'secret-wf']);
    const nodeReads = queries.mock.calls.filter(([sql]) => sql.includes('FROM node_executions'));
    expect(nodeReads).toHaveLength(4);
    expect(nodeReads.every(([, params]) => params?.[1] !== 'secret-wf')).toBe(true);
    f.authorize.mockClear();
    f.permissions.set('child-wf', 'deny');
    const second = await f.get();
    expect(second.body.items.map((item: { childFlowId: string }) => item.childFlowId).sort()).toEqual(['same-1', 'same-2']);
    expect(f.authorize.mock.calls.map(call => call[1]).sort()).toEqual(['child-wf', 'parent-wf', 'secret-wf']);
  });


  it('prioritizes trusted rows, merges historical links, and suppresses conflicting or unauthorized links', async () => {
    const f = await fixture();
    for (const id of ['stored', 'legacy', 'other-parent', 'mismatch', 'denied']) f.run(id, id === 'denied' ? 'secret-wf' : 'child-wf');
    const add = f.raw.prepare('INSERT INTO flow_run_links VALUES (?, ?, ?, ?, ?, ?)');
    add.run('stored', 'parent', 'trusted-node', 3, 'child-wf', 4);
    add.run('other-parent', 'another-parent', 'node', 1, 'child-wf', 3);
    add.run('mismatch', 'parent', 'node', 1, 'wrong-wf', 2);
    add.run('denied', 'parent', 'node', 1, 'secret-wf', 1);
    for (const id of ['stored', 'legacy', 'other-parent', 'mismatch', 'denied']) f.event(id, 'subworkflow_linked', 'forged-node');
    const result = await f.get();
    expect(result.status).toBe(200);
    expect(result.body.items.map((x: { childFlowId: string }) => x.childFlowId)).toEqual(['stored', 'legacy']);
    expect(result.body.items[0]).toMatchObject({ parentNodeId: 'trusted-node', parentAttempt: 3 });
    expect(JSON.stringify(result.body)).not.toMatch(/other-parent|mismatch|denied|secret-wf/);
  });

  it('fails closed when the independent relation table is absent', async () => {
    const f = await fixture(); f.run('child'); f.event('child');
    f.raw.exec('DROP TABLE flow_run_links');
    expect(await f.get()).toMatchObject({ status: 503, body: { error: 'Observability unavailable', code: 'UNAVAILABLE' } });
  });

  it('fails closed when a required real SQLite table is unavailable', async () => {
    const f = await fixture(); f.run('child'); f.event('child');
    f.raw.exec('DROP TABLE node_executions');
    const result = await f.get();
    expect(result.status).toBe(503);
    expect(result.body).toEqual({ error: 'Observability unavailable', code: 'UNAVAILABLE' });
  });

  it('supports historical missing/malformed events, never trusts an unrelated event or node workflow', async () => {
    const f = await fixture(); f.run('child'); f.node('child', 1); f.node('child', 2, 'wrong-wf');
    f.event('child', 'subworkflow_started'); f.event('child', 'subworkflow_linked', 'bad-node', 'wrong-wf');
    f.raw.exec("INSERT INTO flow_events (flow_id, workflow_id, event_type, time, data_json) VALUES ('parent','parent-wf','subworkflow_finished',1,'not-json')");
    expect((await f.get()).body).toEqual({ items: [], completeness: 'best_effort' });
    f.event('child', 'subworkflow_finished');
    expect((await f.get()).body.items[0].nodes).toHaveLength(1);
  });

  it('fails closed without leaking database or authorization errors, including late child reads', async () => {
    const f = await fixture(); f.run('child'); f.event('child');
    const query = vi.spyOn(f.db, 'query');
    query.mockImplementationOnce(async () => { throw new Error('private database error'); });
    let result = await f.get(); expect(result.status).toBe(503); expect(JSON.stringify(result.body)).not.toContain('private');
    query.mockRestore();
    const original = f.db.query.bind(f.db);
    vi.spyOn(f.db, 'query').mockImplementation(async (sql, params) => {
      if (sql.includes('node_executions')) throw new Error('private node error');
      return original(sql, params);
    });
    result = await f.get(); expect(result.status).toBe(503); expect(result.body.items).toBeUndefined();
    vi.restoreAllMocks(); f.authorize.mockRejectedValueOnce(new Error('private permission error'));
    result = await f.get(); expect(result.status).toBe(503); expect(JSON.stringify(result.body)).not.toContain('private');
    f.authorize.mockResolvedValueOnce({ actorId: 'trusted', canEdit: false }).mockRejectedValueOnce(new Error('private child access error'));
    result = await f.get(); expect(result.status).toBe(503); expect(result.body.items).toBeUndefined();
  });

  it('bounds event and node reads and keeps only newest evidence', async () => {
    const f = await fixture(); f.run('child');
    for (let i = 0; i < 55; i++) f.node('child', i + 1);
    f.event('child');
    for (let i = 0; i < 200; i++) f.event('missing-' + i);
    expect((await f.get()).body.items).toEqual([]);
    f.event('child', 'subworkflow_finished');
    const result = await f.get(); expect(result.body.items).toHaveLength(1);
    expect(result.body.items[0].nodes).toHaveLength(50);
    expect(result.body.items[0].nodes[0].attempt).toBe(55);
  });
  it('fails closed when database or authorizer is unavailable', async () => {
    const f = await fixture();
    const app = express();
    app.use('/noop', createSubworkflowObservabilityRouter({ ...f.db, dbType: 'noop' } as IDatabase, f.authorize));
    app.use('/missing-auth', createSubworkflowObservabilityRouter(f.db, undefined as unknown as typeof f.authorize));
    const server = await new Promise<Server>(resolve => { const s = app.listen(0, '127.0.0.1', () => resolve(s)); });
    cleanup.unshift(() => new Promise<void>(resolve => server.close(() => resolve())));
    const base = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
    for (const path of ['/noop/parent/subworkflows', '/missing-auth/parent/subworkflows']) {
      const response = await fetch(base + path); expect(response.status).toBe(503);
      expect(await response.json()).toEqual({ error: 'Observability unavailable', code: 'UNAVAILABLE' });
    }
  });

  it('uses portable placeholder parameters for all variable SQL values', async () => {
    const f = await fixture(); f.run('child'); f.event('child'); f.node('child', 1);
    const queries: Array<{ sql: string; params: unknown[] }> = [];
    // Exercise the MySQL interface using the same SQL against SQLite, not a real MySQL server.
    const mysqlLike = { dbType: 'mysql', dialect: f.db.dialect, query: async (sql: string, params: unknown[] = []) => {
      queries.push({ sql, params }); return f.db.query(sql, params);
    } } as IDatabase;
    const app = express(); app.use(createSubworkflowObservabilityRouter(mysqlLike, f.authorize));
    const server = await new Promise<Server>(resolve => { const s = app.listen(0, '127.0.0.1', () => resolve(s)); });
    cleanup.unshift(() => new Promise<void>(resolve => server.close(() => resolve())));
    const response = await fetch(`http://127.0.0.1:${(server.address() as { port: number }).port}/parent/subworkflows`);
    expect(response.status).toBe(200); expect((await response.json()).items).toHaveLength(1);
    expect(queries.map(q => q.params)).toEqual([['parent'], ['parent'], ['parent', 'parent-wf'], ['child'], ['child'], ['child', 'child-wf']]);
    expect(queries.every(q => !q.sql.includes('parent-wf') && !q.sql.includes('child-wf'))).toBe(true);
  });

});
