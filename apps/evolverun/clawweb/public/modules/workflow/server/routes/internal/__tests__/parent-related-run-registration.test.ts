// @vitest-environment node
import { afterEach, describe, expect, it } from 'vitest';
import { parentRelationFixture } from './parent-relation-fixture.js';

const cleanups: Array<() => Promise<void>> = [];
afterEach(async () => { for (const cleanup of cleanups.splice(0).reverse()) await cleanup(); });
async function fixture(fanout = false, itemId = 'one') { const f = await parentRelationFixture(fanout, itemId); cleanups.push(f.close); return f; }
const expectedLink = { child_flow_id: 'native-child', parent_flow_id: 'parent', parent_node_id: 'call', parent_attempt: 2, child_workflow_id: 'child-wf' };

describe.each([false, true])('ordinary registration with verified parent relation (fanout: %s)', fanout => {
  it('atomically creates the native run and link and echoes exactly the validated snake-case relation', async () => {
    const f = await fixture(fanout);
    const response = await f.post();
    expect(response).toMatchObject({ status: 201, body: { success: true, data: { flow_id: 'native-child', parent_relation: f.relation } } });
    expect(f.child()).toBeDefined(); expect(f.links()).toEqual([expectedLink]);
    expect((await f.repo.findFullByFlowId('native-child'))).not.toHaveProperty('parent_relation');
  });

  it('rejoins exactly and rejects changes to registration metadata or relation without overwriting', async () => {
    const f = await fixture(fanout);
    const initial = await f.post();
    expect(initial.status).toBe(201);
    // A lost response can be replayed after execution advances. It must not rewind run state.
    await f.db.exec(`UPDATE flow_runs SET status='succeeded', state_json='{"workflowId":"child-wf","currentPhase":"P2"}', current_phase='P2' WHERE flow_id='native-child'`);
    // The parent remains running while the child owns waiting/blocked/terminal state.
    await f.db.exec("UPDATE node_executions SET status='running' WHERE flow_id='parent'");
    expect(await f.post({ ...f.body, started_at: 999 })).toMatchObject({ status: 200, body: { data: { parent_relation: f.relation, status: 'succeeded' } } });
    for (const patch of [
      { params_json: '{"value":"different"}' }, { input_json: '{"different":true}' }, { origin_bot_id: 'other:owner' },
      { credentials_json: '{"different":true}' }, { workflow_version: 3 }, { user_id: 'other' },
      { origin_session_key: 'other' }, { origin_session_id: 'other' },
      { parent_relation: { ...f.relation, parent_attempt: 3 } },
    ]) expect((await f.post({ ...f.body, ...patch })).status).toBe(409);
    expect(f.links()).toEqual([expectedLink]);
    expect(f.child()).toMatchObject({ status: 'succeeded', input_json: '{}' });
  });

  it('rolls back the child and all link-related writes on link persistence failure', async () => {
    const f = await fixture(fanout);
    const before = f.raw.prepare("SELECT state_json FROM flow_runs WHERE flow_id='parent'").get();
    f.raw.exec("CREATE TRIGGER refuse_link BEFORE INSERT ON flow_run_links BEGIN SELECT RAISE(ABORT, 'private write failure'); END");
    const response = await f.post();
    expect(response).toEqual({ status: 503, body: { success: false, error: 'Service Unavailable' } });
    expect(f.child()).toBeUndefined(); expect(f.links()).toEqual([]);
    expect(f.raw.prepare("SELECT state_json FROM flow_runs WHERE flow_id='parent'").get()).toEqual(before);
    f.raw.exec('DROP TRIGGER refuse_link');
    expect((await f.post()).status).toBe(201);
  });

  it('does not trust child-reported metadata when identity, node, attempt or parent authority differs', async () => {
    const f = await fixture(fanout);
    for (const patch of [
      { origin_bot_id: 'bot:other-owner' }, { user_id: 'another-user' }, { origin_session_key: 'forged-session' },
      { origin_session_id: 'forged-session-id' }, { workflow_id: 'forged-wf' },
      { parent_relation: { ...f.relation, parent_node_id: 'missing' } },
      { parent_relation: { ...f.relation, parent_attempt: 1 } },
      { parent_relation: { ...f.relation, parent_flow_id: 'missing' } },
    ]) {
      const result = await f.post({ ...f.body, ...patch, state_json: JSON.stringify({ subworkflowMeta: { parentFlowId: 'parent', parentNodeId: 'call' }, fanoutOrigin: { workflowId: 'child-wf' } }) });
      expect([404, 409]).toContain(result.status);
      expect(f.child()).toBeUndefined(); expect(f.links()).toEqual([]);
    }
    await f.db.exec("UPDATE node_executions SET status='succeeded' WHERE flow_id='parent'");
    expect((await f.post()).status).toBe(409);
    expect(f.child()).toBeUndefined(); expect(f.links()).toEqual([]);
  });

  it('rejects conflicting existing links and unproven existing native runs', async () => {
    const f = await fixture(fanout);
    await f.db.exec("INSERT INTO flow_run_links (child_flow_id,parent_flow_id,parent_node_id,parent_attempt,child_workflow_id) VALUES ('native-child','other-parent','call',2,'child-wf')");
    expect((await f.post()).status).toBe(409);
    expect(f.child()).toBeUndefined();
    await f.db.exec('DELETE FROM flow_run_links');
    expect(await f.repo.insert({ flowId: 'native-child', workflowId: 'child-wf', status: 'running', startedAt: 2 })).toBe(true);
    expect((await f.post()).status).toBe(409);
    expect(f.links()).toEqual([]);
  });

  it('serializes concurrent exact retries into one child and one link', async () => {
    const f = await fixture(fanout);
    const results = await Promise.all([f.post(), f.post()]);
    expect(results.map(r => r.status).sort()).toEqual([200, 201]);
    expect(results.every(r => JSON.stringify(r.body.data.parent_relation) === JSON.stringify(f.relation))).toBe(true);
    expect(f.links()).toEqual([expectedLink]);
  });
});

