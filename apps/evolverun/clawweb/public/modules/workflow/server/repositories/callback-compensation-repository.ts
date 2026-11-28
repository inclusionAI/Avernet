import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { postAuthorityPredicate, type PostAuthority } from './callback-compensation-authority.js';
import type { ItemState } from '../services/callback-compensation/types.js';

export interface PreviewRow { preview_id: string; scope_key: string; expires_ms: number; dto_json: string; frozen_cipher: string | null }
export interface BatchRow {
  batch_id: string; environment_key: string; scope_key: string; request_key: string; request_hash: string;
  metadata_json: string; stopped: number;
}
export interface ItemRow {
  item_id: string; batch_id: string; environment_key: string; delivery_key: string; flow_id: string;
  state: ItemState; frozen_cipher: string; reason: string | null; http_status: number | null;
  sending_until_ms: number | null; audit_pending: number; audit_id: number | null;
}
const previewTable = 'workflow_callback_compensation_previews';
const batchTable = 'workflow_callback_compensation_batches';
const itemTable = 'workflow_callback_compensation_items';
export { previewTable, batchTable, itemTable };

/** Writes use affectedRows as CAS evidence. All failures propagate; no best-effort empty reads. */
export class CallbackCompensationRepository {
  constructor(readonly db: IDatabase) {}
  async preview(id: string): Promise<PreviewRow | null> {
    return (await this.db.query<PreviewRow>(`SELECT * FROM ${previewTable} WHERE preview_id = ?`, [id]))[0] ?? null;
  }
  async insertPreview(row: PreviewRow): Promise<void> {
    await this.db.exec(`INSERT INTO ${previewTable} (preview_id, scope_key, expires_ms, dto_json, frozen_cipher,
      gmt_create, gmt_modified) VALUES (?, ?, ?, ?, ?, ?, ?)`,
    [row.preview_id, row.scope_key, row.expires_ms, row.dto_json, row.frozen_cipher, this.db.dialect.now(), this.db.dialect.now()]);
  }
  async batch(id: string): Promise<BatchRow | null> {
    return (await this.db.query<BatchRow>(`SELECT * FROM ${batchTable} WHERE batch_id = ?`, [id]))[0] ?? null;
  }
  async request(requestKey: string): Promise<BatchRow | null> {
    return (await this.db.query<BatchRow>(`SELECT * FROM ${batchTable} WHERE request_key = ?`, [requestKey]))[0] ?? null;
  }
  async items(batchId: string): Promise<ItemRow[]> {
    return this.db.query<ItemRow>(`SELECT * FROM ${itemTable} WHERE batch_id = ? ORDER BY id`, [batchId]);
  }
  async existingDelivery(deliveryKey: string): Promise<ItemRow | null> {
    return (await this.db.query<ItemRow>(`SELECT * FROM ${itemTable} WHERE delivery_key = ?`, [deliveryKey]))[0] ?? null;
  }
  async insertBatch(row: BatchRow): Promise<void> {
    await this.db.exec(`INSERT INTO ${batchTable} (batch_id, environment_key, scope_key, request_key, request_hash,
      metadata_json, stopped, gmt_create, gmt_modified) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)`,
    [row.batch_id, row.environment_key, row.scope_key, row.request_key, row.request_hash, row.metadata_json,
      this.db.dialect.now(), this.db.dialect.now()]);
  }
  async insertItem(row: Pick<ItemRow, 'item_id'|'batch_id'|'environment_key'|'delivery_key'|'flow_id'|'frozen_cipher'>): Promise<void> {
    await this.db.exec(`INSERT INTO ${itemTable} (item_id, batch_id, environment_key, delivery_key, flow_id,
      state, frozen_cipher, audit_pending, gmt_create, gmt_modified) VALUES (?, ?, ?, ?, ?, 'queued', ?, 0, ?, ?)`,
    [row.item_id, row.batch_id, row.environment_key, row.delivery_key, row.flow_id, row.frozen_cipher,
      this.db.dialect.now(), this.db.dialect.now()]);
  }
  async stop(batchId: string): Promise<void> {
    await this.db.exec(`UPDATE ${batchTable} SET stopped = 1, gmt_modified = ? WHERE batch_id = ?`, [this.db.dialect.now(), batchId]);
    await this.db.exec(`UPDATE ${itemTable} SET state = 'skipped', reason = 'ADMIN_STOP', gmt_modified = ?
      WHERE batch_id = ? AND state = 'queued'`, [this.db.dialect.now(), batchId]);
  }
  async recover(environmentKey: string, now: number): Promise<void> {
    await this.db.exec(`UPDATE ${itemTable} SET state = 'unknown', reason = 'SENDING_LEASE_EXPIRED',
      audit_pending = 1, gmt_modified = ? WHERE environment_key = ? AND state = 'sending' AND sending_until_ms <= ?`,
    [this.db.dialect.now(), environmentKey, now]);
  }
  async work(environmentKey: string): Promise<ItemRow[]> {
    return this.db.query<ItemRow>(`SELECT i.* FROM ${itemTable} i JOIN ${batchTable} b ON b.batch_id = i.batch_id
      WHERE i.environment_key = ? AND ((i.state = 'queued' AND b.stopped = 0) OR i.audit_pending = 1)
      ORDER BY i.id LIMIT 100`, [environmentKey]);
  }
  async claim(itemId: string, until: number): Promise<boolean> {
    const r = await this.db.exec(`UPDATE ${itemTable} SET state = 'sending', sending_until_ms = ?, gmt_modified = ?
      WHERE item_id = ? AND state = 'queued' AND EXISTS
      (SELECT 1 FROM ${batchTable} b WHERE b.batch_id = ${itemTable}.batch_id AND b.stopped = 0)`,
    [until, this.db.dialect.now(), itemId]);
    return r.affectedRows === 1;
  }
  /** Last short CAS before POST: an expired/recovered claim or stopped batch may not send.
   * Increment deadline even at an identical clock tick so MySQL changed-row semantics are unambiguous.
   */
  async authorizePost(itemId: string, originalUntil: number, now: number, nextUntil: number, authority: PostAuthority | null): Promise<boolean> {
    if (!authority) return false;
    const guard = postAuthorityPredicate(this.db, authority);
    const result = await this.db.exec(`UPDATE ${itemTable} SET sending_until_ms = ?, gmt_modified = ?
      WHERE item_id = ? AND state = 'sending' AND sending_until_ms = ? AND sending_until_ms > ?
      AND EXISTS (SELECT 1 FROM ${batchTable} b WHERE b.batch_id = ${itemTable}.batch_id AND b.stopped = 0)
      AND ${guard.sql}`,
    [nextUntil, this.db.dialect.now(), itemId, originalUntil, now, ...guard.params]);
    return result.affectedRows === 1;
  }
  async abandonBeforePost(itemId: string, originalUntil: number, now: number, reason = 'AUTHORITY_CHANGED_BEFORE_POST'): Promise<void> {
    // No network has started. Stop is a skip; expired sending is still conservative unknown.
    await this.db.exec(`UPDATE ${itemTable} SET state = 'skipped', reason = 'ADMIN_STOP_BEFORE_POST', gmt_modified = ?
      WHERE item_id = ? AND state = 'sending' AND EXISTS
      (SELECT 1 FROM ${batchTable} b WHERE b.batch_id = ${itemTable}.batch_id AND b.stopped = 1)`,
    [this.db.dialect.now(), itemId]);
    await this.db.exec(`UPDATE ${itemTable} SET state = 'skipped', reason = ?, gmt_modified = ?
      WHERE item_id = ? AND state = 'sending' AND sending_until_ms = ? AND sending_until_ms > ?`,
    [reason, this.db.dialect.now(), itemId, originalUntil, now]);
    if (now >= originalUntil) await this.db.exec(`UPDATE ${itemTable} SET state = 'unknown',
      reason = 'SENDING_LEASE_EXPIRED', audit_pending = 1, gmt_modified = ? WHERE item_id = ? AND state = 'sending'`,
    [this.db.dialect.now(), itemId]);
  }
  async settle(itemId: string, state: ItemState, reason: string, httpStatus: number | null, auditPending: boolean): Promise<void> {
    const r = await this.db.exec(`UPDATE ${itemTable} SET state = ?, reason = ?, http_status = ?, audit_pending = ?,
      gmt_modified = ? WHERE item_id = ? AND state = 'sending'`,
    [state, reason, httpStatus, auditPending ? 1 : 0, this.db.dialect.now(), itemId]);
    if (r.affectedRows !== 1) throw new Error('Compensation outcome CAS lost; never resend');
  }
}
