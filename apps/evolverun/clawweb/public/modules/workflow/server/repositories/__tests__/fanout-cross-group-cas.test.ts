// @vitest-environment node
import { createHash } from 'node:crypto';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FanoutStateRepository } from '../fanout-state-repository.js';
import { RunSubworkflowsRepository } from '../run-subworkflows-repository.js';
import { RunSubworkflowsService } from '../../services/run-subworkflows-service.js';
import { fixture, group } from './fanout-state-fixture.js';

const digest = (value: string) => createHash('sha256').update(value, 'utf8').digest('hex');
let f: ReturnType<typeof fixture>; let writer: FanoutStateRepository; let reader: RunSubworkflowsService;
const save = (g: Record<string, any>, expectedRevision: number | null = null) =>
  writer.saveGroup('parent', g.groupNodeId, { group: g, expectedRevision });
function g(node: string, key: string, native: string | null = null) {
  const value = group(node); value.children[0].childFlowId = key; value.children[0].nativeFlowId = native; return value;
}
const parentState = () => JSON.parse(f.raw.prepare("SELECT state_json FROM flow_runs WHERE flow_id='parent'").get().state_json);
const inspect = (node: string) => reader.readGroup({ actorId: null, isAdmin: true }, 'parent', node,
  { groupExecutionId: `execution-${node}` });

