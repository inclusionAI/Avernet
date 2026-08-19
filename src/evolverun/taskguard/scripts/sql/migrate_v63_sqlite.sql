-- ============================================================================
-- Migration v63: Migrate flow_runs.status from "completed" to "succeeded" (SQLite)
-- Purpose:
--   Unify flow_runs.status values. The legacy TaskFlow backend wrote
--   "completed" as a terminal status. All ClawMind code now uses "succeeded".
--   This migration normalises existing rows so they match the new convention.
--
--   The isTerminalFlowStatus() helper in db/repositories/types.ts retains
--   "completed" in its set for backward compatibility during the transition.
-- ============================================================================

UPDATE flow_runs SET status = 'succeeded' WHERE status = 'completed';