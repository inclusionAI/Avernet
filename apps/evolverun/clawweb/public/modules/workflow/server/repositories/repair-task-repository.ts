import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { RepairBatchError, canonicalRepairJson, digestRepairJson, type RepairRevision } from '../contracts/repair-batch.js';
import type { RepairExecutionIdentity } from '../contracts/repair-workbench.js';
import type { RepairTaskDetail } from '../contracts/repair-workbench.js';

function submissionUser(responseJson: string | null, legacyUserId: string): string {
  try {
    const submittedBy: unknown = JSON.parse(responseJson ?? '{}')?.submittedBy;
    if (typeof submittedBy === 'string' && submittedBy.trim()) return submittedBy;
  } catch { /* Historical dispatches retain their task-level identity. */ }
  return legacyUserId;
}

type ExecutionRow = { status: string; error_code: string | null; bot_run_id: string | null; output_json: string | null };
function executionFromRow(row: ExecutionRow): NonNullable<RepairTaskDetail['execution']> {
  let output: { attempt?: unknown; executionId?: unknown; rawStatus?: unknown } = {};
  try {
    const parsed = row.output_json ? JSON.parse(row.output_json) : null;
    if (parsed && typeof parsed === 'object') output = parsed;
  } catch { /* Corrupt metadata is shown as absent, never trusted. */ }
  return { status: row.status as NonNullable<RepairTaskDetail['execution']>['status'], errorCode: row.error_code,
    jobId: row.bot_run_id, attempt: Number.isSafeInteger(output.attempt) ? Number(output.attempt) : 0,
    executionId: typeof output.executionId === 'string' ? output.executionId : null,
    rawStatus: typeof output.rawStatus === 'string' ? output.rawStatus : null };
}

