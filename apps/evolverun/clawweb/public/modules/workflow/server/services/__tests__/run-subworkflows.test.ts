// @vitest-environment node
import Database from 'better-sqlite3';
import express from 'express';
import { once } from 'node:events';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db';
import type { RunViewScope } from '@avernet/clawweb-shared/server/services/run-view-permissions';
import { FlowRunRepository } from '../../repositories/flow-run-repository.js';
import { NodeExecutionRepository } from '../../repositories/node-execution-repository.js';
import { FlowEventRepository } from '../../repositories/event-repository.js';
import { RunSubworkflowsRepository } from '../../repositories/run-subworkflows-repository.js';
import { RunSubworkflowsService } from '../run-subworkflows-service.js';
import { createRunsRouter } from '../../routes/runs.js';

function legacyGroupFixture() {
  const child = (i: number, status: string) => ({ itemIndex: i, itemId: `item-${i}`, activeAttemptNumber: 1,
    attempts: [{ attemptNumber: 1, childFlowId: `child-${i}`, status, childExports: { result: `export-${i}` },
      sessionKey: `private-session-${i}`, stateDirRef: `private-path-${i}`,
      error: status === 'failed' ? { category: 'execution', code: 'FAILED', message: `error-${i}`, retryable: false } : null }] });
  return { schemaVersion: 'fanout.v1', kind: 'subworkflowFanoutState', parentFlowId: 'parent',
    groupNodeId: 'group', groupExecutionId: 'generation-1', revision: 17, status: 'succeeded',
    concurrency: 2, successPolicy: 'all_done', finishMode: 'wait_all', resultPolicy: 'collect_all',
    dependencySnapshot: { workflowId: 'child-workflow', publishedVersion: 1 },
    children: [child(0, 'succeeded'), child(1, 'failed'), child(2, 'cancelled')], error: null };
}

function groupFixture() {
  const old = legacyGroupFixture();
  return { schemaVersion: old.schemaVersion, kind: old.kind, parentFlowId: old.parentFlowId,
    groupNodeId: old.groupNodeId, groupExecutionId: old.groupExecutionId, revision: old.revision, status: old.status,
    config: { schemaVersion: 'fanout.v1', concurrency: 2, successPolicy: 'all_done', finishMode: 'wait_all', resultPolicy: 'collect_all',
      items: [], itemIdFrom: ['scene'], groupTimeoutSeconds: 1800, cancelGraceSeconds: 30 },
    childWorkflow: { id: 'child-workflow', title: 'Child workflow' },
    children: old.children.map(({ itemIndex, itemId, attempts }) => ({ itemIndex, itemId,
      ...attempts[0], parentFlowId: 'parent', parentNodeId: 'group', groupExecutionId: 'generation-1',
      item: { scene: itemId }, params: { scene: itemId }, depth: 1, result: attempts[0].childExports })), error: null };
}

