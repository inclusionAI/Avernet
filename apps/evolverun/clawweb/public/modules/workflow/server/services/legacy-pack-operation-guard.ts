import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { PackOperationError } from './pack-operation-service.js';

/** Only the journal itself being absent is optional; do not mask a missing
 * dependency, permission error, connection failure, or incompatible schema. */
function isMissingJournal(db: IDatabase, error: unknown): boolean {
  if (!error || typeof error !== 'object') return false;
  const { code, errno, message, sqlMessage } = error as {
    code?: unknown; errno?: unknown; message?: unknown; sqlMessage?: unknown;
  };
  if (db.dbType === 'sqlite') {
    return code === 'SQLITE_ERROR' && message === 'no such table: clawweb_pack_operations';
  }
  if (db.dbType === 'mysql' || db.dbType === 'zdas') {
    const text = typeof sqlMessage === 'string' ? sqlMessage : message;
    return (code === 'ER_NO_SUCH_TABLE' || errno === 1146) && typeof text === 'string'
      && /^Table '[^']+\.clawweb_pack_operations' doesn't exist$/i.test(text);
  }
  return false;
}

/** Read on each operation, never at startup and never create or migrate a table.
 * This is a retirement preflight, not a lock against older writers: deployments
 * must quiesce those writers before switching to journal-free operations. */
export async function assertNoUnresolvedLegacyPackOperation(db: IDatabase, workflowId: string): Promise<void> {
  let rows: unknown[];
  try {
    rows = await db.query(`SELECT request_id FROM clawweb_pack_operations
      WHERE workflow_id = ? AND (phase IS NULL OR phase NOT IN ('completed', 'failed') OR lock_workflow_id IS NOT NULL) LIMIT 1`, [workflowId]);
  } catch (error) {
    if (isMissingJournal(db, error)) return;
    throw error;
  }
  if (rows.length) {
    const error = new PackOperationError(409, 'LEGACY_PACK_OPERATION_UNRESOLVED');
    error.message = 'An unresolved legacy pack operation exists for this workflow; reconcile it before saving or deploying.';
    throw error;
  }
}
