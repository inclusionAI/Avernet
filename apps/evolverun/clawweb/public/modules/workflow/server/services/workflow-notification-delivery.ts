import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import type { AutoAnalysisSettings } from './task-guard/auto-analysis-settings.js';
import { RepairAutomationDeniedError, type AutomationWorkbench, type WorkflowAutomationPorts } from '../contracts/workflow-automation.js';
import { WorkflowAutomationRepository, type AutomationJob } from '../repositories/workflow-automation-repository.js';

/** Reuse the durable notice queue without requiring an automatic-analysis scheduler. */
export function createWorkflowNotificationDrain(input: {
  db: IDatabase; settings: Pick<AutoAnalysisSettings, 'get' | 'list'>; workbench: Pick<AutomationWorkbench, 'task'>; environment?: string;
  ports: Pick<WorkflowAutomationPorts, 'owner' | 'canEdit' | 'notify'>; now?: () => number;
}): () => Promise<void> {
  const now = input.now ?? Date.now;
  const repo = new WorkflowAutomationRepository(input.db);
  const environment = input.environment ?? 'prod';
  async function notify(record: AutomationJob) {
    if (((await input.settings.get(record.workflowId)).executionEnvironment ?? 'prod') !== environment) return;
    let job = await repo.claim(record.id, now()); if (!job) return;
    const log = (event: string, reason?: string, receipt?: string) => console.info('[workflow-notification]', {
      event, workflowId: job.workflowId, taskId: job.data.taskId ?? null, revision: job.data.revision ?? null,
      noticeId: job.id, environment, recipientId: job.data.lastRecipientId ?? job.data.recipientId ?? null,
      attempts: job.attempts, ...(reason ? { reason } : {}), ...(receipt ? { receipt } : {}),
    });
    if (job.status === 'dispatching' || job.status === 'uncertain') {
      await repo.patch(job, { status: 'uncertain', reason: 'NOTIFICATION_OUTCOME_UNKNOWN', nextAtMs: now() + 300000 }, now(), true);
      log('uncertain', 'NOTIFICATION_OUTCOME_UNKNOWN'); return;
    }
    let actorId: string;
    try {
      const policy = await input.settings.get(job.workflowId);
      if ((policy.executionEnvironment ?? 'prod') !== environment) { await repo.patch(job, {}, now(), true); return; }
      if (job.data.kind === 'issue' && !policy.enabled) throw new RepairAutomationDeniedError('AUTOMATION_DISABLED');
      const recipients = job.data.kind === 'issue' ? [] : policy.notificationRecipients ?? [];
      if (!job.data.fixedRecipient && recipients.length) {
        for (const recipientId of recipients) await repo.ensure(job.workflowId, 'auto_notice', `${job.id}:${recipientId}`, { ...job.data, recipientId, fixedRecipient: true }, now());
        await repo.patch(job, { status: 'done', data: { ...job.data, recipients } }, now(), true);
        // Child records are drained by later bounded batches, never recursively here.
        return;
      }
      const owner = job.data.fixedRecipient ? null : await input.ports.owner(job.workflowId);
      const recipientId = job.data.fixedRecipient ? job.data.recipientId : owner;
      if (typeof recipientId !== 'string' || !recipientId) throw new RepairAutomationDeniedError('OWNER_MISSING');
      actorId = recipientId;
      if (!job.data.fixedRecipient && !await input.ports.canEdit(job.workflowId, actorId)) throw new RepairAutomationDeniedError('OWNER_PERMISSION_DENIED');
    } catch (error) {
      if (!(error instanceof RepairAutomationDeniedError)) throw error;
      await repo.patch(job, { status: 'retryable', reason: error.message, nextAtMs: now() + 30000 }, now(), true);
      log('retryable', error.message); return;
    }
    if (job.data.kind === 'draft') {
      const task = await input.workbench.task(job.data.taskId);
      const reviewable = task.latestAttempt.phase === 'review'
        || task.latestAttempt.phase === 'no_change' && task.automaticProgress?.publishable === true;
      if (task.latestAttempt.revision !== job.data.revision || !reviewable) {
        await repo.patch(job, { status: 'done', reason: 'CANDIDATE_SUPERSEDED' }, now(), true); return;
      }
    }
    if (job.attempts >= 3) { await repo.patch(job, { status: 'blocked', reason: 'NOTIFICATION_REJECTED' }, now(), true); return; }
    job = await repo.patch(job, { status: 'dispatching', attempts: job.attempts + 1, data: { ...job.data, lastRecipientId: actorId } }, now());
    log('dispatching');
    let result: Awaited<ReturnType<WorkflowAutomationPorts['notify']>>;
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      result = await Promise.race([input.ports.notify({ key: job.id, workflowId: job.workflowId, recipientId: actorId, title: job.data.title, text: job.data.text, path: job.data.path }),
        new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new Error('NOTIFICATION_OUTCOME_UNKNOWN')), 20000); })]);
    } catch { result = { status: 'uncertain' }; }
    finally { if (timer) clearTimeout(timer); }
    await repo.patch(job, { status: result.status === 'sent' ? 'done' : result.status, reason: result.status === 'sent' ? null : result.status === 'uncertain' ? 'NOTIFICATION_OUTCOME_UNKNOWN' : 'NOTIFICATION_REJECTED',
      nextAtMs: now() + 60000, data: { ...job.data, receipt: result.receipt ?? null } }, now(), true);
    log(result.status === 'sent' ? 'accepted' : result.status,
      result.status === 'sent' ? undefined : result.status === 'uncertain' ? 'NOTIFICATION_OUTCOME_UNKNOWN' : 'NOTIFICATION_REJECTED', result.receipt);
  }
  return async () => {
    if (environment !== 'pre' && environment !== 'prod') return;
    const preWorkflows = (await input.settings.list()).filter(policy => policy.executionEnvironment === 'pre').map(policy => policy.workflowId);
    // Five actual queue records per tick, in parallel: slow transports consume one
    // existing 20-second timeout window, not recipients × parents × 20 seconds.
    await Promise.all((await repo.readyNotifications(now(), 5, { workflowIds: preWorkflows, exclude: environment === 'prod' })).map(notify));
  };
}
