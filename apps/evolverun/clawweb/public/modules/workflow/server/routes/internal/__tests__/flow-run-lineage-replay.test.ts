// @vitest-environment node
import { createHash } from 'node:crypto';
import { afterEach, describe, expect, it } from 'vitest';
import { RepairReleaseRepository } from '../../../repositories/repair-release-repository.js';
import { lineageFixture } from '../../../repositories/__tests__/flow-run-lineage.fixture.js';
import { parentRelationFixture } from './parent-relation-fixture.js';

const closes: Array<() => Promise<void>> = [];
afterEach(async () => { for (const close of closes.splice(0).reverse()) await close(); });
async function fixture() { const f = await lineageFixture(); closes.push(f.close); return f; }
const path = '/api/internal/runs';
const unknownLineage = { parent_flow_id: null, root_flow_id: null };
const nested = { parent_flow_id: ' exact-parent / e\u0301 ', root_flow_id: ' exact-root / é ' };

// The request-parent mechanisms remain independent authority. Deliberately use
// descriptive lineage different from that authority to catch accidental coupling.
describe.each([false, true])('parent-related replay lineage (fanout: %s)', fanout => {
  it.each([unknownLineage, nested, { parent_flow_id: 'partial', root_flow_id: null }])('checks supplied values without changing a child after state advance (%j)', async lineage => {
    const f = await parentRelationFixture(fanout); closes.push(f.close);
    const body = { ...f.body, ...lineage };
    expect(await f.post(body)).toMatchObject({ status: 201, body: { data: { ...lineage, parent_relation: f.relation } } });
    await f.repo.updateStateJson('native-child', '{"workflowId":"child-wf","currentPhase":"done"}');
    await f.repo.updateCurrentPhase('native-child', 'done');
    await f.repo.updateStatus('native-child', 'succeeded');
    expect(await f.post(body)).toMatchObject({ status: 200, body: { data: { ...lineage, status: 'succeeded', current_phase: 'done' } } });
    expect((await f.post(f.body)).status).toBe(200); // Old retry omits both assertions.
    for (const patch of [{ parent_flow_id: 'conflict' }, { root_flow_id: 'conflict' },
      ...(lineage.root_flow_id !== null ? [{ root_flow_id: null }] : [])]) {
      expect((await f.post({ ...body, ...patch })).status).toBe(409);
    }
    expect(f.child()).toMatchObject({ ...lineage, status: 'succeeded' }); expect(f.links()).toHaveLength(1);
  });
  it('does not backfill old callers from parent_relation and rejects asserted ancestry on later replay', async () => {
    const f = await parentRelationFixture(fanout); closes.push(f.close);
    expect(await f.post()).toMatchObject({ status: 201, body: { data: unknownLineage } });
    expect((await f.post({ ...f.body, ...nested })).status).toBe(409);
    expect((await f.post({ ...f.body, ...unknownLineage })).status).toBe(200);
    expect(f.child()).toMatchObject(unknownLineage);
  });
  it('does not let correct-looking lineage bypass persisted parent authority', async () => {
    const f = await parentRelationFixture(fanout); closes.push(f.close);
    await f.db.exec("UPDATE node_executions SET status='succeeded' WHERE flow_id='parent'");
    expect((await f.post({ ...f.body, parent_flow_id: 'parent', root_flow_id: 'parent' })).status).toBe(409);
    expect(f.child()).toBeUndefined(); expect(f.links()).toEqual([]);
  });
});

async function trialFixture(saved = false) {
  const f = await fixture();
  await f.db.exec("INSERT INTO workflow_specs (workflow_id,spec_json) VALUES ('wf','{}')");
  const trial = { id: '00000000-0000-4000-8000-000000000001', workflowId: 'wf', taskId: 'task', revision: 1,
    stateVersion: 1, createdAtMs: 1, status: 'awaiting_run', flowId: null, mode: saved ? 'saved' : 'pinned',
    candidateCommit: 'candidate', packDigest: 'pack', inputDigest: 'input', target: { botId: 'bot', ownerId: 'owner' }, options: { workflowIds: ['wf', 'child-wf'] } };
  await new RepairReleaseRepository(f.db).insert('trial', { requestId: 'trial' }, trial);
  const body = { flow_id: 'trial-root', workflow_id: 'wf', status: 'running', started_at: 1, workflow_version: -1,
    origin_bot_id: 'bot:owner', ...(saved ? { origin_session_key: `agent:main:workflow-trial:${trial.id}` } : {
      run_request_id: trial.id, run_request_bot_id: 'bot', run_request_owner_id: 'owner',
      candidate_commit: trial.candidateCommit, pack_digest: trial.packDigest, input_digest: trial.inputDigest }) };
  return { ...f, body };
}
describe.each([false, true])('trial registration lineage (saved compatibility: %s)', saved => {
  it('keeps root/child lineage independent from trial identity, verifies replays after completion, and refuses backfill', async () => {
    const f = await trialFixture(saved);
    expect(await f.request(path, f.body)).toMatchObject({ status: 201, body: { data: unknownLineage } });
    const child = { ...f.body, flow_id: 'trial-child', workflow_id: 'child-wf', ...nested,
      ...(saved ? { origin_session_key: undefined, state_json: '{"subworkflowMeta":{"parentFlowId":"trial-root"}}' }
        : { run_request_parent_flow_id: 'trial-root' }) };
    expect(await f.request(path, child)).toMatchObject({ status: 201, body: { data: nested } });
    const { parent_flow_id: _reportedParent, root_flow_id: _reportedRoot, ...legacyChild } = child;
    expect(await f.request(path, { ...legacyChild, flow_id: 'legacy-trial-child' }))
      .toMatchObject({ status: 201, body: { data: unknownLineage } });
    await f.repo.updateCompletion('trial-child', { status: 'succeeded', completedAt: 10, inputJson: '{"changed":true}', succeededCount: 1, failedCount: 0 });
    await f.repo.updateStateJson('trial-child', '{"workflowId":"child-wf","currentPhase":"done"}');
    expect(await f.request(path, child)).toMatchObject({ status: 200, body: { data: { ...nested, status: 'succeeded' } } });
    const { parent_flow_id: _parent, root_flow_id: _root, ...omitted } = child;
    expect((await f.request(path, omitted)).status).toBe(200);
    for (const patch of [{ parent_flow_id: 'different' }, { root_flow_id: 'different' }, { root_flow_id: null }]) {
      expect((await f.request(path, { ...child, ...patch })).status).toBe(409);
    }
    expect((await f.request(path, { ...f.body, ...nested })).status).toBe(409);
    expect((await f.request(path, { ...f.body, ...unknownLineage })).status).toBe(200);
    expect(await f.repo.findFullByFlowId('trial-root')).toMatchObject(unknownLineage);
    expect(await f.repo.findFullByFlowId('trial-child')).toMatchObject({ ...nested, input_json: '{"changed":true}' });
    expect(await f.repo.countRuns()).toBe(3);
  });
});

