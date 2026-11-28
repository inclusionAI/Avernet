import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { isMissingRetryEpoch, matchesRetryEpoch, terminalStatuses } from './flow-run-write-fence.js';

export class NodeReconcileConflict extends Error {
  constructor() { super('Node reconciliation conflict: parent terminal status or retry epoch does not match'); }
}

/** Lock the parent and fence it again in the child UPDATE, including raw epoch NULLs. */
export async function reconcileTerminalNodes(
  db: IDatabase, flowId: string, flowStatus: string, customErrorText?: string, retryEpoch?: number,
): Promise<number> {
  if (!terminalStatuses.includes(flowStatus)) throw new NodeReconcileConflict();
  return db.transaction(async tx => {
    const lock = tx.dialect.driver === 'mysql2' ? ' FOR UPDATE' : '';
    const exact = (column: string) => tx.dialect.driver === 'mysql2'
      ? `BINARY ${column} = BINARY ?` : `${column} COLLATE BINARY = ?`;
    const read = (epoch: boolean) => tx.query<{ status: string; retry_epoch?: number | null }>(
      `SELECT status${epoch ? ', retry_epoch' : ''} FROM flow_runs WHERE ${exact('flow_id')}${lock}`, [flowId]);
    let parent: { status: string; retry_epoch?: number | null } | undefined;
    try { [parent] = await read(true); }
    catch (error) { if (!isMissingRetryEpoch(error)) throw error; [parent] = await read(false); }
    if (!parent || parent.status !== flowStatus || !matchesRetryEpoch(parent, retryEpoch)) throw new NodeReconcileConflict();
    const target = flowStatus === 'failed' ? 'failed' : 'skipped';
    const message = flowStatus === 'succeeded' ? 'Node still running when workflow succeeded — reconciled to skipped'
      : flowStatus === 'failed' ? 'Node still running when workflow failed — reconciled to failed'
      : 'Node still running when workflow was aborted — reconciled to skipped';
    const conditions = [exact('flow_id'), exact('status')];
    const parentParams: unknown[] = [flowId, parent.status];
    if (Object.hasOwn(parent, 'retry_epoch')) {
      conditions.push(parent.retry_epoch === null ? 'retry_epoch IS NULL' : 'retry_epoch = ?');
      if (parent.retry_epoch !== null) parentParams.push(parent.retry_epoch);
    }
    const result = await tx.exec(`UPDATE node_executions SET status = ?, error_text = ?, completed_at = ?, gmt_modified = ?
      WHERE ${exact('flow_id')} AND ${exact('status')}
      AND EXISTS (SELECT 1 FROM flow_runs WHERE ${conditions.join(' AND ')})`,
    [target, customErrorText ?? message, Math.floor(Date.now() / 1000), tx.dialect.now(), flowId, 'running', ...parentParams]);
    if (!Number.isSafeInteger(result.affectedRows) || result.affectedRows < 0) throw new Error('Invalid node reconciliation affectedRows');
    // The lock/transaction makes a zero count meaningful: the admitted parent
    // cannot be replaced by a retry while the child write is in flight.
    return result.affectedRows;
  });
}