describe('fanout HTTP reads against durable SQLite state', () => {
  let raw: Database.Database;
  let db: SqliteDatabase;
  let server: ReturnType<ReturnType<typeof express>['listen']>;
  let base: string;
  let group: ReturnType<typeof groupFixture>;
  let scopes: Map<string, RunViewScope>;
  const persist = () => raw.prepare('UPDATE flow_runs SET state_json = ? WHERE flow_id = ?')
    .run(JSON.stringify({ flowId: 'parent', subworkflowFanouts: { group } }), 'parent');
  const read = (path: string, user = 'reader') => fetch(base + path, { headers: user ? { 'x-user-id': user } : {} });
  const endpoint = '/parent/nodes/group/subworkflows?groupExecutionId=generation-1';

  beforeEach(async () => {
    raw = new Database(':memory:'); db = new SqliteDatabase(raw); group = groupFixture();
    raw.exec(`CREATE TABLE flow_runs (
      id INTEGER PRIMARY KEY, flow_id TEXT, workflow_id TEXT, workflow_title TEXT, status TEXT,
      origin_bot_id TEXT, state_json TEXT, input_json TEXT, result_json TEXT, params_json TEXT,
      credentials_json TEXT, origin_session_key TEXT, origin_session_id TEXT, started_at INTEGER,
      completed_at INTEGER, total_duration_ms INTEGER, node_count INTEGER, succeeded_count INTEGER,
      failed_count INTEGER, total_token_usage INTEGER, triggered_by TEXT, identity_key TEXT, current_phase TEXT,
      user_id TEXT, plugin_version TEXT, engine TEXT, workflow_version INTEGER, workflow_deploy_number INTEGER,
      gmt_create INTEGER, gmt_modified INTEGER, evolution_analysis_status TEXT);
      CREATE TABLE node_executions (id INTEGER PRIMARY KEY, flow_id TEXT, node_id TEXT, workflow_id TEXT,
        executor_type TEXT, node_title TEXT, status TEXT, attempt INTEGER, started_at INTEGER, completed_at INTEGER,
        input_json TEXT, output_json TEXT, error_text TEXT, system_context_json TEXT, progress_message TEXT);
      CREATE TABLE flow_events (id INTEGER PRIMARY KEY, flow_id TEXT, event_type TEXT, time INTEGER, data_json TEXT, error_text TEXT);
      INSERT INTO flow_runs (flow_id, workflow_id, origin_bot_id, status, state_json, started_at, node_count)
        VALUES ('parent','parent-workflow','bot:owner-a','succeeded','{}',1,1),
        ('child-0','child-workflow','bot:owner-a','succeeded','{}',2,2),
        ('child-1','child-workflow','bot:owner-b','failed','{}',3,2),
        ('child-2','child-workflow','bot:owner-a','cancelled','{}',4,2);
      INSERT INTO node_executions (flow_id,node_id,executor_type,status,attempt,started_at,output_json)
        VALUES ('parent','group','subworkflow','succeeded',1,1,'hidden-child-1'),
        ('child-0','launch','cli-script','succeeded',1,1,'visible-output'),
        ('child-1','secret-node','cli-script','failed',1,1,'hidden-child-1');
      INSERT INTO flow_events (flow_id,event_type,time,data_json,error_text)
        VALUES ('parent','subworkflow.child.failed',1,'hidden-child-1','hidden-child-1'),
        ('child-1','failed',1,'hidden-child-1','hidden-child-1');
      UPDATE flow_runs SET result_json='hidden-child-1', input_json='hidden-child-1', params_json='hidden-child-1' WHERE flow_id='parent';`);
    persist();
    scopes = new Map([['parent-workflow', 'all'], ['child-workflow', { bots: [{ botId: 'bot', ownerId: 'owner-a' }] }]]);
    const permissions = {
      resolveRunViewScope: async (workflow: string, user: string) => user === 'reader' ? scopes.get(workflow) ?? 'deny' : 'deny',
      listRunViewScopes: async (user: string) => user === 'reader' ? scopes : new Map(),
      getViewByIdsForOwner: async () => ({ restrictedIds: new Set(scopes.keys()), viewableIds: new Set(scopes.keys()) }),
      resolveViewScope: async () => 'all',
    };
    const app = express();
    app.use('/api/runs', createRunsRouter(new FlowRunRepository(db), new NodeExecutionRepository(db),
      new FlowEventRepository(db), null, null, null, permissions as never, null, null,
      new RunSubworkflowsService(new RunSubworkflowsRepository(db), permissions)));
    server = app.listen(0, '127.0.0.1'); await once(server, 'listening');
    base = `http://127.0.0.1:${(server.address() as import('node:net').AddressInfo).port}/api/runs`;
  });
  afterEach(async () => { await new Promise<void>(resolve => server.close(() => resolve())); raw.close(); vi.restoreAllMocks(); });

  it('authorizes child workflow AND full origin owner, filters before counts and removes index holes', async () => {
    const response = await read(endpoint + '&limit=1'); expect(response.status).toBe(200);
    const body = await response.json();
    expect(body).toMatchObject({ revision: 17, countScope: 'visible', groupTotal: null, visibleTotal: 2,
      returnedCount: 1, status: 'succeeded', concurrency: 2, successPolicy: 'all_done',
      counts: { successCount: 1, failedCount: 0, pendingCount: 0, cancelledCount: 1 } });
    expect(body.children[0]).toMatchObject({ displayIndex: 0, itemId: 'item-0', childFlowId: 'child-0' });
    expect(body.children[0]).not.toHaveProperty('itemIndex');
    expect(JSON.stringify(body)).not.toMatch(/child-1|item-1|error-1|export-1|private-session|private-path/);
    const next = await read(endpoint + '&limit=1&cursor=' + encodeURIComponent(body.nextCursor));
    expect(next.status).toBe(200);
    expect(await next.json()).toMatchObject({ visibleTotal: 2, returnedCount: 1, nextCursor: null,
      children: [{ displayIndex: 1, childFlowId: 'child-2' }] });
  });
  it('also accepts the approved legacy attempts snapshot and only counts the active attempt', async () => {
    const legacy = legacyGroupFixture();
    legacy.children[0].attempts.push({ ...legacy.children[0].attempts[0], attemptNumber: 2, childFlowId: 'child-new', status: 'failed' });
    raw.prepare('UPDATE flow_runs SET state_json=? WHERE flow_id=?').run(JSON.stringify({ subworkflowFanouts: { group: legacy } }), 'parent');
    expect(await (await read(endpoint)).json()).toMatchObject({ visibleTotal: 2, counts: { successCount: 1, failedCount: 0 } });
  });
  it('does not reveal child IDs or their counts in the global run list with a forged owner', async () => {
    const response = await read('/?botOwnerId=owner-b&workflowId=child-workflow&limit=1');
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body).toMatchObject({ total: 2, statusCounts: { succeeded: 1, cancelled: 1 } });
    expect(JSON.stringify(body)).not.toContain('child-1');
  });
  it('does not grant direct child access when the workflow, origin or owner is unproven', async () => {
    raw.prepare("UPDATE flow_runs SET origin_bot_id=NULL WHERE flow_id='child-0'").run();
    expect((await read('/child-0')).status).toBe(403);
    expect(await (await read(endpoint)).json()).toMatchObject({ visibleTotal: 1 });
    scopes.set('child-workflow', 'deny');
    expect(await (await read(endpoint)).json()).toMatchObject({ visibleTotal: 0, groupTotal: null, children: [] });
  });
  it.each(['steps', 'replay', 'dynamic', 'topology'])('blocks raw parent %s when child view is restricted', async path => {
    expect((await read('/parent/' + path)).status).toBe(403);
  });
  it('requires full workflow grants for full statistics, not merely visibility of every current row', async () => {
    raw.prepare("UPDATE flow_runs SET origin_bot_id='bot:owner-a' WHERE flow_id='child-1'").run();
    expect(await (await read(endpoint)).json()).toMatchObject({ countScope: 'visible', groupTotal: null, visibleTotal: 3 });
    scopes.set('child-workflow', 'all');
    expect(await (await read(endpoint)).json()).toMatchObject({ countScope: 'full', groupTotal: 3, visibleTotal: 3,
      counts: { successCount: 1, failedCount: 1, cancelledCount: 1 } });
  });
  it('rejects anonymous/forged owner and denied parent before exposing group or child identity', async () => {
    expect((await read(endpoint + '&botOwnerId=reader', '')).status).toBe(401);
    scopes.set('parent-workflow', 'deny');
    const response = await read(endpoint); expect(response.status).toBe(403);
    expect(await response.text()).not.toMatch(/child-|item-|generation-1/);
  });
  it.each(['/child-1', '/child-1/nodes?full=true', '/child-1/events'])('closes direct child bypass %s', async path => {
    const response = await read(path + (path.includes('?') ? '&' : '?') + 'botOwnerId=owner-b');
    expect(response.status).toBe(403); expect(await response.text()).not.toContain('hidden-child-1');
  });
  it.each(['/parent', '/parent/nodes?full=true', '/parent/events'])('does not leak hidden children via %s', async path => {
    const response = await read(path); expect(response.status).toBe(200);
    const text = await response.text(); expect(text).not.toMatch(/hidden-child-1|child-1|item-1|error-1|export-1|private-session|private-path/);
  });
  it('keeps authorized child summary and node list readable', async () => {
    const response = await read('/child-0'); expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({ run: { flow_id: 'child-0', status: 'succeeded', node_count: 2 },
      nodes: [{ node_id: 'launch', status: 'succeeded' }] });
  });
  it('distinguishes missing state, empty fanout, bad generation and corrupt persistence', async () => {
    group.children = []; persist();
    expect(await (await read(endpoint)).json()).toMatchObject({ visibleTotal: 0, children: [], returnedCount: 0 });
    expect((await read(endpoint.replace('generation-1', 'other'))).status).toBe(409);
    group.parentFlowId = 'other'; persist(); expect((await read(endpoint)).status).toBe(409);
    raw.prepare("UPDATE flow_runs SET state_json='{}' WHERE flow_id='parent'").run();
    expect((await read(endpoint)).status).toBe(404);
    raw.prepare("UPDATE flow_runs SET state_json='{broken' WHERE flow_id='parent'").run();
    expect((await read(endpoint)).status).toBe(500);
  });
  it('pins cursors to revision and authorization and reads the parent state only once', async () => {
    const query = vi.spyOn(db, 'query');
    const first = await (await read(endpoint + '&limit=1')).json();
    expect(query.mock.calls.filter(([sql, args]) => sql.includes('state_json') && args?.includes('parent'))).toHaveLength(1);
    group.revision++; persist();
    expect((await read(endpoint + '&cursor=' + encodeURIComponent(first.nextCursor))).status).toBe(409);
    group.revision--; persist(); scopes.set('child-workflow', 'all');
    expect((await read(endpoint + '&cursor=' + encodeURIComponent(first.nextCursor))).status).toBe(409);
  });
  it.each(['', '&limit=0', '&limit=101', '&limit=1.5', '&cursor=garbage'])('validates input %s', async suffix => {
    expect((await read(suffix ? endpoint + suffix : '/parent/nodes/group/subworkflows')).status).toBe(400);
  });
  it('validates each flat child identity, not just its containing group', async () => {
    group.children[0].groupExecutionId = 'other-generation'; persist();
    expect((await read(endpoint)).status).toBe(409);
    group.children[0].groupExecutionId = 'generation-1'; group.children[0].parentFlowId = 'other-parent'; persist();
    expect((await read(endpoint)).status).toBe(409);
    group.children[0].parentFlowId = 'parent'; group.children[0].parentNodeId = 'other-node'; persist();
    expect((await read(endpoint)).status).toBe(409);
  });
  it('denies the parent before parsing its corrupt state or reading any children', async () => {
    scopes.set('parent-workflow', 'deny');
    raw.prepare("UPDATE flow_runs SET state_json='{invalid' WHERE flow_id='parent'").run();
    const query = vi.spyOn(db, 'query');
    expect((await read(endpoint)).status).toBe(403);
    expect(query.mock.calls.filter(([sql]) => sql.includes('flow_id IN'))).toHaveLength(0);
  });
  it('keeps durable counters/outputs untouched and never selects credential values', async () => {
    const before = raw.prepare('SELECT total_changes() AS count').get();
    const query = vi.spyOn(db, 'query');
    expect((await read(endpoint)).status).toBe(200);
    expect(raw.prepare('SELECT total_changes() AS count').get()).toEqual(before);
    expect(query.mock.calls.every(([sql]) => /^SELECT\b/.test(sql.trim()) && !sql.includes('credentials_json'))).toBe(true);
  });
  it('fails closed when a child identity lookup fails instead of reporting an empty group', async () => {
    const actual = db.query.bind(db);
    vi.spyOn(db, 'query').mockImplementation((sql, args) => {
      if (sql.includes('flow_id IN')) throw new Error('private SQL error hidden-child-1');
      return actual(sql, args);
    });
    const response = await read(endpoint); expect(response.status).toBe(500);
    expect(await response.text()).not.toMatch(/private SQL|hidden-child/);
  });
  it('only accepts the explicit all-owner wildcard and keeps its statistics visible-scoped', async () => {
    scopes.set('child-workflow', { bots: [{ botId: 'bot', ownerId: '*' }] });
    expect(await (await read(endpoint)).json()).toMatchObject({ countScope: 'visible', visibleTotal: 3, groupTotal: null });
    scopes.set('child-workflow', { bots: [{ botId: 'bo', ownerId: '*' }] });
    expect(await (await read(endpoint)).json()).toMatchObject({ visibleTotal: 0 });
  });
  it('does not treat an empty owner suffix as an all-owner grant in collection reads', async () => {
    scopes.set('child-workflow', { bots: [{ botId: 'bot', ownerId: '*' }] });
    raw.prepare("UPDATE flow_runs SET origin_bot_id='bot:' WHERE flow_id='child-0'").run();
    expect((await read('/child-0')).status).toBe(403);
    const response = await read('/?workflowId=child-workflow');
    expect(await response.json()).toMatchObject({ total: 2 });
  });
  it.each(['groupExecutionId=generation-1&groupExecutionId=other', 'groupExecutionId=generation-1&limit=1&limit=2'])('rejects repeated query values %s', async query => {
    expect((await read('/parent/nodes/group/subworkflows?' + query)).status).toBe(400);
  });
  it('scopes workflow counts and status by full owner and ignores query impersonation', async () => {
    const response = await read('/workflow-types?botOwnerId=owner-b'); expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.workflows.find((row: { workflow_id: string }) => row.workflow_id === 'child-workflow')).toMatchObject({ run_count: 2, last_status: 'cancelled' });
    expect((await read('/workflow-types?botOwnerId=reader', '')).status).toBe(401);
  });
  it('does not let raw input search infer hidden child data from a parent', async () => {
    const response = await read('/?workflowId=parent-workflow&query=hidden-child-1');
    expect(await response.json()).toMatchObject({ total: 0, runs: [] });
    const input = await read('/?workflowId=parent-workflow&inputQuery=hidden-child-1');
    expect(await input.json()).toMatchObject({ total: 0, runs: [] });
  });
  it.each(['/child-1', '/child-1/nodes?full=true', '/child-1/events'])('authorizes HEAD exactly like GET on %s', async path => {
    expect((await fetch(base + path, { method: 'HEAD' })).status).toBe(401);
    const denied = await fetch(base + path, { method: 'HEAD', headers: { 'x-user-id': 'reader' } });
    expect(denied.status).toBe(403);
    expect(denied.headers.get('cache-control')).toBe('private, no-store');
  });
  it.each(['/parent', '/parent/nodes?full=true', '/parent/events'])('HEAD metadata uses the same redacted representation on %s', async path => {
    const get = await read(path); expect(get.status).toBe(200);
    const head = await fetch(base + path, { method: 'HEAD', headers: { 'x-user-id': 'reader' } });
    expect(head.status).toBe(200); expect(head.headers.get('cache-control')).toBe('private, no-store');
    expect(head.headers.get('content-length')).toBe(get.headers.get('content-length'));
    expect(head.headers.get('etag')).toBe(get.headers.get('etag'));
    expect(await head.text()).toBe('');
  });
  it('supports a legacy schema missing only evolution_analysis_status on list, detail and fanout reads', async () => {
    raw.exec('ALTER TABLE flow_runs DROP COLUMN evolution_analysis_status');
    const list = await read('/?workflowId=child-workflow'); expect(list.status).toBe(200);
    expect(await list.json()).toMatchObject({ total: 2, runs: [{ flow_id: 'child-2' }, { flow_id: 'child-0' }] });
    const detail = await read('/child-0'); expect(detail.status).toBe(200);
    expect(await detail.json()).toMatchObject({ run: { flow_id: 'child-0', status: 'succeeded' } });
    const fanout = await read(endpoint); expect(fanout.status).toBe(200);
    expect(await fanout.json()).toMatchObject({ visibleTotal: 2 });
  });
  it.each(['connection lost reading evolution_analysis_status', 'no such column: evolution_analysis_status_backup'])('does not fall back for unrelated storage error %s', async message => {
    const query = vi.spyOn(db, 'query').mockRejectedValue(new Error(message));
    const response = await read('/child-0'); expect(response.status).toBe(500);
    expect(query).toHaveBeenCalledTimes(1);
    expect(await response.text()).not.toContain('connection lost');
  });
  it('uses the same narrow fallback for MySQL missing-optional-column errors', async () => {
    const actual = db.query.bind(db);
    vi.spyOn(db, 'query').mockImplementation((sql, args) => {
      if (sql.includes('SELECT') && sql.includes('evolution_analysis_status')) {
        throw new Error("Unknown column 'evolution_analysis_status' in 'field list'");
      }
      return actual(sql, args);
    });
    expect((await read('/child-0')).status).toBe(200);
    expect(await (await read('/?workflowId=child-workflow')).json()).toMatchObject({ total: 2 });
  });
  it('returns failure rather than empty children when storage fails', async () => {
    raw.exec('DROP TABLE flow_runs'); expect((await read(endpoint)).status).toBe(500);
  });
});
