import { productionRunsSql, isTrialRun } from "@avernet/clawweb-shared/server/services/run-purpose";
import { logWorkflowBackground, startWorkflowBackgroundRun } from '@avernet/clawweb-shared/server/services/workflow-background-log';
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
import { previousShanghaiDay, selectAutomaticScope } from './workflow-automation-selection.js';
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
    catch (error) { await repo.patch(job, { status: 'retryable', reason: error instanceof Error ? error.message : 'OWNER_PERMISSION_DENIED', nextAtMs: now() + 60000 }, now(), true); return; }
    job = await repo.patch(job, { status: 'dispatching', attempts: job.attempts + 1, data: { ...job.data, actorId: access.actorId } }, now());
    let result: AnalysisObservation;
    logWorkflowBackground('workflow_analysis_dispatch', 'dispatch', { environment, workflowId: job.workflowId, flowId: job.data.flowId });
    try { result = await bounded(input.ports.analysis.start({ workflowId: job.workflowId, flowId: job.data.flowId, actorId: access.actorId, policyVersion: access.policy.version, dispatchToken: job.leaseToken!, requestId: job.id })); }
    catch { result = { status: 'uncertain' }; }
    await repo.patch(job, { status: result.status === 'completed' ? 'done' : result.status === 'failed' ? 'retryable' : result.status === 'running' ? 'waiting' : 'uncertain',
      nextAtMs: now() + 30000, reason: result.status === 'uncertain' ? 'ANALYSIS_OUTCOME_UNKNOWN' : result.status === 'failed' ? 'ANALYSIS_FAILED' : result.status === 'completed' && result.complete === false ? 'PARTIAL_ANALYSIS' : null,
      data: { ...job.data, analysisId: result.analysisId ?? null, complete: result.complete !== false } }, now(), true);
  }
  async function scan(job: AutomationJob, policy: WorkflowAutomationPolicy) {
    const due = automationDue(now(), policy.dailyTime);
    if (!job.data.openDue && (!policy.dailyConfigured || due.at < policy.updatedAtMs || job.data.completedDue === due.key)) return job;
    if (!job.data.openDue) job = await repo.patch(job, { data: { ...job.data, openDue: due.key, cutoffMs: now() - 2000, scopeUntilMs: previousShanghaiDay(due.at).untilMs, cursor: null } }, now());
    const watermark = Number(job.data.scannedThroughMs ?? job.data.scanFromMs ?? now() - DAY);
    // Re-read the whole preceding natural day: today's completed runs were
    // deliberately excluded by the previous scan even if already modified.
    const sinceMs = (policy.scopeVersion === 'all-qualified/v1' ? Math.min(watermark, Number(job.data.scopeUntilMs) - DAY) : watermark) - 60000;
    const cursor = job.data.cursor as { modified: number | string; id: number } | null;
    const rows = await input.db.query<{ id: number; flow_id: string; modified: number | string }>(`SELECT id, flow_id, COALESCE(gmt_modified, gmt_create) AS modified FROM ${productionRunsSql()} flow_runs
      WHERE workflow_id = ? AND completed_at IS NOT NULL AND status IN ('succeeded', 'failed', 'cancelled')
      ${policy.scopeVersion === 'all-qualified/v1' ? 'AND started_at > 0 AND ((started_at < 1000000000000 AND started_at < ?) OR (started_at >= 1000000000000 AND started_at < ?))' : ''}
      AND COALESCE(gmt_modified, gmt_create) >= ? AND COALESCE(gmt_modified, gmt_create) <= ?
      ${cursor ? 'AND (COALESCE(gmt_modified, gmt_create) > ? OR (COALESCE(gmt_modified, gmt_create) = ? AND id > ?))' : ''}
      ORDER BY COALESCE(gmt_modified, gmt_create), id LIMIT 20`, [job.workflowId, ...(policy.scopeVersion === 'all-qualified/v1' ? [job.data.scopeUntilMs / 1000, job.data.scopeUntilMs] : []), input.db.dialect.epochToDb(Math.floor(sinceMs / 1000)),
        input.db.dialect.epochToDb(Math.floor(job.data.cutoffMs / 1000)), ...(cursor ? [cursor.modified, cursor.modified, cursor.id] : [])]);
    for (const row of rows) await repo.ensure(job.workflowId, 'auto_run', row.flow_id, { flowId: row.flow_id, evidenceCutoffMs: job.data.cutoffMs }, now());
    const last = rows.at(-1);
    return repo.patch(job, { data: rows.length === 20 ? { ...job.data, cursor: { modified: last!.modified, id: last!.id } }
      : { ...job.data, completedDue: job.data.openDue, openDue: null, cursor: null, scannedThroughMs: job.data.cutoffMs } }, now());
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
  async function planDraft(cycle: AutomationJob) {
    const { actorId, policy } = await authority(cycle.workflowId, true);
    if (!cycle.data.completedDue || policy.scopeVersion !== 'all-qualified/v1' && cycle.data.draftDue === cycle.data.completedDue) return cycle;
    const pendingTaskId = await repo.pendingAutomaticTask(cycle.workflowId);
    if (pendingTaskId) return repo.patch(cycle, { reason: 'DRAFT_IN_PROGRESS', data: { ...cycle.data, blockingTaskId: pendingTaskId } }, now());
    // Planning is DB-only. Git generation material is resolved by workbench.create,
    // after a real, version-bound selection exists, not on every timer tick.
    const header = await input.workbench.candidates(cycle.workflowId, { state: 'pending', pageSize: 1, versionScope: 'current' });
    const snapshot = header.activeSnapshot;
    if (snapshot?.status !== 'resolved' || snapshot.snapshot.kind !== 'deployment') {
      return repo.patch(cycle, { reason: 'AUTOMATION_SNAPSHOT_UNAVAILABLE' }, now());
    }
    const baseline = { activeDeployNumber: snapshot.snapshot.deployNumber, specDigest: snapshot.snapshot.configDigest };
    if (policy.scopeVersion === 'all-qualified/v1') {
      const occupied=await repo.activeDraft(cycle.workflowId,undefined,baseline.activeDeployNumber??undefined);
      if(occupied)return repo.patch(cycle,{reason:'DRAFT_IN_PROGRESS',data:{...cycle.data,blockingTaskId:occupied.data.realTaskId??null}},now());
    }
    const selected = await selectAutomaticScope(input.workbench, issues, cycle.workflowId, baseline, policy.scopeVersion === 'all-qualified/v1'
      ? { db: input.db, minimumImpact: policy.minimumImpact ?? 'medium', evidenceCutoffMs: now() } : undefined);
    if (selected?.batches) {
      if (!selected.items.length && cycle.data.round) return cycle;
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
      if (!selected.batches.length) { const claimed = await repo.claim(round.id, now()); if (claimed) await repo.patch(claimed, { status: 'blocked', reason: 'NO_ELIGIBLE_SCOPE' }, now(), true); }
      return repo.patch(cycle, { data: { ...cycle.data, round: { id: round.id, total: selected.items.length, batches: selected.batches.length,
        completedBatches: round.data.completedBatches ?? 0, deferred, ...selected.window, evidenceCutoffMs: round.data.evidenceCutoffMs } } }, now());
    }
    if (!selected) return cycle;
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
    return repo.patch(cycle, { data: { ...cycle.data, draftDue: cycle.data.completedDue } }, now());
  }
  async function runDraft(record: AutomationJob) {
    let job = await repo.claim(record.id, now()); if (!job || !job.data.request) return;
    if (job.data.batches) return runAutomaticRound({ job, repo, workbench: input.workbench, now, authority: () => authority(job!.workflowId, true) });
    const request = job.data.request as RepairSelectionRequest;
    const existing = await input.workbench.request(job.workflowId, request.requestId);
    let taskId = existing?.taskId ?? job.data.realTaskId;
    if (!taskId) {
      if (['dispatching', 'uncertain'].includes(record.status) && record.attempts > 0) {
        await repo.patch(job, { status: 'uncertain', reason: 'DRAFT_REQUEST_REQUIRES_LOOKUP', nextAtMs: now() + 30000 }, now(), true); return;
      }
      if (await repo.pendingAutomaticTask(job.workflowId)) {
        await repo.patch(job, { status: 'pending', reason: 'DRAFT_IN_PROGRESS', nextAtMs: now() + 60000 }, now(), true); return;
      }
      let access;
      try { access = await authority(job.workflowId, true); }
      catch (error) { if (!(error instanceof RepairAutomationDeniedError)) throw error;
        await repo.patch(job, { status: 'retryable', reason: error.message, nextAtMs: now() + 30000 }, now(), true); return; }
      if (access.actorId !== job.data.actorId || access.policy.version !== request.automation!.policyVersion) {
        await repo.patch(job, { status: 'blocked', reason: 'OWNER_OR_POLICY_CHANGED' }, now(), true); return;
      }
      if (job.attempts >= 2) { await repo.patch(job, { status: 'blocked', reason: 'DRAFT_REQUIRES_MANUAL_REVIEW' }, now(), true); return; }
      job = await repo.patch(job, { status: 'dispatching', attempts: job.attempts + 1 }, now());
      logWorkflowBackground('workflow_automatic_draft', 'dispatch', { environment, workflowId: job.workflowId });
      try { taskId = (await bounded(input.workbench.create(access.actorId, request))).taskId; }
      catch {
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
  async function workflow(policy: WorkflowAutomationPolicy) {
    if (!policy.enabled) {
      // Park never-dispatched analysis without claiming/writing it each poll.
      // Filter before LIMIT so parked jobs cannot starve in-flight reconciliation.
      for (const job of await repo.ready(policy.workflowId, 'auto_run', now(), 3, true)) await runAnalysis(job);
      for (const job of await repo.ready(policy.workflowId, 'auto_draft', now(), 3, true)) await runDraft(job);
      return;
    }
    const initial = await repo.ensure(policy.workflowId, 'auto_cycle', 'state', { scanFromMs: policy.scopeVersion === 'all-qualified/v1' ? previousShanghaiDay(now()).fromMs : now() - DAY, scannedThroughMs: null, completedDue: null }, now());
    let cycle = await repo.claim(initial.id, now()); if (!cycle) return;
    try {
      if (policy.enabled && policy.dailyConfigured) {
        try { await authority(policy.workflowId); cycle = await scan(cycle, policy); cycle = await repo.patch(cycle, { reason: null }, now()); }
        catch (error) { if (!(error instanceof RepairAutomationDeniedError)) throw error;
          cycle = await repo.patch(cycle, { reason: error.message }, now()); }

      }
      if (!policy.enabled) cycle = await repo.patch(cycle, { reason: 'AUTOMATION_DISABLED' }, now());
      // Existing analysis/draft requests are read/reconciled even while policy is disabled.
      for (const job of (await repo.ready(policy.workflowId, 'auto_run', now(), 3))) await runAnalysis(job);
      for (const job of (await repo.ready(policy.workflowId, 'auto_draft', now(), 3, !policy.autoDraft || !policy.dailyConfigured))) await runDraft(job);
      const current = await input.settings.get(policy.workflowId);
      if (current.enabled && current.dailyConfigured && cycle.data.completedDue) {
        const analysisVersion = await input.db.query(`SELECT COUNT(*) AS count, MAX(id) AS latest, MAX(completed_at_ms) AS completed, SUM(state_version) AS revision
          FROM workflow_evolution_analysis_runs WHERE workflow_id = ?`, [policy.workflowId]);
        const active = await input.db.query('SELECT deploy_number FROM workflow_deploy_history WHERE workflow_id = ? AND is_active = 1', [policy.workflowId]);
        const evaluated = digestRepairJson([analysisVersion, active, current.version, cycle.data.completedDue]);
        if (cycle.data.evaluated !== evaluated) {
          await queueIssues(policy.workflowId, current);
          if (current.autoDraft) cycle = await planDraft(cycle);
          if (cycle.reason !== 'DRAFT_IN_PROGRESS') cycle = await repo.patch(cycle, { data: { ...cycle.data, evaluated, blockingTaskId: null } }, now());
        }
      }
      await repo.patch(cycle, { nextAtMs: now() + 10000 }, now(), true);
    } catch (error) {
      if (!(error instanceof RepairAutomationDeniedError)) throw error;
      await repo.patch(cycle, { reason: error.message, nextAtMs: now() + 30000 }, now(), true);
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
      // Fence after policy I/O, then renew at each workflow boundary. Never
      // hold a DB transaction over candidate queries or external dispatch.
      lease = await planner.checkpoint(lease, lease.cursor, now());
      for (let i = 0; i < count; i++) {
        const policy = policies[(start + i) % policies.length];
        try { await workflow(policy); }
        catch (error) { logWorkflowBackground('workflow_policy', 'failed', { environment, workflowId: policy.workflowId });
          console.warn('[workflow-automation] reconcile failed', { workflowId: policy.workflowId, environment,
          code: error instanceof RepairAutomationDeniedError ? error.message : 'AUTOMATION_RECONCILE_FAILED' }); }
        lease = await planner.checkpoint(lease, policy.workflowId, now());
      }
      await planner.checkpoint(lease, lease.cursor, now(), true); lease = null;
      // Manual completions and in-flight automatic results notify even with automation disabled/unconfigured.
      await drainNotifications();
      end('succeeded', { policyCount: policies.length, enabledPolicyCount: policies.filter(policy => policy.enabled).length, count });
      return count;
      } catch (error) { end('failed', { errorCode: error instanceof Error && error.message === 'AUTOMATION_PLANNER_LEASE_LOST'
        ? 'AUTOMATION_PLANNER_LEASE_LOST' : 'AUTOMATION_RECONCILE_FAILED' }); throw error; }
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
      } catch (error) { end('failed'); throw error; }
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
