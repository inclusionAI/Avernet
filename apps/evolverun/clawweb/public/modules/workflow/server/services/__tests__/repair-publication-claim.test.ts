// @vitest-environment node
import { expect, it } from 'vitest';
import { repairFixture } from './repair-batch-fixtures.js';
import { repairReleaseMigrations } from '@avernet/clawweb-shared/server/schema-repair-release';
import { createRepairPublicationClaimService } from '../repair-publication-claim-service.js';
import { RepairReleaseRepository } from '../../repositories/repair-release-repository.js';
import type { RepairDeployment } from '../../contracts/repair-release.js';

it('binds a one-worker signed publication claim to the frozen Bot and recipe, without exposing its ticket', async () => {
  const f = await repairFixture(1);
  try {
    for (const m of repairReleaseMigrations) for (const sql of m.sql) await f.db.exec(f.db.dialect.renderDdl(sql));
    const baseline = await f.executionPort.resolveBaseline('wf-1');
    const deployment: RepairDeployment = { id: 'deployment-1', taskId: 'task-1', workflowId: 'wf-1', revision: 1,
      requestId: 'request-1', actorId: 'reviewer', approvalId: 'approved-1', candidateCommit: 'd'.repeat(40), packDigest: 'e'.repeat(40),
      baselineDeployNumber: 7, baseline, target: { id: 'pre:owner:bot', ownerId: 'owner', botId: 'bot', environment: 'pre', label: 'Bot' },
      status: 'dispatching', stateVersion: 0, receipt: null, error: null, createdAtMs: Date.now(), updatedAtMs: Date.now() };
    await new RepairReleaseRepository(f.db).insert('deployment', { requestId: 'request-1' }, deployment);
    const claims = createRepairPublicationClaimService(f.db);
    const recipe = { packId: 'pack', specPath: 'wf.yaml', specDigest: 'f'.repeat(64), deployNumber: 8, releaseVersion: 8, tagName: 'deploy/wf-1/#8' };
    const token = await claims.prepare(deployment, recipe);
    const request = { deploymentId: deployment.id, claimToken: token, botId: 'bot', ownerId: 'owner', workerId: 'worker-1' };
    await expect(claims.claim({ ...request, botId: 'wrong' })).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
    const input = await claims.claim(request);
    expect(input).toMatchObject({ candidateCommit: deployment.candidateCommit, baselineDeployNumber: 7, target: { botId: 'bot' }, recipe: { deployNumber: 8 } });
    await expect(claims.claim({ ...request, workerId: 'worker-2' })).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
    const history = { workflowId: 'wf-1', packId: 'pack', deployNumber: 8, version: 8, tagName: 'deploy/wf-1/#8',
      expectedActiveDeployNumber: 7, botId: 'bot', ownerId: 'owner', note: 'workflow-repair-deployment:deployment-1', publicationTicket: token };
    expect(await claims.authorizeHistory(history)).toBe(true);
    expect(await claims.authorizeHistory({ ...history, deployNumber: 9 })).toBe(false);
    expect(await claims.authorizeHistory({ ...history, publicationTicket: 'forged' })).toBe(false);
    await expect(claims.reissue(deployment)).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
    await claims.report({ ...request, status: 'uncertain' });
    const resumedToken = await claims.reissue(deployment);
    expect(await claims.authorizeHistory(history)).toBe(false);
    const resumed = await claims.claim({ ...request, claimToken: resumedToken, workerId: 'worker-2' });
    expect(resumed.recipe).toEqual(input.recipe);
    expect(resumed.candidateCommit).toBe(input.candidateCommit);
    await f.db.exec('CREATE TABLE workflow_deploy_history (workflow_id TEXT, deploy_number INTEGER, note TEXT)');
    await claims.stop(deployment);
    expect(await claims.authorizeHistory({ ...history, publicationTicket: resumedToken })).toBe(false);
    const shown = await new RepairReleaseRepository(f.db).list(deployment.taskId, 'deployment');
    expect(JSON.stringify(shown)).not.toContain(token);
  } finally { await f.db.close(); }
});
