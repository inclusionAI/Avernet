// @vitest-environment node
import { afterEach, describe, expect, it } from 'vitest';
import { repairFixture } from './repair-batch-fixtures.js';
import { createRepairWorkbenchService } from '../repair-batch-service.js';
import { createRepairAutoVerification } from '../repair-auto-verification.js';
import { createRepairReleaseService } from '../repair-release-service.js';
import { digestRepairJson } from '../../contracts/repair-batch.js';
import { repairReleaseMigrations } from '@avernet/clawweb-shared/server/schema-repair-release';

describe('automatic batch candidate chain', () => {
  const fixtures: Awaited<ReturnType<typeof repairFixture>>[] = [];
  afterEach(async () => { for (const f of fixtures.splice(0)) await f.db.close(); });
  async function setup() {
    const f = await repairFixture(3); fixtures.push(f);
    for (const m of repairReleaseMigrations) for (const sql of m.sql) await f.db.exec(f.db.dialect.renderDdl(sql));
    const baseline = { ...await f.executionPort.resolveBaseline('wf-1'), activeDeployNumber: 7 };
    f.executionPort.resolveBaseline = async () => baseline;
    f.executionPort.diff = async input => ({ ...input, files: [{ path: 'workflows/wf-1.yaml', status: 'M', before: 'old', after: 'new', patch: '@@ -1 +1 @@\n-old\n+new', binary: false, truncated: false }], truncated: false });
    const workbench = createRepairWorkbenchService(f.db, f.sourcePort, f.executionPort);
    const origin = { kind: 'daily' as const, intentId: '1'.repeat(64), roundId: '2'.repeat(64), policyVersion: 1, evidenceCutoffMs: 1000, batchCount: 3 };
    const request = async (batchIndex: number) => ({ workflowId: 'wf-1', itemIds: [`item-${batchIndex}`], inputDigest: (await workbench.candidates('wf-1', { includeHistorical: true })).inputDigest,
      includeHistorical: true, instructions: '', requestId: `batch-${batchIndex}`, automation: { ...origin, batchIndex } });
    const report = async (taskId: string, index: number, noChange = false) => {
      const r = (await workbench.task(taskId)).latestAttempt;
      const draft = { candidateCommit: String(index + 4).repeat(40), parentCandidateCommit: r.input.parentCandidateCommit ?? baseline.packCommit, packDigest: 'e'.repeat(40),
        itemResults: [{ itemId: `item-${index}`, status: noChange ? 'not_needed' : 'changed', reason: 'checked' }] };
      await workbench.report({ workflowId: 'wf-1', taskId, revision: r.revision, expectedStateVersion: r.stateVersion, status: noChange ? 'no_change' : 'succeeded', draft,
        checks: { schemaVersion: 'workflow-repair-checks/v1', candidateCommit: draft.candidateCommit, packDigest: draft.packDigest, candidateDigest: digestRepairJson(draft), static: { status: 'passed' }, mock: { status: 'passed' }, coverage: [{ itemId: `item-${index}` }], uncoveredChecks: [] } });
    };
    const release = createRepairReleaseService({ db: f.db, workbench, publisher: {
      targets: async () => [{ id: 'workflow', scope: 'workflow' as const, ownerId: 'owner', environment: 'local', label: 'workflow' }], activeBaseline: async () => baseline,
      dispatch: async d => ({ status: 'succeeded', receipt: { requestId: d.requestId, candidateCommit: d.candidateCommit, packDigest: d.packDigest, deployNumber: 8, releaseVersion: 8, tagName: 'deploy/wf-1/#8', targetId: d.target.id } }),
      reconcile: async () => ({ status: 'failed' }),
    } });
    const approve = async (taskId: string, revision: number) => release.confirm('human', taskId, { revision, expectedStateVersion: (await workbench.task(taskId)).revisions.find(r => r.revision === revision)!.stateVersion, requestId: `approve-${revision}`, targetId: 'workflow', acknowledgeUncoveredChecks: true });
    return { ...f, baseline, workbench, request, report, release, approve };
  }
  it.each([1, 2])('reserves prior items and settles every changed batch when batch %s needs no change', async noChangeIndex => {
    const f = await setup();
    const first = await f.workbench.create('scheduler', await f.request(0));
    await f.report(first.taskId, 0);
    await expect(f.approve(first.taskId, 1)).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
    for (let index = 1; index < 3; index++) {
      const task = await f.workbench.task(first.taskId);
      await f.workbench.revise('scheduler', first.taskId, { ...await f.request(index), expectedAttemptRevision: index, parentCandidateCommit: task.latestAttempt.draft!.candidateCommit as string, feedback: '' });
      expect((await f.workbench.item('wf-1', 'item-0')).state).toBe('processing');
      await f.report(first.taskId, index, index === noChangeIndex);
    }
    expect((await f.workbench.candidates('wf-1')).tasks[0]).toMatchObject({ itemCount: 3, phase: 'review', title: '工作流优化（分 3 批）', source: 'automatic' });
    expect(await f.workbench.task(first.taskId)).toMatchObject({ scopeItemCount: 3, automaticProgress: { completedBatches: 3, totalBatches: 3, changedItems: 2, publishable: true } });
    const approval = await f.approve(first.taskId, 3);
    await f.release.deploy('human', first.taskId, { revision: 3, expectedApprovalVersion: approval.stateVersion, requestId: 'deploy' });
    expect((await f.workbench.item('wf-1', 'item-0')).state).toBe('awaiting_verification');
    expect((await f.workbench.item('wf-1', 'item-1')).state).toBe(noChangeIndex === 1 ? 'pending' : 'awaiting_verification');
    expect((await f.workbench.item('wf-1', 'item-2')).state).toBe(noChangeIndex === 2 ? 'pending' : 'awaiting_verification');
    await f.db.exec('CREATE TABLE workflow_deploy_history (workflow_id TEXT, deploy_number INTEGER, is_active INTEGER)');
    await f.db.exec("INSERT INTO workflow_deploy_history VALUES ('wf-1', 8, 1)");
    const deployment = (await f.release.detail(first.taskId)).deployments[0];
    expect((await createRepairAutoVerification(f.db).decisions(deployment)).map(row => row.itemId)).toEqual(noChangeIndex === 1 ? ['item-0', 'item-2'] : ['item-0', 'item-1']);
  });
  it('rejects skipped batches, changed round identity and manual replacement of an automatic chain', async () => {
    const f = await setup();
    const first = await f.workbench.create('scheduler', await f.request(0)); await f.report(first.taskId, 0);
    const base = { ...await f.request(1), expectedAttemptRevision: 1, parentCandidateCommit: '4'.repeat(40), feedback: '' };
    for (const automation of [undefined, { ...base.automation, batchIndex: 2 }, { ...base.automation, evidenceCutoffMs: 2000 }, { ...base.automation, policyVersion: 2 }]) {
      const { automation: _origin, ...manual } = base;
      await expect(f.workbench.revise('scheduler', first.taskId, automation ? { ...base, automation } : manual)).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
    }
    expect((await f.workbench.task(first.taskId)).revisions).toHaveLength(1);
  });
  it('checks the real active baseline again before dispatching another batch', async () => {
    const f = await setup(); const first = await f.workbench.create('scheduler', await f.request(0)); await f.report(first.taskId, 0);
    f.executionPort.resolveBaseline = async () => ({ ...f.baseline, activeDeployNumber: 9 });
    await expect(f.workbench.revise('scheduler', first.taskId, { ...await f.request(1), expectedAttemptRevision: 1, parentCandidateCommit: '4'.repeat(40), feedback: '' })).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
  });
  it('does not approve a fallback candidate after later failure and resumes the exact failed batch', async () => {
    const f = await setup(); const first = await f.workbench.create('scheduler', await f.request(0)); await f.report(first.taskId, 0);
    await f.workbench.revise('scheduler', first.taskId, { ...await f.request(1), expectedAttemptRevision: 1, parentCandidateCommit: '4'.repeat(40), feedback: '' });
    await f.workbench.report({ workflowId: 'wf-1', taskId: first.taskId, revision: 2, expectedStateVersion: 0, status: 'failed', error: { code: 'AIS_FAILED' } });
    await expect(f.approve(first.taskId, 1)).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
    f.setRemoteStatus({ status: 'failed', rawStatus: 'failed', errorMessage: 'failed' });
    const retry = await f.workbench.retryGeneration('scheduler', first.taskId, { expectedRevision: 2, requestId: 'retry-batch-1' });
    expect(retry.input.automation?.batchIndex).toBe(1);
    expect(retry.input.parentCandidateCommit).toBe('4'.repeat(40));
    await f.report(first.taskId, 1);
    expect((await f.workbench.item('wf-1', 'item-0')).state).toBe('processing');
  });
  it('requires all earlier static checks and contiguous persisted batch records at final approval', async () => {
    const f = await setup(); const first = await f.workbench.create('scheduler', await f.request(0)); await f.report(first.taskId, 0);
    for (let i = 1; i < 3; i++) {
      const task = await f.workbench.task(first.taskId);
      await f.workbench.revise('scheduler', first.taskId, { ...await f.request(i), expectedAttemptRevision: i, parentCandidateCommit: task.latestAttempt.draft!.candidateCommit as string, feedback: '' }); await f.report(first.taskId, i);
    }
    const rows = await f.db.query<{ checks_json: string }>('SELECT checks_json FROM workflow_repair_revisions WHERE task_id = ? AND revision = 1', [first.taskId]);
    const bad = { ...JSON.parse(rows[0].checks_json), static: { status: 'failed' } };
    await f.db.exec('UPDATE workflow_repair_revisions SET checks_json = ? WHERE task_id = ? AND revision = 1', [JSON.stringify(bad), first.taskId]);
    await expect(f.approve(first.taskId, 3)).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
    await f.db.exec('DELETE FROM workflow_repair_revisions WHERE task_id = ? AND revision = 1', [first.taskId]);
    await expect(f.approve(first.taskId, 3)).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
  });

});
