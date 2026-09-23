import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import type { DeliveryKey, DeliveryRecord, RecoveryDeliveryStore } from "../services/session-recovery/contracts.js";
import { SessionRecoveryError } from "../services/session-recovery/errors.js";

type Row = { request_fingerprint: string; started_at_ms: number; settle_after_ms: number; outcome_json: string | null; callback_delivered: number };
const table = "insight_session_recovery_delivery";
const params = (key: DeliveryKey) => [key.event_id, key.delivery_key];
const unavailable = () => new SessionRecoveryError(503, "recovery_store_unavailable", "投递记录暂不可用");

/** Unique insertion owns the send; conditional finalization makes the result immutable. */
export class SessionRecoveryDeliveryRepository implements RecoveryDeliveryStore {
  constructor(private readonly db: IDatabase) {}
  private async checkSchema(): Promise<void> {
    if (this.db.dbType === "noop") throw unavailable();
    // Never send against an unprovisioned table or a schema missing its deduplication key.
    if (this.db.dbType === "sqlite") {
      const indexes = await this.db.query<{ name: string; unique: number; partial: number }>(`PRAGMA index_list(${table})`);
      for (const index of indexes.filter(i => i.unique === 1 && i.partial === 0)) {
        const columns = await this.db.query<{ name: string }>(`SELECT name FROM pragma_index_info(?) ORDER BY seqno`, [index.name]);
        if (columns.map(c => c.name).join(",") === "event_id,delivery_key") return;
      }
    } else {
      const indexes = await this.db.query<{ Key_name: string; Non_unique: number; Seq_in_index: number; Column_name: string; Sub_part: number | null }>(`SHOW INDEX FROM ${table}`);
      const groups = new Map<string, typeof indexes>();
      for (const index of indexes) groups.set(index.Key_name, [...(groups.get(index.Key_name) ?? []), index]);
      for (const group of groups.values()) {
        if (group.every(i => Number(i.Non_unique) === 0 && i.Sub_part == null)
          && group.sort((a, b) => a.Seq_in_index - b.Seq_in_index).map(i => i.Column_name).join(",") === "event_id,delivery_key") return;
      }
    }
    throw unavailable();
  }
  private async read(key: DeliveryKey): Promise<DeliveryRecord> {
    const [row] = await this.db.query<Row>(`SELECT request_fingerprint, started_at_ms, settle_after_ms, outcome_json, callback_delivered FROM ${table} WHERE event_id = ? AND delivery_key = ?`, params(key));
    if (!row) throw unavailable();
    return { fingerprint: row.request_fingerprint, startedAt: Number(row.started_at_ms), settleAfter: Number(row.settle_after_ms),
      outcome: row.outcome_json === null ? null : JSON.parse(row.outcome_json), callbackDelivered: Number(row.callback_delivered) === 1 };
  }
  async claim(key: DeliveryKey, fingerprint: string, startedAt: number, settleAfter: number) {
    await this.checkSchema();
    try {
      const inserted = await this.db.exec(`INSERT INTO ${table} (event_id, delivery_key, request_fingerprint, started_at_ms, settle_after_ms) VALUES (?, ?, ?, ?, ?)`, [...params(key), fingerprint, startedAt, settleAfter]);
      if (inserted.affectedRows !== 1) throw unavailable();
    } catch (error) {
      const code = (error as { code?: string }).code;
      // Do not interpret connection/permission/write failures as duplicate requests.
      if (code !== "SQLITE_CONSTRAINT_UNIQUE" && code !== "ER_DUP_ENTRY") throw error;
      const record = await this.read(key);
      if (record.fingerprint !== fingerprint) throw new SessionRecoveryError(409, "session_recovery_conflict", "相同 event_id 和 delivery_key 的请求内容不一致");
      return { acquired: false, record };
    }
    return { acquired: true, record: { fingerprint, startedAt, settleAfter, outcome: null, callbackDelivered: false } };
  }
  async finish(key: DeliveryKey, outcome: NonNullable<DeliveryRecord["outcome"]>): Promise<DeliveryRecord> {
    await this.db.exec(`UPDATE ${table} SET outcome_json = ? WHERE event_id = ? AND delivery_key = ? AND outcome_json IS NULL`, [JSON.stringify(outcome), ...params(key)]);
    const record = await this.read(key);
    if (!record.outcome) throw unavailable();
    return record;
  }
  async markReported(key: DeliveryKey): Promise<void> {
    await this.db.exec(`UPDATE ${table} SET callback_delivered = 1 WHERE event_id = ? AND delivery_key = ? AND outcome_json IS NOT NULL`, params(key));
    if (!(await this.read(key)).callbackDelivered) throw unavailable();
  }
}
