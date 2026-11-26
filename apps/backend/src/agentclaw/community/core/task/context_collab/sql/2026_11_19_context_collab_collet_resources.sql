-- Add L4-owned Collet resource references; these are not resource Grants.
-- Apply once to existing tables before deploying the new runtime.
-- Fresh installations using the current baseline must skip this ALTER.
-- NULL preserves empty resources for legacy rows; no event-based backfill.

ALTER TABLE `ac_context_collab_collet`
  ADD COLUMN `resources` mediumtext DEFAULT NULL
  COMMENT 'JSON: L4 ResourceBinding list; NULL means empty legacy resources';
