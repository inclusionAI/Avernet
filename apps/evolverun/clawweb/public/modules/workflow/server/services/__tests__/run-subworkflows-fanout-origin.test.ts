// @vitest-environment node
import Database from 'better-sqlite3';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db';
import type { RunViewScope } from '@avernet/clawweb-shared/server/services/run-view-permissions';
import { RunSubworkflowsRepository } from '../../repositories/run-subworkflows-repository.js';
import { RunSubworkflowsService } from '../run-subworkflows-service.js';

const actor = { actorId: 'reader', isAdmin: false };
const opaquePayload = 'opaque-secret-value';
const identity = { parentFlowId: 'parent', parentNodeId: 'group', groupExecutionId: 'generation',
  itemId: 'item', itemIndex: 0, attemptNumber: 1, childFlowId: 'logical', workflowId: 'child-wf', requestDigest: 'digest' };
let raw: Database.Database; let db: SqliteDatabase; let service: RunSubworkflowsService;
let group: any; let scopes: Map<string, RunViewScope>;
const writeParent = () => raw.prepare('UPDATE flow_runs SET state_json=? WHERE flow_id=?')
  .run(JSON.stringify({ subworkflowFanouts: { group }, secret: 'parent-secret' }), 'parent');
const writeChild = (value: unknown) => raw.prepare('UPDATE flow_runs SET state_json=? WHERE flow_id=?')
  .run(typeof value === 'string' ? value : JSON.stringify(value), 'native');
const read = () => service.readGroup(actor, 'parent', 'group', { groupExecutionId: 'generation' });

