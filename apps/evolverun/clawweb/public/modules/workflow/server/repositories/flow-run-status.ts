import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { matchesRetryEpoch, protectedTerminalStatuses, withRetryEpochRead } from './flow-run-write-fence.js';

/** Status mirrors cannot settle failed/succeeded or retire a protected epoch. */
export async function writeRunStatus(db: IDatabase, flowId: string, status: string, retryEpoch?: number): Promise<boolean> {
  if (typeof status !== 'string' || !status || status !== status.trim().toLowerCase()
    || ['succeeded', 'failed', 'completed'].includes(status)) return false;
  type Parent = { status: string; completed_at: number | null; retry_epoch?: number | null };
  return withRetryEpochRead(db, async (tx, epoch) => (await tx.query<Parent>(
    `SELECT status, completed_at${epoch ? ', retry_epoch' : ''} FROM flow_runs WHERE flow_id = ?`, [flowId]))[0], async (db, saved) => {
    if (!saved || saved.status !== saved.status.trim().toLowerCase() || !matchesRetryEpoch(saved, retryEpoch)
      || ['cancelled', 'canceled'].includes(saved.status)) return false;
    if (protectedTerminalStatuses.includes(saved.status) || saved.completed_at !== null) return false;
    const exact = (column: string) => db.dialect.driver === 'mysql2' ? `BINARY ${column} = BINARY ?` : `${column} COLLATE BINARY = ?`;
    const conditions = [exact('flow_id'), exact('status')];
    const params: unknown[] = [status, db.dialect.now(), flowId, saved.status];
    conditions.push(saved.completed_at === null ? 'completed_at IS NULL' : 'completed_at = ?');
    if (saved.completed_at !== null) params.push(saved.completed_at);
    if (Object.hasOwn(saved, 'retry_epoch')) {
      conditions.push(saved.retry_epoch === null ? 'retry_epoch IS NULL' : 'retry_epoch = ?');
      if (saved.retry_epoch !== null) params.push(saved.retry_epoch);
    }
    const result = await db.exec(`UPDATE flow_runs SET status = ?, gmt_modified = ?
      WHERE ${conditions.join(' AND ')}`, params);
    return result.affectedRows > 0;
  });
}
