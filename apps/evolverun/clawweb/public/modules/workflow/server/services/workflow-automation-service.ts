import { productionRunsSql, isTrialRun } from "@avernet/clawweb-shared/server/services/run-purpose";
import { logWorkflowBackground, startWorkflowBackgroundRun, workflowBackgroundError } from '@avernet/clawweb-shared/server/services/workflow-background-log';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { IssueAggregationRepository } from '@avernet/clawevolve/server/repositories/issue-aggregation-repository';
import type { AutoAnalysisSettings } from './task-guard/auto-analysis-settings.js';
import type { AnalysisObservation, AutomationWorkbench, WorkflowAutomationPorts, WorkflowAutomationService, WorkflowAutomationPolicy, AutomationOrigin } from '../contracts/workflow-automation.js';
import { RepairAutomationDeniedError } from '../contracts/workflow-automation.js';
import type { RepairSelectionRequest } from '../contracts/repair-workbench.js';
import { digestRepairJson } from '../contracts/repair-batch.js';
import { WorkflowAutomationRepository, type AutomationJob } from '../repositories/workflow-automation-repository.js';
import { WorkflowPlannerLeaseRepository } from '../repositories/workflow-planner-lease-repository.js';
import { runAutomaticRound } from './workflow-automation-round.js';
import { selectAutomaticScope } from './workflow-automation-selection.js';
import { WorkflowAutomationHistoryRepository } from '../repositories/workflow-automation-history-repository.js';
import { createWorkflowNotificationDrain } from './workflow-notification-delivery.js';
const DAY = 86400000;
export function automationDue(now: number, dailyTime: string) {
  const day = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(now));
  const today = Date.parse(`${day}T${dailyTime}:00+08:00`);
  const at = now >= today ? today : today - DAY;
  return { at, key: new Date(at + 8 * 3600000).toISOString().slice(0, 10) };
}
async function bounded<T>(action: Promise<T>): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try { return await Promise.race([action, new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new Error('AUTOMATION_OUTCOME_UNKNOWN')), 20000); })]); }
  finally { if (timer) clearTimeout(timer); }
}
export function createWorkflowAutomation(input: { db: IDatabase; settings: AutoAnalysisSettings; ports: WorkflowAutomationPorts; environment?: string;
  workbench: AutomationWorkbench; now?: () => number }): WorkflowAutomationService {
  const now = input.now ?? Date.now;
  const repo = new WorkflowAutomationRepository(input.db);
  const drainNotifications = createWorkflowNotificationDrain(input);
  const issues = new IssueAggregationRepository(input.db);
  const planner = new WorkflowPlannerLeaseRepository(input.db);
  const environment = input.environment ?? 'prod';
  logWorkflowBackground('workflow_automation', 'configured', { enabled: true, environment });
  async function authority(workflowId: string, draft = false) {
    const policy = await input.settings.get(workflowId);
    if ((policy.executionEnvironment ?? 'prod') !== environment) throw new RepairAutomationDeniedError('EXECUTION_ENVIRONMENT_MISMATCH');
    if (!policy.enabled || draft && (!policy.autoDraft || !policy.dailyConfigured)) throw new RepairAutomationDeniedError('AUTOMATION_DISABLED');
    const actorId = await input.ports.owner(workflowId);
    if (!actorId) throw new RepairAutomationDeniedError('OWNER_MISSING');
    if (!await input.ports.canEdit(workflowId, actorId)) throw new RepairAutomationDeniedError('OWNER_PERMISSION_DENIED');
    return { actorId, policy };
  }
  async function runAnalysis(record: AutomationJob) {
    let job = await repo.claim(record.id, now()); if (!job) return;
    if (await isTrialRun(input.db, job.data.flowId)) {
      await repo.patch(job, { status: 'blocked', reason: 'CANDIDATE_TRIAL' }, now(), true); return;
    }
    const observed = await bounded(input.ports.analysis.lookup(job.data.flowId, job.workflowId));
    if (observed.status === 'completed') {
      await repo.patch(job, { status: 'done', reason: observed.complete === false ? 'PARTIAL_ANALYSIS' : null,
        data: { ...job.data, analysisId: observed.analysisId ?? null, complete: observed.complete !== false } }, now(), true); return;
    }
    if (observed.status === 'running') { await repo.patch(job, { status: now() - job.createdAtMs > 1800000 ? 'uncertain' : 'waiting', reason: now() - job.createdAtMs > 1800000 ? 'ANALYSIS_TIMEOUT' : null, nextAtMs: now() + 30000 }, now(), true); return; }
    if (observed.status === 'uncertain' || ['dispatching', 'uncertain', 'waiting'].includes(job.status) && observed.status === 'absent') {
      await repo.patch(job, { status: 'uncertain', reason: 'ANALYSIS_OUTCOME_UNKNOWN', nextAtMs: now() + 60000 }, now(), true); return;
    }
    if (job.attempts >= 3) { await repo.patch(job, { status: 'blocked', reason: 'ANALYSIS_FAILED' }, now(), true); return; }
    let access;
    try { access = await authority(job.workflowId); }
    catch (error) {
      logWorkflowBackground('workflow_analysis_dispatch', 'skipped', { environment, workflowId: job.workflowId, jobId: job.id,
        flowId: job.data.flowId, phase: 'analysis_authorization', ...workflowBackgroundError(error) });
      await repo.patch(job, { status: 'retryable', reason: error instanceof Error ? error.message : 'OWNER_PERMISSION_DENIED', nextAtMs: now() + 60000 }, now(), true); return;
    }
    job = await repo.patch(job, { status: 'dispatching', attempts: job.attempts + 1, data: { ...job.data, actorId: access.actorId } }, now());
    let result: AnalysisObservation;
    logWorkflowBackground('workflow_analysis_dispatch', 'dispatch', { environment, workflowId: job.workflowId, flowId: job.data.flowId });
    try { result = await bounded(input.ports.analysis.start({ workflowId: job.workflowId, flowId: job.data.flowId, actorId: access.actorId, policyVersion: access.policy.version, dispatchToken: job.leaseToken!, requestId: job.id })); }
    catch (error) {
      logWorkflowBackground('workflow_analysis_dispatch', 'failed', { environment, workflowId: job.workflowId, jobId: job.id,
        flowId: job.data.flowId, phase: 'analysis_dispatch', ...workflowBackgroundError(error) });
      result = { status: 'uncertain' };
    }
    await repo.patch(job, { status: result.status === 'completed' ? 'done' : result.status === 'failed' ? 'retryable' : result.status === 'running' ? 'waiting' : 'uncertain',
      nextAtMs: now() + 30000, reason: result.status === 'uncertain' ? 'ANALYSIS_OUTCOME_UNKNOWN' : result.status === 'failed' ? 'ANALYSIS_FAILED' : result.status === 'completed' && result.complete === false ? 'PARTIAL_ANALYSIS' : null,
      data: { ...job.data, analysisId: result.analysisId ?? null, complete: result.complete !== false } }, now(), true);
  }
  async function queueIssues(workflowId: string, policy: WorkflowAutomationPolicy) {
    for (let page = 1;; page++) {
      const ranked = await issues.listPage(workflowId, { page, pageSize: 20, sort: 'priority' });
      for (const group of ranked.groups) {
        if (!['high', 'critical'].includes(group.priority?.severity ?? '')) continue;
        const key = digestRepairJson([group.signature, group.priority?.severity, [...new Set(group.flowIds)].sort()]);
        await repo.ensure(workflowId, 'auto_notice', `issue:${key}`, { title: '工作流发现重点问题',
          text: `影响程度：${group.priority!.severity}；受影响运行 ${group.flowIds.length} 次。请审阅证据和处理方案。`,
          path: `/workflows/workspace?workflowId=${encodeURIComponent(workflowId)}&tab=evolution`, kind: 'issue' }, now());
      }
      if (policy.scopeVersion !== 'all-qualified/v1' || page >= ranked.page.totalPages) break;
    }
  }
  async function planDraft(cycle: AutomationJob, dueKey: string) {
    const { actorId, policy } = await authority(cycle.workflowId, true);
    if (policy.scopeVersion !== 'all-qualified/v1' && cycle.data.draftDue === dueKey) {
      logWorkflowBackground('workflow_automatic_draft', 'skipped', { environment, workflowId: cycle.workflowId, reason: 'ALREADY_PLANNED_TODAY' });
      return repo.patch(cycle, { reason: 'ALREADY_PLANNED_TODAY' }, now());
    }
    const pendingTaskId = await repo.pendingAutomaticTask(cycle.workflowId);
    if (pendingTaskId) {
      logWorkflowBackground('workflow_automatic_draft', 'skipped', { environment, workflowId: cycle.workflowId, taskId: pendingTaskId, reason: 'DRAFT_IN_PROGRESS' });
      return repo.patch(cycle, { reason: 'DRAFT_IN_PROGRESS', data: { ...cycle.data, blockingTaskId: pendingTaskId } }, now());
    }
    // Planning is DB-only. Git generation material is resolved by workbench.create,
    // after a real, version-bound selection exists, not on every timer tick.
    const header = await input.workbench.candidates(cycle.workflowId, { state: 'pending', pageSize: 1, versionScope: 'current' });
    const snapshot = header.activeSnapshot;
    if (snapshot?.status !== 'resolved' || snapshot.snapshot.kind !== 'deployment') {
      logWorkflowBackground('workflow_automatic_draft', 'skipped', { environment, workflowId: cycle.workflowId, reason: 'AUTOMATION_SNAPSHOT_UNAVAILABLE' });
      return repo.patch(cycle, { reason: 'AUTOMATION_SNAPSHOT_UNAVAILABLE' }, now());
    }
    const baseline = { activeDeployNumber: snapshot.snapshot.deployNumber, specDigest: snapshot.snapshot.configDigest };
    if (policy.scopeVersion === 'all-qualified/v1') {
      const occupied=await repo.activeDraft(cycle.workflowId,undefined,baseline.activeDeployNumber??undefined);
      if(occupied){
        logWorkflowBackground('workflow_automatic_draft', 'skipped', { environment, workflowId: cycle.workflowId, taskId: occupied.data.realTaskId, jobId: occupied.id, reason: 'DRAFT_IN_PROGRESS' });
        return repo.patch(cycle,{reason:'DRAFT_IN_PROGRESS',data:{...cycle.data,blockingTaskId:occupied.data.realTaskId??null}},now());
      }
    }
    const selected = await selectAutomaticScope(input.workbench, issues, cycle.workflowId, baseline, policy.scopeVersion === 'all-qualified/v1'
      ? { db: input.db, minimumImpact: policy.minimumImpact ?? 'medium', evidenceCutoffMs: now() } : undefined);
    if (!selected?.items.length || selected.batches && !selected.batches.length) {
      logWorkflowBackground('workflow_automatic_draft', 'skipped', { environment, workflowId: cycle.workflowId, reason: 'NO_ELIGIBLE_SUGGESTIONS', count: 0 });
      const reasons = Object.values(selected?.deferred ?? {});
      for (const reason of new Set(reasons)) logWorkflowBackground('workflow_automatic_draft', 'filtered', {
        environment, workflowId: cycle.workflowId, reason, count: reasons.filter(value => value === reason).length });
      return repo.patch(cycle, { reason: 'NO_ELIGIBLE_SCOPE' }, now());
    }
    if (selected?.batches) {
      const deferred = Object.values(selected.deferred ?? {}).reduce<Record<string, number>>((out, reason) => { out[reason] = (out[reason] ?? 0) + 1; return out; }, {});
      const round = await repo.ensure(cycle.workflowId, 'auto_draft', digestRepairJson([selected.fingerprint, policy.version]), { actorId, fingerprint: selected.fingerprint, baseline,...(selected.targetSnapshotKey?{targetSnapshotKey:selected.targetSnapshotKey}:{}),
        total: selected.items.length, batches: selected.batches, revisions: selected.revisions, deferredItems: selected.deferred,
        completedBatches: 0, window: selected.window, evidenceCutoffMs: now() }, now());
      if (!round.data.request && selected.batches.length) {
        const claimed = await repo.claim(round.id, now());
        if (claimed) {
          const request: RepairSelectionRequest = { workflowId: cycle.workflowId, itemIds: selected.batches[0], inputDigest: selected.inputDigest,
            includeHistorical: true, instructions: '', ...(selected.targetSnapshotKey?{targetSnapshotKey:selected.targetSnapshotKey,historicalItemIds:[]}:{}),requestId: `${round.id}:0`, automation: { kind: 'daily', intentId: round.id, roundId: round.id,
              policyVersion: policy.version, evidenceCutoffMs: round.data.evidenceCutoffMs, batchIndex: 0, batchCount: selected.batches.length, totalItems: selected.items.length } };
          await repo.patch(claimed, { data: { ...claimed.data, request } }, now(), true);
        }
      }
      logWorkflowBackground('workflow_automatic_draft', 'planned', { environment, workflowId: cycle.workflowId, jobId: round.id, count: selected.items.length });
      return repo.patch(cycle, { reason: null, data: { ...cycle.data, round: { id: round.id, total: selected.items.length, batches: selected.batches.length,
        completedBatches: round.data.completedBatches ?? 0, deferred, ...selected.window, evidenceCutoffMs: round.data.evidenceCutoffMs } } }, now());
    }
    const intent = await repo.ensure(cycle.workflowId, 'auto_draft', selected.fingerprint, { actorId, fingerprint: selected.fingerprint, baseline, ...(selected.targetSnapshotKey ? { targetSnapshotKey: selected.targetSnapshotKey } : {}) }, now());
    const existing = intent.data.request;
    if (!existing) {
      const claimed = await repo.claim(intent.id, now());
      if (claimed) {
        const request: RepairSelectionRequest = { workflowId: cycle.workflowId, itemIds: selected.items.map(item => item.itemId), inputDigest: selected.inputDigest,
          instructions: '', ...(selected.targetSnapshotKey?{targetSnapshotKey:selected.targetSnapshotKey,historicalItemIds:[]}:{}),requestId: intent.id, automation: { kind: 'daily', intentId: intent.id, policyVersion: policy.version, evidenceCutoffMs: now() } };
        await repo.patch(claimed, { data: { ...claimed.data, request } }, now(), true);
      }
    }
    logWorkflowBackground('workflow_automatic_draft', 'planned', { environment, workflowId: cycle.workflowId, jobId: intent.id, count: selected.items.length });
    return repo.patch(cycle, { reason: null, data: { ...cycle.data, draftDue: dueKey } }, now());
  }
  async function runDraft(record: AutomationJob) {
    let job = await repo.claim(record.id, now()); if (!job || !job.data.request) return;
    if (job.data.batches) return runAutomaticRound({ job, repo, workbench: input.workbench, now, authority: () => authority(job!.workflowId, true) });
    const request = job.data.request as RepairSelectionRequest;
    const existing = await input.workbench.request(job.workflowId, request.requestId);
    let taskId = existing?.taskId ?? job.data.realTaskId;
    if (!taskId) {
      if (['dispatching', 'uncertain'].includes(record.status) && record.attempts > 0) {
        logWorkflowBackground('workflow_automatic_draft', 'skipped', { environment, workflowId: job.workflowId, jobId: job.id, reason: 'DRAFT_REQUEST_REQUIRES_LOOKUP' });
        await repo.patch(job, { status: 'uncertain', reason: 'DRAFT_REQUEST_REQUIRES_LOOKUP', nextAtMs: now() + 30000 }, now(), true); return;
      }
      if (await repo.pendingAutomaticTask(job.workflowId)) {
        logWorkflowBackground('workflow_automatic_draft', 'skipped', { environment, workflowId: job.workflowId, jobId: job.id, reason: 'DRAFT_IN_PROGRESS' });
        await repo.patch(job, { status: 'pending', reason: 'DRAFT_IN_PROGRESS', nextAtMs: now() + 60000 }, now(), true); return;
      }
      let access;
      try { access = await authority(job.workflowId, true); }
      catch (error) { if (!(error instanceof RepairAutomationDeniedError)) throw error;
        logWorkflowBackground('workflow_automatic_draft', 'skipped', { environment, workflowId: job.workflowId, jobId: job.id,
          phase: 'draft_authorization', ...workflowBackgroundError(error) });
        await repo.patch(job, { status: 'retryable', reason: error.message, nextAtMs: now() + 30000 }, now(), true); return; }
      if (access.actorId !== job.data.actorId || access.policy.version !== request.automation!.policyVersion) {
        logWorkflowBackground('workflow_automatic_draft', 'skipped', { environment, workflowId: job.workflowId, jobId: job.id, reason: 'OWNER_OR_POLICY_CHANGED' });
        await repo.patch(job, { status: 'blocked', reason: 'OWNER_OR_POLICY_CHANGED' }, now(), true); return;
      }
      if (job.attempts >= 2) {
        logWorkflowBackground('workflow_automatic_draft', 'skipped', { environment, workflowId: job.workflowId, jobId: job.id, reason: 'DRAFT_REQUIRES_MANUAL_REVIEW' });
        await repo.patch(job, { status: 'blocked', reason: 'DRAFT_REQUIRES_MANUAL_REVIEW' }, now(), true); return;
      }
      job = await repo.patch(job, { status: 'dispatching', attempts: job.attempts + 1 }, now());
      logWorkflowBackground('workflow_automatic_draft', 'dispatch', { environment, workflowId: job.workflowId });
      try {
        taskId = (await bounded(input.workbench.create(access.actorId, request))).taskId;
        logWorkflowBackground('workflow_automatic_draft', 'created', { environment, workflowId: job.workflowId, jobId: job.id, taskId });
      }
      catch (error) {
        logWorkflowBackground('workflow_automatic_draft', 'failed', { environment, workflowId: job.workflowId, jobId: job.id,
          phase: 'create_task', ...workflowBackgroundError(error) });
        const recovered = await input.workbench.request(job.workflowId, request.requestId);
        if (!recovered) { await repo.patch(job, { status: 'uncertain', reason: 'DRAFT_REQUEST_REQUIRES_LOOKUP', nextAtMs: now() + 30000 }, now(), true); return; }
        taskId = recovered.taskId;
      }
    }
    const detail = await input.workbench.task(taskId);
    const revision = detail.latestAttempt;
    const data = { ...job.data, realTaskId: taskId };
    if (revision.phase === 'review' && detail.latestSuccessful?.revision === revision.revision && (revision.checks?.static as { status?: string })?.status === 'passed') {
      await repo.ensure(job.workflowId, 'auto_notice', `draft:${taskId}:${revision.revision}`, { title: '自动优化草稿已可审阅', text: '候选及检查记录已就绪。请审阅完整差异和未覆盖风险后确认是否发布。',
        path: `/workflows/workspace?workflowId=${encodeURIComponent(job.workflowId)}&tab=evolution&repairTaskId=${encodeURIComponent(taskId)}`, taskId, revision: revision.revision, kind: 'draft' }, now());
      await repo.patch(job, { status: 'done', data }, now(), true); return;
    }
    const terminal = ['failed', 'blocked', 'cancelled', 'no_change', 'published'].includes(revision.phase);
    await repo.patch(job, { status: terminal ? 'blocked' : 'waiting', reason: terminal ? 'DRAFT_REQUIRES_MANUAL_REVIEW' : null, data, nextAtMs: now() + 30000 }, now(), true);
  }
  async function workflow(policy: WorkflowAutomationPolicy, progress: { phase: string; failed: boolean }) {
    if (!policy.enabled) {
      // Park never-dispatched analysis without claiming/writing it each poll.
      // Filter before LIMIT so parked jobs cannot starve in-flight reconciliation.
      progress.phase = 'draft_recovery';
      for (const job of await repo.ready(policy.workflowId, 'auto_draft', now(), 3, true)) await runDraft(job);
      progress.phase = 'analysis_recovery';
      for (const job of await repo.ready(policy.workflowId, 'auto_run', now(), 3, true)) await runAnalysis(job);
      return;
    }
    progress.phase = 'cycle_claim';
    // Retain the existing journal identity, but never read or resume a historical-run scan.
    const initial = await repo.ensure(policy.workflowId, 'auto_cycle', 'state', {}, now());
    let cycle = await repo.claim(initial.id, now());
    if (!cycle) {
      logWorkflowBackground('workflow_automatic_draft', 'skipped', { environment, workflowId: policy.workflowId, reason: 'CYCLE_NOT_CLAIMED' });
      return;
    }
    try {
      progress.phase = 'settings_read';
      const current = await input.settings.get(policy.workflowId);
      const due = automationDue(now(), current.dailyTime);
      let notifyIssues = false;
      if (current.enabled && current.dailyConfigured && due.at >= current.updatedAtMs) {
        // A daily opportunity is handled once, even when empty. New analyses wait
        // for the next daily plan; existing draft/result recovery below is independent.
        if (cycle.data.evaluated !== due.key) {
          progress.phase = 'draft_planning';
          try {
            if (current.autoDraft) cycle = await planDraft(cycle, due.key);
            else logWorkflowBackground('workflow_automatic_draft', 'skipped', { environment, workflowId: policy.workflowId, reason: 'AUTO_DRAFT_DISABLED' });
          } catch (error) {
            progress.failed = true;
            logWorkflowBackground('workflow_policy', 'failed', { environment, workflowId: policy.workflowId,
              phase: progress.phase, ...workflowBackgroundError(error) });
            cycle = await repo.patch(cycle, { reason: error instanceof RepairAutomationDeniedError ? error.message : 'AUTOMATION_RECONCILE_FAILED' }, now());
          }
          progress.phase = 'daily_settlement';
          cycle = await repo.patch(cycle, { data: { ...cycle.data, evaluated: due.key,
            ...(cycle.reason === 'DRAFT_IN_PROGRESS' ? {} : { blockingTaskId: null }) } }, now());
          notifyIssues = true;
        }
      } else logWorkflowBackground('workflow_automatic_draft', 'skipped', { environment, workflowId: policy.workflowId,
        reason: !current.enabled ? 'AUTOMATION_DISABLED' : !current.dailyConfigured ? 'DAILY_NOT_CONFIGURED' : 'NOT_DUE' });

      // Notification bookkeeping must not prevent dispatch of an already-planned draft.
      progress.phase = 'notification_queue';
      if (notifyIssues) try { await queueIssues(policy.workflowId, current); }
      catch (error) {
        progress.failed = true;
        logWorkflowBackground('workflow_policy', 'failed', { environment, workflowId: policy.workflowId,
          phase: progress.phase, ...workflowBackgroundError(error) });
      }
      // Dispatch new drafts in this pass, before unrelated analysis recovery.
      progress.phase = 'draft_dispatch';
      for (const job of await repo.ready(policy.workflowId, 'auto_draft', now(), 3, !current.autoDraft || !current.dailyConfigured)) await runDraft(job);
      progress.phase = 'analysis_recovery';
      for (const job of await repo.ready(policy.workflowId, 'auto_run', now(), 3)) {
        try { await runAnalysis(job); }
        catch (error) {
          progress.failed = true;
          logWorkflowBackground('workflow_policy', 'failed', { environment, workflowId: policy.workflowId, jobId: job.id,
            flowId: job.data.flowId, phase: progress.phase, ...workflowBackgroundError(error) });
        }
      }
      progress.phase = 'cycle_settlement';
      await repo.patch(cycle, { nextAtMs: now() + 10000 }, now(), true);
    } catch (error) {
      try { await repo.patch(cycle, { reason: error instanceof RepairAutomationDeniedError ? error.message : 'AUTOMATION_RECONCILE_FAILED', nextAtMs: now() + 60000 }, now(), true); }
      catch (releaseError) {
        logWorkflowBackground('workflow_policy', 'failed', { environment, workflowId: policy.workflowId,
          phase: 'cycle_release', ...workflowBackgroundError(releaseError) });
      }
      throw error;
    }
  }
  // Status and recovery share eligibility; inspecting status never claims or dispatches work.
  async function draftRecoveries(workflowId: string, enforceAuthority = false, jobId?: string) {
    const policy = await input.settings.get(workflowId);
    const failures = await repo.recoverableDrafts(workflowId, now(), policy.version, jobId);
    if (!failures.length) return [];
    let access;
    try { access = await authority(workflowId, true); }
    catch (error) { if (!enforceAuthority && error instanceof RepairAutomationDeniedError) return []; throw error; }
    const result: Array<{ job: AutomationJob; recovery: { expectedRevision: number; requestId: string } }> = [];
    for (const { job, revision } of failures) {
      if (job.data.request.automation.policyVersion !== access.policy.version) continue;
      result.push({ job, recovery: { expectedRevision: revision, requestId: `${job.id}:retry:${revision}` } });
    }
    return result;
  }
  function summarize(job: AutomationJob) {
    return { id: job.id, kind: job.kind, status: job.status, attempts: job.attempts, reason: job.reason, createdAtMs: job.createdAtMs,
      ...(job.data.realTaskId || job.data.taskId ? { taskId: job.data.realTaskId || job.data.taskId } : {}),
      ...(job.data.flowId ? { flowId: job.data.flowId } : {}),
      ...(job.data.analysisId ? { analysisId: job.data.analysisId } : {}),
      ...(typeof job.data.complete === 'boolean' ? { complete: job.data.complete } : {}) };
  }
  return {
    async tick() {
      const end = startWorkflowBackgroundRun('workflow_automation', 'timer', { environment });
      let lease: Awaited<ReturnType<typeof planner.claim>> = null;
      try {
      lease = await planner.claim(environment, now());
      if (!lease) {
        // Planning ownership must not pause the independent completion queue.
        await drainNotifications();
        end('skipped', { reason: 'PLANNER_NOT_CLAIMED' }); return 0;
      }
      const policies = (await input.settings.list()).filter(policy => (policy.executionEnvironment ?? 'prod') === environment)
        .sort((a, b) => a.workflowId < b.workflowId ? -1 : a.workflowId > b.workflowId ? 1 : 0);
      const count = Math.min(10, policies.length);
      const after = lease.cursor === null ? 0 : policies.findIndex(policy => policy.workflowId > lease!.cursor!);
      const start = after < 0 ? 0 : after;
      let failed = false;
      // Fence after policy I/O, then renew at each workflow boundary. Never
      // hold a DB transaction over candidate queries or external dispatch.
      lease = await planner.checkpoint(lease, lease.cursor, now());
      for (let i = 0; i < count; i++) {
        const policy = policies[(start + i) % policies.length];
        const progress = { phase: 'policy', failed: false };
        try { await workflow(policy, progress); }
        catch (error) { progress.failed = true;
          logWorkflowBackground('workflow_policy', 'failed', { environment, workflowId: policy.workflowId, phase: progress.phase, ...workflowBackgroundError(error) });
          console.warn('[workflow-automation] reconcile failed', { workflowId: policy.workflowId, environment,
          code: error instanceof RepairAutomationDeniedError ? error.message : 'AUTOMATION_RECONCILE_FAILED' }); }
        failed ||= progress.failed;
        lease = await planner.checkpoint(lease, policy.workflowId, now());
      }
      await planner.checkpoint(lease, lease.cursor, now(), true); lease = null;
      // Manual completions and in-flight automatic results notify even with automation disabled/unconfigured.
      await drainNotifications();
      end(failed ? 'failed' : 'succeeded', { policyCount: policies.length, enabledPolicyCount: policies.filter(policy => policy.enabled).length, count,
        ...(failed ? { reason: 'WORKFLOW_POLICY_FAILED' } : {}) });
      return count;
      } catch (error) { end('failed', { phase: 'planner', ...workflowBackgroundError(error) }); throw error; }
      finally {
        if (lease) try { await planner.checkpoint(lease, lease.cursor, now(), true); }
        catch { logWorkflowBackground('workflow_automation', 'release_failed', { environment, reason: 'PLANNER_LEASE_UNAVAILABLE' }); }
      }
    },
    async onRunTerminal(event) {
      if (event.status !== 'failed') return;
      const end = startWorkflowBackgroundRun('workflow_failed_run_analysis', 'run_failed', { environment, flowId: event.flowId });
      try {
      const row = (await input.db.query<{ workflow_id: string }>(`SELECT workflow_id FROM ${productionRunsSql()} flow_runs WHERE flow_id = ?`, [event.flowId]))[0];
      if (!row) { end('skipped', { reason: 'RUN_NOT_FOUND' }); return; }
      try { await authority(row.workflow_id); } catch (error) { if (error instanceof RepairAutomationDeniedError) {
        end('skipped', { workflowId: row.workflow_id, reason: error.message }); return;
      } throw error; }
      const job = await repo.ensure(row.workflow_id, 'auto_run', event.flowId, { flowId: event.flowId, evidenceCutoffMs: now() }, now());
      await runAnalysis(job);
      end('succeeded', { workflowId: row.workflow_id });
      } catch (error) { end('failed', { phase: 'failed_run_analysis', ...workflowBackgroundError(error) }); throw error; }
    },
    records(workflowId, query) { return new WorkflowAutomationHistoryRepository(input.db).page(workflowId, query, now()); },
    async status(workflowId) {
      const jobs = await repo.recent(workflowId);
      return { schemaVersion: 'workflow-automation/v1', workflowId, coverage: await repo.coverage(workflowId), pauseReason: jobs.find(job => job.kind === 'auto_cycle')?.reason ?? null, lastScannedAtMs: jobs.find(job => job.kind === 'auto_cycle')?.data.scannedThroughMs ?? null,
        ...(jobs.find(job => job.kind === 'auto_cycle')?.data.round ? { round: { ...jobs.find(job => job.kind === 'auto_cycle')!.data.round,
          completedBatches: (await repo.get(jobs.find(job => job.kind === 'auto_cycle')!.data.round.id))?.data.completedBatches ?? 0 } } : {}),
        jobs: jobs.map(summarize),
        retryableJobs: [...await repo.retryableFailures(workflowId, now()), ...(await draftRecoveries(workflowId)).map(entry => entry.job)].map(summarize) };
    },
    async retryFailures(workflowId, jobId) {
      const retried = await repo.retryFailures(workflowId, now(), jobId);
      const drafts = await draftRecoveries(workflowId, true, jobId);
      if (jobId && !retried && !drafts.length) throw new RepairAutomationDeniedError('TASK_NOT_RETRYABLE');
      for (const { job, recovery } of drafts) {
        await repo.recoverDraft(job, { ...job.data, recovery, request: { ...job.data.request, requestId: recovery.requestId } }, now());
      }
    },
    async authorizeDraft(request) {
      const job = await repo.get(request.origin.intentId);
      const access = await authority(request.workflowId, true);
      if (!job || job.kind !== 'auto_draft' || job.workflowId !== request.workflowId || job.status !== 'dispatching' || job.leaseUntilMs <= now()
        || job.data.actorId !== request.actorId || access.actorId !== request.actorId || access.policy.version !== request.origin.policyVersion
        || digestRepairJson(job.data.request?.automation ?? null) !== digestRepairJson(request.origin)
        || digestRepairJson([...job.data.request.itemIds].sort()) !== digestRepairJson([...request.itemIds].sort())
        || job.data.realTaskId && job.data.realTaskId !== request.taskId) throw new RepairAutomationDeniedError('AUTOMATION_AUTHORIZATION_CHANGED');
      await repo.patch(job, { data: { ...job.data, realTaskId: request.taskId } }, now());
    },
  };
}
