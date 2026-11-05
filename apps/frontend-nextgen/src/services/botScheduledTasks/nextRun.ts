import { parseExpression } from 'cron-parser';
import type { ManagedRoutine } from './types';

export type RoutineSchedule = Pick<ManagedRoutine, 'cron' | 'timezone' | 'enabled'>;

/** A local schedule estimate, never an engine execution guarantee. */
export function estimateNextRun(task: RoutineSchedule, now: number): string | undefined {
  if (!task.enabled || !task.timezone || task.cron.trim().split(/\s+/).length !== 5) return undefined;
  try {
    // Reject missing/invalid zones rather than implicitly using the browser's zone.
    new Intl.DateTimeFormat('zh-CN', { timeZone: task.timezone }).format(now);
    return parseExpression(task.cron, { currentDate: new Date(now), tz: task.timezone })
      .next()
      .toISOString();
  } catch {
    return undefined;
  }
}
