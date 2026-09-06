/**
 * ScheduledTriggerRepository — CRUD for scheduled_triggers table via raw SQL.
 * No dependency on ClawFlow; shares the same database schema.
 */
import type { IDatabase } from "@avernet/clawweb-shared/server/db";

export type ScheduledTriggerRow = {
  id: number;
  trigger_id: string;
  workflow_id: string;
  pack_id: string;
  cron_expression: string;
  timezone: string;
  params_json: string | null;
  max_concurrent: number;
  enabled: number;
  last_fire_time: number | null;
  next_fire_time: number | null;
  gmt_create: number;
  gmt_modified: number;
};

export type CreateScheduledTriggerInput = {
  triggerId: string;
  workflowId: string;
  packId: string;
  cronExpression: string;
  timezone?: string;
  paramsJson?: string | null;
  maxConcurrent?: number;
  enabled?: boolean;
  nextFireTime?: number | null;
};

export type UpdateScheduledTriggerInput = {
  cronExpression?: string;
  timezone?: string;
  paramsJson?: string | null;
  maxConcurrent?: number;
  enabled?: boolean;
};

export class ScheduledTriggerRepository {
  constructor(private db: IDatabase) {}

  // ── Write methods ──

  async create(input: CreateScheduledTriggerInput): Promise<ScheduledTriggerRow> {
    const now = this.db.dialect.now();
    await this.db.exec(
      `INSERT INTO scheduled_triggers (trigger_id, workflow_id, pack_id, cron_expression, timezone, params_json, max_concurrent, enabled, last_fire_time, next_fire_time, gmt_create, gmt_modified)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)`,
      [
        input.triggerId,
        input.workflowId,
        input.packId,
        input.cronExpression,
        input.timezone ?? "UTC",
        input.paramsJson ?? null,
        input.maxConcurrent ?? 1,
        input.enabled !== false ? 1 : 0,
        input.nextFireTime ?? null,
        now,
        now,
      ],
    );
    const result = await this.getById(input.triggerId);
    return result!;
  }

  async update(triggerId: string, updates: UpdateScheduledTriggerInput): Promise<ScheduledTriggerRow | null> {
    const existing = await this.getById(triggerId);
    if (!existing) return null;

    const now = this.db.dialect.now();
    const sets: string[] = [];
    const values: unknown[] = [];

    const fields: Array<[string, unknown]> = [
      ["cron_expression", updates.cronExpression],
      ["timezone", updates.timezone],
      ["params_json", updates.paramsJson],
      ["max_concurrent", updates.maxConcurrent],
      ["enabled", updates.enabled !== undefined ? (updates.enabled ? 1 : 0) : undefined],
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
      `UPDATE scheduled_triggers SET ${sets.join(", ")} WHERE trigger_id = ?`,
      values,
    );
    return this.getById(triggerId);
  }

