import {historicalReference} from '../contracts/repair-version-scope.js';
import { automaticChain, changedChainItems, isAutomaticBatch } from '../contracts/repair-automatic-chain.js';
import { randomUUID } from 'node:crypto';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { RepairBatchError, digestRepairJson, type RepairRevision } from '../contracts/repair-batch.js';
import type { RepairWorkbenchService } from '../contracts/repair-workbench.js';
import type { PublicationStatus, RepairApproval, RepairConfirmRequest, RepairDeployment, RepairDeployRequest,
  RepairPublicationPort, RepairReleaseService } from '../contracts/repair-release.js';
import { RepairReleaseRepository } from '../repositories/repair-release-repository.js';
import { RepairBatchRepository } from '../repositories/repair-batch-repository.js';

function conflict(message: string, reasonCode = 'RELEASE_STATE_CHANGED'): never {
  throw new RepairBatchError('STATE_CONFLICT', message, { reasonCode });
}
function identity(actor: string, task: string, requestId: string, revision: number): void {
  if (![actor, task, requestId].every(value => typeof value === 'string' && value.trim() && value.length <= 128)
    || !Number.isSafeInteger(revision) || revision < 1) throw new RepairBatchError('INVALID_INPUT', '审核发布请求身份无效');
}
function candidate(revision: RepairRevision): { commit: string; packDigest: string } {
  const commit = revision.draft?.candidateCommit;
  const packDigest = revision.draft?.packDigest;
  if (typeof commit !== 'string' || !/^(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(commit)
    || typeof packDigest !== 'string' || !/^(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(packDigest)) conflict('候选 Git 身份不完整，不能确认');
  return { commit, packDigest };
}

export function createRepairReleaseService(input: {
  db: IDatabase; workbench: RepairWorkbenchService; publisher?: RepairPublicationPort;
}): RepairReleaseService {
  const { db, workbench } = input;
  const repo = new RepairReleaseRepository(db);
  function publisher(): RepairPublicationPort {
    if (!input.publisher) throw new RepairBatchError('CAPABILITY_UNAVAILABLE', '当前宿主尚未接入精确候选部署能力');
    return input.publisher;
  }
  async function detail(taskId: string) {
    const task = await workbench.task(taskId);
    const approvals = await repo.list<RepairApproval>(taskId, 'approval');
    const selected = approvals.filter(a => a.revision === task.latestSuccessful?.revision)
      .sort((a, b) => b.stateVersion - a.stateVersion)[0];
    return { approval: selected ?? null, deployments: await repo.list<RepairDeployment>(taskId, 'deployment'),
      repositoryWebUrl: input.publisher?.repositoryWebUrl?.() };
  }
  async function lock(tx: IDatabase, taskId: string) {
    const batches = new RepairBatchRepository(tx);
    const workflowId = await batches.taskWorkflow(taskId);
    if (!workflowId) throw new RepairBatchError('TASK_NOT_FOUND', '修复任务不存在');
    await batches.lockWorkflow(tx, workflowId);
    return { workflowId, revisions: await batches.listRevisions(workflowId, taskId), records: new RepairReleaseRepository(tx) };
  }
  async function authorized(actor: string, workflowId: string, targetId: string) {
    const target = (await publisher().targets(workflowId, actor)).find(t => t.id === targetId);
    if (!target) conflict('没有此工作流发布范围的有效权限', 'DEPLOY_TARGET_DENIED');
    return target;
  }
  async function recoveryAuthorized(actor: string, deployment: RepairDeployment) {
    if (deployment.target.scope === 'workflow') return authorized(actor, deployment.workflowId, deployment.target.id);
    const targets = await publisher().targets(deployment.workflowId, actor);
    const target = targets.find(target => target.scope === 'workflow' || target.id === deployment.target.id);
    if (!target) conflict('没有核对此工作流历史发布请求的权限', 'DEPLOY_TARGET_DENIED');
    return target;
  }
  async function settle(deployment: RepairDeployment, status: PublicationStatus): Promise<RepairDeployment> {
    return db.transaction(async tx => {
      const { records } = await lock(tx, deployment.taskId);
      const latest = (await records.list<RepairDeployment>(deployment.taskId, 'deployment')).find(d => d.id === deployment.id);
      if (!latest) conflict('部署记录已不存在');
      if (latest.status === 'succeeded') return latest;
      if (latest.status === 'failed') return latest;
      const next: RepairDeployment = { ...latest, stateVersion: latest.stateVersion + 1,
        status: status.status, updatedAtMs: Date.now(), error: status.status === 'succeeded' ? null : status.error ?? null };
      if (status.status === 'succeeded') {
        const r = status.receipt;
        if (r.requestId !== latest.requestId || r.candidateCommit !== latest.candidateCommit || r.packDigest !== latest.packDigest
          || r.targetId !== latest.target.id || !Number.isSafeInteger(r.deployNumber) || r.deployNumber <= latest.baselineDeployNumber
          || !Number.isSafeInteger(r.releaseVersion) || r.releaseVersion < 1 || r.tagName !== `deploy/${latest.workflowId}/#${r.deployNumber}`) {
          conflict('部署回执与已确认的候选或目标不一致；请核对真实发布状态', 'DEPLOY_RECEIPT_MISMATCH');
        }
        next.receipt = r;
        const revision = (await new RepairBatchRepository(tx).listRevisions(latest.workflowId, latest.taskId)).find(v => v.revision === latest.revision);
        if (!revision || revision.draft?.candidateCommit !== latest.candidateCommit) conflict('发布候选与任务记录不一致');
        await tx.exec(`UPDATE workflow_repair_revisions SET phase = 'published', state_version = state_version + 1, updated_at_ms = ?
          WHERE task_id = ? AND revision = ?`, [Date.now(), latest.taskId, latest.revision]);
        const chain = await new RepairBatchRepository(tx).listRevisions(latest.workflowId, latest.taskId);
        for (const outcome of changedChainItems(chain, revision)) {
          if(historicalReference(outcome))continue;
          {
            const updated = await tx.exec(`UPDATE workflow_repair_items SET state = 'awaiting_verification', active_task_id = ?, active_revision = ?,
            state_version = state_version + 1, updated_at_ms = ? WHERE item_id = ? AND workflow_id = ?
            AND ((state = 'processing' AND active_task_id = ?) OR (state = 'pending' AND active_task_id IS NULL))`, [latest.taskId, latest.revision, Date.now(), outcome.itemId, latest.workflowId, latest.taskId]);
            if (updated.affectedRows !== 1) conflict('部署已回执，但处理项状态冲突；保留发布请求，请核对后恢复验证关联', 'DEPLOY_ITEM_CONFLICT');
          }
        }
        await tx.exec(`UPDATE workflow_repair_items SET state = 'pending', active_task_id = NULL, active_revision = NULL,
          state_version = state_version + 1, updated_at_ms = ? WHERE workflow_id = ? AND active_task_id = ? AND state = 'processing'`,
        [Date.now(), latest.workflowId, latest.taskId]);
      }
      if (status.status === 'failed') {
        // Only confirmed failure opens retry; partial/unknown publication remains uncertain.
        await tx.exec(`UPDATE workflow_repair_revisions SET phase = 'review', state_version = state_version + 1, updated_at_ms = ?
          WHERE task_id = ? AND revision = ? AND phase = 'publishing'`, [Date.now(), latest.taskId, latest.revision]);
      }
      await records.update(next, latest.stateVersion);
      return next;
    });
  }
  return {
    configured: !!input.publisher,
    detail,
    async targets(actor, taskId) { const task = await workbench.task(taskId); return publisher().targets(task.workflowId, actor); },
    async publish(actor, taskId, request) {
      // One user decision; approval remains an internal immutable audit record.
      const approval = await this.confirm(actor, taskId, request);
      if (approval.target.scope !== 'workflow') conflict('请重新审阅工作流发布作用域，历史 Bot 授权不可迁移');
      return this.deploy(actor, taskId, { revision: request.revision, expectedApprovalVersion: approval.stateVersion,
        requestId: digestRepairJson(['publish', request.requestId]) });
    },
    async confirm(actor, taskId, request: RepairConfirmRequest) {
      identity(actor, taskId, request.requestId, request.revision);
      const prior = await repo.retry<RepairApproval>(taskId, 'approval', { actor, ...request });
      if (prior) return prior;
      const task = await workbench.task(taskId);
      const revision = task.revisions.find(r => r.revision === request.revision);
      if (!revision || task.latestSuccessful?.revision !== request.revision || !['review', ...(isAutomaticBatch(revision.input) ? ['no_change'] : [])].includes(revision.phase)
        || !['review', 'failed', 'no_change', 'blocked'].includes(task.latestAttempt.phase) || revision.stateVersion !== request.expectedStateVersion) conflict('候选已变化或不是当前可审阅版本，请刷新');
      const scope = changedChainItems(task.revisions, revision, true);
      const c = candidate(revision);
      if ((revision.checks?.static as { status?: string })?.status !== 'passed'
        || !Array.isArray(revision.draft?.itemResults)
        || revision.draft.itemResults.some(item => item.status === 'unresolved')
        || !scope.length) conflict('候选检查失败、存在未解决问题或没有实际修改，不能确认');
      const target = await authorized(actor, task.workflowId, request.targetId);
      const diff = await workbench.diff(taskId, request.revision, 'baseline');
      if (diff.truncated || !diff.files.length || diff.files.some(f => f.truncated || !f.binary && !f.patch)) conflict('完整行级差异尚不可用，不能确认候选', 'DIFF_INCOMPLETE');
      const uncovered = revision.checks?.uncoveredChecks;
      if (((Array.isArray(uncovered) && uncovered.length) || diff.files.some(f => f.binary)
        || (revision.checks?.mock as { status?: string })?.status !== 'passed') && request.acknowledgeUncoveredChecks !== true) conflict('请明确确认未覆盖的验证风险');
      if (!Number.isSafeInteger(revision.input.baseline.activeDeployNumber) || revision.input.baseline.activeDeployNumber! < 1) conflict('任务缺少可信部署基线，请先部署工作流');
      return db.transaction(async tx => {
        const locked = await lock(tx, taskId);
        const retry = await locked.records.retry<RepairApproval>(taskId, 'approval', { actor, ...request });
        if (retry) return retry;
        const fresh = locked.revisions.find(r => r.revision === request.revision);
        if (!fresh || fresh.stateVersion !== request.expectedStateVersion || !['review', ...(isAutomaticBatch(fresh.input) ? ['no_change'] : [])].includes(fresh.phase)
          || fresh.candidateDigest !== revision.candidateDigest || locked.revisions[0].phase === 'drafting'
          || locked.revisions.find(r => r.draft !== null && (r.phase !== 'no_change' || isAutomaticBatch(r.input)))?.revision !== request.revision) conflict('审核期间候选已变化，请重新审阅');
        automaticChain(locked.revisions, fresh, true);
        const priorApprovals = await locked.records.list<RepairApproval>(taskId, 'approval');
        const approval: RepairApproval = { id: randomUUID(), taskId, workflowId: task.workflowId, revision: request.revision,
          stateVersion: Math.max(0, ...priorApprovals.map(a => a.stateVersion)) + 1, requestId: request.requestId, actorId: actor,
          candidateCommit: c.commit, packDigest: c.packDigest, candidateDigest: digestRepairJson(revision.draft),
          checksDigest: digestRepairJson(revision.checks), baseline: revision.input.baseline,
          baselineDeployNumber: revision.input.baseline.activeDeployNumber!, target,
          acknowledgeUncoveredChecks: request.acknowledgeUncoveredChecks, status: 'approved', createdAtMs: Date.now() };
        await locked.records.insert('approval', { actor, ...request }, approval);
        await tx.exec(`UPDATE workflow_repair_revisions SET confirmation_json = ?, state_version = state_version + 1, updated_at_ms = ?
          WHERE task_id = ? AND revision = ? AND state_version = ?`, [JSON.stringify(approval), Date.now(), taskId, request.revision, request.expectedStateVersion]);
        return approval;
      });
    },
    async revoke(actor, taskId, request) {
      identity(actor, taskId, request.requestId, request.revision);
      return db.transaction(async tx => {
        const { records } = await lock(tx, taskId);
        const prior = await records.retry<RepairApproval>(taskId, 'approval', { actor, ...request });
        if (prior) return prior;
        const approvals = (await records.list<RepairApproval>(taskId, 'approval')).filter(a => a.revision === request.revision)
          .sort((a, b) => b.stateVersion - a.stateVersion);
        const approval = approvals[0];
        if (!approval || approval.stateVersion !== request.expectedApprovalVersion) conflict('审核确认已变化，请刷新');
        const deployments = await records.list<RepairDeployment>(taskId, 'deployment');
        if (deployments.some(d => d.revision === request.revision)) conflict('此候选已经提交部署，不能撤销确认或隐式回滚');
        const revoked = { ...approval, id: randomUUID(), actorId: actor, stateVersion: approval.stateVersion + 1,
          status: 'revoked' as const, requestId: request.requestId, createdAtMs: Date.now() };
        // Revocation is an immutable approval event; its request identity is distinct.
        await records.insert('approval', { actor, ...request }, revoked);
        await tx.exec('UPDATE workflow_repair_revisions SET confirmation_json = ?, state_version = state_version + 1 WHERE task_id = ? AND revision = ?',
          [JSON.stringify(revoked), taskId, request.revision]);
        return revoked;
      });
    },
    async deploy(actor, taskId, request: RepairDeployRequest) {
      identity(actor, taskId, request.requestId, request.revision);
      const retry = await repo.retry<RepairDeployment>(taskId, 'deployment', { actor, ...request });
      if (retry) return retry;
      const release = await detail(taskId);
      const approval = release.approval;
      if (!approval || approval.status !== 'approved' || approval.revision !== request.revision
        || approval.stateVersion !== request.expectedApprovalVersion) conflict('请先确认当前候选');
      await authorized(actor, approval.workflowId, approval.target.id);
      const active = await publisher().activeBaseline(approval.workflowId);
      if (active.activeDeployNumber !== approval.baselineDeployNumber || active.packCommit !== approval.baseline.packCommit
        || active.specDigest !== approval.baseline.specDigest) conflict('线上基线已经变化，请基于当前版本重新生成候选', 'BASELINE_STALE');
      const created = await db.transaction(async tx => {
        const { records, revisions } = await lock(tx, taskId);
        const repeated = await records.retry<RepairDeployment>(taskId, 'deployment', { actor, ...request });
        if (repeated) return { value: repeated, dispatch: false };
        const freshApproval = (await records.list<RepairApproval>(taskId, 'approval')).sort((a, b) => b.stateVersion - a.stateVersion)[0];
        const selected = revisions.find(r => r.revision === request.revision);
        if (!freshApproval || freshApproval.id !== approval.id || !selected || !['review', ...(isAutomaticBatch(selected.input) ? ['no_change'] : [])].includes(selected.phase)
          || selected.draft?.candidateCommit !== approval.candidateCommit || selected.draft?.packDigest !== approval.packDigest
          || digestRepairJson(selected.draft) !== approval.candidateDigest || digestRepairJson(selected.checks) !== approval.checksDigest || revisions[0].phase === 'drafting'
          || revisions.find(r => r.draft !== null && (r.phase !== 'no_change' || isAutomaticBatch(r.input)))?.revision !== request.revision) conflict('部署前候选或审核确认已变化');
        for (const item of changedChainItems(revisions, selected, true)) {
          if(historicalReference(item))continue;
          const current = await new RepairBatchRepository(tx).getItem(approval.workflowId, item.itemId);
          if (!current || !(current.state === 'processing' && current.activeTaskId === taskId || current.state === 'pending' && current.activeTaskId === null)) conflict('此候选的处理项已被移出、处置或占用，请重新核对修复范围');
          // Reserve the full reviewed scope before any external publication.
          // A later failed revision may have released items from this candidate.
          const reserved = await tx.exec(`UPDATE workflow_repair_items SET state = 'processing', active_task_id = ?, active_revision = ?,
            state_version = state_version + 1, updated_at_ms = ? WHERE workflow_id = ? AND item_id = ? AND state_version = ?
            AND ((state = 'pending' AND active_task_id IS NULL) OR (state = 'processing' AND active_task_id = ?))`,
          [taskId, request.revision, Date.now(), approval.workflowId, item.itemId, current.stateVersion, taskId]);
          if (reserved.affectedRows !== 1) conflict('部署前处理项占用失败，请重新核对修复范围');
        }
        const inFlight = (await records.list<RepairDeployment>(taskId, 'deployment')).find(d => d.status !== 'failed');
        if (inFlight) conflict('已有部署请求，请先核对其状态，不能重复发布');
        const deployment: RepairDeployment = { id: randomUUID(), workflowId: approval.workflowId, taskId, revision: request.revision,
          requestId: request.requestId, actorId: actor, approvalId: approval.id, candidateCommit: approval.candidateCommit,
          packDigest: approval.packDigest, baselineDeployNumber: approval.baselineDeployNumber, baseline: approval.baseline,
          target: approval.target, status: 'dispatching', stateVersion: 0, receipt: null, error: null,
          createdAtMs: Date.now(), updatedAtMs: Date.now() };
        await records.insert('deployment', { actor, ...request }, deployment);
        await tx.exec(`UPDATE workflow_repair_revisions SET phase = 'publishing', state_version = state_version + 1, updated_at_ms = ?
          WHERE task_id = ? AND revision = ?`, [Date.now(), taskId, request.revision]);
        return { value: deployment, dispatch: true };
      });
      if (!created.dispatch) return created.value;
      let status: PublicationStatus;
      try { status = await publisher().dispatch(created.value); }
      catch { status = { status: 'uncertain', error: '部署请求提交结果尚未确认，请核对状态后再决定是否重试' }; }
      return settle(created.value, status);
    },
    async reconcile(actor, taskId, deploymentId) {
      const deployment = (await repo.list<RepairDeployment>(taskId, 'deployment')).find(d => d.id === deploymentId);
      if (!deployment) conflict('部署记录不存在');
      if (!actor?.trim()) throw new RepairBatchError('INVALID_INPUT', '缺少核对身份');
      await recoveryAuthorized(actor, deployment);
      if (deployment.status === 'succeeded') return deployment;
      return settle(deployment, await publisher().reconcile(deployment));
    },
    async resume(actor, taskId, deploymentId, requestId) {
      identity(actor, taskId, requestId, 1);
      const p = publisher();
      if (!p.resume) throw new RepairBatchError('CAPABILITY_UNAVAILABLE', '宿主未接入同一部署的恢复能力');
      const deployment = (await repo.list<RepairDeployment>(taskId, 'deployment')).find(row => row.id === deploymentId);
      if (!deployment || !['uncertain', 'running', 'dispatching'].includes(deployment.status)) conflict('此部署不需要恢复，请刷新核对');
      await recoveryAuthorized(actor, deployment);
      const payload = { actor, deploymentId, requestId };
      const claimed = await db.transaction(async tx => {
        const { records } = await lock(tx, taskId);
        if (await records.retry(taskId, 'deployment_resume', payload)) return false;
        await records.insert('deployment_resume', payload, { id: randomUUID(), workflowId: deployment.workflowId, taskId,
          revision: deployment.revision, stateVersion: 0, deploymentId, createdAtMs: Date.now() });
        return true;
      });
      if (!claimed) return this.reconcile(actor, taskId, deploymentId);
      const status = await p.resume(deployment);
      return settle(deployment, status);
    },
    async stop(actor, taskId, deploymentId, requestId) {
      identity(actor, taskId, requestId, 1);
      const p = publisher();
      if (!p.stop) throw new RepairBatchError('CAPABILITY_UNAVAILABLE', '宿主未接入安全停止未激活发布的能力');
      const d = (await repo.list<RepairDeployment>(taskId, 'deployment')).find(row => row.id === deploymentId);
      if (!d || d.status === 'succeeded') conflict('此候选已部署或记录不存在，不能停止或隐式回滚');
      await recoveryAuthorized(actor, d);
      if (d.status === 'failed') return d;
      const payload = { actor, deploymentId, requestId };
      const first = await db.transaction(async tx => {
        const { records } = await lock(tx, taskId);
        if (await records.retry(taskId, 'deployment_stop', payload)) return false;
        await records.insert('deployment_stop', payload, { id: randomUUID(), workflowId: d.workflowId, taskId,
          revision: d.revision, deploymentId, actorId: actor, stateVersion: 0, createdAtMs: Date.now() });
        return true;
      });
      if (!first) return this.reconcile(actor, taskId, deploymentId);
      const result = await p.stop(d);
      if (result.status !== 'failed') return settle(d, result);
      return settle(d, { status: 'failed', error: '此发布请求已停止，未激活候选。保留审阅内容；基线已变化时请返回问题列表创建当前基线的新任务。不会回滚现有版本。' });
    },
  };
}
