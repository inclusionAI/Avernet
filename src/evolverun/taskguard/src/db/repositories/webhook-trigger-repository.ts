/**
 * Repository for webhook trigger CRUD operations.
 *
 * Provides typed access to the `webhook_triggers` table.
 */
import crypto from "node:crypto";
import type { IDatabase, Row } from "../types.js";
import type { WebhookTrigger } from "../../webhook/types.js";

type CreateWebhookTriggerInput = {
  triggerId?: string;
  workflowId: string;
  packId?: string;
  secret?: string;
  payloadMapping?: Record<string, string> | null;
  allowedIps?: string[] | null;
  description?: string;
  enabled?: boolean;
};

export class WebhookTriggerRepository {
  constructor(private db: IDatabase) {}

  async create(input: CreateWebhookTriggerInput): Promise<WebhookTrigger> {
    const triggerId = input.triggerId ?? `trg_${crypto.randomBytes(4).toString("hex")}`;
    const now = Math.floor(Date.now() / 1000);

    const payloadJson = input.payloadMapping ? JSON.stringify(input.payloadMapping) : null;
    const allowedIpsJson = input.allowedIps ? JSON.stringify(input.allowedIps) : null;

    try {
      await this.db.exec(
        `INSERT INTO webhook_triggers (trigger_id, workflow_id, pack_id, secret, payload_mapping, allowed_ips, enabled, description, gmt_create, gmt_modified)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
        [
          triggerId,
          input.workflowId,
          input.packId ?? null,
          input.secret ?? null,
          payloadJson,
          allowedIpsJson,
          input.enabled !== false ? 1 : 0,
          input.description ?? null,
          now,
          now,
        ],
      );
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      if (msg.includes("UNIQUE") || msg.includes("duplicate")) {
        throw new Error(`Webhook trigger already exists: ${triggerId}`);
      }
      throw error;
    }

    return this.getByTriggerId(triggerId) as Promise<WebhookTrigger>;
  }

  async getByTriggerId(triggerId: string): Promise<WebhookTrigger | null> {
    const rows = await this.db.query<Row & WebhookTrigger>(
      "SELECT * FROM webhook_triggers WHERE trigger_id = ?",
      [triggerId],
    );
    return rows[0] ?? null;
  }

  async findByWorkflowId(workflowId: string): Promise<WebhookTrigger[]> {
    return this.db.query<Row & WebhookTrigger>(
      "SELECT * FROM webhook_triggers WHERE workflow_id = ? ORDER BY gmt_create",
      [workflowId],
    );
  }

  async findAll(): Promise<WebhookTrigger[]> {
    return this.db.query<Row & WebhookTrigger>(
      "SELECT * FROM webhook_triggers ORDER BY gmt_create",
    );
  }

  async update(triggerId: string, updates: {
    workflowId?: string;
    packId?: string;
    secret?: string | null;
    payloadMapping?: Record<string, string> | null;
    allowedIps?: string[] | null;
    enabled?: boolean;
    description?: string | null;
  }): Promise<WebhookTrigger | null> {
    const existing = await this.getByTriggerId(triggerId);
    if (!existing) return null;

    const now = Math.floor(Date.now() / 1000);
    const payloadJson = updates.payloadMapping !== undefined
      ? (updates.payloadMapping ? JSON.stringify(updates.payloadMapping) : null)
      : existing.payload_mapping;
    const allowedIpsJson = updates.allowedIps !== undefined
      ? (updates.allowedIps ? JSON.stringify(updates.allowedIps) : null)
      : existing.allowed_ips;

    const enabled = updates.enabled !== undefined ? (updates.enabled ? 1 : 0) : existing.enabled;

    await this.db.exec(
      `UPDATE webhook_triggers
       SET workflow_id = ?, pack_id = ?, secret = ?, payload_mapping = ?, allowed_ips = ?, enabled = ?, description = ?, gmt_modified = ?
       WHERE trigger_id = ?`,
      [
        updates.workflowId ?? existing.workflow_id,
        updates.packId ?? existing.pack_id,
        updates.secret !== undefined ? updates.secret : existing.secret,
        payloadJson,
        allowedIpsJson,
        enabled,
        updates.description !== undefined ? updates.description : existing.description,
        now,
        triggerId,
      ],
    );

    return this.getByTriggerId(triggerId);
  }

  async delete(triggerId: string): Promise<boolean> {
    const existing = await this.getByTriggerId(triggerId);
    if (!existing) return false;

    await this.db.exec("DELETE FROM webhook_triggers WHERE trigger_id = ?", [triggerId]);
    return true;
  }
}