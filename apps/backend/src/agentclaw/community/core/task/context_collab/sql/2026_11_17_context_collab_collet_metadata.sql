-- Add L4-owned description/schema/audit metadata to an existing Collet table.
-- Apply once after the Task and creation_facts cutover, before new app writers.
-- Fresh installations using the current baseline already have this column:
-- do NOT apply this ALTER to those installations.
-- NULL denotes unknown legacy history. Do not infer historical audit from
-- gmt_modified or deployment time; L6 composition can modify the same SQL row.
-- New application writes provide complete metadata atomically with the Collet.

ALTER TABLE `ac_context_collab_collet`
  ADD COLUMN `collet_metadata` mediumtext DEFAULT NULL
  COMMENT 'JSON: L4 schema, description and audit; NULL for unknown legacy history';
