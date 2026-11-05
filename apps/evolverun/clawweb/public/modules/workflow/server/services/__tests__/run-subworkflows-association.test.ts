// @vitest-environment node
import { createHash } from 'node:crypto';
import Database from 'better-sqlite3';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db';
import { RunSubworkflowsRepository } from '../../repositories/run-subworkflows-repository.js';
import { RunSubworkflowsService } from '../run-subworkflows-service.js';

const digest = (value: string) => createHash('sha256').update(value).digest('hex');
const parentMarker = 'a'.repeat(64); const childMarker = 'b'.repeat(64);
const actor = { actorId: 'reader', isAdmin: false };
const query = { groupExecutionId: 'gen', limit: 1 };
const permissions = {
  resolveRunViewScope: async (workflow: string) => workflow === 'parent-wf' ? 'all' as const
    : { bots: [{ botId: 'bot', ownerId: 'alice' }] },
  listRunViewScopes: async () => new Map(), getViewByIdsForOwner: async () => ({ restrictedIds: new Set<string>(), viewableIds: new Set<string>() }),
  resolveViewScope: async () => 'all' as const,
};
let raw: Database.Database; let db: SqliteDatabase; let service: RunSubworkflowsService; let group: any;
function persist() {
  raw.prepare('UPDATE flow_runs SET state_json=? WHERE flow_id=?').run(JSON.stringify({ flowId: 'parent', subworkflowFanouts: { group } }), 'parent');
}
function bind(key: string, child: string, options: { parent?: string; workflow?: string; parentMarker?: string; childMarker?: string } = {}) {
  raw.prepare(`INSERT INTO ordinary_run_requests (request_key_hash,parent_flow_id,child_flow_id,child_workflow_id,parent_run_marker,child_run_marker)
    VALUES (?,?,?,?,?,?)`).run(digest(key), options.parent ?? 'parent', child, options.workflow ?? 'child-wf',
      options.parentMarker ?? parentMarker, options.childMarker ?? childMarker);
}
function native(id = 'native') {
  raw.prepare('INSERT INTO flow_runs (flow_id,workflow_id,origin_bot_id,ordinary_run_marker,status) VALUES (?,?,?,?,?)')
    .run(id, 'child-wf', 'bot:alice', childMarker, 'running');
}
const read = () => service.readGroup(actor, 'parent', 'group', query);

