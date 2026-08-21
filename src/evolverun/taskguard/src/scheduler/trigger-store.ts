/**
 * ScheduledTriggerRepository — persists and queries scheduled triggers.
 *
 * Manages the `scheduled_triggers` table: CRUD, enable/disable,
 * due-trigger queries for the poll loop, and fire-time updates.
 */
import type { IDatabase } from "../db/types.js";
import type { CreateTriggerInput, ScheduledTrigger, UpdateTriggerInput } from "./types.js";
import { SchedulerError } from "./types.js";
import { computeNextFireTime } from "./cron-parser.js";

// ── Helpers ──

/** Generate a trigger ID: `trig_` + 8 hex chars from crypto randomness. */
export function generateTriggerId(): string {
  const bytes = new Uint8Array(4);
  try {
    const crypto = globalThis.crypto;
    crypto.getRandomValues(bytes);
  } catch {
    // Fallback for environments without crypto.getRandomValues
    for (let i = 0; i < 4; i++) bytes[i] = Math.floor(Math.random() * 256);
  }
  return `trig_${Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("")}`;
}

// ── Repository ──

export class ScheduledTriggerRepository {
  constructor(private db: IDatabase) {}

  /**
   * Create a new trigger. Generates trigger_id and sets initial timestamps.
   * Rejects duplicate trigger_id with SchedulerError.
   */
  async create(input: CreateTriggerInput): Promise<ScheduledTrigger> {
    const triggerId = generateTriggerId();
    const now = Math.floor(Date.now() / 1000);
    const nextFireTime = computeNextFireTime(input.cronExpression, input.timezone ?? "UTC");

    try {
      await this.db.exec(
        `INSERT INTO scheduled_triggers
          (trigger_id, workflow_id, pack_id, cron_expression, timezone,
           params_json, max_concurrent, enabled, last_fire_time, next_fire_time,
           gmt_create, gmt_modified)
         VALUES (?, ?, ?, ?, ?, ?, ?, 1, NULL, ?, ?, ?)`,
        [
          triggerId,
          input.workflowId,
          input.packId,
          input.cronExpression,
          input.timezone ?? "UTC",
          input.paramsJson ?? null,
          input.maxConcurrent ?? 1,
          nextFireTime,
          now,
          now,
        ],
      );

      const row = await this.getById(triggerId);
      if (!row) throw new SchedulerError(`Trigger ${triggerId} not found after insert`);
      return row;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      if (msg.includes("UNIQUE") || msg.includes("unique") || msg.includes("duplicate")) {
        throw new SchedulerError(`Trigger with ID '${triggerId}' already exists`);
      }
      throw new SchedulerError(`Failed to create trigger: ${msg}`);
    }
  }

  /** Get a trigger by trigger_id. Returns null if not found. */
  async getById(triggerId: string): Promise<ScheduledTrigger | null> {
    const rows = await this.db.query<ScheduledTrigger>(
      `SELECT * FROM scheduled_triggers WHERE trigger_id = ?`,
      [triggerId],
    );
    return rows[0] ?? null;
  }

  /** List all triggers for a given workflow. */
  async listByWorkflow(workflowId: string): Promise<ScheduledTrigger[]> {
    return this.db.query<ScheduledTrigger>(
      `SELECT * FROM scheduled_triggers WHERE workflow_id = ? ORDER BY gmt_create ASC`,
      [workflowId],
    );
  }

  /** List all enabled triggers. */
  async listEnabled(): Promise<ScheduledTrigger[]> {
    return this.db.query<ScheduledTrigger>(
      `SELECT * FROM scheduled_triggers WHERE enabled = 1 ORDER BY next_fire_time ASC`,
    );
  }

  /** Find triggers that are due to fire (enabled and next_fire_time <= now). */
  async findDueTriggers(now: number): Promise<ScheduledTrigger[]> {
    return this.db.query<ScheduledTrigger>(
      `SELECT * FROM scheduled_triggers
       WHERE enabled = 1 AND next_fire_time IS NOT NULL AND next_fire_time <= ?
       ORDER BY next_fire_time ASC`,
      [now],
    );
  }

