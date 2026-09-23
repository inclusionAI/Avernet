import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { RepairBatchError, canonicalRepairJson, digestRepairJson, type RepairRevision } from '../contracts/repair-batch.js';
import type { RepairExecutionIdentity } from '../contracts/repair-workbench.js';
import type { RepairTaskDetail } from '../contracts/repair-workbench.js';

/** Workflow task projection in the existing task tables; never call the legacy suggestion finalizer. */
export class RepairTaskRepository {
  constructor(private readonly db: IDatabase) {}
  stepId(taskId: string, revision: number): string { return digestRepairJson(['workflow_repair_draft', taskId, revision]); }
  async execution(taskId: string, revision: number): Promise<RepairTaskDetail['execution']> {
    const row = (await this.db.query<{ status: string; error_code: string | null; bot_run_id: string | null; output_json: string | null }>(
      "SELECT status, error_code, bot_run_id, output_json FROM ce_steps WHERE task_id = ? AND step_id = ? AND step_type = 'workflow_repair_draft'", [taskId, this.stepId(taskId, revision)]))[0];
    if (!row) return undefined;
    let output: { attempt?: unknown; executionId?: unknown; rawStatus?: unknown } = {};
    try { output = row.output_json ? JSON.parse(row.output_json) : {}; } catch { /* Corrupt metadata is shown as absent, never trusted. */ }
    return { status: row.status as NonNullable<RepairTaskDetail['execution']>['status'], errorCode: row.error_code,
      jobId: row.bot_run_id, attempt: Number.isSafeInteger(output.attempt) ? Number(output.attempt) : 0,
      executionId: typeof output.executionId === 'string' ? output.executionId : null,
      rawStatus: typeof output.rawStatus === 'string' ? output.rawStatus : null };
  }
  async ensureNoLegacyApply(workflowId: string): Promise<void> {
    const tasks = await this.db.query<{ remark: string | null; config_json: string }>(
      "SELECT remark, config_json FROM ce_tasks WHERE task_type = 'suggestion_apply' AND status NOT IN ('completed', 'failed', 'canceled', 'cancelled')");
    for (const task of tasks) {
      let config: Record<string, any>;
      try { config = JSON.parse(task.config_json); } catch { throw new RepairBatchError('STATE_CONFLICT', 'An active legacy application has unreadable ownership'); }
      if (task.remark === `group-repair:${workflowId}` || config.workflowId === workflowId || config.applicationInput?.workflowId === workflowId
        || config.repairSelection?.workflowId === workflowId) throw new RepairBatchError('ACTIVE_TASK_CONFLICT', 'A legacy application is still active');
    }
  }
  async append(revision: RepairRevision, actorId: string): Promise<void> {
    const config = { schemaVersion: 'workflow-repair/v2', workflowId: revision.workflowId,
      latestAttemptRevision: revision.revision, latestSuccessfulRevision: null, approvedRevision: null };
    const now = this.db.dialect.now();
    if (revision.revision === 1) {
      await this.db.exec(`INSERT INTO ce_tasks (task_id, task_name, remark, task_type, user_id, bot_id, status, config_json, created_by, gmt_create, gmt_modified)
        VALUES (?, 'Workflow repair', ?, 'workflow_repair', ?, '', 'running', ?, ?, ?, ?)`,
      [revision.taskId, `workflow-repair:${revision.workflowId}`, actorId, canonicalRepairJson(config), actorId, now, now]);
    } else {
      const existing = await this.config(revision.taskId);
      await this.db.exec("UPDATE ce_tasks SET status = 'running', config_json = ?, gmt_modified = ? WHERE task_id = ? AND task_type = 'workflow_repair'",
        [canonicalRepairJson({ ...existing, latestAttemptRevision: revision.revision, approvedRevision: null }), now, revision.taskId]);
    }
    await this.db.exec(`INSERT INTO ce_steps (step_id, task_id, step_type, step_no, round_no, command, status, gmt_create, gmt_modified)
      VALUES (?, ?, 'workflow_repair_draft', ?, ?, '', 'created', ?, ?)`,
    [this.stepId(revision.taskId, revision.revision), revision.taskId, revision.revision, revision.revision, now, now]);
  }
  private async config(taskId: string): Promise<Record<string, unknown>> {
    const row = (await this.db.query<{ config_json: string }>("SELECT config_json FROM ce_tasks WHERE task_id = ? AND task_type = 'workflow_repair'", [taskId]))[0];
    if (!row) throw new RepairBatchError('TASK_NOT_FOUND', 'Repair task projection is missing');
    return JSON.parse(row.config_json);
  }
  async claimDispatch(taskId: string, revision: number, executionId: string): Promise<(RepairExecutionIdentity & { actorId: string }) | null> {
    const stepId = this.stepId(taskId, revision);
    const row = (await this.db.query<{ output_json: string | null; user_id: string }>(`SELECT s.output_json, t.user_id FROM ce_steps s
      JOIN ce_tasks t ON t.task_id = s.task_id WHERE s.task_id = ? AND s.step_id = ? AND s.step_type = 'workflow_repair_draft'`, [taskId, stepId]))[0];
    if (!row) return null;
    let prior: { attempt?: unknown } = {};
    try { prior = row.output_json ? JSON.parse(row.output_json) : {}; } catch { /* Start a new explicit attempt. */ }
    const attempt = (Number.isSafeInteger(prior.attempt) ? Number(prior.attempt) : 0) + 1;
    const output = canonicalRepairJson({ attempt, executionId, rawStatus: null });
    const result = await this.db.exec(`UPDATE ce_steps SET status = 'dispatching', gmt_modified = ? WHERE task_id = ? AND step_id = ?
      AND step_type = 'workflow_repair_draft' AND status IN ('created', 'dispatch_failed')`,
    [this.db.dialect.now(), taskId, stepId]);
    if (result.affectedRows !== 1) return null;
    await this.db.exec("UPDATE ce_steps SET output_json = ? WHERE task_id = ? AND step_id = ? AND status = 'dispatching'", [output, taskId, stepId]);
    return { stepId, attempt, executionId, actorId: row.user_id };
  }
  async finishDispatch(taskId: string, revision: number, jobId: string | null, failed: boolean): Promise<void> {
    await this.db.exec(`UPDATE ce_steps SET status = ?, bot_run_id = ?, error_code = ?, gmt_modified = ? WHERE task_id = ? AND step_id = ? AND status = 'dispatching'`,
      [failed ? 'dispatch_failed' : 'dispatched', jobId, failed ? 'DISPATCH_UNCERTAIN' : null, this.db.dialect.now(), taskId, this.stepId(taskId, revision)]);
  }
  async active(limit: number): Promise<Array<{ taskId: string; revision: number; jobId: string; identity: RepairExecutionIdentity; actorId: string }>> {
    const rows = await this.db.query<{ task_id: string; round_no: number; bot_run_id: string; output_json: string; user_id: string }>(`SELECT s.task_id, s.round_no, s.bot_run_id, s.output_json, t.user_id
      FROM ce_steps s JOIN ce_tasks t ON t.task_id = s.task_id
      WHERE s.step_type = 'workflow_repair_draft' AND s.status IN ('dispatched', 'running') AND s.bot_run_id IS NOT NULL
      ORDER BY s.gmt_modified ASC LIMIT ?`, [limit]);
    return rows.flatMap(row => {
      try {
        const value = JSON.parse(row.output_json) as { attempt?: unknown; executionId?: unknown };
        if (!Number.isSafeInteger(value.attempt) || typeof value.executionId !== 'string') return [];
        return [{ taskId: row.task_id, revision: Number(row.round_no), jobId: row.bot_run_id,
          identity: { stepId: this.stepId(row.task_id, Number(row.round_no)), attempt: Number(value.attempt), executionId: value.executionId }, actorId: row.user_id }];
      } catch { return []; }
    });
  }
  async updateRemoteStatus(taskId: string, revision: number, identity: RepairExecutionIdentity, rawStatus: string): Promise<boolean> {
    const row = (await this.db.query<{ output_json: string | null }>("SELECT output_json FROM ce_steps WHERE task_id = ? AND step_id = ?", [taskId, this.stepId(taskId, revision)]))[0];
    if (!row) return false;
    let output: Record<string, unknown>;
    try { output = JSON.parse(row.output_json ?? '{}'); } catch { return false; }
    if (output.executionId !== identity.executionId || output.attempt !== identity.attempt) return false;
    output.rawStatus = rawStatus;
    const result = await this.db.exec(`UPDATE ce_steps SET status = CASE WHEN status = 'dispatched' THEN 'running' ELSE status END,
      output_json = ?, gmt_modified = ? WHERE task_id = ? AND step_id = ? AND status IN ('dispatched', 'running')`,
    [canonicalRepairJson(output), this.db.dialect.now(), taskId, identity.stepId]);
    return result.affectedRows === 1;
  }
  async settle(revision: RepairRevision): Promise<void> {
    const config = await this.config(revision.taskId);
    if (Number(config.latestAttemptRevision) !== revision.revision) throw new RepairBatchError('STATE_CONFLICT', 'Task attempt changed');
    const succeeded = revision.draft !== null;
    const errorCode = !succeeded && typeof revision.error?.code === 'string' && revision.error.code.length <= 128 ? revision.error.code : null;
    await this.db.exec(`UPDATE ce_steps SET status = ?, summary = ?, error_code = ?, completed_at = ?, gmt_modified = ?
      WHERE task_id = ? AND step_id = ? AND step_type = 'workflow_repair_draft'`,
    [succeeded ? 'succeeded' : 'failed', succeeded ? 'Awaiting review' : 'Draft failed', errorCode, this.db.dialect.now(), this.db.dialect.now(), revision.taskId, this.stepId(revision.taskId, revision.revision)]);
    await this.db.exec("UPDATE ce_tasks SET status = 'running', config_json = ?, gmt_modified = ? WHERE task_id = ? AND task_type = 'workflow_repair'",
      [canonicalRepairJson({ ...config, ...(succeeded ? { latestSuccessfulRevision: revision.revision } : {}) }), this.db.dialect.now(), revision.taskId]);
  }
  async cancel(taskId: string): Promise<void> {
    await this.db.exec("UPDATE ce_tasks SET status = 'failed', error_message = 'Repair cancelled by user', gmt_modified = ? WHERE task_id = ? AND task_type = 'workflow_repair'", [this.db.dialect.now(), taskId]);
    await this.db.exec("UPDATE ce_steps SET status = 'cancelled', completed_at = ?, gmt_modified = ? WHERE task_id = ? AND step_type = 'workflow_repair_draft' AND status IN ('created', 'dispatching', 'dispatch_failed', 'dispatched', 'running')", [this.db.dialect.now(), this.db.dialect.now(), taskId]);
  }
}
