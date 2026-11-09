// @vitest-environment node
import Database from 'better-sqlite3';
import express from 'express';
import { once } from 'node:events';
import { createHash } from 'node:crypto';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db';
import type { RunViewScope } from '@avernet/clawweb-shared/server/services/run-view-permissions';
import { FlowRunRepository } from '../../repositories/flow-run-repository.js';
import { NodeExecutionRepository } from '../../repositories/node-execution-repository.js';
import { FlowEventRepository } from '../../repositories/event-repository.js';
import { RunSubworkflowsRepository } from '../../repositories/run-subworkflows-repository.js';
import { RunSubworkflowsService } from '../run-subworkflows-service.js';
import { createRunsRouter } from '../../routes/runs.js';
import { createInternalRunsRouter } from '../../routes/internal/runs.js';

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
      ...attempts[0], nativeFlowId: attempts[0].childFlowId, sessionKey: `fanout:${attempts[0].childFlowId}`, stateDirRef: `fanout-state:${attempts[0].childFlowId}`,
      parentFlowId: 'parent', parentNodeId: 'group', groupExecutionId: 'generation-1',
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
  const persistLegacy = (legacy = legacyGroupFixture()) => raw.prepare('UPDATE flow_runs SET state_json=? WHERE flow_id=?')
    .run(JSON.stringify({ subworkflowFanouts: { group: legacy } }), 'parent');
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
      gmt_create INTEGER, gmt_modified INTEGER, evolution_analysis_status TEXT, ordinary_run_marker TEXT, parent_run_marker TEXT, child_run_marker TEXT, extension_field TEXT);
      CREATE TABLE ordinary_run_requests (request_key_hash TEXT PRIMARY KEY, parent_flow_id TEXT, child_flow_id TEXT,
        child_workflow_id TEXT, parent_run_marker TEXT, child_run_marker TEXT);
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
    raw.prepare('UPDATE flow_runs SET ordinary_run_marker=? WHERE flow_id=?').run('a'.repeat(64), 'parent');
    for (const child of group.children) {
      raw.prepare('UPDATE flow_runs SET ordinary_run_marker=? WHERE flow_id=?').run('b'.repeat(64), child.childFlowId);
      raw.prepare('INSERT INTO ordinary_run_requests VALUES (?,?,?,?,?,?)').run(
        createHash('sha256').update(child.childFlowId).digest('hex'), 'parent', child.childFlowId, 'child-workflow', 'a'.repeat(64), 'b'.repeat(64));
      const identity = { parentFlowId: 'parent', parentNodeId: 'group', groupExecutionId: 'generation-1',
        childFlowId: child.childFlowId, workflowId: 'child-workflow', itemId: child.itemId,
        itemIndex: child.itemIndex, attemptNumber: child.attemptNumber, sessionKey: child.sessionKey, stateDirRef: child.stateDirRef };
      raw.prepare('UPDATE flow_runs SET state_json=?, origin_session_key=? WHERE flow_id=?').run(
        JSON.stringify({ _fanoutIdentity: identity, _fanoutRevision: 1, _fanoutStarted: true,
          subworkflowMeta: identity }), child.sessionKey, child.childFlowId);
    }
    persist();
    scopes = new Map([['parent-workflow', 'all'], ['child-workflow', { bots: [{ botId: 'bot', ownerId: 'owner-a' }] }]]);
    const permissions = {
      resolveRunViewScope: async (workflow: string, user: string) => user === 'reader' ? scopes.get(workflow) ?? 'deny' : 'deny',
      listRunViewScopes: async (user: string) => user === 'reader' ? scopes : new Map(),
      getViewByIdsForOwner: async () => ({ restrictedIds: new Set(scopes.keys()), viewableIds: new Set(scopes.keys()) }),
      resolveViewScope: async () => 'all',
    };
    const app = express();
    app.use(express.json());
    app.use('/api/runs', createRunsRouter(new FlowRunRepository(db), new NodeExecutionRepository(db),
      new FlowEventRepository(db), null, null, null, permissions as never, null, null,
      new RunSubworkflowsService(new RunSubworkflowsRepository(db), permissions)));
    app.use('/internal', createInternalRunsRouter(new FlowRunRepository(db)));
    app.use('/legacy', createRunsRouter(new FlowRunRepository(db), new NodeExecutionRepository(db),
      new FlowEventRepository(db), null, null));
    server = app.listen(0, '127.0.0.1'); await once(server, 'listening');
    base = `http://127.0.0.1:${(server.address() as import('node:net').AddressInfo).port}/api/runs`;
  });
  afterEach(async () => { await new Promise<void>(resolve => server.close(() => resolve())); raw.close(); vi.restoreAllMocks(); });

  it('authorizes child workflow AND full origin owner, filters before counts and removes index holes', async () => {
    persistLegacy();
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
  it('preserves the approved attempts-format identity even if an extraneous native field is present', async () => {
    const legacy = legacyGroupFixture();
    Object.assign(legacy.children[0].attempts[0], { nativeFlowId: 'child-2' });
    raw.prepare('UPDATE flow_runs SET state_json=? WHERE flow_id=?').run(JSON.stringify({ subworkflowFanouts: { group: legacy } }), 'parent');
    const response = await read(endpoint); expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.children.map((child: { childFlowId: string }) => child.childFlowId)).toEqual(['child-0', 'child-2']);
  });
  it('serves matched ClawMind children through HTTP without bypassing child owner or exposing origin state', async () => {
    for (const child of group.children) {
      const origin = { parentFlowId: 'parent', parentNodeId: 'group', groupExecutionId: 'generation-1',
        itemId: child.itemId, itemIndex: child.itemIndex, attemptNumber: child.attemptNumber,
        childFlowId: child.childFlowId, workflowId: 'child-workflow', requestDigest: 'private-digest' };
      raw.prepare('UPDATE flow_runs SET state_json=?, credentials_json=?, params_json=?, input_json=?, result_json=?, origin_session_key=?, origin_session_id=? WHERE flow_id=?')
        .run(JSON.stringify({ fanoutOrigin: origin, privateValue: 'child-state-secret', businessStatus: 'RUNNING', currentPhase: 'P1', activeNodes: ['work'], nodeStates: { work: { status: 'running', result: 'node-secret' } } }),
          'credential-secret', 'params-secret', 'input-secret', 'result-secret', 'session-key-secret', 'session-id-secret', child.childFlowId);
    }
    persist();
    const response = await read(endpoint); expect(response.status).toBe(200);
    const page = await response.json();
    expect(page).toMatchObject({ countScope: 'visible', groupTotal: null, visibleTotal: 2,
      children: [{ childFlowId: 'child-0' }, { childFlowId: 'child-2' }] });
    expect(JSON.stringify(page)).not.toMatch(/child-1|item-1|private-digest|child-state-secret|credential-secret|fanoutOrigin/);
    raw.prepare('UPDATE node_executions SET input_json=?, output_json=?, error_text=?, system_context_json=?, progress_message=? WHERE flow_id=?')
      .run('node-input-secret', 'node-output-secret', 'node-error-secret', 'node-context-secret', 'node-progress-secret', 'child-0');
    raw.prepare('UPDATE flow_runs SET status=? WHERE flow_id=?').run('failed', 'child-0');
    raw.prepare('INSERT INTO flow_events (flow_id,event_type,time,data_json,error_text) VALUES (?,?,?,?,?)')
      .run('child-0', 'node.error', 8, 'event-data-secret', 'event-error-secret');
    const storedSession = raw.prepare('SELECT origin_session_key,origin_session_id FROM flow_runs WHERE flow_id=?').get('child-0');
    scopes.set('parent-workflow', 'deny'); // Child access must not depend on permission to its parent.
    const child = await read('/child-0'); expect(child.status).toBe(200);
    const body = await child.json(); expect(JSON.parse(body.run.state_json)).toEqual({ businessStatus: 'RUNNING', currentPhase: 'P1', activeNodes: ['work'], nodeStates: { work: { status: 'running' } } });
    expect(JSON.stringify(body)).not.toMatch(/private-digest|child-state-secret|credential-secret|params-secret|input-secret|result-secret|session-key-secret|session-id-secret|fanoutOrigin|node-secret/);
    expect(body.nodes).toMatchObject([{ node_id: 'launch', status: 'succeeded' }]);
    expect(JSON.stringify(body.nodes)).not.toMatch(/node-input-secret|node-output-secret|node-error-secret|node-context-secret|node-progress-secret/);
    for (const suffix of ['/child-0/nodes', '/child-0/nodes?full=true']) {
      const nodesResponse = await read(suffix); expect(nodesResponse.status).toBe(200);
      expect(JSON.stringify(await nodesResponse.json())).not.toMatch(/node-input-secret|node-output-secret|node-error-secret|node-context-secret|node-progress-secret/);
    }
    expect((await (await read('/child-0/events')).json())).toEqual([]);
    expect((await read('/child-0/steps')).status).toBe(403);
    expect((await read('/child-0/replay')).status).toBe(403);
    const interventionResponse = await read('/child-0/interventions'); expect(interventionResponse.status).toBe(200);
    const intervention = await interventionResponse.json();
    expect(intervention).toMatchObject({ canIntervene: false, availableInterventions: [], interventionReady: false,
      interventionReadOnly: true, sessionConfigured: true, originSessionKey: null, originSessionId: null, hasCredentials: null });
    expect(body.availableInterventions).toEqual([]);
    expect(JSON.stringify(intervention)).not.toMatch(/session-key-secret|session-id-secret/);
    expect(raw.prepare('SELECT origin_session_key,origin_session_id FROM flow_runs WHERE flow_id=?').get('child-0')).toEqual(storedSession);
    raw.prepare('UPDATE flow_runs SET origin_session_key=NULL, origin_session_id=NULL WHERE flow_id=?').run('child-2');
    expect(await (await read('/child-2/interventions')).json()).toMatchObject({ interventionReadOnly: true,
      sessionConfigured: false, interventionReady: false, originSessionKey: null });
    expect((await read('/child-1')).status).toBe(403);
    expect((await read('/child-1/events')).status).toBe(403);
    expect((await read('/child-1/interventions')).status).toBe(403);
  });
  it('preserves ordinary run nodes, events, and intervention session fields', async () => {
    raw.prepare('UPDATE flow_runs SET state_json=?, status=?, origin_session_key=?, origin_session_id=? WHERE flow_id=?')
      .run('{}', 'failed', 'ordinary-session-key', 'ordinary-session-id', 'child-0');
    raw.prepare('UPDATE node_executions SET output_json=? WHERE flow_id=?').run('ordinary-output', 'child-0');
    raw.prepare('INSERT INTO flow_events (flow_id,event_type,time,data_json) VALUES (?,?,?,?)')
      .run('child-0', 'ordinary-event', 9, 'ordinary-event-data');
    expect((await (await read('/child-0')).json()).nodes[0].output_json).toBe('ordinary-output');
    expect(await (await read('/child-0/events')).json()).toMatchObject([{ data_json: 'ordinary-event-data' }]);
    expect(await (await read('/child-0/interventions')).json()).toMatchObject({ interventionReady: true,
      originSessionKey: 'ordinary-session-key', originSessionId: 'ordinary-session-id', canIntervene: true });
  });
  it('does not expose flat native IDs without trusted group and item provenance', async () => {
    Object.assign(group.children[0], { nativeFlowId: 'native-0', sessionKey: undefined, stateDirRef: undefined });
    Object.assign(group.children[1], { nativeFlowId: 'native-1', sessionKey: undefined, stateDirRef: undefined });
    Object.assign(group.children[2], { nativeFlowId: 'native-2', sessionKey: undefined, stateDirRef: undefined });
    raw.exec(`INSERT INTO flow_runs (flow_id,workflow_id,origin_bot_id,status,started_at) VALUES
      ('native-0','child-workflow','bot:owner-a','running',5),
      ('native-1','child-workflow','bot:owner-b','running',6),
      ('native-2','foreign-workflow','bot:owner-a','running',7)`);
    for (let i = 0; i < 3; i++) {
      raw.prepare('UPDATE flow_runs SET ordinary_run_marker=? WHERE flow_id=?').run('b'.repeat(64), `native-${i}`);
      raw.prepare('UPDATE ordinary_run_requests SET child_flow_id=? WHERE request_key_hash=?')
        .run(`native-${i}`, createHash('sha256').update(`child-${i}`).digest('hex'));
    }
    persist();
    const body = await (await read(endpoint)).json();
    expect(body).toMatchObject({ countScope: 'visible', groupTotal: null, visibleTotal: 0,
      children: [], counts: { successCount: 0, pendingCount: 0 } });
    expect(await (await read('/native-0')).json()).toMatchObject({ run: { flow_id: 'native-0', status: 'running' } });
    expect(JSON.stringify(body)).not.toMatch(/child-[012]|native-[012]|item-[012]/);
  });
  it('hides unbound and ambiguous new items even when association keys name authorized runs', async () => {
    scopes.set('child-workflow', 'all');
    Object.assign(group.children[0], { nativeFlowId: null, sessionKey: undefined, stateDirRef: undefined });
    Object.assign(group.children[1], { sessionKey: undefined, stateDirRef: undefined });
    delete (group.children[1] as { nativeFlowId?: string }).nativeFlowId;
    Object.assign(group.children[2], { nativeFlowId: null });
    persist();
    const before = raw.prepare('SELECT state_json FROM flow_runs WHERE flow_id=?').get('parent');
    const query = vi.spyOn(db, 'query');
    const body = await (await read(endpoint)).json();
    expect(query.mock.calls.filter(([sql]) => sql.includes('flow_id IN'))).toHaveLength(0);
    expect(body).toMatchObject({ countScope: 'visible', groupTotal: null, visibleTotal: 0, children: [] });
    expect(raw.prepare('SELECT state_json FROM flow_runs WHERE flow_id=?').get('parent')).toEqual(before);
    expect((await read('/parent/steps')).status).toBe(403);
  });
  it('hides a flat item with missing native ID even when its forged checkpoint looks legacy', async () => {
    scopes.set('child-workflow', 'all');
    delete (group.children[0] as { nativeFlowId?: string }).nativeFlowId;
    persist();
    const body = await (await read(endpoint)).json();
    expect(body).toMatchObject({ countScope: 'visible', groupTotal: null, visibleTotal: 0 });
    expect(JSON.stringify(body)).not.toContain('child-0');
  });
  it('fails closed for duplicate native identities and invalid native identity values', async () => {
    Object.assign(group.children[0], { nativeFlowId: 'child-0' });
    Object.assign(group.children[1], { nativeFlowId: 'child-0' }); persist();
    expect((await read(endpoint)).status).toBe(500);
    Object.assign(group.children[1], { nativeFlowId: '' }); persist();
    expect((await read(endpoint)).status).toBe(500);
  });
  it('invalidates an approved legacy attempts cursor when child authorization changes', async () => {
    persistLegacy();
    const first = await (await read(endpoint + '&limit=1')).json();
    expect(first.children[0].childFlowId).toBe('child-0');
    raw.prepare("UPDATE flow_runs SET origin_bot_id='bot:owner-b' WHERE flow_id='child-2'").run();
    expect((await read(endpoint + '&cursor=' + encodeURIComponent(first.nextCursor))).status).toBe(409);
  });
  it('rejects copied groups with the same flat association key on HTTP and never returns child links', async () => {
    const copy = structuredClone(group);
    copy.groupNodeId = 'group-copy'; copy.groupExecutionId = 'generation-copy';
    copy.children.forEach(child => { child.parentNodeId = 'group-copy'; child.groupExecutionId = 'generation-copy'; });
    raw.prepare('UPDATE flow_runs SET state_json=? WHERE flow_id=?').run(
      JSON.stringify({ flowId: 'parent', subworkflowFanouts: { group, 'group-copy': copy } }), 'parent');
    for (const path of [endpoint, '/parent/nodes/group-copy/subworkflows?groupExecutionId=generation-copy', '/parent']) {
      const response = await read(path); expect(response.status).toBe(500);
      expect(await response.text()).not.toMatch(/child-[012]|item-[012]/);
    }
  });
  it('does not reveal child IDs or their counts in the global run list with a forged owner', async () => {
    const response = await read('/?botOwnerId=owner-b&workflowId=child-workflow&limit=1');
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body).toMatchObject({ total: 2, statusCounts: { succeeded: 1, cancelled: 1 } });
    expect(JSON.stringify(body)).not.toContain('child-1');
  });
  it('does not grant direct child access when the workflow, origin or owner is unproven', async () => {
    persistLegacy();
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
    persistLegacy();
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
  it('never exposes ordinary association markers in managed or ordinary HTTP run details', async () => {
    raw.prepare('UPDATE flow_runs SET parent_run_marker=?, child_run_marker=? WHERE flow_id=?')
      .run('private-parent-marker', 'private-child-marker', 'child-0');
    for (const url of [base + '/parent', base + '/child-0', base.replace('/api/runs', '/legacy') + '/child-0']) {
      const response = await fetch(url, { headers: { 'x-user-id': 'reader' } });
      expect(response.status).toBe(200);
      const body = await response.json();
      expect(body.run).not.toHaveProperty('ordinary_run_marker');
      expect(body.run).not.toHaveProperty('parent_run_marker');
      expect(body.run).not.toHaveProperty('child_run_marker');
      expect(JSON.stringify(body)).not.toMatch(/private-parent-marker|private-child-marker|a{64}|b{64}/);
    }
  });
  it('does not expose an ordinary run marker through the internal run read route', async () => {
    const response = await fetch(base.replace('/api/runs', '/internal') + '/child-0');
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.data).not.toHaveProperty('ordinary_run_marker');
  });
  it.each(['created', 'existing'] as const)('redacts markers from the legacy internal POST %s response without dropping other fields', async result => {
    raw.prepare('UPDATE flow_runs SET parent_run_marker=?, child_run_marker=?, credentials_json=? WHERE flow_id=?')
      .run('parent-private', 'child-private', '{"keep":"credential"}', 'child-0');
    vi.spyOn(FlowRunRepository.prototype, 'insertForRequest').mockResolvedValue(result === 'existing' ? 'existing' : null);
    vi.spyOn(FlowRunRepository.prototype, 'insert').mockResolvedValue(true);
    const response = await fetch(base.replace('/api/runs', '/internal'), {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ flow_id: 'child-0', workflow_id: 'child-workflow', status: 'running' }),
    });
    expect(response.status).toBe(result === 'existing' ? 200 : 201);
    const body = await response.json();
    expect(body.data).toMatchObject({ flow_id: 'child-0', credentials_json: '{"keep":"credential"}' });
    for (const key of ['ordinary_run_marker', 'parent_run_marker', 'child_run_marker']) expect(body.data).not.toHaveProperty(key);
  });
  it('redacts markers from a real legacy POST insert while preserving the stored marker and run fields', async () => {
    raw.exec(`CREATE TABLE workflow_repair_release_records (record_kind TEXT, record_id TEXT, body_json TEXT);
      CREATE TRIGGER set_marker AFTER INSERT ON flow_runs WHEN NEW.flow_id = 'new-legacy-run'
      BEGIN UPDATE flow_runs SET ordinary_run_marker = 'private-marker' WHERE id = NEW.id; END`);
    const response = await fetch(base.replace('/api/runs', '/internal'), {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ flow_id: 'new-legacy-run', workflow_id: 'child-workflow', status: 'running', credentials_json: '{"keep":true}' }),
    });
    expect(response.status).toBe(201);
    const body = await response.json();
    expect(body.data).toMatchObject({ flow_id: 'new-legacy-run', workflow_id: 'child-workflow', credentials_json: '{"keep":true}' });
    expect(body.data).not.toHaveProperty('ordinary_run_marker');
    expect(raw.prepare('SELECT ordinary_run_marker FROM flow_runs WHERE flow_id=?').get('new-legacy-run'))
      .toEqual({ ordinary_run_marker: 'private-marker' });
  });
  it('supports a real legacy POST on schemas without optional marker or analysis columns', async () => {
    raw.exec(`ALTER TABLE flow_runs DROP COLUMN ordinary_run_marker;
      ALTER TABLE flow_runs DROP COLUMN evolution_analysis_status;
      CREATE TABLE workflow_repair_release_records (record_kind TEXT, record_id TEXT, body_json TEXT);`);
    const response = await fetch(base.replace('/api/runs', '/internal'), {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ flow_id: 'old-schema-run', workflow_id: 'child-workflow', status: 'running' }),
    });
    expect(response.status).toBe(201);
    const body = await response.json();
    expect(body.data).toMatchObject({ flow_id: 'old-schema-run', workflow_id: 'child-workflow' });
    expect(body.data).not.toHaveProperty('ordinary_run_marker');
  });
  it('preserves full repository fields except internal markers while hiding unproven associations', async () => {
    raw.prepare('UPDATE flow_runs SET parent_run_marker=?, child_run_marker=?, credentials_json=?, extension_field=? WHERE flow_id=?')
      .run('parent-private', 'child-private', '{"keep":"credential"}', 'keep-extension', 'child-0');
    const row = await new FlowRunRepository(db).findFullByFlowId('child-0');
    expect(row).toMatchObject({ flow_id: 'child-0', credentials_json: '{"keep":"credential"}',
      origin_bot_id: 'bot:owner-a', extension_field: 'keep-extension' });
    for (const key of ['ordinary_run_marker', 'parent_run_marker', 'child_run_marker']) expect(row).not.toHaveProperty(key);
    expect(await (await read(endpoint)).json()).toMatchObject({ countScope: 'visible', visibleTotal: 0, children: [] });
    expect((raw.prepare('SELECT ordinary_run_marker FROM flow_runs WHERE flow_id=?').get('child-0') as { ordinary_run_marker: string }).ordinary_run_marker).toBe('b'.repeat(64));
  });
  it('reads ordinary internal and legacy public details when optional columns do not exist', async () => {
    raw.exec('ALTER TABLE flow_runs DROP COLUMN ordinary_run_marker; ALTER TABLE flow_runs DROP COLUMN evolution_analysis_status');
    for (const path of ['/internal/child-0', '/legacy/child-0']) {
      const response = await fetch(base.replace('/api/runs', path));
      expect(response.status).toBe(200);
      const body = await response.json(); const row = body.data ?? body.run;
      expect(row).toMatchObject({ flow_id: 'child-0' });
      expect(row).not.toHaveProperty('ordinary_run_marker');
    }
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
    const legacy = legacyGroupFixture(); persistLegacy(legacy);
    const query = vi.spyOn(db, 'query');
    const first = await (await read(endpoint + '&limit=1')).json();
    expect(query.mock.calls.filter(([sql, args]) => sql.includes('state_json') && args?.includes('parent'))).toHaveLength(1);
    legacy.revision++; persistLegacy(legacy);
    expect((await read(endpoint + '&cursor=' + encodeURIComponent(first.nextCursor))).status).toBe(409);
    legacy.revision--; persistLegacy(legacy); scopes.set('child-workflow', 'all');
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
  it('fails closed when an approved legacy child identity lookup fails', async () => {
    persistLegacy();
    const actual = db.query.bind(db);
    vi.spyOn(db, 'query').mockImplementation((sql, args) => {
      if (sql.includes('flow_id IN')) throw new Error('private SQL error hidden-child-1');
      return actual(sql, args);
    });
    const response = await read(endpoint); expect(response.status).toBe(500);
    expect(await response.text()).not.toMatch(/private SQL|hidden-child/);
  });
  it('only accepts the explicit all-owner wildcard and keeps its statistics visible-scoped', async () => {
    persistLegacy();
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
  it('reads parent detail and hides flat items when the association table is absent', async () => {
    raw.exec('DROP TABLE ordinary_run_requests');
    const parent = await read('/parent'); expect(parent.status).toBe(200);
    const detail = await parent.json();
    expect(detail.run).toMatchObject({ flow_id: 'parent', state_json: null, fanoutPayloadsRedacted: true });
    expect(JSON.stringify(detail)).not.toMatch(/child-[012]|item-[012]/);
    const response = await read(endpoint); expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({ countScope: 'visible', groupTotal: null, visibleTotal: 0, children: [] });
  });
  it('never queries association storage for hidden flat items, even if that storage would fail', async () => {
    const actual = db.query.bind(db); const query = vi.spyOn(db, 'query').mockImplementation((sql, args) => {
      if (sql.includes('ordinary_run_requests')) throw new Error('association database unavailable');
      return actual(sql, args);
    });
    expect((await read('/parent')).status).toBe(200);
    const response = await read(endpoint); expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({ countScope: 'visible', groupTotal: null, visibleTotal: 0, children: [] });
    expect(query.mock.calls.filter(([sql]) => sql.includes('ordinary_run_requests'))).toHaveLength(0);
  });
  it('keeps flat and ordinary reads available without association storage', async () => {
    raw.exec('DROP TABLE ordinary_run_requests');
    const response = await read(endpoint);
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({ countScope: 'visible', groupTotal: null, visibleTotal: 0, children: [] });
    expect((await read('/child-0')).status).toBe(200);
  });
  it('does not call failing association storage while reading flat and ordinary runs', async () => {
    const actual = db.query.bind(db);
    vi.spyOn(db, 'query').mockImplementation((sql, args) => {
      if (sql.includes('FROM ordinary_run_requests')) throw new Error('private association database failure');
      return actual(sql, args);
    });
    const response = await read(endpoint); expect(response.status).toBe(200);
    const body = await response.text();
    expect(body).not.toContain('private association database failure');
    expect(JSON.parse(body)).toMatchObject({ countScope: 'visible', groupTotal: null, visibleTotal: 0, children: [] });
    expect((await read('/child-0')).status).toBe(200);
  });
  it('keeps ordinary HTTP reads working without either optional column while bound fanout fails closed', async () => {
    raw.exec('ALTER TABLE flow_runs DROP COLUMN ordinary_run_marker; ALTER TABLE flow_runs DROP COLUMN evolution_analysis_status');
    const detail = await read('/child-0'); expect(detail.status).toBe(200);
    expect(await detail.json()).toMatchObject({ run: { flow_id: 'child-0' } });
    expect((await read('/?workflowId=child-workflow')).status).toBe(200);
    const fanout = await read(endpoint); expect(fanout.status).toBe(200);
    const body = await fanout.text();
    expect(JSON.parse(body)).toMatchObject({ countScope: 'visible', groupTotal: null, visibleTotal: 0, children: [] });
    expect(body).not.toMatch(/child-0|item-0/);
  });
  it('supports a legacy schema missing only evolution_analysis_status on list, detail and fanout reads', async () => {
    persistLegacy();
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
