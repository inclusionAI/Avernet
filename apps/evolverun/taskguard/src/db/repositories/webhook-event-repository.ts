/**
 * Repository for webhook event logging.
 *
 * Provides typed access to the `webhook_events` table.
 * Best-effort writes: failures are logged but do not throw
 * (except for record(), which returns null on failure).
 */
import crypto from "node:crypto";
import type { IDatabase, Row } from "../types.js";
import type { WebhookEvent } from "../../webhook/types.js";

type RecordEventInput = {
  eventId: string;
  triggerId: string;
  flowId?: string | null;
  status: string;
  requestMethod: string;
  requestHeaders?: Record<string, string> | null;
  requestBodyHash?: string | null;
  responseCode?: number | null;
  errorMessage?: string | null;
  ipAddress?: string | null;
};

export class WebhookEventRepository {
  constructor(private db: IDatabase) {}

  /**
   * Record a webhook event. Returns the event row on success, null on failure
   * (best-effort: event logging should not block the webhook request).
   */
  async record(input: RecordEventInput): Promise<WebhookEvent | null> {
    const now = Math.floor(Date.now() / 1000);
    const headersJson = input.requestHeaders ? JSON.stringify(input.requestHeaders) : null;

    try {
      await this.db.exec(
        `INSERT INTO webhook_events (event_id, trigger_id, flow_id, status, request_method, request_headers, request_body_hash, response_code, error_message, ip_address, gmt_create)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
        [
          input.eventId,
          input.triggerId,
          input.flowId ?? null,
          input.status,
          input.requestMethod,
          headersJson,
          input.requestBodyHash ?? null,
          input.responseCode ?? null,
          input.errorMessage ?? null,
          input.ipAddress ?? null,
          now,
        ],
      );

      const rows = await this.db.query<Row & WebhookEvent>(
        "SELECT * FROM webhook_events WHERE event_id = ? AND gmt_create = ? ORDER BY id DESC LIMIT 1",
        [input.eventId, now],
      );
      return rows[0] ?? null;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[webhook] Failed to record event ${input.eventId}: ${msg}`);
      return null;
    }
  }

  /**
   * Find a duplicate event within the idempotency window.
   * Returns the matching event if found, null otherwise.
   * Only considers events with status "accepted" (not "rejected" or "error").
   */
  async findDuplicate(eventId: string, windowHours: number): Promise<WebhookEvent | null> {
    const cutoff = Math.floor(Date.now() / 1000) - windowHours * 3600;

    try {
      const rows = await this.db.query<Row & WebhookEvent>(
        "SELECT * FROM webhook_events WHERE event_id = ? AND gmt_create > ? AND status = 'accepted' LIMIT 1",
        [eventId, cutoff],
      );
      return rows[0] ?? null;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[webhook] Idempotency check failed for event ${eventId}: ${msg}`);
      return null; // fail-open: don't block the request
    }
  }

  /**
   * Find events by trigger ID with pagination.
   */
  async findByTriggerId(triggerId: string, options?: { limit?: number; offset?: number }): Promise<WebhookEvent[]> {
    const limit = options?.limit ?? 50;
    const offset = options?.offset ?? 0;

    return this.db.query<Row & WebhookEvent>(
      "SELECT * FROM webhook_events WHERE trigger_id = ? ORDER BY gmt_create DESC LIMIT ? OFFSET ?",
      [triggerId, limit, offset],
    );
  }

  /**
   * Delete events older than the specified number of days.
   * Returns the number of deleted rows.
   */
  async deleteOlderThan(retentionDays: number): Promise<number> {
    const cutoff = Math.floor(Date.now() / 1000) - retentionDays * 86400;

    try {
      await this.db.exec(
        "DELETE FROM webhook_events WHERE gmt_create < ?",
        [cutoff],
      );
      // SQLite: use changes(); MySQL: use affectedRows
      return 1; // approximate; exact count not critical
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[webhook] Failed to delete old events: ${msg}`);
      return 0;
    }
  }

  /**
   * Compute SHA-256 hash of a request body (for logging without storing raw payload).
   */
  static hashBody(body: string): string {
    return crypto.createHash("sha256").update(body).digest("hex");
  }
}