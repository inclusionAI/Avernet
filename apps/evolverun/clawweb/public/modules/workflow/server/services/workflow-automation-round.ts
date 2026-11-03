import {workflowSnapshotKey} from '@avernet/clawweb-shared/server/contracts/workflow-snapshot';
import { logWorkflowBackground, workflowBackgroundError } from '@avernet/clawweb-shared/server/services/workflow-background-log';
import type { AutomationWorkbench, WorkflowAutomationPolicy } from '../contracts/workflow-automation.js';
import type { RepairFeedbackRequest, RepairSelectionRequest } from '../contracts/repair-workbench.js';
import { automaticChain } from '../contracts/repair-automatic-chain.js';
import { digestRepairJson } from '../contracts/repair-batch.js';
import { WorkflowAutomationRepository, type AutomationJob } from '../repositories/workflow-automation-repository.js';
import { automaticItemDigest } from './workflow-automation-selection.js';

async function bounded<T>(promise: Promise<T>): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try { return await Promise.race([promise, new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new Error('AUTOMATION_OUTCOME_UNKNOWN')), 20000); })]); }
  finally { if (timer) clearTimeout(timer); }
}

/** One persisted round and one task; only its bounded next batch can dispatch. */
export async function runAutomaticRound(input: { job: AutomationJob; repo: WorkflowAutomationRepository; workbench: AutomationWorkbench;
  now: () => number; authority: () => Promise<{ actorId: string; policy: WorkflowAutomationPolicy }> }) {
  const { repo, workbench, now } = input;
  let job = input.job;
  const stop = async (reason: string) => {
    logWorkflowBackground('workflow_automatic_draft', 'skipped', { workflowId: job.workflowId, jobId: job.id,
      taskId: job.data.realTaskId, reason });
    job = await repo.patch(job, { status: 'blocked', reason }, now(), true);
  };
  try {
    const request = job.data.request as RepairSelectionRequest | RepairFeedbackRequest;
    const existing = await workbench.request(job.workflowId, request.requestId);
    let taskId: string | undefined = existing?.taskId ?? job.data.realTaskId;
    if (!existing) {
      const access = await input.authority();
      if (access.actorId !== job.data.actorId || access.policy.version !== request.automation!.policyVersion) return stop('OWNER_OR_POLICY_CHANGED');
      // Resolve unknown outcomes via the stable request key before considering another target.
      if (job.status === 'uncertain' || job.status === 'dispatching') {
        logWorkflowBackground('workflow_automatic_draft', 'skipped', { workflowId: job.workflowId, jobId: job.id, reason: 'DRAFT_REQUEST_REQUIRES_LOOKUP' });
        job = await repo.patch(job, { status: 'uncertain', reason: 'DRAFT_REQUEST_REQUIRES_LOOKUP', nextAtMs: now() + 30000 }, now(), true); return;
      }
      if (!taskId && await repo.pendingAutomaticTask(job.workflowId)) {
        logWorkflowBackground('workflow_automatic_draft', 'skipped', { workflowId: job.workflowId, jobId: job.id, reason: 'DRAFT_IN_PROGRESS' });
        await repo.patch(job, { status: 'pending', reason: 'DRAFT_IN_PROGRESS', nextAtMs: now() + 60000 }, now(), true); return;
      }
      const snapshot = await workbench.candidates(job.workflowId, { includeHistorical: true, state: 'all', pageSize: 1,versionScope:'current' });
      if(request.targetSnapshotKey&&snapshot.activeSnapshot!==undefined&&(snapshot.activeSnapshot?.status!=='resolved'||workflowSnapshotKey(snapshot.activeSnapshot.snapshot)!==request.targetSnapshotKey))return stop('TARGET_VERSION_CHANGED');
      if (snapshot.inputDigest !== request.inputDigest) return stop('SOURCE_CHANGED');
      for (const item of await workbench.items(job.workflowId, request.itemIds)) {
        if (automaticItemDigest(item) !== job.data.revisions[item.itemId]) return stop('SOURCE_CHANGED');
      }
      job = await repo.patch(job, { status: 'dispatching', attempts: job.attempts + 1 }, now());
      logWorkflowBackground('workflow_automatic_draft', 'dispatch', { workflowId: job.workflowId, taskId });
      try {
        const result = await bounded(taskId && job.data.recovery ? workbench.retryGeneration(access.actorId, taskId, job.data.recovery) : taskId ? workbench.revise(access.actorId, taskId, request as RepairFeedbackRequest)
          : workbench.create(access.actorId, request));
        taskId = result.taskId;
        logWorkflowBackground('workflow_automatic_draft', 'created', { workflowId: job.workflowId, jobId: job.id, taskId, revision: result.revision });
      } catch (error) {
        logWorkflowBackground('workflow_automatic_draft', 'failed', { workflowId: job.workflowId, jobId: job.id, taskId,
          phase: 'create_task', ...workflowBackgroundError(error) });
        const recovered = await workbench.request(job.workflowId, request.requestId);
        if (!recovered) {
          const code = (error as { code?: string }).code;
          if (code) return stop(code);
          job = await repo.patch(job, { status: 'uncertain', reason: 'DRAFT_REQUEST_REQUIRES_LOOKUP', nextAtMs: now() + 30000 }, now(), true); return;
        }
        taskId = recovered.taskId;
      }
    }
    if (!taskId) return stop('DRAFT_REQUIRES_MANUAL_REVIEW');
    const task = await workbench.task(taskId);
    const revision = task.latestAttempt;
    const index = request.automation!.batchIndex!;
    const batches = job.data.batches as string[][];
    const data = { ...job.data, realTaskId: taskId };
    if (revision.requestId !== request.requestId) return stop('CANDIDATE_SUPERSEDED');
    if (['review', 'no_change'].includes(revision.phase)
      && (revision.checks?.static as { status?: string })?.status === 'passed' && typeof revision.draft?.candidateCommit === 'string') {
      try { automaticChain(task.revisions, revision); } catch { return stop('AUTOMATIC_CHAIN_INCOMPLETE'); }
      if (index + 1 < batches.length) {
        const next: RepairFeedbackRequest = { ...request, itemIds: batches[index + 1], requestId: `${job.id}:${index + 1}`,
          expectedAttemptRevision: revision.revision, parentCandidateCommit: revision.draft.candidateCommit, feedback: '',
          automation: { ...request.automation!, batchIndex: index + 1 } };
        // Persist the exact next request before another process can claim/dispatch it.
        job = await repo.patch(job, { status: 'pending', attempts: 0, reason: null, nextAtMs: now(), data: { ...data, recovery: null, completedBatches: index + 1, request: next } }, now(), true);
        return;
      }
      await repo.ensure(job.workflowId, 'auto_notice', `round:${job.id}`, { title: '自动优化草稿已可审阅',
        text: `本轮 ${job.data.total} 项建议已分批核对。请审阅完整差异和保留项后确认是否发布。`,
        path: `/workflows/workspace?workflowId=${encodeURIComponent(job.workflowId)}&tab=evolution&repairTaskId=${encodeURIComponent(taskId)}`,
        taskId, revision: revision.revision, kind: 'draft' }, now());
      await repo.patch(job, { status: 'done', reason: null, data: { ...data, completedBatches: batches.length } }, now(), true); return;
    }
    const terminal = ['failed', 'blocked', 'cancelled', 'published', 'no_change'].includes(revision.phase);
    await repo.patch(job, { status: terminal ? 'blocked' : 'waiting', reason: terminal ? 'DRAFT_REQUIRES_MANUAL_REVIEW' : null,
      data, nextAtMs: now() + 30000 }, now(), true);
  } catch (error) {
    logWorkflowBackground('workflow_automatic_draft', 'failed', { workflowId: job.workflowId, jobId: job.id,
      phase: 'draft_recovery', ...workflowBackgroundError(error) });
    const reason = error instanceof Error ? error.message : 'DRAFT_REQUIRES_MANUAL_REVIEW';
    await repo.patch(job, { status: reason === 'AUTOMATION_DISABLED' ? 'retryable' : 'blocked', reason, nextAtMs: now() + 30000 }, now(), true);
  }
}