it('accepts composite fanout item IDs and keeps percent escapes distinct', async () => {
  // ClawMind joins encodeURIComponent'd values with a literal `/`. That
  // separator must remain an exact string and must not be normalized to `%2F`.
  for (const item_id of [
    'real_banrestrict_content_sg_b15_new/20261111-real_banrestrict_content_sg_b15_new-r1_text_20260716',
    'scene%2Fpartition',
    'scene/par%2Ftition',
  ]) {
    const f = await fixture(true, item_id);
    const relation = { ...f.relation, fanout: { ...f.relation.fanout!, item_id } };
    const response = await f.post({ ...f.body, parent_relation: relation });
    expect(response).toMatchObject({ status: 201, body: { data: { parent_relation: relation } } });
    expect(f.child()).toBeDefined();
    expect(f.links()).toEqual([{ ...expectedLink }]);
    const parent = await f.repo.findFullByFlowId('parent');
    expect(JSON.parse(parent!.state_json!).subworkflowFanouts.call.children[0].itemId).toBe(item_id);
  }

  const f = await fixture(true, 'scene/partition');
  const literal = f.relation;
  const escaped = { ...f.relation, fanout: { ...f.relation.fanout!, item_id: 'scene%2Fpartition' } };
  expect((await f.post({ ...f.body, parent_relation: literal })).status).toBe(201);
  const retry = await f.post({ ...f.body, parent_relation: literal });
  expect(retry.status).toBe(200);
  expect((await f.post({ ...f.body, parent_relation: escaped })).status).toBe(409);
  expect(f.links()).toEqual([expectedLink]);
});

it('rejects unsafe fanout item IDs before writing a child or link', async () => {
  for (const item_id of ['', 'scene partition', 'scene?partition', 'bad%', 'scene#partition']) {
    const f = await fixture(true);
    const relation = { ...f.relation, fanout: { ...f.relation.fanout!, item_id } };
    expect((await f.post({ ...f.body, parent_relation: relation })).status).toBe(400);
  }
  const f = await fixture(true);
  expect(f.child()).toBeUndefined(); expect(f.links()).toEqual([]);

  // The final unmodified request still writes exactly one child and link.
  expect((await f.post()).status).toBe(201);
  expect(f.links()).toEqual([expectedLink]);
});

it('requires a registered immutable snapshot rather than a mutable spec or a forged current state', async () => {
  const f = await fixture();
  await f.db.exec("UPDATE flow_runs SET workflow_snapshot_json=NULL WHERE flow_id='parent'");
  expect((await f.post()).status).toBe(409);
  expect(f.child()).toBeUndefined(); expect(f.links()).toEqual([]);
});

it('remains available when externally provisioned flow_runs predates the snapshot association column', async () => {
  const f = await fixture();
  // Model a deployment-bound pre-v145 schema. A saved/draft run cannot use this
  // compatibility path because its only pin lives in mutable state_json.
  f.raw.prepare('INSERT INTO workflow_deploy_history (pack_id, workflow_id, deploy_number, version, tag_name, action, spec_json, is_active) VALUES (?, ?, ?, ?, ?, ?, ?, ?)')
    .run('pack', 'parent-wf', 1, 1, 'pack/1', 'deploy', JSON.stringify(f.spec), 1);
  f.raw.exec("UPDATE flow_runs SET workflow_version=1, workflow_deploy_number=1 WHERE flow_id='parent'");
  f.raw.exec('ALTER TABLE flow_runs DROP COLUMN workflow_snapshot_json');
  const response = await f.post();
  expect(response).toMatchObject({ status: 201, body: { success: true } });
  expect(f.links()).toEqual([expectedLink]);
});