/** Workflow task projection in the existing task tables; never call the legacy suggestion finalizer. */
export class RepairTaskRepository {
  constructor(private readonly db: IDatabase) {}
  stepId(taskId: string, revision: number): string { return digestRepairJson(['workflow_repair_draft', taskId, revision]); }
  async execution(taskId: string, revision: number): Promise<RepairTaskDetail['execution']> {
    const row = (await this.db.query<ExecutionRow>(
      "SELECT status, error_code, bot_run_id, output_json FROM ce_steps WHERE task_id = ? AND step_id = ? AND step_type = 'workflow_repair_draft'", [taskId, this.stepId(taskId, revision)]))[0];
    return row ? executionFromRow(row) : undefined;
  }
  async submissionUser(taskId: string, revision: number): Promise<string> {
    const row = (await this.db.query<{ user_id: string; bot_response_json: string | null }>(
      `SELECT t.user_id, s.bot_response_json FROM ce_steps s JOIN ce_tasks t ON t.task_id = s.task_id
       WHERE s.task_id = ? AND s.step_id = ? AND s.step_type = 'workflow_repair_draft'`,
      [taskId, this.stepId(taskId, revision)]))[0];
    if (!row) throw new RepairBatchError('TASK_NOT_FOUND', 'AIS submission identity is missing');
    return submissionUser(row.bot_response_json, row.user_id);
  }
  async executions(taskId: string, revisions: number[]): Promise<Map<number, NonNullable<RepairTaskDetail['execution']>>> {
    const byStep = new Map(revisions.map(revision => [this.stepId(taskId, revision), revision]));
    const rows = await this.db.query<ExecutionRow & { step_id: string }>(
      "SELECT step_id, status, error_code, bot_run_id, output_json FROM ce_steps WHERE task_id = ? AND step_type = 'workflow_repair_draft'", [taskId]);
    return new Map(rows.flatMap(row => {
      const revision = byStep.get(row.step_id);
      return revision === undefined ? [] : [[revision, executionFromRow(row)] as const];
    }));
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
    const output = canonicalRepairJson({ attempt, executionId, rawStatus: null, requestedAtMs: Date.now() });
    const result = await this.db.exec(`UPDATE ce_steps SET status = 'dispatching', gmt_modified = ? WHERE task_id = ? AND step_id = ?
      AND step_type = 'workflow_repair_draft' AND status IN ('created', 'dispatch_failed')`,
    [this.db.dialect.now(), taskId, stepId]);
    if (result.affectedRows !== 1) return null;
    await this.db.exec("UPDATE ce_steps SET output_json = ? WHERE task_id = ? AND step_id = ? AND status = 'dispatching'", [output, taskId, stepId]);
    return { stepId, attempt, executionId, actorId: row.user_id };
  }
  async finishDispatch(taskId: string, revision: number, jobId: string | null, failure: 'rejected' | 'uncertain' | null, submittedBy: string): Promise<void> {
    await this.db.exec(`UPDATE ce_steps SET status = ?, bot_run_id = ?, bot_response_json = ?, error_code = ?, gmt_modified = ? WHERE task_id = ? AND step_id = ? AND status = 'dispatching'`,
      [failure === 'uncertain' ? 'dispatch_uncertain' : failure === 'rejected' ? 'dispatch_failed' : 'dispatched', jobId, canonicalRepairJson({ jobId, submittedBy }),
        failure === 'uncertain' ? 'DISPATCH_UNCERTAIN' : failure === 'rejected' ? 'DISPATCH_REJECTED' : null,
        this.db.dialect.now(), taskId, this.stepId(taskId, revision)]);
  }
  async expireStaleDispatches(cutoffMs: number): Promise<number> {
    const cutoff = this.db.dialect.epochToDb(Math.floor(cutoffMs / 1000));
    const result = await this.db.exec(`UPDATE ce_steps SET status = 'dispatch_uncertain', error_code = 'DISPATCH_UNCERTAIN', gmt_modified = ?
      WHERE step_type = 'workflow_repair_draft' AND status = 'dispatching' AND gmt_modified < ?`,
    [this.db.dialect.now(), cutoff]);
    return result.affectedRows;
  }
  async active(limit: number): Promise<Array<{ taskId: string; revision: number; jobId: string; identity: RepairExecutionIdentity; actorId: string; requestedAtMs: number; successObservedAtMs: number | null }>> {
    const rows = await this.db.query<{ task_id: string; round_no: number; bot_run_id: string; output_json: string; bot_response_json: string | null; user_id: string }>(`SELECT s.task_id, s.round_no, s.bot_run_id, s.output_json, s.bot_response_json, t.user_id
      FROM ce_steps s JOIN ce_tasks t ON t.task_id = s.task_id
      WHERE s.step_type = 'workflow_repair_draft' AND s.status IN ('dispatched', 'running') AND s.bot_run_id IS NOT NULL
      ORDER BY s.gmt_modified ASC LIMIT ?`, [limit]);
    return rows.flatMap(row => {
      try {
        const value = JSON.parse(row.output_json) as { attempt?: unknown; executionId?: unknown; requestedAtMs?: unknown; successObservedAtMs?: unknown };
        if (!Number.isSafeInteger(value.attempt) || typeof value.executionId !== 'string') return [];
        return [{ taskId: row.task_id, revision: Number(row.round_no), jobId: row.bot_run_id,
          identity: { stepId: this.stepId(row.task_id, Number(row.round_no)), attempt: Number(value.attempt), executionId: value.executionId }, actorId: submissionUser(row.bot_response_json, row.user_id),
          requestedAtMs: Number.isSafeInteger(value.requestedAtMs) ? Number(value.requestedAtMs) : Date.now(),
          successObservedAtMs: Number.isSafeInteger(value.successObservedAtMs) ? Number(value.successObservedAtMs) : null }];
      } catch { return []; }
    });
  }
  async updateRemoteStatus(taskId: string, revision: number, identity: RepairExecutionIdentity, rawStatus: string, succeeded = false): Promise<boolean> {
    const row = (await this.db.query<{ output_json: string | null }>("SELECT output_json FROM ce_steps WHERE task_id = ? AND step_id = ?", [taskId, this.stepId(taskId, revision)]))[0];
    if (!row) return false;
    let output: Record<string, unknown>;
    try { output = JSON.parse(row.output_json ?? '{}'); } catch { return false; }
    if (output.executionId !== identity.executionId || output.attempt !== identity.attempt) return false;
    if (Number.isSafeInteger(output.timeoutObservedAtMs)) return false;
    output.rawStatus = rawStatus;
    if (succeeded && !Number.isSafeInteger(output.successObservedAtMs)) output.successObservedAtMs = Date.now();
    const result = await this.db.exec(`UPDATE ce_steps SET status = CASE WHEN status = 'dispatched' THEN 'running' ELSE status END,
      output_json = ?, gmt_modified = ? WHERE task_id = ? AND step_id = ? AND status IN ('dispatched', 'running') AND output_json = ?`,
    [canonicalRepairJson(output), this.db.dialect.now(), taskId, identity.stepId, row.output_json]);
    return result.affectedRows === 1;
  }
  /** Fence a timeout against a concurrently persisted success before stopping AIS. */
  async claimTimeout(taskId: string, identity: RepairExecutionIdentity): Promise<boolean> {
    const row = (await this.db.query<{ output_json: string }>(`SELECT output_json FROM ce_steps
      WHERE task_id = ? AND step_id = ? AND status IN ('dispatched', 'running')`, [taskId, identity.stepId]))[0];
    if (!row) return false;
    let output: Record<string, unknown>;
    try { output = JSON.parse(row.output_json); } catch { return false; }
    if (output.executionId !== identity.executionId || output.attempt !== identity.attempt
      || Number.isSafeInteger(output.successObservedAtMs) || !Number.isSafeInteger(output.requestedAtMs)
      || Date.now() - Number(output.requestedAtMs) <= 2_400_000) return false;
    // A crashed timeout claimant can be resumed; it never opens a result read.
    if (Number.isSafeInteger(output.timeoutObservedAtMs) && Date.now() - Number(output.timeoutObservedAtMs) < 120_000) return false;
    const result = await this.db.exec(`UPDATE ce_steps SET output_json = ?, gmt_modified = ? WHERE task_id = ? AND step_id = ?
      AND status IN ('dispatched', 'running') AND output_json = ?`,
    [canonicalRepairJson({ ...output, timeoutObservedAtMs: Date.now() }), this.db.dialect.now(), taskId, identity.stepId, row.output_json]);
    return result.affectedRows === 1;
  }
  /** Claim once per execution. Expiry permits explicit failure/recovery, never another automatic Git read. */
  async claimResult(taskId: string, identity: RepairExecutionIdentity): Promise<'claimed' | 'busy' | 'interrupted'> {
    const row = (await this.db.query<{ output_json: string }>(`SELECT output_json FROM ce_steps
      WHERE task_id = ? AND step_id = ? AND status IN ('dispatched', 'running')`, [taskId, identity.stepId]))[0];
    if (!row) return 'busy';
    let output: Record<string, unknown>;
    try { output = JSON.parse(row.output_json); } catch { return 'busy'; }
    if (output.executionId !== identity.executionId || output.attempt !== identity.attempt || !Number.isSafeInteger(output.successObservedAtMs)
      || Number.isSafeInteger(output.timeoutObservedAtMs)) return 'busy';
    if (Number.isSafeInteger(output.resultReadAttemptedAtMs)) return Number(output.resultReadUntilMs) > Date.now() ? 'busy' : 'interrupted';
    const next = { ...output, resultReadAttemptedAtMs: Date.now(), resultReadUntilMs: Date.now() + 900_000 };
    const claimed = await this.db.exec(`UPDATE ce_steps SET output_json = ?, gmt_modified = ? WHERE task_id = ? AND step_id = ?
      AND status IN ('dispatched', 'running') AND output_json = ?`,
    [canonicalRepairJson(next), this.db.dialect.now(), taskId, identity.stepId, row.output_json]);
    return claimed.affectedRows === 1 ? 'claimed' : 'busy';
  }
  async settle(revision: RepairRevision): Promise<void> {
    const config = await this.config(revision.taskId);
    if (Number(config.latestAttemptRevision) !== revision.revision) throw new RepairBatchError('STATE_CONFLICT', 'Task attempt changed');
    const succeeded = revision.draft !== null;
    const errorCode = !succeeded && typeof revision.error?.code === 'string' && revision.error.code.length <= 128 ? revision.error.code : null;
    await this.db.exec(`UPDATE ce_steps SET status = ?, summary = ?, error_code = ?, completed_at = ?, gmt_modified = ?
      WHERE task_id = ? AND step_id = ? AND step_type = 'workflow_repair_draft'`,
    [succeeded ? 'succeeded' : 'failed', revision.phase === 'no_change' ? 'No Pack changes needed' : succeeded ? 'Awaiting review' : 'Draft failed', errorCode,
      this.db.dialect.now(), this.db.dialect.now(), revision.taskId, this.stepId(revision.taskId, revision.revision)]);
    await this.db.exec("UPDATE ce_tasks SET status = ?, config_json = ?, gmt_modified = ? WHERE task_id = ? AND task_type = 'workflow_repair'",
      [revision.phase === 'no_change' ? 'completed' : 'running',
        canonicalRepairJson({ ...config, ...(succeeded && revision.phase !== 'no_change' ? { latestSuccessfulRevision: revision.revision } : {}) }),
        this.db.dialect.now(), revision.taskId]);
  }
  async cancel(taskId: string): Promise<void> {
    await this.db.exec("UPDATE ce_tasks SET status = 'failed', error_message = 'Repair cancelled by user', gmt_modified = ? WHERE task_id = ? AND task_type = 'workflow_repair'", [this.db.dialect.now(), taskId]);
    await this.db.exec("UPDATE ce_steps SET status = 'cancelled', completed_at = ?, gmt_modified = ? WHERE task_id = ? AND step_type = 'workflow_repair_draft' AND status IN ('created', 'dispatching', 'dispatch_uncertain', 'dispatch_failed', 'dispatched', 'running')", [this.db.dialect.now(), this.db.dialect.now(), taskId]);
  }
}
