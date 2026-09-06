/**
 * WebhookEventRepository — reads and writes webhook_events table via raw SQL.
 * No dependency on ClawFlow; shares the same database schema.
 */
import type { IDatabase } from "@avernet/clawweb-shared/server/db";

export type WebhookEventRow = {
  id: number;
  event_id: string;
  trigger_id: string;
  flow_id: string | null;
  status: string;
  request_method: string;
  request_headers: string | null;
  request_body_hash: string | null;
  response_code: number | null;
  error_message: string | null;
  ip_address: string | null;
  event_type: string | null;
  payload_json: string | null;
  received_at: number | null;
  gmt_create: number;
  gmt_modified: number;
};

export class WebhookEventRepository {
  constructor(private db: IDatabase) {}

  // ── Write methods (best-effort: catch errors, log, return -1/0) ──

  async record(
    triggerId: string,
    eventType: string,
    payloadJson: string,
    sourceIp?: string | null,
    receivedAt?: number | null,
  ): Promise<number> {
    try {
      const now = this.db.dialect.now();
      const timestamp = receivedAt ?? (typeof now === "number" ? now : Math.floor(Date.now() / 1000));
      const eventId = `evt_${triggerId}_${Date.now()}`;
      const result = await this.db.exec(
        `INSERT INTO webhook_events (event_id, trigger_id, flow_id, status, request_method, request_headers, request_body_hash, response_code, error_message, ip_address, event_type, payload_json, received_at, gmt_create, gmt_modified)
         VALUES (?, ?, NULL, 'received', 'POST', NULL, NULL, NULL, NULL, ?, ?, ?, ?, ?, ?)`,
        [
          eventId,
          triggerId,
          sourceIp ?? null,
          eventType,
          payloadJson,
          timestamp,
          now,
          now,
        ],
      );
      return result.insertId ?? -1;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] WebhookEventRepository.record failed: ${msg}`);
      return -1;
    }
  }

  async findDuplicate(
    triggerId: string,
    eventType: string,
    payloadHash: string,
    withinSeconds: number,
  ): Promise<WebhookEventRow | null> {
    try {
      const now = this.db.dialect.now();
      const cutoff = typeof now === "number" ? now - withinSeconds : Math.floor(Date.now() / 1000) - withinSeconds;
      const rows = await this.db.query<WebhookEventRow>(
        `SELECT * FROM webhook_events
         WHERE trigger_id = ? AND event_type = ? AND request_body_hash = ? AND gmt_create >= ?
         ORDER BY gmt_create DESC LIMIT 1`,
        [triggerId, eventType, payloadHash, cutoff],
      );
      return rows[0] ?? null;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] WebhookEventRepository.findDuplicate failed: ${msg}`);
      return null;
    }
  }

  async deleteOlderThan(olderThanSeconds: number): Promise<number> {
    try {
      const now = this.db.dialect.now();
      const cutoff = typeof now === "number" ? now - olderThanSeconds : Math.floor(Date.now() / 1000) - olderThanSeconds;
      const result = await this.db.exec(
        "DELETE FROM webhook_events WHERE gmt_create < ?",
        [cutoff],
      );
      return result.affectedRows;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] WebhookEventRepository.deleteOlderThan failed: ${msg}`);
      return 0;
    }
  }

  // ── Read methods ──

  async findByTriggerId(triggerId: string, options: { limit?: number; offset?: number } = {}): Promise<WebhookEventRow[]> {
    const limit = options.limit ?? 50;
    const offset = options.offset ?? 0;
    try {
      return await this.db.query<WebhookEventRow>(
        "SELECT * FROM webhook_events WHERE trigger_id = ? ORDER BY gmt_create DESC LIMIT ? OFFSET ?",
        [triggerId, limit, offset],
      );
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] WebhookEventRepository.findByTriggerId failed: ${msg}`);
      return [];
    }
  }

  // ── Backward-compatible aliases ──

  /** @deprecated Use InsertWebhookEventInput with positional args via record() */
  async insert(input: {
    event_id: string;
    trigger_id: string;
    flow_id?: string | null;
    status: string;
    request_method: string;
    request_headers?: string | null;
    request_body_hash?: string | null;
    response_code?: number | null;
    error_message?: string | null;
    ip_address?: string | null;
  }): Promise<WebhookEventRow> {
    const now = this.db.dialect.now();
    const timestamp = typeof now === "number" ? now : Math.floor(Date.now() / 1000);
    await this.db.exec(
      `INSERT INTO webhook_events (event_id, trigger_id, flow_id, status, request_method, request_headers, request_body_hash, response_code, error_message, ip_address, event_type, payload_json, received_at, gmt_create, gmt_modified)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?)`,
      [
        input.event_id,
        input.trigger_id,
        input.flow_id ?? null,
        input.status,
        input.request_method,
        input.request_headers ?? null,
        input.request_body_hash ?? null,
        input.response_code ?? null,
        input.error_message ?? null,
        input.ip_address ?? null,
        timestamp,
        now,
        now,
      ],
    );
    const rows = await this.db.query<WebhookEventRow>(
      "SELECT * FROM webhook_events WHERE event_id = ? ORDER BY id DESC LIMIT 1",
      [input.event_id],
    );
    return rows[0]!;
  }
}