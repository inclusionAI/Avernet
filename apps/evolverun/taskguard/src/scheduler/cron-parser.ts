/**
 * Cron expression parsing and time computation.
 *
 * Wraps the `cron-parser` library to provide:
 * - 5-field cron expression validation (rejects 6-field with seconds)
 * - Next/previous fire time computation with timezone support
 * - IANA timezone validation
 */
import { CronExpressionParser } from "cron-parser";
import { SchedulerValidationError } from "./types.js";

// ── Types ──

export type CronExpression = ReturnType<typeof CronExpressionParser.parse>;

export type ValidationResult = {
  valid: boolean;
  error?: string;
};

// ── Core Functions ──

/**
 * Parse a 5-field cron expression into a CronExpression object.
 * Rejects 6-field expressions (with seconds field).
 */
export function parseCronExpression(expr: string, tz?: string): CronExpression {
  const fields = expr.trim().split(/\s+/);
  if (fields.length !== 5) {
    throw new SchedulerValidationError(
      `Invalid cron expression: '${expr}'. Only 5-field format is supported (minute hour day-of-month month day-of-week), got ${fields.length} fields.`,
    );
  }

  try {
    return CronExpressionParser.parse(expr, {
      currentDate: new Date(),
      ...(tz ? { tz } : {}),
    });
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error);
    throw new SchedulerValidationError(`Invalid cron expression: '${expr}'. ${msg}`);
  }
}

/**
 * Compute the next fire time for a cron expression after a given time.
 * Returns epoch milliseconds.
 */
export function computeNextFireTime(expr: string, tz: string, after?: Date): number {
  const interval = CronExpressionParser.parse(expr, {
    currentDate: after ?? new Date(),
    tz,
  });
  const next = interval.next();
  return next.getTime();
}

/**
 * Compute the previous fire time for a cron expression before a given time.
 * Returns epoch milliseconds. Used for missed-fire recovery.
 */
export function computePrevFireTime(expr: string, tz: string, before?: Date): number {
  const interval = CronExpressionParser.parse(expr, {
    currentDate: before ?? new Date(),
    tz,
  });
  const prev = interval.prev();
  return prev.getTime();
}

/**
 * Validate a cron expression. Returns a result object with descriptive error messages.
 */
export function validateCronExpression(expr: string): ValidationResult {
  const fields = expr.trim().split(/\s+/);
  if (fields.length !== 5) {
    return {
      valid: false,
      error: `Only 5-field cron expressions are supported (minute hour day-of-month month day-of-week), got ${fields.length} fields: '${expr}'`,
    };
  }

  try {
    CronExpressionParser.parse(expr);
    return { valid: true };
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error);
    return { valid: false, error: msg };
  }
}

/**
 * Validate an IANA timezone string.
 */
export function validateTimezone(tz: string): boolean {
  try {
    // Use Intl API to validate timezone — works in Node.js 14+
    Intl.DateTimeFormat(undefined, { timeZone: tz });
    return true;
  } catch {
    return false;
  }
}

/**
 * Get all missed fire times between lastFireTime and now for a given cron expression.
 * Returns an array of epoch milliseconds in chronological order.
 * If lastFireTime is null (never fired), returns all matches from the epoch start.
 * Caps at a maximum of 100 missed fires to prevent runaway computation.
 */
export function getMissedFireTimes(
  expr: string,
  tz: string,
  lastFireTime: number | null,
  now: number,
  maxMissed: number = 100,
): number[] {
  const missed: number[] = [];

  try {
    const startDate = lastFireTime ? new Date(lastFireTime + 1) : new Date(0);
    const interval = CronExpressionParser.parse(expr, { currentDate: startDate, tz });

    let next = interval.next();
    while (next.getTime() <= now && missed.length < maxMissed) {
      missed.push(next.getTime());
      try {
        next = interval.next();
      } catch {
        break;
      }
    }
  } catch {
    // If parsing fails, return empty — this should have been caught at creation time
  }

  return missed;
}