describe('signed ordinary association lineage', () => {
  const start = { type: 'ordinary-run-request.register.v1', ordinary_request_key: 'key', ordinary_parent_flow_id: 'parent',
    run: { flow_id: 'child', workflow_id: 'wf', status: 'pending', started_at: 1 } };
  const registerPath = `${path}/request-association/register`;
  async function ordinaryFixture() {
    const f = await fixture();
    expect(await f.repo.insert({ flowId: 'parent', workflowId: 'wf', status: 'running', startedAt: 1 })).toBe(true);
    return f;
  }
  it('preserves historical fingerprint bytes and compares lineage against the winning row, not mutable run state', async () => {
    const f = await ordinaryFixture();
    const request = { ...start, run: { ...start.run, ...nested } };
    expect((await f.request(registerPath, request)).status).toBe(201);
    const fields = ['wf', null, 'pending', null, null, null, 0, null, null, null, null, null, null, null, null, null, null, null];
    const fingerprint = createHash('sha256').update(JSON.stringify(fields)).digest('hex');
    expect(f.raw.prepare('SELECT request_fingerprint FROM ordinary_run_requests').get()).toEqual({ request_fingerprint: fingerprint });
    await f.repo.updateCompletion('child', { status: 'succeeded', completedAt: 10, inputJson: '{"advanced":true}', succeededCount: 1, failedCount: 0 });
    await f.repo.updateStateJson('child', '{"workflowId":"wf","currentPhase":"done"}');
    await f.repo.updateCurrentPhase('child', 'done');
    expect(await f.request(registerPath, { ...request, run: { ...request.run, flow_id: 'proposed-again', started_at: 99 } }))
      .toMatchObject({ status: 200, body: { data: { created: false, flow_id: 'child', parent_flow_id: 'parent' } } });
    expect((await f.request(registerPath, start)).status).toBe(200);
    for (const patch of [{ parent_flow_id: 'conflict' }, { root_flow_id: 'conflict' }, { parent_flow_id: null }, { root_flow_id: null },
      { parent_flow_id: nested.parent_flow_id.trim() }, { root_flow_id: nested.root_flow_id.normalize('NFD') }]) {
      expect((await f.request(registerPath, { ...request, run: { ...request.run, ...patch } })).status).toBe(409);
    }
    expect(await f.repo.findFullByFlowId('child')).toMatchObject({ ...nested, status: 'succeeded', current_phase: 'done' });
    expect(await f.repo.countRuns()).toBe(2);
  });
  it('treats explicit null/omission as historical unknown and does not backfill from association parent', async () => {
    const f = await ordinaryFixture();
    expect((await f.request(registerPath, start)).status).toBe(201);
    expect((await f.request(registerPath, { ...start, run: { ...start.run, ...unknownLineage } })).status).toBe(200);
    expect((await f.request(registerPath, { ...start, run: { ...start.run, ...nested } })).status).toBe(409);
    expect(await f.repo.findFullByFlowId('child')).toMatchObject(unknownLineage);
  });
  it('does not trust lineage as authority, weaken authentication, or move the signed route boundary', async () => {
    const f = await ordinaryFixture();
    const request = { ...start, run: { ...start.run, ...nested } };
    f.authenticate.mockResolvedValueOnce(null);
    expect((await f.request(registerPath, request)).status).toBe(401);
    f.authorize.mockResolvedValue(false);
    expect((await f.request(registerPath, request)).status).toBe(404);
    expect((await f.request(path, request)).status).toBe(503);
    f.authorize.mockResolvedValue(true);
    expect((await f.request(registerPath, request)).status).toBe(201);
    f.authorize.mockResolvedValue(false);
    expect((await f.request(registerPath, { ...request, run: { ...request.run, root_flow_id: 'conflict' } })).status).toBe(404);
    expect(await f.repo.countRuns()).toBe(2);
  });
  it('validates nested IDs with 400 rather than truncating them', async () => {
    const f = await ordinaryFixture();
    for (const patch of [{ parent_flow_id: '' }, { root_flow_id: {} }, { parent_flow_id: '🚀'.repeat(256) }]) {
      expect((await f.request(registerPath, { ...start, run: { ...start.run, ...patch } })).status).toBe(400);
    }
    expect(await f.repo.countRuns()).toBe(1);
  });
});