  async enable(triggerId: string): Promise<boolean> {
    try {
      const now = this.db.dialect.now();
      const result = await this.db.exec(
        "UPDATE scheduled_triggers SET enabled = 1, gmt_modified = ? WHERE trigger_id = ?",
        [now, triggerId],
      );
      return result.affectedRows > 0;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] ScheduledTriggerRepository.enable failed: ${msg}`);
      return false;
    }
  }

  async disable(triggerId: string): Promise<boolean> {
    try {
      const now = this.db.dialect.now();
      const result = await this.db.exec(
        "UPDATE scheduled_triggers SET enabled = 0, gmt_modified = ? WHERE trigger_id = ?",
        [now, triggerId],
      );
      return result.affectedRows > 0;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] ScheduledTriggerRepository.disable failed: ${msg}`);
      return false;
    }
  }

  async delete(triggerId: string): Promise<boolean> {
    try {
      const result = await this.db.exec(
        "DELETE FROM scheduled_triggers WHERE trigger_id = ?",
        [triggerId],
      );
      return result.affectedRows > 0;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] ScheduledTriggerRepository.delete failed: ${msg}`);
      return false;
    }
  }

  async updateFireTimes(triggerId: string, lastFireTime?: number | null, nextFireTime?: number | null): Promise<boolean> {
    try {
      const now = this.db.dialect.now();
      const sets: string[] = ["gmt_modified = ?"];
      const values: unknown[] = [now];

      if (lastFireTime !== undefined) {
        sets.push("last_fire_time = ?");
        values.push(lastFireTime);
      }
      if (nextFireTime !== undefined) {
        sets.push("next_fire_time = ?");
        values.push(nextFireTime);
      }

      values.push(triggerId);
      const result = await this.db.exec(
        `UPDATE scheduled_triggers SET ${sets.join(", ")} WHERE trigger_id = ?`,
        values,
      );
      return result.affectedRows > 0;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] ScheduledTriggerRepository.updateFireTimes failed: ${msg}`);
      return false;
    }
  }

  // ── Read methods ──

  async getById(triggerId: string): Promise<ScheduledTriggerRow | null> {
    try {
      const rows = await this.db.query<ScheduledTriggerRow>(
        "SELECT * FROM scheduled_triggers WHERE trigger_id = ?",
        [triggerId],
      );
      return rows[0] ?? null;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] ScheduledTriggerRepository.getById failed: ${msg}`);
      return null;
    }
  }

  async listByWorkflow(workflowId: string): Promise<ScheduledTriggerRow[]> {
    try {
      return await this.db.query<ScheduledTriggerRow>(
        "SELECT * FROM scheduled_triggers WHERE workflow_id = ? ORDER BY gmt_create DESC",
        [workflowId],
      );
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] ScheduledTriggerRepository.listByWorkflow failed: ${msg}`);
      return [];
    }
  }

  async listEnabled(): Promise<ScheduledTriggerRow[]> {
    try {
      return await this.db.query<ScheduledTriggerRow>(
        "SELECT * FROM scheduled_triggers WHERE enabled = 1 ORDER BY next_fire_time ASC",
      );
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] ScheduledTriggerRepository.listEnabled failed: ${msg}`);
      return [];
    }
  }

  async findDueTriggers(now: number): Promise<ScheduledTriggerRow[]> {
    try {
      return await this.db.query<ScheduledTriggerRow>(
        "SELECT * FROM scheduled_triggers WHERE enabled = 1 AND next_fire_time IS NOT NULL AND next_fire_time <= ?",
        [now],
      );
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] ScheduledTriggerRepository.findDueTriggers failed: ${msg}`);
      return [];
    }
  }

  // ── Backward-compatible aliases ──

  /** @deprecated Use CreateScheduledTriggerInput with camelCase and create() */
  async insert(input: {
    trigger_id: string;
    workflow_id: string;
    pack_id: string;
    cron_expression: string;
    timezone?: string;
    params_json?: string | null;
    max_concurrent?: number;
    enabled?: number;
  }): Promise<ScheduledTriggerRow> {
    return this.create({
      triggerId: input.trigger_id,
      workflowId: input.workflow_id,
      packId: input.pack_id,
      cronExpression: input.cron_expression,
      timezone: input.timezone,
      paramsJson: input.params_json,
      maxConcurrent: input.max_concurrent,
      enabled: input.enabled === 0 ? false : true,
    });
  }

  /** @deprecated Use getById() */
  async findByTriggerId(triggerId: string): Promise<ScheduledTriggerRow | null> {
    return this.getById(triggerId);
  }

  /** @deprecated Use listByWorkflow() */
  async findByWorkflowId(workflowId: string): Promise<ScheduledTriggerRow[]> {
    return this.listByWorkflow(workflowId);
  }

  /** @deprecated Use listEnabled() with option filter, or findDueTriggers(now) */
  async listAll(options: { enabled?: number; limit?: number; offset?: number } = {}): Promise<ScheduledTriggerRow[]> {
    const limit = options.limit ?? 100;
    const offset = options.offset ?? 0;
    try {
      if (options.enabled !== undefined) {
        return await this.db.query<ScheduledTriggerRow>(
          "SELECT * FROM scheduled_triggers WHERE enabled = ? ORDER BY gmt_create LIMIT ? OFFSET ?",
          [options.enabled, limit, offset],
        );
      }
      return await this.db.query<ScheduledTriggerRow>(
        "SELECT * FROM scheduled_triggers ORDER BY gmt_create LIMIT ? OFFSET ?",
        [limit, offset],
      );
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] ScheduledTriggerRepository.listAll failed: ${msg}`);
      return [];
    }
  }

  /** @deprecated Use findDueTriggers(now) */
  async findDue(): Promise<ScheduledTriggerRow[]> {
    const now = typeof this.db.dialect.now() === "number"
      ? (this.db.dialect.now() as number)
      : Math.floor(Date.now() / 1000);
    return this.findDueTriggers(now);
  }
}