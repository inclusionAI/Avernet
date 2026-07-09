/**
 * Cron runId builders — kept byte-for-byte identical to openclaw so run
 * records stay readable and cross-engine consistent.
 *
 * Two formats, split by trigger source:
 *   * scheduled (timer tick fires when due) — openclaw src/cron/run-id.ts
 *       cron:${jobId}:${startedAt}
 *   * manual (cron.run RPC / explicit run-now) — openclaw src/cron/service/ops.ts
 *       manual:${jobId}:${startedAt}:${attempt}
 *
 * `startedAt` is the fire marker time (epoch-ms); `attempt` is a process-wide
 * monotonic counter for manual triggers ("Nth manual fire since boot").
 */

export function createCronExecutionId(jobId: string, startedAt: number): string {
  return `cron:${jobId}:${startedAt}`;
}

export function createManualRunId(
  jobId: string,
  startedAt: number,
  attempt: number,
): string {
  return `manual:${jobId}:${startedAt}:${attempt}`;
}
