import { randomUUID } from 'node:crypto';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { digestRepairJson } from '../contracts/repair-batch.js';
import { RepairBatchRepository } from './repair-batch-repository.js';
import { RepairReleaseRepository } from './repair-release-repository.js';
export type AutomationKind = 'auto_cycle' | 'auto_run' | 'auto_draft' | 'auto_notice' | 'proposal_scan' | 'proposal_group' | 'proposal_budget';
export type AutomationJob = {
  id: string; workflowId: string; taskId: string; revision: number; stateVersion: number; createdAtMs: number;
  kind: AutomationKind; status: 'pending' | 'waiting' | 'dispatching' | 'retryable' | 'uncertain' | 'done' | 'blocked';
  attempts: number; nextAtMs: number; leaseToken: string | null; leaseUntilMs: number; reason: string | null; data: Record<string, any>;
};
export const taskScope = (workflowId: string) => digestRepairJson(['automation-scope', workflowId]);
export class WorkflowAutomationRepository {
  constructor(readonly db: IDatabase) {}
  async get(id: string): Promise<AutomationJob | null> {
    const row = (await this.db.query<{ body_json: string }>('SELECT body_json FROM workflow_repair_release_records WHERE record_id = ?', [id]))[0];
    return row ? JSON.parse(row.body_json) : null;
  }
  async ensure(workflowId: string, kind: AutomationKind, key: string, data: object, now: number): Promise<AutomationJob> {
    const id = digestRepairJson([workflowId, kind, key]);
    return this.db.transaction(async tx => {
      await new RepairBatchRepository(tx).lockWorkflow(tx, workflowId);
      const current = await new WorkflowAutomationRepository(tx).get(id);
      if (current) return current;
      const job: AutomationJob = { id, workflowId, taskId: taskScope(workflowId), revision: 0, stateVersion: 0, createdAtMs: now,
        kind, status: 'pending', attempts: 0, nextAtMs: now, leaseToken: null, leaseUntilMs: 0, reason: null, data: { ...data } };
      await new RepairReleaseRepository(tx).insert(kind, { requestId: id }, job);
      // A surrounding result transaction may still roll back; this is not a delivery receipt.
      if (kind === 'auto_notice') console.info('[workflow-notification] ' + JSON.stringify({ event: 'enqueue_prepared', transactionPending: true,
        workflowId, noticeId: id, taskId: job.data.taskId ?? null, revision: job.data.revision ?? null,
        kind: job.data.kind ?? null, recipientId: job.data.recipientId ?? null }));
      return job;
    }).catch(error => {
      if (kind === 'auto_notice') console.warn('[workflow-notification] ' + JSON.stringify({ event: 'enqueue_failed', workflowId,
        noticeId: id, reason: 'NOTIFICATION_QUEUE_WRITE_FAILED' }));
      throw error;
    });
  }
  async claim(id: string, now: number): Promise<AutomationJob | null> {
    return this.db.transaction(async tx => {
      const repo = new WorkflowAutomationRepository(tx);
      const first = await repo.get(id); if (!first) return null;
      await new RepairBatchRepository(tx).lockWorkflow(tx, first.workflowId);
      const current = (await repo.get(id))!;
      if (current.leaseUntilMs > now || current.nextAtMs > now || current.status === 'done' || current.status === 'blocked') return null;
      const claimed = { ...current, stateVersion: current.stateVersion + 1, leaseToken: randomUUID(), leaseUntilMs: now + 300000 };
      await new RepairReleaseRepository(tx).update(claimed, current.stateVersion);
      return claimed;
    });
  }
  async patch(job: AutomationJob, value: Partial<Pick<AutomationJob, 'status' | 'attempts' | 'nextAtMs' | 'reason' | 'data'>>, now: number, release = false): Promise<AutomationJob> {
    return this.db.transaction(async tx => {
      await new RepairBatchRepository(tx).lockWorkflow(tx, job.workflowId);
      const current = await new WorkflowAutomationRepository(tx).get(job.id);
      if (!current || current.leaseToken !== job.leaseToken || current.leaseUntilMs <= now) throw new Error('AUTOMATION_LEASE_LOST');
      const next = { ...current, ...value, stateVersion: current.stateVersion + 1,
        ...(release ? { leaseToken: null, leaseUntilMs: 0 } : {}) };
      await new RepairReleaseRepository(tx).update(next, current.stateVersion);
      return next;
    });
  }
  private json(field: string, alias?: string) {
    const sql = `JSON_EXTRACT(${alias ? `${alias}.` : ''}body_json, '$.${field}')`;
    return this.db.dialect.driver === 'better-sqlite3' ? sql : `JSON_UNQUOTE(${sql})`;
  }
  async ready(workflowId: string, kind: AutomationKind, now: number, limit = 20, dispatchedOnly = false): Promise<AutomationJob[]> {
    return (await this.db.query<{ body_json: string }>(`SELECT body_json FROM workflow_repair_release_records
      WHERE task_id = ? AND record_kind = ? AND ${this.json('status')} NOT IN ('done', 'blocked')
      AND CAST(${this.json('nextAtMs')} AS SIGNED) <= ?
      ${dispatchedOnly ? `AND (CAST(${this.json('attempts')} AS SIGNED) > 0
        OR ${this.json('status')} IN ('dispatching', 'waiting', 'uncertain')
        OR COALESCE(${this.json('data.analysisId')}, '') NOT IN ('', 'null'))` : ''}
      ORDER BY updated_at_ms, record_id LIMIT ?`, [taskScope(workflowId), kind, now, limit]))
      .map(row => JSON.parse(row.body_json));
  }
  async readyAcross(kind: AutomationKind, now: number, limit: number, scopeKey: string): Promise<AutomationJob[]> {
    return (await this.db.query<{ body_json: string }>(`SELECT body_json FROM workflow_repair_release_records
      WHERE record_kind = ? AND ${this.json('data.scopeKey')} = ? AND ${this.json('status')} NOT IN ('done','blocked')
      AND CAST(${this.json('nextAtMs')} AS SIGNED) <= ? AND CAST(${this.json('leaseUntilMs')} AS SIGNED) <= ?
      ORDER BY updated_at_ms, record_id LIMIT ?`, [kind,scopeKey,now,now,limit])).map(row => JSON.parse(row.body_json));
  }
  async readyNotifications(now: number, limit = 20, scope?: { workflowIds: string[]; exclude: boolean }): Promise<AutomationJob[]> {
    if (scope && !scope.exclude && !scope.workflowIds.length) return [];
    const filter = scope?.workflowIds.length ? ` AND workflow_id ${scope.exclude ? 'NOT IN' : 'IN'} (${scope.workflowIds.map(() => '?').join(',')})` : '';
    return (await this.db.query<{ body_json: string }>(`SELECT body_json FROM workflow_repair_release_records
      WHERE record_kind = 'auto_notice' AND ${this.json('status')} IN ('pending','retryable','dispatching')
      AND CAST(${this.json('nextAtMs')} AS SIGNED) <= ? AND CAST(${this.json('leaseUntilMs')} AS SIGNED) <= ?
      ${filter} ORDER BY updated_at_ms, record_id LIMIT ?`, [now, now, ...(scope?.workflowIds ?? []), limit])).map(row => JSON.parse(row.body_json));
  }
  /** Filter current eligibility before the batch limit so historical tasks cannot starve recoverable drafts. */
  async recoverableDrafts(workflowId: string, now: number, policyVersion: number, jobId?: string): Promise<Array<{ job: AutomationJob; revision: number }>> {
    const field = (name: string) => this.json(name, 'a');
    const requestMatches = this.db.dialect.driver === 'better-sqlite3'
      ? `r.request_id = ${field('data.request.requestId')}` : `BINARY r.request_id = BINARY ${field('data.request.requestId')}`;
    // Start MySQL with scoped jobs; a reverse nested-loop join can rescan job JSON per failed revision.
    const rows = await this.db.query<{ body_json: string; revision: number }>(`SELECT a.body_json, r.revision
      FROM workflow_repair_release_records a ${this.db.dialect.driver === 'better-sqlite3' ? 'JOIN' : 'STRAIGHT_JOIN'} workflow_repair_revisions r ON r.task_id = ${field('data.realTaskId')}
      AND r.workflow_id = a.workflow_id AND r.phase = 'failed' AND ${requestMatches}
      WHERE a.task_id = ? AND a.record_kind = 'auto_draft' AND ${field('status')} = 'blocked'
      AND ${field('reason')} = 'DRAFT_REQUIRES_MANUAL_REVIEW' AND ${field('data.batches')} IS NOT NULL
      AND CAST(${field('leaseUntilMs')} AS SIGNED) <= ? AND CAST(${field('data.request.automation.policyVersion')} AS SIGNED) = ?
      ${jobId ? 'AND a.record_id = ?' : ''}
      AND NOT EXISTS (SELECT 1 FROM workflow_repair_revisions newer WHERE newer.task_id = r.task_id AND newer.revision > r.revision)
      ORDER BY a.created_at_ms, a.record_id LIMIT 50`, [taskScope(workflowId), now, policyVersion, ...(jobId ? [jobId] : [])]);
    return rows.map(row => ({ job: JSON.parse(row.body_json) as AutomationJob, revision: Number(row.revision) }))
      .filter(({ job }) => job.data.batches && job.data.request?.automation?.policyVersion === policyVersion);
  }
  async recoverDraft(job: AutomationJob, data: AutomationJob['data'], now: number) {
    await this.db.transaction(async tx => {
      await new RepairBatchRepository(tx).lockWorkflow(tx, job.workflowId);
      const current = await new WorkflowAutomationRepository(tx).get(job.id);
      if (!current || current.stateVersion !== job.stateVersion || current.leaseUntilMs > now || current.status !== 'blocked') return;
      const next: AutomationJob = { ...current, status: 'pending', attempts: 0, reason: null, nextAtMs: now, data, stateVersion: current.stateVersion + 1 };
      await new RepairReleaseRepository(tx).update(next, current.stateVersion);
    });
  }
  /** Generation completion does not settle the user's review. Inspect task state, not job status. */
  async pendingAutomaticTask(workflowId: string): Promise<string | null> {
    const rows = await this.db.query<{ task_id: string }>(`SELECT r.task_id FROM workflow_repair_revisions r
      WHERE r.workflow_id = ? AND r.phase IN ('drafting','review','blocked','failed','publishing','no_change')
      AND EXISTS (SELECT 1 FROM workflow_repair_revisions origin WHERE origin.task_id = r.task_id AND origin.revision = 1 AND JSON_EXTRACT(origin.input_json, '$.automation') IS NOT NULL)
      AND NOT EXISTS (SELECT 1 FROM workflow_repair_revisions newer WHERE newer.task_id = r.task_id AND newer.revision > r.revision)
      AND (r.phase <> 'no_change' OR EXISTS (SELECT 1 FROM workflow_repair_items item WHERE item.workflow_id = r.workflow_id AND item.active_task_id = r.task_id AND item.state = 'processing'))
      ORDER BY r.created_at_ms, r.task_id LIMIT 1`, [workflowId]);
    return rows[0]?.task_id ?? null;
  }
  async activeDraft(workflowId: string,targetSnapshotKey?:string,activeDeployNumber?:number): Promise<AutomationJob | null> {
    let after='';
    for(;;){
      const rows=await this.db.query<{record_id:string;body_json:string}>(`SELECT record_id,body_json FROM workflow_repair_release_records
        WHERE task_id=? AND record_kind='auto_draft' AND ${this.json('status')} NOT IN ('done','blocked') AND record_id>?
        ORDER BY record_id LIMIT 100`,[taskScope(workflowId),after]);
      if(!rows.length)return null;
      for(const row of rows){
        const job=JSON.parse(row.body_json) as AutomationJob;
        if(targetSnapshotKey===undefined&&activeDeployNumber===undefined)return job;
        if(targetSnapshotKey&&job.data.targetSnapshotKey===targetSnapshotKey||activeDeployNumber!==undefined&&job.data.baseline?.activeDeployNumber===activeDeployNumber)return job;
        if(['dispatching','uncertain'].includes(job.status))return job;
        const task=job.data.realTaskId
          ?(await this.db.query<{phase:string;input_json:string}>('SELECT phase,input_json FROM workflow_repair_revisions WHERE task_id=? ORDER BY revision DESC LIMIT 1',[job.data.realTaskId]))[0]
          :job.data.request?.requestId?(await this.db.query<{phase:string;input_json:string}>('SELECT phase,input_json FROM workflow_repair_revisions WHERE workflow_id=? AND request_id=? ORDER BY revision DESC LIMIT 1',[workflowId,job.data.request.requestId]))[0]:null;
        if(task){if(['drafting','publishing'].includes(task.phase)||JSON.parse(task.input_json).baseline?.activeDeployNumber===activeDeployNumber)return job;}
        else if(job.data.baseline?.activeDeployNumber===undefined)return job;
      }
      after=rows.at(-1)!.record_id;
    }
  }
  async recent(workflowId: string): Promise<AutomationJob[]> {
    return (await this.db.query<{ body_json: string }>(`SELECT body_json FROM workflow_repair_release_records WHERE task_id = ?
      AND record_kind IN ('auto_cycle', 'auto_run', 'auto_draft', 'auto_notice')
      ORDER BY CASE WHEN ${this.json('status')} IN ('done', 'blocked') THEN 1 ELSE 0 END, updated_at_ms DESC, record_id LIMIT 50`, [taskScope(workflowId)]))
      .map(row => JSON.parse(row.body_json));
  }
  async coverage(workflowId: string) {
    const rows = await this.db.query<{ status: string; complete: unknown; total: number }>(`SELECT ${this.json('status')} AS status,
      ${this.json('data.complete')} AS complete, COUNT(*) AS total FROM workflow_repair_release_records
      WHERE task_id = ? AND record_kind = 'auto_run' GROUP BY ${this.json('status')}, ${this.json('data.complete')}`, [taskScope(workflowId)]);
    const result = { total: 0, pending: 0, failed: 0, partial: 0, unknown: 0 };
    for (const row of rows) {
      const count = Number(row.total); result.total += count;
      if (row.status === 'blocked') result.failed += count;
      else if (row.status === 'uncertain') result.unknown += count;
      else if (row.status !== 'done') result.pending += count;
      else if (!new Set<unknown>([true, 1, '1', 'true']).has(row.complete)) result.partial += count;
    }
    return result;
  }
  async retryableFailures(workflowId: string, now: number, jobId?: string): Promise<AutomationJob[]> {
    const rows = await this.db.query<{ body_json: string }>(`SELECT body_json FROM workflow_repair_release_records
      WHERE task_id = ? AND record_kind IN ('auto_run', 'auto_notice') AND ${this.json('status')} = 'blocked'
      AND ${this.json('reason')} IN ('ANALYSIS_FAILED', 'NOTIFICATION_REJECTED', 'OWNER_MISSING', 'OWNER_PERMISSION_DENIED')
      ${jobId ? 'AND record_id = ?' : ''}
      ORDER BY updated_at_ms, record_id LIMIT 50`, [taskScope(workflowId), ...(jobId ? [jobId] : [])]);
    return rows.map(row => JSON.parse(row.body_json) as AutomationJob).filter(job => job.leaseUntilMs <= now);
  }
  async retryFailures(workflowId: string, now: number, jobId?: string) {
    return this.db.transaction(async tx => {
      await new RepairBatchRepository(tx).lockWorkflow(tx, workflowId);
      const repo = new WorkflowAutomationRepository(tx);
      const jobs = await repo.retryableFailures(workflowId, now, jobId);
      for (const job of jobs) {
        const next: AutomationJob = { ...job, status: 'retryable', attempts: 0, nextAtMs: now, stateVersion: job.stateVersion + 1 };
        await new RepairReleaseRepository(tx).update(next, job.stateVersion);
      }
      return jobs.length;
    });
  }
}
