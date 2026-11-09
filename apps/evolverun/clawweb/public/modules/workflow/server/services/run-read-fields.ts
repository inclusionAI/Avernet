import type { InterventionAction } from "./intervention-message-builder.js";

/**
 * Fix total_duration_ms values that were inflated 1000x by a bug in
 * ClawMind's computeDurationMs() (which multiplied an already-ms difference by 1000).
 * The bug was: (now() - start) * 1000 where both now() and start are in milliseconds.
 * Detection heuristic: if total_duration_ms > (completed_at - started_at) * 1000 * 2
 * (i.e. more than 2x the wall-clock span in microseconds), divide by 1000.
 */
export function fixDurationMs<T extends { started_at: number; completed_at: number | null; total_duration_ms: number | null }>(row: T): T {
  if (row.total_duration_ms == null || row.total_duration_ms === 0) return row
  if (row.completed_at == null || row.started_at == null) return row
  // started_at and completed_at are Unix seconds; wall-clock span in ms:
  const wallClockMs = (row.completed_at - row.started_at) * 1000
  if (wallClockMs <= 0) return row
  // If duration is > 2x the wall-clock span, it was inflated by the bug
  if (row.total_duration_ms > wallClockMs * 2) {
    return { ...row, total_duration_ms: Math.round(row.total_duration_ms / 1000) }
  }
  return row
}

/** Flow statuses that allow human intervention */
export const INTERVENABLE_STATUSES = new Set(["failed", "blocked", "waiting"]);

/** Valid intervention actions */
export const VALID_ACTIONS = new Set<InterventionAction>(["retry", "skip", "revise", "confirm"]);

/**
 * Compute which intervention actions are available for a flow run
 * based on its status and the credentials/session info it carries.
 */
export function computeAvailableInterventions(run: {
  status: string;
  origin_bot_id: string | null;
  origin_session_key: string | null;
}): InterventionAction[] {
  if (!INTERVENABLE_STATUSES.has(run.status)) return [];
  // Must have BaaS routing info to send intervention messages
  if (!run.origin_bot_id || !run.origin_session_key) return [];

  const actions: InterventionAction[] = [];
  if (run.status === "failed" || run.status === "blocked") {
    actions.push("retry", "skip", "revise");
  }
  if (run.status === "waiting") {
    actions.push("confirm", "skip");
  }
  return actions;
}

/**
 * Strip credentials and internal association markers from API responses.
 * Adds derived safe fields instead of exposing raw credentials.
 */
export function toSafeRunFields(run: Record<string, unknown>): Record<string, unknown> {
  const { credentials_json, ordinary_run_marker: _ordinaryMarker, parent_run_marker: _parentMarker,
    child_run_marker: _childMarker, ...safe } = run;
  return {
    ...safe,
    ...("credentials_json" in run ? { hasCredentials: credentials_json != null && credentials_json !== "" } : {}),
  };
}
