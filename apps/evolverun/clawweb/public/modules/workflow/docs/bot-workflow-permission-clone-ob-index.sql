-- OceanBase, MySQL-compatible tenant: exact Bot permission uniqueness.
-- Apply manually once, BEFORE deploying the atomic Clone merge implementation.
-- First follow the preflight in bot-workflow-permission-clone.md:
--   * Inspect SHOW CREATE TABLE and SHOW INDEX on the actual target database.
--   * No duplicate non-NULL (bot_id, bot_owner_id, workflow_id, env) tuples.
--   * No equivalent complete unique index already exists under another name.
--   * uk_bot_wf_perm is not already present (especially as a prefix index).
--   * Confirm full-column key length and partition/index support for this tenant.
-- Do not replace full columns with prefix indexes or use INSERT IGNORE to hide errors.
-- This script does not delete data, shorten columns, or drop existing indexes.

ALTER TABLE `bot_workflow_permissions`
  ADD UNIQUE INDEX `uk_bot_wf_perm`
  (`bot_id`, `bot_owner_id`, `workflow_id`, `env`);

-- Verify Non_unique = 0, all four complete columns (Sub_part IS NULL),
-- and the index is available before enabling Clone writes.
SHOW INDEX FROM `bot_workflow_permissions`
  WHERE `Key_name` = 'uk_bot_wf_perm';
