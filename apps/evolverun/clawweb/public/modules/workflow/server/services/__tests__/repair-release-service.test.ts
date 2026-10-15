// @vitest-environment node
import { afterEach, describe, expect, it } from 'vitest';
import { repairFixture } from './repair-batch-fixtures.js';
import { createRepairWorkbenchService } from '../repair-batch-service.js';
import { createRepairReleaseService } from '../repair-release-service.js';
import { digestRepairJson } from '../../contracts/repair-batch.js';
import { repairReleaseMigrations } from '@avernet/clawweb-shared/server/schema-repair-release';
import type { RepairPublicationPort } from '../../contracts/repair-release.js';
import express from 'express';
import { createRepairBatchesRouter } from '../../routes/repair-batches.js';

describe('reviewed candidate publication', () => {
  const fixtures: Awaited<ReturnType<typeof repairFixture>>[] = [];
  afterEach(async () => { for (const f of fixtures.splice(0)) await f.db.close(); });
  async function setup(count = 1) {
    const f = await repairFixture(count); fixtures.push(f);
    for (const migration of repairReleaseMigrations) for (const sql of migration.sql) await f.db.exec(f.db.dialect.renderDdl(sql));
    const baseline = { ...await f.executionPort.resolveBaseline('wf-1'), activeDeployNumber: 7, releaseRevision: 7 };
    f.executionPort.resolveBaseline = async () => baseline;
    f.executionPort.diff = async input => ({ ...input, files: [{ path: 'workflows/wf-1.yaml', status: 'M',
      before: 'old\n', after: 'new\n', patch: '@@ -1 +1 @@\n-old\n+new\n', binary: false, truncated: false }], truncated: false });
    const workbench = createRepairWorkbenchService(f.db, f.sourcePort, f.executionPort);
    const inbox = await workbench.candidates('wf-1');
    const revision = await workbench.create('reviewer', { workflowId: 'wf-1', itemIds: f.items.map(item => item.itemId),
      inputDigest: inbox.inputDigest, requestId: 'generate', instructions: '' });
    const draft = { candidateCommit: 'd'.repeat(40), parentCandidateCommit: baseline.packCommit, packDigest: 'e'.repeat(40),
      itemResults: f.items.map(item => ({ itemId: item.itemId, status: 'changed', reason: 'fixed' })) };
    await workbench.report({ workflowId: 'wf-1', taskId: revision.taskId, revision: 1, expectedStateVersion: 0,
      status: 'succeeded', draft, checks: { schemaVersion: 'workflow-repair-checks/v1', candidateCommit: draft.candidateCommit,
        packDigest: draft.packDigest, candidateDigest: digestRepairJson(draft), static: { status: 'passed' },
        mock: { status: 'not_covered' }, coverage: f.items.map(item => ({ itemId: item.itemId, mode: 'not_covered' })), uncoveredChecks: ['business_effect'] } });
    const dispatches: unknown[] = [];
    let uncertain = false;
    const publisher: RepairPublicationPort = {
      targets: async (_workflow, actor) => actor === 'reviewer' ? [{ id: 'target-1', botId: 'bot-1', ownerId: 'owner', environment: 'pre', label: 'authorized Bot' }] : [],
      activeBaseline: async () => baseline,
      dispatch: async deployment => { dispatches.push(deployment); if (uncertain) throw new Error('connection lost'); return { status: 'running' }; },
      reconcile: async deployment => ({ status: 'succeeded', receipt: { requestId: deployment.requestId,
        candidateCommit: deployment.candidateCommit, packDigest: deployment.packDigest, deployNumber: 8,
        releaseVersion: 8, tagName: 'deploy/wf-1/#8', targetId: deployment.target.id } }),
    };
    const service = createRepairReleaseService({ db: f.db, workbench, publisher });
    const request = { revision: 1, expectedStateVersion: 1, requestId: 'approve', targetId: 'target-1', acknowledgeUncoveredChecks: true };
    return { ...f, workbench, service, revision, request, dispatches, baseline, publisher, setUncertain: () => { uncertain = true; } };
  }
  it('confirms without deploying and binds the reviewed commit and target', async () => {
    const f = await setup();
    const approval = await f.service.confirm('reviewer', f.revision.taskId, f.request);
    expect(approval).toMatchObject({ candidateCommit: 'd'.repeat(40), baselineDeployNumber: 7, target: { id: 'target-1' } });
    expect(f.dispatches).toEqual([]);
    expect((await f.service.detail(f.revision.taskId)).approval).toEqual(approval);
    expect((await f.workbench.task(f.revision.taskId)).latestAttempt.phase).toBe('review');
  });
  it('rejects unauthorized targets and stale review versions', async () => {
    const f = await setup();
    await expect(f.service.confirm('stranger', f.revision.taskId, f.request)).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
    await expect(f.service.confirm('reviewer', f.revision.taskId, { ...f.request, expectedStateVersion: 0 })).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
  });
  it('requires explicit acknowledgement for uncovered business checks', async () => {
    const f = await setup();
    await expect(f.service.confirm('reviewer', f.revision.taskId, { ...f.request, acknowledgeUncoveredChecks: false })).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
  });
  it('does not dispatch without approval and stops stale-baseline publication', async () => {
    const f = await setup();
    await expect(f.service.deploy('reviewer', f.revision.taskId, { revision: 1, expectedApprovalVersion: 0, requestId: 'deploy' })).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
    const approval = await f.service.confirm('reviewer', f.revision.taskId, f.request);
    f.publisher.activeBaseline = async () => ({ ...f.baseline, activeDeployNumber: 9 });
    await expect(f.service.deploy('reviewer', f.revision.taskId, { revision: 1, expectedApprovalVersion: approval.stateVersion, requestId: 'deploy' })).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
    expect(f.dispatches).toEqual([]);
  });
  it('reconciles a lost dispatch response without republishing and awaits real verification', async () => {
    const f = await setup();
    const approval = await f.service.confirm('reviewer', f.revision.taskId, f.request);
    f.setUncertain();
    const deployment = await f.service.deploy('reviewer', f.revision.taskId, { revision: 1, expectedApprovalVersion: approval.stateVersion, requestId: 'deploy' });
    expect(deployment.status).toBe('uncertain');
    const reconciled = await f.service.reconcile('reviewer', f.revision.taskId, deployment.id);
    expect(reconciled).toMatchObject({ status: 'succeeded', receipt: { deployNumber: 8, candidateCommit: 'd'.repeat(40) } });
    expect(f.dispatches).toHaveLength(1);
    expect((await f.workbench.task(f.revision.taskId)).latestAttempt.phase).toBe('published');
    expect((await f.workbench.item('wf-1', 'item-0')).state).toBe('awaiting_verification');
  });
  it('retries a confirmed failed publication without discarding the reviewed candidate', async () => {
    const f = await setup();
    const approval = await f.service.confirm('reviewer', f.revision.taskId, f.request);
    f.publisher.dispatch = async () => ({ status: 'failed', error: 'target rejected before publication' });
    const failed = await f.service.deploy('reviewer', f.revision.taskId, { revision: 1, expectedApprovalVersion: approval.stateVersion, requestId: 'attempt-1' });
    expect(failed.status).toBe('failed');
    f.publisher.dispatch = async () => ({ status: 'running' });
    const retry = await f.service.deploy('reviewer', f.revision.taskId, { revision: 1, expectedApprovalVersion: approval.stateVersion, requestId: 'attempt-2' });
    expect(retry).toMatchObject({ status: 'running', candidateCommit: approval.candidateCommit });
  });
  it('prevents cancellation while an external publication result is unknown', async () => {
    const f = await setup();
    const approval = await f.service.confirm('reviewer', f.revision.taskId, f.request);
    f.setUncertain();
    await f.service.deploy('reviewer', f.revision.taskId, { revision: 1, expectedApprovalVersion: approval.stateVersion, requestId: 'deploy' });
    await expect(f.workbench.cancel('reviewer', f.revision.taskId, 1)).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
  });
  it('resumes the same uncertain publication idempotently, without another dispatch or release number', async () => {
    const f = await setup();
    const approved = await f.service.confirm('reviewer', f.revision.taskId, f.request);
    f.setUncertain();
    const deployment = await f.service.deploy('reviewer', f.revision.taskId, { revision: 1, expectedApprovalVersion: approved.stateVersion, requestId: 'lost-publish' });
    let resumes = 0;
    f.publisher.resume = async original => { resumes++; expect(original.id).toBe(deployment.id); return { status: 'running' }; };
    await f.service.resume('reviewer', f.revision.taskId, deployment.id, 'resume-intent');
    const recovered = await f.service.resume('reviewer', f.revision.taskId, deployment.id, 'resume-intent');
    expect(resumes).toBe(1); expect(f.dispatches).toHaveLength(1); expect(recovered.receipt?.deployNumber).toBe(8);
    expect((await f.service.detail(f.revision.taskId)).deployments).toHaveLength(1);
  });
  it('publishes the last successful candidate after feedback failure and binds verification to its actual revision', async () => {
    const f = await setup();
    const inbox = await f.workbench.candidates('wf-1');
    await f.workbench.revise('reviewer', f.revision.taskId, { workflowId: 'wf-1', itemIds: ['item-0'], inputDigest: inbox.inputDigest,
      requestId: 'feedback', instructions: '', expectedAttemptRevision: 1, parentCandidateCommit: 'd'.repeat(40), feedback: '调整边界' });
    await f.workbench.report({ workflowId: 'wf-1', taskId: f.revision.taskId, revision: 2, expectedStateVersion: 0, status: 'failed', error: { code: 'AIS_FAILED' } });
    const approved = await f.service.confirm('reviewer', f.revision.taskId, f.request);
    const deployment = await f.service.deploy('reviewer', f.revision.taskId, { revision: 1, expectedApprovalVersion: approved.stateVersion, requestId: 'deploy-old-success' });
    await f.service.reconcile('reviewer', f.revision.taskId, deployment.id);
    expect(await f.workbench.item('wf-1', 'item-0')).toMatchObject({ state: 'awaiting_verification', activeRevision: 1 });
    await expect(f.workbench.retryGeneration('reviewer', f.revision.taskId, { expectedRevision: 2, requestId: 'regen-after-publish' })).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
    expect((await f.workbench.candidates('wf-1')).tasks[0].phase).toBe('published');
  });
  it('reserves items released by failed feedback before publishing an older candidate', async () => {
    const f = await setup(2);
    const inbox = await f.workbench.candidates('wf-1');
    await f.workbench.revise('reviewer', f.revision.taskId, { workflowId: 'wf-1', itemIds: ['item-0'],
      inputDigest: inbox.inputDigest, requestId: 'feedback-subset', instructions: '', expectedAttemptRevision: 1,
      parentCandidateCommit: 'd'.repeat(40), feedback: 'only item zero' });
    await f.workbench.report({ workflowId: 'wf-1', taskId: f.revision.taskId, revision: 2,
      expectedStateVersion: 0, status: 'failed', error: { code: 'AIS_FAILED' } });
    expect(await f.workbench.item('wf-1', 'item-1')).toMatchObject({ state: 'pending', activeTaskId: null });
    const approved = await f.service.confirm('reviewer', f.revision.taskId, f.request);
    f.publisher.dispatch = async () => {
      const reserved = await f.workbench.item('wf-1', 'item-1');
      expect(reserved).toMatchObject({ state: 'processing', activeTaskId: f.revision.taskId, activeRevision: 1 });
      const current = await f.workbench.candidates('wf-1');
      await expect(f.workbench.disposition('reviewer', { workflowId: 'wf-1', itemId: reserved.itemId,
        inputDigest: current.inputDigest, contentRevision: reserved.contentRevision, expectedStateVersion: reserved.stateVersion,
        requestId: 'disposition-during-publish', action: 'no_action', reason: 'skip' })).rejects.toMatchObject({ code: 'ITEM_BUSY' });
      return { status: 'running' };
    };
    const deployment = await f.service.deploy('reviewer', f.revision.taskId, { revision: 1,
      expectedApprovalVersion: approved.stateVersion, requestId: 'deploy-older' });
    expect(deployment.status).toBe('running');
    await f.service.reconcile('reviewer', f.revision.taskId, deployment.id);
    expect(await f.workbench.item('wf-1', 'item-1')).toMatchObject({ state: 'awaiting_verification', activeRevision: 1 });
  });

  it('reauthorizes the target before reconciling and never calls the adapter after permission loss', async () => {
    const f = await setup();
    const approved = await f.service.confirm('reviewer', f.revision.taskId, f.request);
    const deployment = await f.service.deploy('reviewer', f.revision.taskId, { revision: 1,
      expectedApprovalVersion: approved.stateVersion, requestId: 'deploy' });
    let reconciles = 0;
    const original = f.publisher.reconcile;
    f.publisher.reconcile = async value => { reconciles++; return original(value); };
    f.publisher.targets = async () => [];
    await expect(f.service.reconcile('reviewer', f.revision.taskId, deployment.id)).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
    expect(reconciles).toBe(0);
    expect((await f.service.detail(f.revision.taskId)).deployments[0].status).toBe('running');
  });

  it('reconfirms a different production target after failure without regenerating the candidate', async () => {
    const f = await setup();
    const approval = await f.service.confirm('reviewer', f.revision.taskId, f.request);
    f.publisher.dispatch = async () => ({ status: 'failed', error: 'entry missing' });
    await f.service.deploy('reviewer', f.revision.taskId, { revision: 1, expectedApprovalVersion: approval.stateVersion, requestId: 'failed-deploy' });
    f.publisher.targets = async () => [{ id: 'new-prod', botId: 'new-bot', ownerId: 'owner', environment: 'prod', label: '生产 Bot' }];
    const latest = (await f.workbench.task(f.revision.taskId)).latestSuccessful!;
    const updated = await f.service.confirm('reviewer', f.revision.taskId, { ...f.request, targetId: 'new-prod',
      expectedStateVersion: latest.stateVersion, requestId: 'change-target' });
    expect(updated.target.id).toBe('new-prod');
    expect(updated.candidateCommit).toBe(approval.candidateCommit);
    expect((await f.service.detail(f.revision.taskId)).deployments).toHaveLength(1);
    f.publisher.dispatch = async () => ({ status: 'running' });
    expect(await f.service.deploy('reviewer', f.revision.taskId, { revision: 1,
      expectedApprovalVersion: updated.stateVersion, requestId: 'new-target-deploy' })).toMatchObject({ status: 'running', approvalId: updated.id });
  });

  it('reauthorizes the frozen target before stopping, including idempotent retries', async () => {
    const f = await setup();
    const approved = await f.service.confirm('reviewer', f.revision.taskId, f.request);
    const deployment = await f.service.deploy('reviewer', f.revision.taskId, { revision: 1,
      expectedApprovalVersion: approved.stateVersion, requestId: 'publish' });
    const stop = async () => ({ status: 'failed' as const });
    f.publisher.stop = stop;
    await expect(f.service.stop('stranger', f.revision.taskId, deployment.id, 'stop')).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
    expect((await f.service.detail(f.revision.taskId)).deployments[0].status).toBe('running');
    expect((await f.service.stop('reviewer', f.revision.taskId, deployment.id, 'stop')).status).toBe('failed');
    f.publisher.targets = async () => [];
    await expect(f.service.stop('reviewer', f.revision.taskId, deployment.id, 'stop')).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
  });

  it('authorizes HTTP approval and deployment separately from read-only review', async () => {
    const f = await setup();
    const app = express(); app.use(express.json());
    app.use('/repairs', createRepairBatchesRouter({ service: f.workbench, release: f.service,
      authorize: async req => ({ actorId: 'reviewer', canEdit: req.headers['x-test-role'] !== 'viewer' }) }));
    const server = app.listen(0);
    await new Promise<void>(resolve => server.once('listening', resolve));
    const address = server.address() as { port: number };
    const url = `http://127.0.0.1:${address.port}/repairs/${f.revision.taskId}`;
    try {
      const denied = await fetch(`${url}/confirm`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'x-test-role': 'viewer' }, body: JSON.stringify(f.request) });
      expect(denied.status).toBe(403);
      expect((await f.service.detail(f.revision.taskId)).approval).toBeNull();
      const approvalResponse = await fetch(`${url}/confirm`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(f.request) });
      expect(approvalResponse.status).toBe(200);
      expect(f.dispatches).toEqual([]);
      const shown = await (await fetch(url)).json();
      expect(shown.release.approval.candidateCommit).toBe('d'.repeat(40));
      const hidden = await (await fetch(url, { headers: { 'x-test-role': 'viewer' } })).json();
      expect(hidden.capabilities.publication).toBe(false);
    } finally { await new Promise<void>(resolve => { server.close(() => resolve()); server.closeAllConnections(); }); }
  });
});
