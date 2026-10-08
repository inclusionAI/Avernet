/**
 * Pure chatInject level model — no imports, zero cycle risk.
 *
 * InjectLevel (perf/simple/full) replaces the legacy verbosity + performanceMode
 * knobs. Each inject event carries a minLevel; an event fires only when
 * rank(currentLevel) >= rank(event.minLevel). The "never" sentinel (rank 4)
 * suppresses an event in all three levels (e.g. node-retry).
 *
 * @module inject-level
 */

export type InjectLevel = "perf" | "simple" | "full";

/** Sentinel minLevel for events that must never inject in any level. */
export const LEVEL_NEVER = "never" as const;

export type InjectEvent =
  | "workflow-started"
  | "workflow-completed"
  | "node-started"
  | "node-succeeded"
  | "node-failed"
  | "node-skipped"
  | "node-retry"
  | "agent-progress"
  | "agent-agentEvent"
  | "agent-finalOutput"
  | "parallel-progress";

export const INJECT_LEVEL_RANK: Record<InjectLevel, number> = {
  perf: 1,
  simple: 2,
  full: 3,
};

/** Per-event minimum level. "never" suppresses in all levels. */
export const EVENT_MIN_LEVEL: Record<InjectEvent, InjectLevel | "never"> = {
  "workflow-started": "perf",
  "workflow-completed": "perf",
  "node-started": "simple",
  "node-succeeded": "simple",
  "node-failed": "simple",
  "node-skipped": "full",
  "node-retry": LEVEL_NEVER,
  "agent-progress": "full",
  "agent-agentEvent": "full",
  "agent-finalOutput": "full",
  "parallel-progress": "full",
};

/** True when the current level admits the event (rank comparison). */
export function shouldInject(current: InjectLevel, event: InjectEvent): boolean {
  const minLevel = EVENT_MIN_LEVEL[event];
  if (minLevel === LEVEL_NEVER) return false;
  return INJECT_LEVEL_RANK[current] >= INJECT_LEVEL_RANK[minLevel];
}