import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { IssueAggregationRepository } from '@avernet/clawevolve/server/repositories/issue-aggregation-repository';
import type { AutoAnalysisSettings } from './task-guard/auto-analysis-settings.js';
import type { AnalysisObservation, AutomationWorkbench, WorkflowAutomationPorts, WorkflowAutomationService, WorkflowAutomationPolicy, AutomationOrigin } from '../contracts/workflow-automation.js';
import { RepairAutomationDeniedError } from '../contracts/workflow-automation.js';
import type { RepairSelectionRequest } from '../contracts/repair-workbench.js';
import { digestRepairJson } from '../contracts/repair-batch.js';
import { WorkflowAutomationRepository, type AutomationJob } from '../repositories/workflow-automation-repository.js';
import { selectAutomaticScope } from './workflow-automation-selection.js';
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
export function createWorkflowAutomation(input: { db: IDatabase; settings: AutoAnalysisSettings; ports: WorkflowAutomationPorts;
  workbench: AutomationWorkbench; now?: () => number }): WorkflowAutomationService {
  const now = input.now ?? Date.now;
  const repo = new WorkflowAutomationRepository(input.db);
  const issues = new IssueAggregationRepository(input.db);
  let roundRobin = 0;
  async function authority(workflowId: string, draft = false) {
    const policy = await input.settings.get(workflowId);
    if (!policy.enabled || draft && (!policy.autoDraft || !policy.dailyConfigured)) throw new RepairAutomationDeniedError('AUTOMATION_DISABLED');
    const actorId = await input.ports.owner(workflowId);
    if (!actorId) throw new RepairAutomationDeniedError('OWNER_MISSING');
    if (!await input.ports.canEdit(workflowId, actorId)) throw new RepairAutomationDeniedError('OWNER_PERMISSION_DENIED');
    return { actorId, policy };
  }
  async function runAnalysis(record: AutomationJob) {
    let job = await repo.claim(record.id, now()); if (!job) return;
    const observed = await bounded(input.ports.analysis.lookup(job.data.flowId));
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
    try { result = await bounded(input.ports.analysis.start({ workflowId: job.workflowId, flowId: job.data.flowId, actorId: access.actorId, policyVersion: access.policy.version, dispatchToken: job.leaseToken!, requestId: job.id })); }
    catch { result = { status: 'uncertain' }; }
    await repo.patch(job, { status: result.status === 'completed' ? 'done' : result.status === 'failed' ? 'retryable' : result.status === 'running' ? 'waiting' : 'uncertain',
      nextAtMs: now() + 30000, reason: result.status === 'uncertain' ? 'ANALYSIS_OUTCOME_UNKNOWN' : result.status === 'failed' ? 'ANALYSIS_FAILED' : result.status === 'completed' && result.complete === false ? 'PARTIAL_ANALYSIS' : null,
      data: { ...job.data, analysisId: result.analysisId ?? null, complete: result.complete !== false } }, now(), true);
  }
  async function scan(job: AutomationJob, policy: WorkflowAutomationPolicy) {
    const due = automationDue(now(), policy.dailyTime);
    if (!job.data.openDue && (!policy.dailyConfigured || due.at < policy.updatedAtMs || job.data.completedDue === due.key)) return job;
    if (!job.data.openDue) job = await repo.patch(job, { data: { ...job.data, openDue: due.key, cutoffMs: now() - 2000, cursor: null } }, now());
    const sinceMs = Number(job.data.scannedThroughMs ?? job.data.scanFromMs ?? now() - DAY) - 60000;
    const cursor = job.data.cursor as { modified: number | string; id: number } | null;
    const rows = await input.db.query<{ id: number; flow_id: string; modified: number | string }>(`SELECT id, flow_id, COALESCE(gmt_modified, gmt_create) AS modified FROM flow_runs
      WHERE workflow_id = ? AND completed_at IS NOT NULL AND status IN ('succeeded', 'failed', 'cancelled')
      AND COALESCE(gmt_modified, gmt_create) >= ? AND COALESCE(gmt_modified, gmt_create) <= ?
      ${cursor ? 'AND (COALESCE(gmt_modified, gmt_create) > ? OR (COALESCE(gmt_modified, gmt_create) = ? AND id > ?))' : ''}
      ORDER BY COALESCE(gmt_modified, gmt_create), id LIMIT 20`, [job.workflowId, input.db.dialect.epochToDb(Math.floor(sinceMs / 1000)),
        input.db.dialect.epochToDb(Math.floor(job.data.cutoffMs / 1000)), ...(cursor ? [cursor.modified, cursor.modified, cursor.id] : [])]);
    for (const row of rows) await repo.ensure(job.workflowId, 'auto_run', row.flow_id, { flowId: row.flow_id, evidenceCutoffMs: job.data.cutoffMs }, now());
    const last = rows.at(-1);
    return repo.patch(job, { data: rows.length === 20 ? { ...job.data, cursor: { modified: last!.modified, id: last!.id } }
      : { ...job.data, completedDue: job.data.openDue, openDue: null, cursor: null, scannedThroughMs: job.data.cutoffMs } }, now());
  }
  async function queueIssues(workflowId: string) {
    const ranked = await issues.listPage(workflowId, { page: 1, pageSize: 20, sort: 'priority' });
    for (const group of ranked.groups) {
      if (!['high', 'critical'].includes(group.priority?.severity ?? '')) continue;
      const key = digestRepairJson([group.signature, group.priority?.severity, [...new Set(group.flowIds)].sort()]);
      await repo.ensure(workflowId, 'auto_notice', `issue:${key}`, { title: '工作流发现重点问题',
        text: `影响程度：${group.priority!.severity}；受影响运行 ${group.flowIds.length} 次。请审阅证据和处理方案。`,
        path: `/workflows/workspace?workflowId=${encodeURIComponent(workflowId)}&tab=evolution`, kind: 'issue' }, now());
    }
  }
  async function planDraft(cycle: AutomationJob) {
    const { actorId, policy } = await authority(cycle.workflowId, true);
    if (cycle.data.draftDue === cycle.data.completedDue || !cycle.data.completedDue) return cycle;
    const baseline = await input.ports.baseline(cycle.workflowId);
    const selected = await selectAutomaticScope(input.workbench, issues, cycle.workflowId, baseline);
    if (!selected) return cycle;
    const intent = await repo.ensure(cycle.workflowId, 'auto_draft', selected.fingerprint, { actorId, fingerprint: selected.fingerprint }, now());
    const existing = intent.data.request;
    if (!existing) {
      const claimed = await repo.claim(intent.id, now());
      if (claimed) {
        const request: RepairSelectionRequest = { workflowId: cycle.workflowId, itemIds: selected.items.map(item => item.itemId), inputDigest: selected.inputDigest,
          instructions: '', requestId: intent.id, automation: { kind: 'daily', intentId: intent.id, policyVersion: policy.version, evidenceCutoffMs: now() } };
        await repo.patch(claimed, { data: { ...claimed.data, request } }, now(), true);
      }
    }
    return repo.patch(cycle, { data: { ...cycle.data, draftDue: cycle.data.completedDue } }, now());
  }
  async function runDraft(record: AutomationJob) {
    let job = await repo.claim(record.id, now()); if (!job || !job.data.request) return;
    const request = job.data.request as RepairSelectionRequest;
    const existing = await input.workbench.request(job.workflowId, request.requestId);
    let taskId = existing?.taskId ?? job.data.realTaskId;
    if (!taskId) {
      let access;
      try { access = await authority(job.workflowId, true); }
      catch (error) { if (!(error instanceof RepairAutomationDeniedError)) throw error;
        await repo.patch(job, { status: 'retryable', reason: error.message, nextAtMs: now() + 30000 }, now(), true); return; }
      if (access.actorId !== job.data.actorId || access.policy.version !== request.automation!.policyVersion) {
        await repo.patch(job, { status: 'blocked', reason: 'OWNER_OR_POLICY_CHANGED' }, now(), true); return;
      }
      if (job.attempts >= 2) { await repo.patch(job, { status: 'blocked', reason: 'DRAFT_REQUIRES_MANUAL_REVIEW' }, now(), true); return; }
      job = await repo.patch(job, { status: 'dispatching', attempts: job.attempts + 1 }, now());
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
  async function notify(record: AutomationJob) {
    let job = await repo.claim(record.id, now()); if (!job) return;
    if (job.status === 'dispatching' || job.status === 'uncertain') {
      await repo.patch(job, { status: 'uncertain', reason: 'NOTIFICATION_OUTCOME_UNKNOWN', nextAtMs: now() + 300000 }, now(), true); return;
    }
    let access;
    try { access = await authority(job.workflowId); }
    catch (error) { if (!(error instanceof RepairAutomationDeniedError)) throw error;
      await repo.patch(job, { status: 'retryable', reason: error.message, nextAtMs: now() + 30000 }, now(), true); return; }
    const { actorId } = access;
    if (job.data.kind === 'draft') {
      const task = await input.workbench.task(job.data.taskId);
      if (task.latestAttempt.revision !== job.data.revision || task.latestAttempt.phase !== 'review') {
        await repo.patch(job, { status: 'done', reason: 'CANDIDATE_SUPERSEDED' }, now(), true); return;
      }
    }
    if (job.attempts >= 3) { await repo.patch(job, { status: 'blocked', reason: 'NOTIFICATION_REJECTED' }, now(), true); return; }
    job = await repo.patch(job, { status: 'dispatching', attempts: job.attempts + 1, data: { ...job.data, recipientId: actorId } }, now());
    let result: Awaited<ReturnType<WorkflowAutomationPorts['notify']>>;
    try { result = await bounded(input.ports.notify({ key: job.id, workflowId: job.workflowId, recipientId: actorId, title: job.data.title, text: job.data.text, path: job.data.path })); }
    catch { result = { status: 'uncertain' }; }
    await repo.patch(job, { status: result.status === 'sent' ? 'done' : result.status, reason: result.status === 'sent' ? null : result.status === 'uncertain' ? 'NOTIFICATION_OUTCOME_UNKNOWN' : 'NOTIFICATION_REJECTED',
      nextAtMs: now() + 60000, data: { ...job.data, receipt: result.receipt ?? null } }, now(), true);
  }
  async function workflow(policy: WorkflowAutomationPolicy) {
    const initial = await repo.ensure(policy.workflowId, 'auto_cycle', 'state', { scanFromMs: now() - DAY, scannedThroughMs: null, completedDue: null }, now());
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
      for (const job of (await repo.ready(policy.workflowId, 'auto_draft', now(), 3))) await runDraft(job);
      const current = await input.settings.get(policy.workflowId);
      if (current.enabled && current.dailyConfigured && cycle.data.completedDue) {
        const analysisVersion = await input.db.query(`SELECT COUNT(*) AS count, MAX(id) AS latest, MAX(completed_at_ms) AS completed, SUM(state_version) AS revision
          FROM workflow_evolution_analysis_runs WHERE workflow_id = ?`, [policy.workflowId]);
        const active = await input.db.query('SELECT deploy_number FROM workflow_deploy_history WHERE workflow_id = ? AND is_active = 1', [policy.workflowId]);
        const evaluated = digestRepairJson([analysisVersion, active, current.version, cycle.data.completedDue]);
        if (cycle.data.evaluated !== evaluated) {
          await queueIssues(policy.workflowId);
          if (current.autoDraft) cycle = await planDraft(cycle);
          cycle = await repo.patch(cycle, { data: { ...cycle.data, evaluated } }, now());
        }
        for (const job of await repo.ready(policy.workflowId, 'auto_notice', now(), 3)) await notify(job);
      }
      await repo.patch(cycle, { nextAtMs: now() + 10000 }, now(), true);
    } catch (error) {
      if (!(error instanceof RepairAutomationDeniedError)) throw error;
      await repo.patch(cycle, { reason: error.message, nextAtMs: now() + 30000 }, now(), true);
    }
  }
  return {
    async tick() {
      const policies = await input.settings.list(); if (!policies.length) return 0;
      const count = Math.min(10, policies.length);
      for (let i = 0; i < count; i++) await workflow(policies[(roundRobin + i) % policies.length]);
      roundRobin = (roundRobin + count) % policies.length;
      return count;
    },
    async onRunTerminal(event) {
      if (event.status !== 'failed') return;
      const row = (await input.db.query<{ workflow_id: string }>('SELECT workflow_id FROM flow_runs WHERE flow_id = ?', [event.flowId]))[0];
      if (!row) return;
      try { await authority(row.workflow_id); } catch (error) { if (error instanceof RepairAutomationDeniedError) return; throw error; }
      const job = await repo.ensure(row.workflow_id, 'auto_run', event.flowId, { flowId: event.flowId, evidenceCutoffMs: now() }, now());
      await runAnalysis(job);
    },
    async status(workflowId) {
      const jobs = await repo.recent(workflowId);
      return { schemaVersion: 'workflow-automation/v1', workflowId, coverage: await repo.coverage(workflowId), pauseReason: jobs.find(job => job.kind === 'auto_cycle')?.reason ?? null, lastScannedAtMs: jobs.find(job => job.kind === 'auto_cycle')?.data.scannedThroughMs ?? null,
        jobs: jobs.map(job => ({ id: job.id, kind: job.kind, status: job.status, attempts: job.attempts, reason: job.reason,
          ...(job.data.realTaskId ? { taskId: job.data.realTaskId } : {}) })) };
    },
    async retryFailures(workflowId) { await repo.retryFailures(workflowId, now()); },
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
