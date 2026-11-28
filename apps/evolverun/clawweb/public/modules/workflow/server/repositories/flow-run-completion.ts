import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import type { FlowRunCompletion } from './flow-run-repository.js';
import { isTimeoutResult, writeTimeoutFailure, type CompletionOutcome } from './flow-run-timeout-write.js';

function completionPatch(db: IDatabase, flowId: string, completion: FlowRunCompletion) {
  // undefined preserves the previous result/input/phase; null explicitly overwrites it.
  const sets = ['status = ?', 'total_duration_ms = ?', 'total_token_usage = ?',
    'completed_at = ?', 'gmt_modified = ?'];
  const values: unknown[] = [completion.status, completion.totalDurationMs ?? null,
    completion.totalTokenUsage ?? null, completion.completedAt, db.dialect.now()];
  if (completion.currentPhase !== undefined) { sets.push('current_phase = ?'); values.push(completion.currentPhase); }
  if (completion.resultJson !== undefined) { sets.push('result_json = ?'); values.push(completion.resultJson); }
  if (completion.inputJson !== undefined) { sets.push('input_json = ?'); values.push(completion.inputJson); }
  if (completion.succeededCount !== undefined) {
    sets.push('succeeded_count = ?'); values.push(completion.succeededCount);
  } else {
    sets.push("succeeded_count = (SELECT COUNT(*) FROM node_executions WHERE flow_id = ? AND status IN ('succeeded', 'skipped'))");
    values.push(flowId);
  }
  if (completion.failedCount !== undefined) {
    sets.push('failed_count = ?'); values.push(completion.failedCount);
  } else {
    sets.push("failed_count = (SELECT COUNT(*) FROM node_executions WHERE flow_id = ? AND status = 'failed')");
    values.push(flowId);
  }
  return { sets, values };
}

export async function completeFlowRun(db: IDatabase, flowId: string, completion: FlowRunCompletion): Promise<CompletionOutcome> {
  if (typeof completion.status !== 'string' || !completion.status
    || completion.status !== completion.status.trim().toLowerCase()) return 'rejected';
  if (completion.status === 'failed' && isTimeoutResult(completion.resultJson)) {
    return writeTimeoutFailure(db, flowId, { ...completion, resultJson: completion.resultJson },
      completedAt => completionPatch(db, flowId, { ...completion, completedAt }), true);
  }
  const { sets, values } = completionPatch(db, flowId, completion);
  values.push(flowId);
  let savedStatusGuard = '';
  let savedIsFailed = false;
  if (completion.status === 'failed') {
    const [saved] = await db.query<{ status: string }>('SELECT status FROM flow_runs WHERE flow_id = ?', [flowId]);
    if (!saved) return 'rejected';
    const normalizedStatus = saved.status.trim().toLowerCase();
    if (['succeeded', 'cancelled', 'canceled'].includes(normalizedStatus)) return 'rejected';
    savedIsFailed = normalizedStatus === 'failed';
    // JS trim handles tabs/Unicode whitespace consistently across databases.
    // Classification is normalized, but both writes fence the raw saved bytes.
    savedStatusGuard = db.dialect.driver === 'mysql2' ? ' AND BINARY status = BINARY ?' : ' AND status COLLATE BINARY = ?';
    values.push(saved.status);
  }
  // Keep the original generation protocol for other writes. A plain failure may
  // settle a nonterminal run or supplement failed details, never erase success.
  const failedGuard = completion.status === 'failed' ? " AND status <> 'succeeded'" : '';
  // The raw-status fence binds this classification to both writes. Completion
  // time and epoch remain SQL conditions, so concurrent settlement cannot reopen it.
  const unsettledGuard = savedIsFailed ? 'completed_at IS NULL' : "status <> 'failed' OR completed_at IS NULL";
  const epochGuard = completion.retryEpoch === undefined ? '' : ` AND COALESCE(retry_epoch, 0) = ?
    AND status <> 'succeeded'
    AND (COALESCE(retry_epoch, 0) = 0 OR ${unsettledGuard})`;
  if (completion.retryEpoch !== undefined) values.push(completion.retryEpoch);
  const terminalGuard = completion.status === 'failed' ? ` AND (${unsettledGuard})` : '';
  const result = await db.exec(
    `UPDATE flow_runs SET ${sets.join(', ')} WHERE flow_id = ? AND status NOT IN ('cancelled', 'canceled')${savedStatusGuard}${failedGuard}${terminalGuard}${epochGuard}`,
    values);
  if (result.affectedRows > 0) return 'updated';
  if (completion.status !== 'failed') return 'rejected';
  // A failure supplement is accepted but is not another terminal transition.
  // Keep this guard in the write itself, not a racy status preflight read.
  const supplementGuard = savedIsFailed ? '' : " AND status = 'failed'";
  const supplement = await db.exec(
    `UPDATE flow_runs SET ${sets.join(', ')} WHERE flow_id = ?${supplementGuard}${savedStatusGuard}${epochGuard}`, values);
  return supplement.affectedRows > 0 ? 'duplicate' : 'rejected';
}
