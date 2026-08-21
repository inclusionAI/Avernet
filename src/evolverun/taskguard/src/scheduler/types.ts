/**
 * Scheduler type definitions for cron-based workflow triggers.
 */

/** Database row shape for the `scheduled_triggers` table. */
export type ScheduledTrigger = {
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
  gmt_modified: number | null;
};

/** Input for creating a new trigger (trigger_id and timestamps are computed). */
export type CreateTriggerInput = {
  workflowId: string;
  packId: string;
  cronExpression: string;
  timezone?: string;
  paramsJson?: string | null;
  maxConcurrent?: number;
};

/** Fields that can be updated on an existing trigger. */
export type UpdateTriggerInput = {
  cron_expression?: string;
  timezone?: string;
  params_json?: string | null;
  max_concurrent?: number;
};

/** Error thrown for invalid cron expressions or timezones. */
export class SchedulerValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "SchedulerValidationError";
  }
}

/** General scheduler error (duplicate trigger, not found, DB unavailable, etc.). */
export class SchedulerError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "SchedulerError";
  }
}