describe('cross-group association keys under parent CAS', () => {
  beforeEach(() => {
    f = fixture(); writer = new FanoutStateRepository(f.db);
    const columns = new Set((f.raw.prepare('PRAGMA table_info(flow_runs)').all() as { name: string }[]).map(c => c.name));
    for (const column of ['workflow_title', 'origin_bot_id', 'status', 'state_json', 'input_json', 'result_json', 'params_json',
      'node_count', 'succeeded_count', 'failed_count', 'total_duration_ms', 'total_token_usage', 'started_at', 'completed_at',
      'triggered_by', 'current_phase', 'workflow_version', 'workflow_deploy_number', 'origin_session_key', 'origin_session_id',
      'identity_key', 'user_id', 'plugin_version', 'engine', 'gmt_create', 'gmt_modified', 'evolution_analysis_status', 'ordinary_run_marker']) {
      if (!columns.has(column)) f.raw.exec(`ALTER TABLE flow_runs ADD COLUMN ${column} TEXT`);
    }
    f.raw.exec(`CREATE TABLE ordinary_run_requests (request_key_hash TEXT PRIMARY KEY, parent_flow_id TEXT, child_flow_id TEXT,
      child_workflow_id TEXT, parent_run_marker TEXT, child_run_marker TEXT)`);
    f.raw.prepare("UPDATE flow_runs SET ordinary_run_marker=?, origin_bot_id='bot:alice' WHERE flow_id='parent'").run('a'.repeat(64));
    reader = new RunSubworkflowsService(new RunSubworkflowsRepository(f.db), {} as never);
  });
  afterEach(async () => { vi.restoreAllMocks(); await f.db.close(); });

  it('rejects a second group using a persisted key while hiding unproven first-group association', async () => {
    const first = g('one', 'shared', 'native'); await save(first);
    const second = g('two', 'shared', null);
    await expect(save(second)).rejects.toMatchObject({ statusCode: 409 });
    f.raw.prepare('INSERT INTO flow_runs (flow_id,workflow_id,status,origin_bot_id,ordinary_run_marker,started_at) VALUES (?,?,?,?,?,?)')
      .run('native', 'child-wf', 'running', 'bot:alice', 'b'.repeat(64), 1);
    f.raw.prepare('INSERT INTO ordinary_run_requests VALUES (?,?,?,?,?,?)')
      .run(digest('shared'), 'parent', 'native', 'child-wf', 'a'.repeat(64), 'b'.repeat(64));
    expect(await inspect('one')).toMatchObject({ countScope: 'visible', visibleTotal: 0, children: [] });
    expect(Object.keys(parentState().subworkflowFanouts)).toEqual(['one']);
  });
  it('serializes concurrent groups with the same key and never persists two winners', async () => {
    const outcomes = await Promise.allSettled([save(g('one', 'shared')), save(g('two', 'shared'))]);
    expect(outcomes.filter(r => r.status === 'fulfilled')).toHaveLength(1);
    expect(outcomes.filter(r => r.status === 'rejected')).toMatchObject([{ reason: { statusCode: 409 } }]);
    expect(Object.keys(parentState().subworkflowFanouts)).toHaveLength(1);
  });
  it('rechecks collisions after a CAS miss caused by a concurrent group', async () => {
    const competitor = g('two', 'shared'); const pending = g('one', 'shared'); let raced = false;
    const actual = f.db.exec.bind(f.db);
    vi.spyOn(f.db, 'exec').mockImplementation(async (sql, params) => {
      if (!raced && sql.startsWith('UPDATE flow_runs SET state_json = ?')) {
        raced = true;
        f.raw.prepare("UPDATE flow_runs SET state_json=? WHERE flow_id='parent'")
          .run(JSON.stringify({ subworkflowFanouts: { two: competitor } }));
      }
      return actual(sql, params);
    });
    await expect(save(pending)).rejects.toMatchObject({ statusCode: 409 });
    expect(raced).toBe(true);
    expect(Object.keys(parentState().subworkflowFanouts)).toEqual(['two']);
  });
  it('rejects UTF-8 hash aliases both within and across new groups', async () => {
    const first = g('one', 'key-\ud800'); await save(first);
    await expect(save(g('two', 'key-\ufffd'))).rejects.toMatchObject({ statusCode: 409 });
    const other = g('three', 'other-\ud800');
    other.children.push({ ...other.children[0], itemId: 'two', itemIndex: 1, childFlowId: 'other-\ufffd' });
    await expect(save(other)).rejects.toMatchObject({ statusCode: 409 });
  });
  it('lets already-conflicting groups advance or cancel and unrelated groups persist, without resolving the ambiguity as a link', async () => {
    const one = g('one', 'shared', 'native'); const two = g('two', 'shared', null);
    f.raw.prepare("UPDATE flow_runs SET state_json=? WHERE flow_id='parent'")
      .run(JSON.stringify({ subworkflowFanouts: { one, two } }));
    await expect(inspect('one')).rejects.toMatchObject({ code: 'invalid_state' });
    await save({ ...two, revision: 1, status: 'cancelling' }, 0);
    await save(g('three', 'unique'));
    await expect(inspect('one')).rejects.toMatchObject({ code: 'invalid_state' });
    const rekey = structuredClone(one); rekey.revision = 1; rekey.children[0].childFlowId = 'rekey';
    await expect(save(rekey, 0)).rejects.toMatchObject({ statusCode: 409 });
    expect(parentState().subworkflowFanouts.two.revision).toBe(1);
  });
  it('does not treat approved legacy attempts keys as flat association keys', async () => {
    const legacy = { schemaVersion: 'fanout.v1', kind: 'subworkflowFanoutState', parentFlowId: 'parent',
      groupNodeId: 'legacy', groupExecutionId: 'legacy-gen', revision: 1, status: 'running', concurrency: 1,
      successPolicy: 'all_done', finishMode: 'wait_all', resultPolicy: 'collect_all', dependencySnapshot: { workflowId: 'child-wf' },
      children: [{ itemIndex: 0, itemId: 'legacy', activeAttemptNumber: 1,
        attempts: [{ attemptNumber: 1, childFlowId: 'same', status: 'running', error: null }] }] };
    f.raw.prepare("UPDATE flow_runs SET state_json=? WHERE flow_id='parent'").run(JSON.stringify({ subworkflowFanouts: { legacy } }));
    await expect(save(g('one', 'same'))).resolves.toMatchObject({ group: { groupNodeId: 'one' } });
    expect((await inspect('one')).visibleTotal).toBe(0);
  });
});