describe('ordinary association backed fanout visibility', () => {
  beforeEach(() => {
    raw = new Database(':memory:');
    raw.exec(`CREATE TABLE flow_runs (id INTEGER PRIMARY KEY, flow_id TEXT COLLATE NOCASE UNIQUE, workflow_id TEXT, workflow_title TEXT,
      origin_bot_id TEXT, status TEXT, state_json TEXT, input_json TEXT, result_json TEXT, params_json TEXT,
      node_count INTEGER, succeeded_count INTEGER, failed_count INTEGER, total_duration_ms INTEGER, total_token_usage INTEGER,
      started_at INTEGER, completed_at INTEGER, triggered_by TEXT, current_phase TEXT, workflow_version INTEGER,
      workflow_deploy_number INTEGER, origin_session_key TEXT, origin_session_id TEXT, identity_key TEXT, user_id TEXT,
      plugin_version TEXT, engine TEXT, gmt_create INTEGER, gmt_modified INTEGER, evolution_analysis_status TEXT, ordinary_run_marker TEXT);
      CREATE TABLE ordinary_run_requests (request_key_hash TEXT COLLATE NOCASE PRIMARY KEY, parent_flow_id TEXT COLLATE NOCASE, child_flow_id TEXT COLLATE NOCASE,
        child_workflow_id TEXT, parent_run_marker TEXT, child_run_marker TEXT);
      INSERT INTO flow_runs (flow_id,workflow_id,origin_bot_id,ordinary_run_marker,status) VALUES ('parent','parent-wf','bot:alice','${parentMarker}','running');`);
    group = { schemaVersion: 'fanout.v1', kind: 'subworkflowFanoutState', parentFlowId: 'parent', groupNodeId: 'group',
      groupExecutionId: 'gen', revision: 1, status: 'running', childWorkflow: { id: 'child-wf' },
      config: { concurrency: 2, successPolicy: 'all_done', finishMode: 'wait_all', resultPolicy: 'collect_all' },
      children: [{ itemId: 'item-0', itemIndex: 0, attemptNumber: 1, childFlowId: 'key-0', nativeFlowId: 'native',
        parentFlowId: 'parent', parentNodeId: 'group', groupExecutionId: 'gen', status: 'running', error: null }] };
    native(); persist();
    db = new SqliteDatabase(raw);
    service = new RunSubworkflowsService(new RunSubworkflowsRepository(db), permissions);
  });
  afterEach(() => raw.close());

  it.each([false, true])('reads an ordinary run without marker when analysis column is also missing: %s', async missingAnalysis => {
    raw.exec('ALTER TABLE flow_runs DROP COLUMN ordinary_run_marker');
    if (missingAnalysis) raw.exec('ALTER TABLE flow_runs DROP COLUMN evolution_analysis_status');
    raw.prepare("UPDATE flow_runs SET state_json='{}' WHERE flow_id='parent'").run();
    const inspection = await service.inspectRun(actor, 'parent');
    expect(inspection).toMatchObject({ managed: false, restricted: false, run: { flow_id: 'parent' } });
    expect(inspection.run).not.toHaveProperty('ordinary_run_marker');
  });
  it('supports MySQL-style errors for both missing optional columns on ordinary reads', async () => {
    raw.prepare("UPDATE flow_runs SET state_json='{}' WHERE flow_id='parent'").run();
    const actual = db.query.bind(db);
    vi.spyOn(db, 'query').mockImplementation((sql, args) => {
      if (sql.includes('ordinary_run_marker')) throw new Error("Unknown column 'ordinary_run_marker' in 'field list'");
      if (sql.includes('evolution_analysis_status')) throw new Error("Unknown column 'evolution_analysis_status' in 'field list'");
      return actual(sql, args);
    });
    expect(await service.inspectRun(actor, 'parent')).toMatchObject({ managed: false, run: { flow_id: 'parent' } });
  });
  it('hides a flat bound item without needing the run marker column', async () => {
    bind('key-0', 'native');
    raw.exec('ALTER TABLE flow_runs DROP COLUMN ordinary_run_marker');
    expect(await read()).toMatchObject({ countScope: 'visible', groupTotal: null, visibleTotal: 0, children: [] });
  });
  it.each(['no such column: ordinary_run_marker_backup', 'connection lost while reading ordinary_run_marker'])(
    'does not hide unrelated metadata errors: %s', async message => {
      const querySpy = vi.spyOn(db, 'query').mockRejectedValue(new Error(message));
      await expect(service.inspectRun(actor, 'parent')).rejects.toThrow(message);
      expect(querySpy).toHaveBeenCalledTimes(1);
    });
  it('does not expose flat children even when the ordinary association matches', async () => {
    expect(await read()).toMatchObject({ countScope: 'visible', groupTotal: null, visibleTotal: 0, children: [] });
    bind('key-0', 'native');
    const repo = new RunSubworkflowsRepository(db);
    const parent = await repo.readRun('parent');
    expect(await repo.readAssociatedIdentities(parent!, 'child-wf', [{ requestKeyHash: digest('key-0'), nativeFlowId: 'native' }]))
      .toMatchObject([{ flow_id: 'native' }]);
    expect(await read()).toMatchObject({ visibleTotal: 0, countScope: 'visible', children: [] });
  });
  it('rejects a request key bound to a different native run', async () => {
    native('other-native'); bind('key-0', 'other-native');
    expect(await read()).toMatchObject({ countScope: 'visible', groupTotal: null, visibleTotal: 0 });
  });
  it.each([
    ['other parent', { parent: 'other-parent' }], ['other workflow', { workflow: 'other-wf' }],
    ['other parent incarnation', { parentMarker: 'c'.repeat(64) }], ['other child incarnation', { childMarker: 'c'.repeat(64) }],
  ])('hides an association with %s', async (_label, options) => {
    bind('key-0', 'native', options);
    expect(await read()).toMatchObject({ countScope: 'visible', groupTotal: null, visibleTotal: 0 });
  });
  it.each(['parent', 'native'])('hides stale associations after %s is recreated with a new marker', async id => {
    bind('key-0', 'native');
    const row = raw.prepare('SELECT * FROM flow_runs WHERE flow_id=?').get(id) as { workflow_id: string; origin_bot_id: string; state_json: string | null; status: string };
    raw.prepare('DELETE FROM flow_runs WHERE flow_id=?').run(id);
    raw.prepare('INSERT INTO flow_runs (flow_id,workflow_id,origin_bot_id,state_json,status,ordinary_run_marker) VALUES (?,?,?,?,?,?)')
      .run(id, row.workflow_id, row.origin_bot_id, row.state_json, row.status, 'c'.repeat(64));
    expect(await read()).toMatchObject({ countScope: 'visible', groupTotal: null, visibleTotal: 0 });
  });
  it('does not fall back to an association for a missing or null native ID, even with forged legacy metadata', async () => {
    bind('key-0', 'native');
    Object.assign(group.children[0], { sessionKey: 'fanout:key-0', stateDirRef: 'fanout-state:key-0', nativeFlowId: null });
    persist(); expect(await read()).toMatchObject({ visibleTotal: 0, countScope: 'visible' });
    delete group.children[0].nativeFlowId; persist();
    expect(await read()).toMatchObject({ visibleTotal: 0, countScope: 'visible' });
  });
  it('treats native=parent as unresolved instead of invalidating the group', async () => {
    group.children[0].nativeFlowId = 'parent'; persist();
    expect(await read()).toMatchObject({ visibleTotal: 0, countScope: 'visible', groupTotal: null });
  });
  it.each([true, false])('fails closed when another flat group reuses the association key (bound=%s)', async bound => {
    bind('key-0', 'native');
    const copied = structuredClone(group);
    copied.groupNodeId = 'other-node'; copied.groupExecutionId = 'other-gen';
    copied.children[0].parentNodeId = 'other-node'; copied.children[0].groupExecutionId = 'other-gen';
    if (!bound) copied.children[0].nativeFlowId = null;
    raw.prepare('UPDATE flow_runs SET state_json=? WHERE flow_id=?').run(
      JSON.stringify({ flowId: 'parent', subworkflowFanouts: { group, 'other-node': copied } }), 'parent');
    await expect(read()).rejects.toMatchObject({ code: 'invalid_state' });
    await expect(service.readGroup(actor, 'parent', 'other-node', { groupExecutionId: 'other-gen' }))
      .rejects.toMatchObject({ code: 'invalid_state' });
    await expect(service.inspectRun(actor, 'parent')).rejects.toMatchObject({ code: 'invalid_state' });
  });
  it('keeps approved attempts snapshots separate from flat-group key uniqueness', async () => {
    bind('key-0', 'native');
    const legacy = { schemaVersion: 'fanout.v1', kind: 'subworkflowFanoutState', parentFlowId: 'parent',
      groupNodeId: 'legacy', groupExecutionId: 'legacy-gen', revision: 1, status: 'running', concurrency: 1,
      successPolicy: 'all_done', finishMode: 'wait_all', resultPolicy: 'collect_all',
      dependencySnapshot: { workflowId: 'child-wf' }, children: [{ itemIndex: 0, itemId: 'legacy-item', activeAttemptNumber: 1,
        attempts: [{ attemptNumber: 1, childFlowId: 'key-0', status: 'running', error: null }] }] };
    raw.prepare('UPDATE flow_runs SET state_json=? WHERE flow_id=?').run(
      JSON.stringify({ flowId: 'parent', subworkflowFanouts: { group, legacy } }), 'parent');
    expect(await read()).toMatchObject({ visibleTotal: 0, countScope: 'visible', children: [] });
  });
  it('keeps distinct flat groups hidden even if their ordinary associations match', async () => {
    bind('key-0', 'native'); native('native-2'); bind('key-1', 'native-2');
    const copied = structuredClone(group); copied.groupNodeId = 'other-node'; copied.groupExecutionId = 'other-gen';
    Object.assign(copied.children[0], { parentNodeId: 'other-node', groupExecutionId: 'other-gen', childFlowId: 'key-1', nativeFlowId: 'native-2' });
    raw.prepare('UPDATE flow_runs SET state_json=? WHERE flow_id=?').run(
      JSON.stringify({ flowId: 'parent', subworkflowFanouts: { group, 'other-node': copied } }), 'parent');
    expect(await read()).toMatchObject({ countScope: 'visible', visibleTotal: 0, children: [] });
    expect(await service.readGroup(actor, 'parent', 'other-node', { groupExecutionId: 'other-gen' }))
      .toMatchObject({ countScope: 'visible', visibleTotal: 0, children: [] });
  });
  it('rejects distinct keys whose UTF-8 hashes collide across flat groups', async () => {
    group.children[0].childFlowId = 'key-\ud800';
    const copied = structuredClone(group);
    copied.groupNodeId = 'other-node'; copied.groupExecutionId = 'other-gen';
    copied.children[0].parentNodeId = 'other-node'; copied.children[0].groupExecutionId = 'other-gen';
    copied.children[0].childFlowId = 'key-\ufffd';
    raw.prepare('UPDATE flow_runs SET state_json=? WHERE flow_id=?').run(
      JSON.stringify({ flowId: 'parent', subworkflowFanouts: { group, 'other-node': copied } }), 'parent');
    expect(digest(group.children[0].childFlowId)).toBe(digest(copied.children[0].childFlowId));
    await expect(read()).rejects.toMatchObject({ code: 'invalid_state' });
  });
  it('rejects conflicting native IDs across items', async () => {
    group.children.push({ ...group.children[0], itemIndex: 1, itemId: 'item-1', childFlowId: 'key-1' }); persist();
    await expect(read()).rejects.toMatchObject({ code: 'invalid_state' });
  });
  it('does not confuse native ID with an association-key alias from another item', async () => {
    group.children[0].nativeFlowId = 'key-1';
    group.children.push({ ...group.children[0], itemIndex: 1, itemId: 'item-1', childFlowId: 'key-1', nativeFlowId: null });
    native('key-1'); bind('key-1', 'key-1'); persist();
    expect(await read()).toMatchObject({ visibleTotal: 0, countScope: 'visible' });
  });
  it.each(['request hash', 'parent id', 'child id'])('rejects case-insensitive SQL alias matches for %s', async which => {
    bind('key-0', 'native');
    const column = which === 'request hash' ? 'request_key_hash' : which === 'parent id' ? 'parent_flow_id' : 'child_flow_id';
    raw.prepare(`UPDATE ordinary_run_requests SET ${column}=UPPER(${column})`).run();
    expect(await read()).toMatchObject({ countScope: 'visible', visibleTotal: 0 });
  });
  it('rejects a parent replaced and rebound between the parent read and joined lookup', async () => {
    bind('key-0', 'native');
    const actual = db.query.bind(db); let changed = false;
    vi.spyOn(db, 'query').mockImplementation((sql, args) => {
      if (!changed && sql.includes('FROM ordinary_run_requests')) {
        changed = true;
        raw.prepare('UPDATE flow_runs SET ordinary_run_marker=? WHERE flow_id=?').run('c'.repeat(64), 'parent');
        raw.prepare('UPDATE ordinary_run_requests SET parent_run_marker=?').run('c'.repeat(64));
      }
      return actual(sql, args);
    });
    const repo = new RunSubworkflowsRepository(db);
    const parent = await repo.readRun('parent');
    expect(await repo.readAssociatedIdentities(parent!, 'child-wf', [{ requestKeyHash: digest('key-0'), nativeFlowId: 'native' }])).toEqual([]);
    expect(changed).toBe(true);
  });
  it.each([['workflow_id', 'different-parent-wf'], ['origin_bot_id', 'bot:bob']])(
    'hides a match when the parent %s changes after the initial authorization', async (column, value) => {
      bind('key-0', 'native');
      const actual = db.query.bind(db);
      vi.spyOn(db, 'query').mockImplementation((sql, args) => {
        if (sql.includes('FROM ordinary_run_requests')) raw.prepare(`UPDATE flow_runs SET ${column}=? WHERE flow_id='parent'`).run(value);
        return actual(sql, args);
      });
      const repo = new RunSubworkflowsRepository(db);
      const parent = await repo.readRun('parent');
      expect(await repo.readAssociatedIdentities(parent!, 'child-wf', [{ requestKeyHash: digest('key-0'), nativeFlowId: 'native' }])).toEqual([]);
    });
  it('fails closed on duplicated association proof rows', async () => {
    bind('key-0', 'native');
    const actual = db.query.bind(db);
    vi.spyOn(db, 'query').mockImplementation(async (sql, args) => {
      const rows = await actual(sql, args);
      return sql.includes('FROM ordinary_run_requests') ? [...rows, ...rows] : rows;
    });
    expect(await read()).toMatchObject({ visibleTotal: 0, countScope: 'visible' });
  });
  it('does not query associations or individual checkpoints for flat children', async () => {
    bind('key-0', 'native'); native('native-2'); bind('key-1', 'native-2');
    group.children.push({ ...group.children[0], itemIndex: 1, itemId: 'item-1', childFlowId: 'key-1', nativeFlowId: 'native-2' }); persist();
    const spy = vi.spyOn(db, 'query');
    expect((await read()).visibleTotal).toBe(0);
    expect(spy.mock.calls.filter(([sql]) => sql.includes('FROM ordinary_run_requests'))).toHaveLength(0);
    expect(spy.mock.calls.filter(([sql, args]) => sql.includes('FROM flow_runs WHERE flow_id = ?') && args?.[0] !== 'parent')).toHaveLength(0);
  });
  it('hides flat items without reading the missing association table', async () => {
    raw.exec('DROP TABLE ordinary_run_requests');
    expect(await read()).toMatchObject({ countScope: 'visible', groupTotal: null, visibleTotal: 0, children: [] });
  });
  it('hides an association if the current child owner or workflow is unauthorized', async () => {
    bind('key-0', 'native');
    raw.prepare("UPDATE flow_runs SET origin_bot_id='bot:bob' WHERE flow_id='native'").run();
    expect(await read()).toMatchObject({ visibleTotal: 0 });
    raw.prepare("UPDATE flow_runs SET origin_bot_id='bot:alice', workflow_id='foreign' WHERE flow_id='native'").run();
    expect(await read()).toMatchObject({ visibleTotal: 0 });
  });
  it('never issues a child cursor for unproven flat associations', async () => {
    bind('key-0', 'native'); native('native-2'); bind('key-1', 'native-2');
    group.children.push({ ...group.children[0], itemIndex: 1, itemId: 'item-1', childFlowId: 'key-1', nativeFlowId: 'native-2' }); persist();
    expect(await read()).toMatchObject({ visibleTotal: 0, groupTotal: null, nextCursor: null, children: [] });
    raw.prepare('DELETE FROM ordinary_run_requests WHERE request_key_hash=?').run(digest('key-1'));
    expect(await read()).toMatchObject({ visibleTotal: 0, groupTotal: null, nextCursor: null, children: [] });
  });
});
