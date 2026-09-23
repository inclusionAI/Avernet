// @vitest-environment node
import { afterEach, describe, expect, it } from 'vitest';
import { createRepairWorkbenchService } from '../repair-batch-service.js';
import { digestRepairJson } from '../../contracts/repair-batch.js';
import { repairFixture } from './repair-batch-fixtures.js';

describe('repair workbench control service', () => {
  const fixtures: Awaited<ReturnType<typeof repairFixture>>[] = [];
  afterEach(async () => { for (const fixture of fixtures.splice(0)) await fixture.db.close(); });
  async function setup(count = 2, enabled = true) {
    const f = await repairFixture(count); fixtures.push(f);
    const service = createRepairWorkbenchService(f.db, f.sourcePort, enabled ? f.executionPort : undefined);
    const inbox = await service.candidates('wf-1');
    const request = { workflowId: 'wf-1', itemIds: f.items.map(i => i.itemId), inputDigest: inbox.inputDigest, instructions: '', requestId: 'create-1' };
    return { ...f, service, inbox, request };
  }
  it('keeps GET read-only, merges lifecycle history, and allows no_action/restore without AIS', async () => {
    const f = await setup(2, false);
    expect(f.inbox.capabilities).toMatchObject({ generation: false, publication: false });
    expect(await f.db.query('SELECT * FROM workflow_repair_items')).toEqual([]);
    await expect(f.service.create('human', f.request)).rejects.toMatchObject({ code: 'CAPABILITY_UNAVAILABLE' });
    const disposition = { workflowId: 'wf-1', itemId: 'item-0', inputDigest: f.inbox.inputDigest, expectedStateVersion: 0,
      contentRevision: 1, action: 'no_action' as const, reason: 'Later', requestId: 'ignore-1' };
    const ignored = await f.service.disposition('human', disposition);
    expect(ignored.state).toBe('no_action');
    expect(await f.service.disposition('human', disposition)).toEqual(ignored);
    f.items.splice(0, 1);
    const historical = await f.service.candidates('wf-1');
    expect(historical.items.find(i => i.itemId === 'item-0')).toMatchObject({ state: 'no_action', sourceAvailable: false });
    const restored = await f.service.disposition('human', { ...disposition, action: 'restore', inputDigest: historical.inputDigest, expectedStateVersion: 1, requestId: 'restore-1' });
    expect(restored.state).toBe('pending');
  });
  it.each([39, 100])('freezes %i sources and atomically creates one running task and one draft step', async count => {
    const f = await setup(count);
    const revision = await f.service.create('human', f.request);
    expect(revision.input.items).toHaveLength(count);
    expect(await f.db.query('SELECT task_type, status, created_by FROM ce_tasks')).toEqual([{ task_type: 'workflow_repair', status: 'running', created_by: 'human' }]);
    expect(await f.db.query('SELECT step_type FROM ce_steps')).toEqual([{ step_type: 'workflow_repair_draft' }]);
    expect(f.calls).toHaveLength(1);
    expect(f.calls[0]).toMatchObject({ actorId: 'human', identity: { attempt: 1 }, input: revision.input });
    expect((await f.service.task(revision.taskId)).execution).toMatchObject({ status: 'dispatched', jobId: 'job-1', attempt: 1 });
    expect((await f.service.create('human', f.request)).taskId).toBe(revision.taskId);
    expect(f.calls).toHaveLength(1);
  });
  it('rejects >100, stale digest, byte limits and cross-workflow selections before persisting tasks', async () => {
    const f = await setup(101);
    await expect(f.service.create('human', f.request)).rejects.toMatchObject({ code: 'INVALID_INPUT' });
    await expect(f.service.create('human', { ...f.request, itemIds: ['item-0'], inputDigest: '0'.repeat(64) })).rejects.toMatchObject({ code: 'SOURCE_CHANGED' });
    await expect(f.service.create('human', { ...f.request, itemIds: ['item-0'], instructions: '界'.repeat(22000) })).rejects.toMatchObject({ code: 'PAYLOAD_TOO_LARGE' });
    const other = await f.service.candidates('wf-2');
    await expect(f.service.create('human', { ...f.request, workflowId: 'wf-2', itemIds: ['item-0'], inputDigest: other.inputDigest })).rejects.toMatchObject({ code: 'ITEM_NOT_FOUND' });
    expect(await f.db.query('SELECT * FROM ce_tasks')).toEqual([]);
  });
  it('arbitrates concurrent create attempts and rejects reused requests with changed instructions', async () => {
    const f = await setup();
    const attempts = await Promise.allSettled([f.service.create('human', f.request), f.service.create('human', { ...f.request, requestId: 'create-2' })]);
    expect(attempts.filter(r => r.status === 'fulfilled')).toHaveLength(1);
    expect(await f.db.query('SELECT * FROM ce_tasks')).toHaveLength(1);
    await expect(f.service.create('human', { ...f.request, instructions: 'changed' })).rejects.toMatchObject({ code: 'IDEMPOTENCY_MISMATCH' });
  });
  it('retains a failed dispatch as the same frozen attempt and only retries its idempotent dispatch', async () => {
    const f = await setup();
    let attempts = 0;
    f.executionPort.dispatch = async () => { if (++attempts === 1) throw new Error('network outcome unknown'); return { jobId: 'job-retry' }; };
    const revision = await f.service.create('human', f.request);
    expect(revision.phase).toBe('drafting');
    expect(await f.db.query('SELECT status FROM ce_steps')).toEqual([{ status: 'dispatch_failed' }]);
    f.items.splice(0);
    const retried = await f.service.create('human', f.request);
    expect(retried.input).toEqual(revision.input);
    expect(attempts).toBe(2);
    expect(await f.db.query('SELECT * FROM ce_tasks')).toHaveLength(1);
  });
  it('exposes dispatch failure after reload and explicitly retries the same input without resolving sources', async () => {
    const f = await setup();
    f.executionPort.dispatch = async () => { throw new Error('unknown outcome'); };
    const revision = await f.service.create('human', f.request);
    expect((await f.service.task(revision.taskId)).execution).toMatchObject({ status: 'dispatch_failed', errorCode: 'DISPATCH_UNCERTAIN' });
    f.executionPort.dispatch = async input => { f.calls.push(input); return { jobId: 'job-retry' }; };
    f.executionPort.resolveBaseline = async () => { throw new Error('must not reload baseline'); };
    f.sourcePort.load = async () => { throw new Error('must not reload sources'); };
    await f.service.retryDispatch('human', revision.taskId, 1);
    expect(f.calls[0]).toMatchObject({ input: revision.input, identity: { attempt: 2 } });
    expect((await f.service.task(revision.taskId)).execution).toMatchObject({ status: 'dispatched', jobId: 'job-retry', attempt: 2 });
    await expect(f.service.retryDispatch('human', revision.taskId, 1)).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
    expect(await f.db.query('SELECT * FROM workflow_repair_revisions')).toHaveLength(1);
  });
  it('keeps legacy applied sources read-only and blocks an active legacy application', async () => {
    const f = await setup();
    f.sourcePort.load = async () => f.items.map(item => ({ item, episodeKey: 'initial', initialState: 'verified' }));
    const old = await f.service.candidates('wf-1');
    expect(old.items[0]).toMatchObject({ state: 'verified', sourceAvailable: false });
    await expect(f.service.create('human', { ...f.request, inputDigest: old.inputDigest })).rejects.toMatchObject({ code: 'ITEM_NOT_FOUND' });
    f.sourcePort.load = async () => f.items.map(item => ({ item, episodeKey: 'initial' }));
    await f.db.exec(`INSERT INTO ce_tasks (task_id, task_name, task_type, user_id, bot_id, status, config_json, created_by)
      VALUES ('legacy', 'Old apply', 'suggestion_apply', 'human', 'bot', 'running', ?, 'human')`, [JSON.stringify({ applicationInput: { workflowId: 'wf-1' } })]);
    await expect(f.service.create('human', f.request)).rejects.toMatchObject({ code: 'ACTIVE_TASK_CONFLICT' });
  });
  it('rolls back revision/item ownership when task step persistence fails', async () => {
    const f = await setup();
    f.raw.exec(`CREATE TRIGGER fail_step BEFORE INSERT ON ce_steps BEGIN SELECT RAISE(ABORT, 'step unavailable'); END`);
    await expect(f.service.create('human', f.request)).rejects.toThrow('step unavailable');
    expect(await f.db.query('SELECT * FROM ce_tasks')).toHaveLength(0);
    expect(await f.db.query('SELECT * FROM workflow_repair_revisions')).toHaveLength(0);
    expect(await f.db.query('SELECT * FROM workflow_repair_items')).toHaveLength(0);
    expect(f.calls).toHaveLength(0);
  });
  it('refreshes same-content evidence without undoing no_action or replacing frozen context', async () => {
    const f = await setup(1);
    f.items[0].context = { problem: 'first observation', evidence: ['event-1'] };
    const inbox = await f.service.candidates('wf-1');
    await f.service.disposition('human', { workflowId: 'wf-1', itemId: 'item-0', inputDigest: inbox.inputDigest,
      expectedStateVersion: 0, contentRevision: 1, action: 'no_action', reason: '', requestId: 'ignore' });
    f.items[0].context = { problem: 'more evidence', evidence: ['event-1', 'event-2'] };
    const refreshed = await f.service.candidates('wf-1');
    expect(refreshed.items[0]).toMatchObject({ state: 'no_action', context: f.items[0].context });
    expect(JSON.parse((await f.db.query<{ content_json: string }>('SELECT content_json FROM workflow_repair_items'))[0].content_json).context.evidence).toEqual(['event-1']);
    await f.service.disposition('human', { workflowId: 'wf-1', itemId: 'item-0', inputDigest: refreshed.inputDigest,
      expectedStateVersion: 1, contentRevision: 1, action: 'restore', reason: '', requestId: 'restore' });
    const created = await f.service.create('human', { ...f.request, inputDigest: refreshed.inputDigest });
    expect(created.input.items[0].context?.evidence).toEqual(['event-1', 'event-2']);
    f.items[0].context = { problem: 'third observation', evidence: ['event-3'] };
    await f.service.candidates('wf-1');
    expect((await f.service.task(created.taskId)).latestAttempt.input.items[0].context?.evidence).toEqual(['event-1', 'event-2']);
  });
  it('rejects incomplete or mismatched trusted reports without settling the task', async () => {
    const f = await setup();
    const created = await f.service.create('human', f.request);
    const draft = { candidateCommit: 'd'.repeat(40), parentCandidateCommit: created.input.baseline.packCommit, packDigest: 'e'.repeat(40),
      itemResults: [{ itemId: 'item-0', status: 'changed', reason: 'One only' }] };
    const checks = { schemaVersion: 'workflow-repair-checks/v1', candidateCommit: draft.candidateCommit, packDigest: draft.packDigest,
      candidateDigest: digestRepairJson(draft), static: { status: 'passed' }, mock: { status: 'not_covered' }, coverage: [{ itemId: 'item-0' }] };
    const report = { workflowId: 'wf-1', taskId: created.taskId, revision: 1, expectedStateVersion: 0, status: 'succeeded' as const, draft, checks };
    await expect(f.service.report(report)).rejects.toMatchObject({ code: 'INVALID_INPUT' });
    await expect(f.service.report({ ...report, checks: { ...checks, candidateCommit: 'f'.repeat(40) } })).rejects.toMatchObject({ code: 'INVALID_INPUT' });
    expect((await f.service.task(created.taskId)).latestAttempt.phase).toBe('drafting');
    expect((await f.service.task(created.taskId)).execution?.status).toBe('dispatched');
  });
  it('polls the stored AIS job and settles a Git-backed result without a callback', async () => {
    const f = await setup();
    const created = await f.service.create('human', f.request);
    const execution = (await f.service.task(created.taskId)).execution!;
    expect(execution).toMatchObject({ jobId: 'job-1', attempt: 1, rawStatus: null });
    f.setRemoteStatus({ status: 'running', rawStatus: 'executing', errorMessage: null });
    expect(await f.service.reconcile(10)).toBe(0);
    expect((await f.service.task(created.taskId)).execution).toMatchObject({ status: 'running', rawStatus: 'executing' });
    const draft = { candidateCommit: 'd'.repeat(40), parentCandidateCommit: created.input.baseline.packCommit, packDigest: 'e'.repeat(40),
      itemResults: f.items.map(i => ({ itemId: i.itemId, status: 'changed', reason: 'Fixed' })) };
    const checks = { schemaVersion: 'workflow-repair-checks/v1', candidateCommit: draft.candidateCommit, packDigest: draft.packDigest,
      candidateDigest: digestRepairJson(draft), static: { status: 'passed' }, mock: { status: 'not_covered' }, coverage: f.items.map(i => ({ itemId: i.itemId })) };
    f.setGeneratedResult({ status: 'succeeded', draft, checks });
    f.setRemoteStatus({ status: 'success', rawStatus: 'success', errorMessage: null });
    expect(await f.service.reconcile(10)).toBe(1);
    expect((await f.service.task(created.taskId)).latestAttempt.phase).toBe('review');
    expect((await f.service.task(created.taskId)).execution).toMatchObject({ status: 'succeeded', jobId: 'job-1' });
  });
  it('persists cancellation before best-effort stopping the exact AIS attempt', async () => {
    const f = await setup();
    const created = await f.service.create('human', f.request);
    await f.service.cancel('human', created.taskId, 1);
    expect((await f.service.task(created.taskId)).latestAttempt.phase).toBe('cancelled');
    expect(f.stops).toHaveLength(1);
    expect(f.stops[0]).toMatchObject({ jobId: 'job-1', identity: { attempt: 1 }, input: created.input });
  });
  it('reports oversized inbox reads explicitly instead of dropping items', async () => {
    const f = await setup(13);
    f.items.forEach(item => { item.context = { evidence: 'x'.repeat(350000) }; });
    const outcome = await f.service.candidates('wf-1').then(() => 'unexpected success', error => error.code);
    expect(outcome).toBe('PAYLOAD_TOO_LARGE');
    expect(await f.db.query('SELECT * FROM workflow_repair_items')).toHaveLength(0);
  });
  it('settles trusted success to review and failed iteration retains success; cancellation releases items', async () => {
    const f = await setup();
    f.executionPort.dispatch = async () => { throw new Error('uncertain dispatch'); };
    const r1 = await f.service.create('human', f.request);
    const draft = { candidateCommit: 'd'.repeat(40), parentCandidateCommit: r1.input.baseline.packCommit, packDigest: 'e'.repeat(40),
      itemResults: f.items.map(i => ({ itemId: i.itemId, status: 'changed', reason: 'Fixed' })) };
    const checks = { schemaVersion: 'workflow-repair-checks/v1', candidateCommit: draft.candidateCommit, packDigest: draft.packDigest,
      candidateDigest: digestRepairJson(draft), static: { status: 'passed' }, mock: { status: 'not_covered' }, coverage: f.items.map(i => ({ itemId: i.itemId })) };
    await f.service.report({ workflowId: 'wf-1', taskId: r1.taskId, revision: 1, expectedStateVersion: 0, status: 'succeeded', draft, checks });
    expect((await f.service.task(r1.taskId)).latestAttempt.phase).toBe('review');
    expect((await f.service.task(r1.taskId)).execution).toMatchObject({ status: 'succeeded', errorCode: null, attempt: 1 });
    expect((await f.db.query<{ status: string }>('SELECT status FROM ce_tasks'))[0].status).toBe('running');
    f.executionPort.resolveBaseline = async () => { throw new Error('feedback must retain frozen baseline after deployment changes'); };
    const r2 = await f.service.revise('human', r1.taskId, { ...f.request, requestId: 'revise-2', expectedAttemptRevision: 1, parentCandidateCommit: draft.candidateCommit, feedback: 'Try more' });
    await f.service.report({ workflowId: 'wf-1', taskId: r2.taskId, revision: 2, expectedStateVersion: 0, status: 'failed', error: { code: 'MODEL_FAILED' } });
    const task = await f.service.task(r1.taskId);
    expect(task.latestSuccessful?.revision).toBe(1);
    expect(task.latestAttempt.phase).toBe('failed');
    expect(JSON.parse((await f.db.query<{ config_json: string }>('SELECT config_json FROM ce_tasks'))[0].config_json)).toMatchObject({ latestAttemptRevision: 2, latestSuccessfulRevision: 1 });
    await f.service.cancel('human', r1.taskId, 2);
    expect((await f.service.candidates('wf-1')).items.every(i => i.state === 'pending')).toBe(true);
    await expect(f.service.report({ workflowId: 'wf-1', taskId: r2.taskId, revision: 2, expectedStateVersion: 1, status: 'failed', error: { code: 'LATE' } })).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
  });
});
