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
const taskScope = (workflowId: string) => digestRepairJson(['automation-scope', workflowId]);
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
      return job;
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
  private json(field: string) {
    const sql = `JSON_EXTRACT(body_json, '$.${field}')`;
    return this.db.dialect.driver === 'better-sqlite3' ? sql : `JSON_UNQUOTE(${sql})`;
  }
  async ready(workflowId: string, kind: AutomationKind, now: number, limit = 20): Promise<AutomationJob[]> {
    return (await this.db.query<{ body_json: string }>(`SELECT body_json FROM workflow_repair_release_records
      WHERE task_id = ? AND record_kind = ? AND ${this.json('status')} NOT IN ('done', 'blocked')
      AND CAST(${this.json('nextAtMs')} AS SIGNED) <= ? ORDER BY updated_at_ms, record_id LIMIT ?`, [taskScope(workflowId), kind, now, limit]))
      .map(row => JSON.parse(row.body_json));
  }
  async readyAcross(kind: AutomationKind, now: number, limit: number, scopeKey: string): Promise<AutomationJob[]> {
    return (await this.db.query<{ body_json: string }>(`SELECT body_json FROM workflow_repair_release_records
      WHERE record_kind = ? AND ${this.json('data.scopeKey')} = ? AND ${this.json('status')} NOT IN ('done','blocked')
      AND CAST(${this.json('nextAtMs')} AS SIGNED) <= ? AND CAST(${this.json('leaseUntilMs')} AS SIGNED) <= ?
      ORDER BY updated_at_ms, record_id LIMIT ?`, [kind,scopeKey,now,now,limit])).map(row => JSON.parse(row.body_json));
  }
  async failedDrafts(workflowId: string): Promise<AutomationJob[]> {
    return (await this.db.query<{ body_json: string }>(`SELECT body_json FROM workflow_repair_release_records WHERE task_id = ? AND record_kind = 'auto_draft'
      AND ${this.json('status')} = 'blocked' AND ${this.json('reason')} = 'DRAFT_REQUIRES_MANUAL_REVIEW' ORDER BY updated_at_ms`, [taskScope(workflowId)])).map(row => JSON.parse(row.body_json));
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
  async retryFailures(workflowId: string, now: number) {
    await this.db.transaction(async tx => {
      await new RepairBatchRepository(tx).lockWorkflow(tx, workflowId);
      const repo = new WorkflowAutomationRepository(tx);
      const rows = await tx.query<{ body_json: string }>(`SELECT body_json FROM workflow_repair_release_records
        WHERE task_id = ? AND record_kind IN ('auto_run', 'auto_notice') AND ${repo.json('status')} = 'blocked'
        AND ${repo.json('reason')} IN ('ANALYSIS_FAILED', 'NOTIFICATION_REJECTED', 'OWNER_MISSING', 'OWNER_PERMISSION_DENIED')
        ORDER BY updated_at_ms, record_id LIMIT 50`, [taskScope(workflowId)]);
      for (const row of rows) {
        const job: AutomationJob = JSON.parse(row.body_json);
        if (job.status !== 'blocked' || !['ANALYSIS_FAILED', 'NOTIFICATION_REJECTED', 'OWNER_MISSING', 'OWNER_PERMISSION_DENIED'].includes(job.reason ?? '')) continue;
        if (job.leaseUntilMs > now) continue;
        const next: AutomationJob = { ...job, status: 'retryable', attempts: 0, nextAtMs: now, stateVersion: job.stateVersion + 1 };
        await new RepairReleaseRepository(tx).update(next, job.stateVersion);
      }
    });
  }
}