  /**
   * Update mutable fields on a trigger.
   * Recomputes next_fire_time if cron_expression or timezone changes.
   */
  async update(triggerId: string, input: UpdateTriggerInput): Promise<ScheduledTrigger | null> {
    const existing = await this.getById(triggerId);
    if (!existing) return null;

    const cronExpression = input.cron_expression ?? existing.cron_expression;
    const timezone = input.timezone ?? existing.timezone;

    const now = Math.floor(Date.now() / 1000);
    const needsRecompute =
      input.cron_expression !== undefined || input.timezone !== undefined;

    const nextFireTime = needsRecompute
      ? computeNextFireTime(cronExpression, timezone)
      : existing.next_fire_time;

    await this.db.exec(
      `UPDATE scheduled_triggers
       SET cron_expression = ?, timezone = ?, params_json = ?,
           max_concurrent = ?, next_fire_time = ?, gmt_modified = ?
       WHERE trigger_id = ?`,
      [
        cronExpression,
        timezone,
        input.params_json !== undefined ? input.params_json : existing.params_json,
        input.max_concurrent !== undefined ? input.max_concurrent : existing.max_concurrent,
        nextFireTime,
        now,
        triggerId,
      ],
    );

    return this.getById(triggerId);
  }

  /**
   * Update last_fire_time and next_fire_time after a successful fire.
   * Uses epoch milliseconds for fire times.
   */
  async updateFireTimes(
    triggerId: string,
    lastFireTime: number,
    nextFireTime: number,
  ): Promise<void> {
    const now = Math.floor(Date.now() / 1000);
    await this.db.exec(
      `UPDATE scheduled_triggers
       SET last_fire_time = ?, next_fire_time = ?, gmt_modified = ?
       WHERE trigger_id = ?`,
      [lastFireTime, nextFireTime, now, triggerId],
    );
  }

  /**
   * Enable a trigger and recompute its next_fire_time.
   * No-op if already enabled.
   */
  async enable(triggerId: string): Promise<ScheduledTrigger | null> {
    const existing = await this.getById(triggerId);
    if (!existing) return null;
    if (existing.enabled === 1) return existing;

    const nextFireTime = computeNextFireTime(existing.cron_expression, existing.timezone);
    const now = Math.floor(Date.now() / 1000);

    await this.db.exec(
      `UPDATE scheduled_triggers SET enabled = 1, next_fire_time = ?, gmt_modified = ? WHERE trigger_id = ?`,
      [nextFireTime, now, triggerId],
    );

    return this.getById(triggerId);
  }

  /** Disable a trigger. No-op if already disabled. */
  async disable(triggerId: string): Promise<ScheduledTrigger | null> {
    const existing = await this.getById(triggerId);
    if (!existing) return null;
    if (existing.enabled === 0) return existing;

    const now = Math.floor(Date.now() / 1000);
    await this.db.exec(
      `UPDATE scheduled_triggers SET enabled = 0, gmt_modified = ? WHERE trigger_id = ?`,
      [now, triggerId],
    );

    return this.getById(triggerId);
  }

  /** Delete a trigger by trigger_id. Returns true if deleted. */
  async delete(triggerId: string): Promise<boolean> {
    const result = await this.db.exec(
      `DELETE FROM scheduled_triggers WHERE trigger_id = ?`,
      [triggerId],
    );
    return result.affectedRows > 0;
  }

  /**
   * Count currently running flows for a workflow.
   * Used by the scheduler's concurrency check.
   */
  async countRunningFlows(workflowId: string): Promise<number> {
    const rows = await this.db.query<{ count: number }>(
      `SELECT COUNT(*) as count FROM flow_runs
       WHERE workflow_id = ? AND status IN ('running', 'blocked')`,
      [workflowId],
    );
    return rows[0]?.count ?? 0;
  }
}