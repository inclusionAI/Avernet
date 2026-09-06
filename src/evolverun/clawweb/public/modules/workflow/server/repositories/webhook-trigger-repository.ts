/**
 * WebhookTriggerRepository — CRUD for webhook_triggers table.
 */
import type { IDatabase } from "@avernet/clawweb-shared/server/db";

export type WebhookTriggerRow = {
  id: number;
  trigger_id: string;
  workflow_id: string;
  pack_id: string | null;
  secret: string | null;
  payload_mapping: string | null;
  allowed_ips: string | null;
  enabled: number;
  description: string | null;
  gmt_create: number;
  gmt_modified: number;
};

export type CreateWebhookTriggerInput = {
  trigger_id: string;
  workflow_id: string;
  pack_id?: string | null;
  secret?: string | null;
  payload_mapping?: string | null;
  allowed_ips?: string | null;
  enabled?: number;
  description?: string | null;
};

export type UpdateWebhookTriggerInput = {
  workflow_id?: string;
  pack_id?: string | null;
  secret?: string | null;
  payload_mapping?: string | null;
  allowed_ips?: string | null;
  enabled?: number;
  description?: string | null;
};

export class WebhookTriggerRepository {
  constructor(private db: IDatabase) {}

  async insert(input: CreateWebhookTriggerInput): Promise<WebhookTriggerRow> {
    const now = this.db.dialect.now();
    await this.db.exec(
      `INSERT INTO webhook_triggers (trigger_id, workflow_id, pack_id, secret, payload_mapping, allowed_ips, enabled, description, gmt_create, gmt_modified)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [
        input.trigger_id,
        input.workflow_id,
        input.pack_id ?? null,
        input.secret ?? null,
        input.payload_mapping ?? null,
        input.allowed_ips ?? null,
        input.enabled ?? 1,
        input.description ?? null,
        now,
        now,
      ],
    );
    const result = await this.findByTriggerId(input.trigger_id);
    return result!;
  }

  async findByTriggerId(triggerId: string): Promise<WebhookTriggerRow | null> {
    const rows = await this.db.query<WebhookTriggerRow>(
      "SELECT * FROM webhook_triggers WHERE trigger_id = ?",
      [triggerId],
    );
    return rows[0] ?? null;
  }

  async findByWorkflowId(workflowId: string): Promise<WebhookTriggerRow[]> {
    return this.db.query<WebhookTriggerRow>(
      "SELECT * FROM webhook_triggers WHERE workflow_id = ? ORDER BY gmt_create",
      [workflowId],
    );
  }

  async listAll(options: { enabled?: number; limit?: number; offset?: number } = {}): Promise<WebhookTriggerRow[]> {
    const limit = options.limit ?? 100;
    const offset = options.offset ?? 0;

    if (options.enabled !== undefined) {
      return this.db.query<WebhookTriggerRow>(
        "SELECT * FROM webhook_triggers WHERE enabled = ? ORDER BY gmt_create LIMIT ? OFFSET ?",
        [options.enabled, limit, offset],
      );
    }
    return this.db.query<WebhookTriggerRow>(
      "SELECT * FROM webhook_triggers ORDER BY gmt_create LIMIT ? OFFSET ?",
      [limit, offset],
    );
  }

  async update(triggerId: string, input: UpdateWebhookTriggerInput): Promise<WebhookTriggerRow | null> {
    const existing = await this.findByTriggerId(triggerId);
    if (!existing) return null;

    const now = this.db.dialect.now();
    const sets: string[] = [];
    const values: unknown[] = [];

    const fields: Array<[string, unknown]> = [
      ["workflow_id", input.workflow_id],
      ["pack_id", input.pack_id],
      ["secret", input.secret],
      ["payload_mapping", input.payload_mapping],
      ["allowed_ips", input.allowed_ips],
      ["enabled", input.enabled],
      ["description", input.description],
    ];

    for (const [col, val] of fields) {
      if (val !== undefined) {
        sets.push(`${col} = ?`);
        values.push(val);
      }
    }

    if (sets.length === 0) return existing;

    sets.push("gmt_modified = ?");
    values.push(now);
    values.push(triggerId);

    await this.db.exec(
      `UPDATE webhook_triggers SET ${sets.join(", ")} WHERE trigger_id = ?`,
      values,
    );
    return this.findByTriggerId(triggerId);
  }

  async delete(triggerId: string): Promise<boolean> {
    const result = await this.db.exec(
      "DELETE FROM webhook_triggers WHERE trigger_id = ?",
      [triggerId],
    );
    return result.affectedRows > 0;
  }
}