describe('trusted-engine fanoutOrigin correlation with SQLite', () => {
  beforeEach(() => {
    raw = new Database(':memory:');
    raw.exec(`CREATE TABLE flow_runs (id INTEGER PRIMARY KEY, flow_id TEXT COLLATE NOCASE UNIQUE, workflow_id TEXT,
      workflow_title TEXT, origin_bot_id TEXT, status TEXT, state_json TEXT, input_json TEXT, result_json TEXT, params_json TEXT,
      node_count INTEGER, succeeded_count INTEGER, failed_count INTEGER, total_duration_ms INTEGER, total_token_usage INTEGER,
      started_at INTEGER, completed_at INTEGER, triggered_by TEXT, current_phase TEXT, workflow_version INTEGER,
      workflow_deploy_number INTEGER, origin_session_key TEXT, origin_session_id TEXT, identity_key TEXT, user_id TEXT,
      plugin_version TEXT, engine TEXT, gmt_create INTEGER, gmt_modified INTEGER, evolution_analysis_status TEXT,
      ordinary_run_marker TEXT, credentials_json TEXT);
      INSERT INTO flow_runs (flow_id, workflow_id, origin_bot_id, status) VALUES
        ('parent','parent-wf','bot:alice','running'), ('native','child-wf','bot:alice','running');`);
    group = { schemaVersion: 'fanout.v1', kind: 'subworkflowFanoutState', parentFlowId: 'parent',
      groupNodeId: 'group', groupExecutionId: 'generation', revision: 1, status: 'running',
      config: { concurrency: 1, successPolicy: 'all_done', finishMode: 'wait_all', resultPolicy: 'collect_all' },
      childWorkflow: { id: 'child-wf' }, children: [{ ...identity, nativeFlowId: 'native', status: 'running', error: null }] };
    delete group.children[0].workflowId; delete group.children[0].requestDigest;
    writeParent(); writeChild({ fanoutOrigin: identity, credentials: 'child-secret' });
    scopes = new Map([['parent-wf', { bots: [{ botId: 'bot', ownerId: 'alice' }] }],
      ['child-wf', { bots: [{ botId: 'bot', ownerId: 'alice' }] }]]);
    const permissions = { resolveRunViewScope: async (id: string) => scopes.get(id) ?? 'deny' as const,
      listRunViewScopes: async () => scopes, getViewByIdsForOwner: async () => ({ restrictedIds: new Set<string>(), viewableIds: new Set<string>() }),
      resolveViewScope: async () => 'all' as const };
    db = new SqliteDatabase(raw); service = new RunSubworkflowsService(new RunSubworkflowsRepository(db), permissions);
  });
  afterEach(() => raw.close());

  it('shows only the authorized native ID and public projection, without raw state or credentials', async () => {
    const page = await read();
    expect(page).toMatchObject({ countScope: 'visible', groupTotal: null, visibleTotal: 1,
      children: [{ itemId: 'item', childFlowId: 'native', attemptNumber: 1, status: 'running' }] });
    expect(JSON.stringify(page)).not.toMatch(/parent-secret|child-secret|fanoutOrigin|requestDigest|logical/);
    const child = await service.inspectRun(actor, 'native');
    expect(JSON.parse(child.run.state_json!)).toEqual({});
    expect(JSON.stringify(child)).not.toMatch(/child-secret|credentials_json|fanoutOrigin/);
  });
  it('projects useful child execution state while omitting opaque payloads and association data', async () => {
    scopes.set('parent-wf', 'deny');
    writeChild({ workflowId: 'child-wf', workflowVersion: 2, businessStatus: 'PROCESSING', currentPhase: 'P1',
      executionMode: 'private', activeNodes: ['work', 'agent:secret-session', { token: opaquePayload }],
      nodeStates: { work: { status: 'running', phase: 'P1', executor: 'agent', attempts: 2, manualRetries: 1,
        startedAt: 100, updatedAt: 120, completedAt: null, result: { secret: 'result-secret' }, error: 'error-secret',
        childSessionKey: 'session-secret', callbackToken: 'callback-secret', waitPrompt: 'prompt-secret',
        bcsApproval: { token: opaquePayload }, params: { token: opaquePayload },
        subworkflowFanouts: { nested: { childFlowId: 'nested-association-secret' } } },
        invalid: { status: { token: opaquePayload }, startedAt: 'timestamp-secret' },
        'agent:session-secret': { status: 'running' } },
      fanoutOrigin: identity, originCredentials: 'credential-secret', originSessionKey: 'session-secret',
      params: { token: opaquePayload }, workflowData: { token: opaquePayload },
      actionOutputs: { work: 'output-secret' }, subworkflowMeta: { parentFlowId: 'parent-secret' },
      workflowSnapshot: { token: opaquePayload }, fanoutRuntimeContext: { sessionKey: 'context-secret' } });
    const inspection = await service.inspectRun(actor, 'native');
    expect(JSON.parse(inspection.run.state_json!)).toEqual({ workflowId: 'child-wf', workflowVersion: 2,
      businessStatus: 'PROCESSING', currentPhase: 'P1', executionMode: 'private', activeNodes: ['work'],
      nodeStates: { work: { status: 'running', phase: 'P1', executor: 'agent', attempts: 2, manualRetries: 1,
        startedAt: 100, updatedAt: 120 }, invalid: {} } });
    expect(inspection.run).toMatchObject({ params_json: null, input_json: null, result_json: null, origin_session_key: null, origin_session_id: null });
    expect(JSON.stringify(inspection)).not.toMatch(/secret|fanoutOrigin|requestDigest|workflowData/);
  });
  it.each(Object.keys(identity))('hides a missing or mismatched origin field %s', async key => {
    group.children[0].requestDigest = identity.requestDigest; writeParent();
    const missing = { ...identity } as Record<string, unknown>; delete missing[key];
    writeChild({ fanoutOrigin: missing }); expect((await read()).visibleTotal).toBe(0);
    writeChild({ fanoutOrigin: { ...identity, [key]: typeof (identity as Record<string, unknown>)[key] === 'number' ? -1 : 'wrong' } });
    expect((await read()).visibleTotal).toBe(0);
  });
  it.each([null, '', '{', '{}', '[]', '{"fanoutOrigin":null}', '{"fanoutOrigin":[]}'])('hides missing or malformed child state %j', async state => {
    raw.prepare('UPDATE flow_runs SET state_json=? WHERE flow_id=?').run(state, 'native');
    expect((await read()).visibleTotal).toBe(0);
  });
  it.each(['', '  ', null, 0, {}])('hides invalid requestDigest %j', async requestDigest => {
    writeChild({ fanoutOrigin: { ...identity, requestDigest } });
    expect((await read()).visibleTotal).toBe(0);
  });
  it('retains full counts only for unrestricted parent and child grants', async () => {
    scopes.set('parent-wf', 'all'); scopes.set('child-wf', 'all');
    expect(await read()).toMatchObject({ countScope: 'full', groupTotal: 1, visibleTotal: 1,
      children: [{ itemIndex: 0, childFlowId: 'native' }] });
  });
  it('checks the parent request digest when present, not the unrelated input digest', async () => {
    group.inputDigest = 'other'; group.children[0].requestDigest = 'other'; writeParent();
    expect((await read()).visibleTotal).toBe(0);
    group.children[0].requestDigest = 'digest'; writeParent(); expect((await read()).visibleTotal).toBe(1);
  });
  it('hides unknown native IDs, missing rows, mismatched stored workflow and case-insensitive SQL matches', async () => {
    for (const id of [null, undefined, 'missing', 'NATIVE']) {
      group.children[0].nativeFlowId = id; writeParent(); expect((await read()).visibleTotal).toBe(0);
    }
    group.children[0].nativeFlowId = 'native'; writeParent();
    raw.prepare("UPDATE flow_runs SET workflow_id='other' WHERE flow_id='native'").run();
    expect((await read()).visibleTotal).toBe(0);
  });
  it('requires independent owner-qualified parent and child permissions', async () => {
    scopes.set('child-wf', { bots: [{ botId: 'bot', ownerId: 'bob' }] });
    expect((await read()).visibleTotal).toBe(0);
    scopes.set('child-wf', 'deny'); expect((await read()).visibleTotal).toBe(0);
    scopes.set('child-wf', 'all'); scopes.set('parent-wf', { bots: [{ botId: 'bot', ownerId: 'bob' }] });
    const spy = vi.spyOn(db, 'query');
    await expect(read()).rejects.toMatchObject({ code: 'forbidden' });
    expect(spy.mock.calls.some(([sql]) => sql.includes('flow_id IN'))).toBe(false);
  });
  it('invalidates a cursor when a matched child loses authorization or provenance', async () => {
    const second = { ...group.children[0], itemId: 'item-2', itemIndex: 1, childFlowId: 'logical-2', nativeFlowId: 'native-2' };
    group.children.push(second); writeParent();
    raw.prepare('INSERT INTO flow_runs (flow_id,workflow_id,origin_bot_id,status,state_json) VALUES (?,?,?,?,?)').run(
      'native-2', 'child-wf', 'bot:alice', 'running',
      JSON.stringify({ fanoutOrigin: { ...identity, itemId: 'item-2', itemIndex: 1, childFlowId: 'logical-2' } }));
    const first = await service.readGroup(actor, 'parent', 'group', { groupExecutionId: 'generation', limit: 1 });
    expect(first.nextCursor).toBeTruthy();
    scopes.set('child-wf', 'deny');
    await expect(service.readGroup(actor, 'parent', 'group', { groupExecutionId: 'generation', limit: 1, cursor: first.nextCursor! }))
      .rejects.toMatchObject({ code: 'snapshot_changed' });
    scopes.set('child-wf', { bots: [{ botId: 'bot', ownerId: 'alice' }] });
    writeChild({ fanoutOrigin: { ...identity, groupExecutionId: 'different' } });
    await expect(service.readGroup(actor, 'parent', 'group', { groupExecutionId: 'generation', limit: 1, cursor: first.nextCursor! }))
      .rejects.toMatchObject({ code: 'snapshot_changed' });
  });
  it('propagates storage failures instead of reporting a hidden child', async () => {
    const actual = db.query.bind(db);
    vi.spyOn(db, 'query').mockImplementation((sql, params) => {
      if (sql.includes('flow_id IN')) throw new Error('storage failure');
      return actual(sql, params);
    });
    await expect(read()).rejects.toThrow('storage failure');
  });
});
