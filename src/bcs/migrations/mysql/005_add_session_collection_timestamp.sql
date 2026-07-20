-- Session collection event time: per-participant `collected_at` timestamp.
-- Populated when a row transitions collected 0 -> 1 (first collect); cleared on
-- uncollect so a re-collect records a fresh event. Used to order the collected
-- session list by collect event (newest first), with COALESCE fallback to the
-- session's gmt_create for rows collected before this migration ran.
ALTER TABLE `bcs_session_participants`
  ADD COLUMN `collected_at` datetime(3) NULL DEFAULT NULL;

-- Covers the collected-list query shape: filter by (env, group_id, bot_uuid,
-- collected = 1) then ORDER BY collected_at. `collected_at` trailing so the
-- index can also satisfy the sort. The narrower idx_collected from migration
-- 004 is now a redundant prefix of this index; left in place rather than
-- dropped here, because DROP INDEX IF EXISTS isn't standard MySQL/OceanBase
-- and connection-pooled multi-statement DDL is fragile.
CREATE INDEX `idx_collected_at`
  ON `bcs_session_participants` (`env`, `group_id`, `bot_uuid`, `collected`, `collected_at`);