import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import type { FlowRunCompletion } from './flow-run-repository.js';
import { isTimeoutResult } from './flow-run-timeout-write.js';
import { writeRunSettlement, type CompletionOutcome } from './flow-run-write-fence.js';

function completionPatch(db: IDatabase, flowId: string, completion: FlowRunCompletion) {
  // undefined preserves result/input/phase; null explicitly clears those fields.
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
  return writeRunSettlement(db, flowId, completion, () => completionPatch(db, flowId,
    completion.status === 'failed' && isTimeoutResult(completion.resultJson)
      ? { ...completion, completedAt: Math.floor(Date.now() / 1000) } : completion), true);
}