it('checks a saved snapshot against its registered digest, not the child claim', async () => {
  const f = await fixture();
  const parent = await f.repo.findFullByFlowId('parent');
  const state = JSON.parse(parent!.state_json!); state.workflowSnapshot.nodes[0].executor.workflowId = 'changed';
  await f.db.exec("UPDATE flow_runs SET state_json=? WHERE flow_id='parent'", [JSON.stringify(state)]);
  expect((await f.post()).status).toBe(409);
  expect(f.child()).toBeUndefined(); expect(f.links()).toEqual([]);
});

it('matches the persisted fanout dispatch claim and forbids binding a different native child to the same claim', async () => {
  const f = await fixture(true);
  for (const patch of [{ dispatch_claim_token: 'wrong' }, { item_id: 'wrong' }, { item_attempt: 2 }, { group_execution_id: 'wrong' }]) {
    expect((await f.post({ ...f.body, parent_relation: { ...f.relation, fanout: { ...f.relation.fanout, ...patch } } })).status).toBe(409);
    expect(f.child()).toBeUndefined(); expect(f.links()).toEqual([]);
  }
  expect((await f.post()).status).toBe(201);
  expect((await f.post({ ...f.body, flow_id: 'other-native' })).status).toBe(409);
  expect(await f.db.query("SELECT flow_id FROM flow_runs WHERE flow_id='other-native'")).toEqual([]);
  const parent = await f.repo.findFullByFlowId('parent');
  const stored = JSON.parse(parent!.state_json!).subworkflowFanouts.call;
  // Registration must not silently advance the launcher's group CAS revision.
  expect(stored.children[0].nativeFlowId).toBe('native-child');
});

it('rejects unclaimed, expired, terminal or stopped fanout dispatches before creating a run', async () => {
  const f = await fixture(true);
  const original = JSON.parse((await f.repo.findFullByFlowId('parent'))!.state_json!);
  for (const mutation of ['unclaimed', 'expired', 'terminal', 'stopped']) {
    const state = structuredClone(original); const g = state.subworkflowFanouts.call;
    if (mutation === 'unclaimed') delete g.children[0].dispatchClaimToken;
    if (mutation === 'expired') g.deadlineAt = new Date(0).toISOString();
    if (mutation === 'terminal') g.status = 'cancelled';
    await f.db.exec("UPDATE flow_runs SET state_json=?,status=? WHERE flow_id='parent'", [JSON.stringify(state), mutation === 'stopped' ? 'cancelled' : 'running']);
    expect((await f.post()).status).toBe(409);
    expect(f.child()).toBeUndefined(); expect(f.links()).toEqual([]);
  }
});

it('rejects malformed relations, trial mixing, query parameters and injected registration receipts without writing', async () => {
  const f = await fixture();
  for (const parent_relation of [null, {}, { ...f.relation, extra: true }, { ...f.relation, parent_attempt: 0 },
    { ...f.relation, fanout: {} }]) {
    expect((await f.post({ ...f.body, parent_relation })).status).toBe(400);
  }
  expect((await f.post({ ...f.body, parent_relation: { ...f.relation, parent_flow_id: 'native-child' } })).status).toBe(409);
  expect((await f.post(f.body, '?owner=forged')).status).toBe(400);
  expect((await f.post({ ...f.body, run_request_id: 'trial' })).status).toBe(409);
  expect(f.child()).toBeUndefined(); expect(f.links()).toEqual([]);
});

it('does not derive a relationship from legacy child markers and keeps the old response unchanged', async () => {
  const f = await fixture();
  const { parent_relation: _relation, ...body } = f.body;
  const response = await f.post({ ...body, state_json: JSON.stringify({ subworkflowMeta: { parentFlowId: 'parent', parentNodeId: 'call' } }) });
  expect(response.status).toBe(201); expect(response.body.data).not.toHaveProperty('parent_relation');
  expect(f.links()).toEqual([]);
});

it('keeps the persisted single-subworkflow invocation immutable', async () => {
  const f = await parentRelationFixture(true); cleanups.push(f.close);
  const current = JSON.parse((await f.repo.findFullByFlowId('parent'))!.state_json!).subworkflowFanouts.call;
  const changed = structuredClone(current); changed.revision = current.revision + 1;
  changed.invocation.parentAttempt = 3;
  await expect(f.repo.fanoutState.saveGroup('parent', 'call', { group: changed, expectedRevision: current.revision }))
    .rejects.toMatchObject({ statusCode: 409 });
  const persisted = JSON.parse((await f.repo.findFullByFlowId('parent'))!.state_json!).subworkflowFanouts.call;
  expect(persisted.invocation).toEqual(current.invocation);
});
