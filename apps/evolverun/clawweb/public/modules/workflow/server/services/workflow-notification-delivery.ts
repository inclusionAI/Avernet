import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { logWorkflowBackground, startWorkflowBackgroundRun } from '@avernet/clawweb-shared/server/services/workflow-background-log';
import { isInvalidAutoAnalysisSettings, type AutoAnalysisSettings } from './task-guard/auto-analysis-settings.js';
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
  logWorkflowBackground('workflow_notification_drain', 'configured', { environment, enabled: environment === 'pre' || environment === 'prod' });
  let firstPoll = true;
  let lastSummaryAt = -Infinity, lastFailureAt = -Infinity, suppressed = 0;
  async function notify(record: AutomationJob) {
    let job = record;
    const log = (event: string, reason?: string, receipt?: string) => console.info('[workflow-notification] ' + JSON.stringify({
      event, workflowId: job.workflowId, taskId: job.data.taskId ?? null, revision: job.data.revision ?? null,
      noticeId: job.id, environment, recipientId: job.data.lastRecipientId ?? job.data.recipientId ?? null,
      attempts: job.attempts, ...(reason ? { reason } : {}), ...(receipt ? { receipt } : {}),
    }));
    try {
      if (((await input.settings.get(record.workflowId)).executionEnvironment ?? 'prod') !== environment) { log('skipped', 'ENVIRONMENT_MISMATCH'); return; }
    } catch (error) {
      // Claim only to defer this record, never infer an environment from invalid settings.
      if (!isInvalidAutoAnalysisSettings(error)) throw error;
    }
    const claimed = await repo.claim(record.id, now());
    if (!claimed) { log('skipped', 'NOTIFICATION_NOT_CLAIMED'); return; }
    job = claimed;
    if (job.status === 'dispatching' || job.status === 'uncertain') {
      await repo.patch(job, { status: 'uncertain', reason: 'NOTIFICATION_OUTCOME_UNKNOWN', nextAtMs: now() + 300000 }, now(), true);
      log('uncertain', 'NOTIFICATION_OUTCOME_UNKNOWN'); return;
    }
    let actorId: string;
    try {
      const policy = await input.settings.get(job.workflowId);
      if ((policy.executionEnvironment ?? 'prod') !== environment) { await repo.patch(job, {}, now(), true); log('skipped', 'ENVIRONMENT_MISMATCH'); return; }
      if (job.data.kind === 'issue' && !policy.enabled) throw new RepairAutomationDeniedError('AUTOMATION_DISABLED');
      const recipients = job.data.kind === 'issue' ? [] : policy.notificationRecipients ?? [];
      if (!job.data.fixedRecipient && recipients.length) {
        for (const recipientId of recipients) await repo.ensure(job.workflowId, 'auto_notice', `${job.id}:${recipientId}`, { ...job.data, recipientId, fixedRecipient: true }, now());
        await repo.patch(job, { status: 'done', data: { ...job.data, recipients } }, now(), true);
        log('recipients_queued');
        // Child records are drained by later bounded batches, never recursively here.
        return;
      }
      const owner = job.data.fixedRecipient ? null : await input.ports.owner(job.workflowId);
      const recipientId = job.data.fixedRecipient ? job.data.recipientId : owner;
      if (typeof recipientId !== 'string' || !recipientId) throw new RepairAutomationDeniedError('OWNER_MISSING');
      actorId = recipientId;
      if (!job.data.fixedRecipient && !await input.ports.canEdit(job.workflowId, actorId)) throw new RepairAutomationDeniedError('OWNER_PERMISSION_DENIED');
    } catch (error) {
      if (isInvalidAutoAnalysisSettings(error)) {
        await repo.patch(job, { status: 'retryable', reason: 'NOTIFICATION_SETTINGS_INVALID', nextAtMs: now() + 60000 }, now(), true);
        log('retryable', 'NOTIFICATION_SETTINGS_INVALID'); return;
      }
      if (!(error instanceof RepairAutomationDeniedError)) throw error;
      await repo.patch(job, { status: 'retryable', reason: error.message, nextAtMs: now() + 30000 }, now(), true);
      log('retryable', error.message); return;
    }
    if (job.data.kind === 'draft') {
      const task = await input.workbench.task(job.data.taskId);
      const reviewable = task.latestAttempt.phase === 'review'
        || task.latestAttempt.phase === 'no_change' && task.automaticProgress?.publishable === true;
      if (task.latestAttempt.revision !== job.data.revision || !reviewable) {
        await repo.patch(job, { status: 'done', reason: 'CANDIDATE_SUPERSEDED' }, now(), true); log('skipped', 'CANDIDATE_SUPERSEDED'); return;
      }
    }
    if (job.attempts >= 3) { await repo.patch(job, { status: 'blocked', reason: 'NOTIFICATION_REJECTED' }, now(), true); log('blocked', 'NOTIFICATION_REJECTED'); return; }
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
    const summarize = now() - lastSummaryAt >= 60_000;
    let end = summarize ? startWorkflowBackgroundRun('workflow_notification_drain', 'poll', { environment }) : undefined;
    if (summarize) lastSummaryAt = now();
    const first = firstPoll; firstPoll = false;
    const enabled = environment === 'pre' || environment === 'prod';
    if (first) console.info('[workflow-notification] ' + JSON.stringify({ event: 'worker_started', environment, enabled }));
    if (!enabled) { end?.('skipped', { reason: 'ENVIRONMENT_DISABLED' }); return; }
    let stage = 'settings_scan';
    try {
      const preWorkflows = (await input.settings.list()).filter(policy => policy.executionEnvironment === 'pre').map(policy => policy.workflowId);
      stage = 'queue_read';
      const ready = await repo.readyNotifications(now(), 5, { workflowIds: preWorkflows, exclude: environment === 'prod' });
      if (ready.length && !end) end = startWorkflowBackgroundRun('workflow_notification_drain', 'poll', { environment });
      if (first || ready.length) console.info('[workflow-notification] ' + JSON.stringify({ event: 'batch_ready', environment,
        eligibleCount: ready.length, preWorkflowCount: preWorkflows.length }));
      stage = 'delivery';
      // Five records in parallel, with the existing timeout and claims unchanged.
      await Promise.all(ready.map(record => notify(record).catch(error => {
        console.warn('[workflow-notification] ' + JSON.stringify({ event: 'delivery_failed', environment, workflowId: record.workflowId,
          noticeId: record.id, taskId: record.data.taskId ?? null, revision: record.data.revision ?? null, reason: 'NOTIFICATION_DELIVERY_FAILED' }));
        throw error;
      })));
      end?.('succeeded', { count: ready.length, suppressed });
      if (end) suppressed = 0;
    } catch (error) {
      end?.('failed', { reason: stage, suppressed });
      if (now() - lastFailureAt >= 60_000) {
        console.warn('[workflow-notification] ' + JSON.stringify({ event: 'drain_failed', environment, stage, reason: 'NOTIFICATION_DRAIN_FAILED', suppressed }));
        lastFailureAt = now(); suppressed = 0;
      } else suppressed++;
      throw error;
    }
  };
}
