import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { itemTable } from '../../repositories/callback-compensation-repository.js';
import type { ItemRow } from '../../repositories/callback-compensation-repository.js';

/** An outcome is durably committed BEFORE this is called. Transaction + row lock makes
 * audit-only repair idempotent even across processes. No HTTP dependency exists here.
 */
export async function repairAudit(db: IDatabase, itemId: string, metadata: {
  workflowId: string; flowId: string; configId: string; configName: string; url: string;
}): Promise<void> {
  await db.transaction(async tx => {
    const lock = tx.dbType === 'sqlite' ? '' : ' FOR UPDATE';
    const item = (await tx.query<ItemRow>(`SELECT * FROM ${itemTable} WHERE item_id = ?${lock}`, [itemId]))[0];
    if (!item || item.audit_pending !== 1 || item.state === 'sending' || item.state === 'queued') return;
    if (item.audit_id != null) {
      await tx.exec(`UPDATE ${itemTable} SET audit_pending = 0, gmt_modified = ? WHERE item_id = ?`, [tx.dialect.now(), itemId]);
      return;
    }
    // Paths and queries can themselves contain receiver access tokens. Only origin is public audit data.
    const target = new URL(metadata.url);
    const inserted = await tx.exec(`INSERT INTO http_callback_logs
      (flow_id, workflow_id, config_id, config_name, callback_url, notify_event, node_id, attempt, max_attempts,
        request_body, request_headers, response_status_code, response_body, duration_ms, status, error_message,
        callback_source, gmt_create, gmt_modified)
      VALUES (?, ?, ?, ?, ?, 'workflow_succeeded', NULL, 1, 1, NULL, NULL, ?, NULL, NULL, ?, ?, 'workflow-level', ?, ?)`,
    [metadata.flowId, metadata.workflowId, metadata.configId, metadata.configName,
      target.origin, item.http_status,
      item.state === 'received' ? 'delivered' : 'unknown', item.reason,
      tx.dialect.now(), tx.dialect.now()]);
    if (!inserted.insertId) throw new Error('Compensation audit insert did not return an ID');
    const result = await tx.exec(`UPDATE ${itemTable} SET audit_id = ?, audit_pending = 0, gmt_modified = ?
      WHERE item_id = ? AND audit_pending = 1 AND audit_id IS NULL`, [inserted.insertId, tx.dialect.now(), itemId]);
    if (result.affectedRows !== 1) throw new Error('Compensation audit CAS lost');
  });
}
