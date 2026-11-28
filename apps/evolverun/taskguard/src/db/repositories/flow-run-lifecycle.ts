import type { IDatabase } from '../types.js';
import { nowForDb } from '../types.js';
import { expectedRetryEpoch } from './retry-epoch.js';
import { prepareRetryCheckpoint, validateRetryCheckpoint } from './retry-checkpoint.js';

export type RetryResumeOutcome = 'resumed' | 'not_found' | 'conflict';
const live = "status IN ('running', 'waiting', 'blocked', 'pending') AND completed_at IS NULL";
/** Status mirrors cannot settle a run or reopen any terminal attempt, including epoch zero. */
export async function mirrorRunStatus(db: IDatabase, flowId: string, status: string, retryEpoch?: number): Promise<boolean> {
  const epoch = expectedRetryEpoch(retryEpoch);
  if (!['running', 'waiting', 'blocked', 'pending'].includes(status)) return false;
  const result = await db.exec(
    `UPDATE flow_runs SET status = ?, gmt_modified = ? WHERE flow_id = ? AND retry_epoch = ? AND ${live}`,
    [status, nowForDb(db.dbType), flowId, epoch],
  );
  return result.affectedRows > 0;
}

export async function mirrorRunPhase(db: IDatabase, flowId: string, phase: string, retryEpoch?: number): Promise<boolean> {
  const epoch = expectedRetryEpoch(retryEpoch);
  const result = await db.exec(
    `UPDATE flow_runs SET current_phase = ?, gmt_modified = ? WHERE flow_id = ? AND retry_epoch = ? AND ${live}`,
    [phase, nowForDb(db.dbType), flowId, epoch],
  );
  return result.affectedRows > 0;
}

/** Retry admission is the only operation allowed to reopen a settled run. */
export async function resumeRunForRetry(db: IDatabase, flowId: string, nextEpoch: number, stateJson?: string): Promise<RetryResumeOutcome> {
  expectedRetryEpoch(nextEpoch);
  if (nextEpoch < 1) throw new TypeError('retry epoch must advance to a positive safe integer');
  if (stateJson !== undefined) validateRetryCheckpoint(stateJson, nextEpoch);
  return db.transaction(async tx => {
    type SavedRun = { status: string; retry_epoch: number; workflow_id: string; state_json: string | null; params_json: string | null };
    const [row] = await tx.query<SavedRun>(
      'SELECT status, retry_epoch, workflow_id, state_json, params_json FROM flow_runs WHERE flow_id = ?', [flowId],
    );
    if (!row) return 'not_found';
    // A lost-response replay cannot replace checkpoints from the running generation.
    if (row.retry_epoch === nextEpoch && row.status === 'running') return 'resumed';
    if (row.retry_epoch !== nextEpoch - 1 || ['cancelled', 'canceled'].includes(row.status.trim().toLowerCase())) return 'conflict';
    const checkpoint = prepareRetryCheckpoint(row, nextEpoch, stateJson);
    const result = await tx.exec(
      `UPDATE flow_runs SET status = 'running', retry_epoch = ?, state_json = ?, params_json = ?, completed_at = NULL, gmt_modified = ?
       WHERE flow_id = ? AND LOWER(TRIM(status)) NOT IN ('cancelled', 'canceled') AND retry_epoch = ?`,
      [nextEpoch, checkpoint.stateJson, checkpoint.paramsJson, nowForDb(tx.dbType), flowId, nextEpoch - 1],
    );
    return result.affectedRows > 0 ? 'resumed' : 'conflict';
  });
}
