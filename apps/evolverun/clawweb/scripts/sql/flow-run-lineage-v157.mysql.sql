-- ClawWeb / ClawMind shared flow_runs, schema v157 (MySQL / OceanBase compatible).
-- Operator-reviewed provisioning only. Select the intended database explicitly.
-- Never run from a request or startup runtime account; no host/credential defaults.
-- v155/v156 are retired NO-DDL tombstones, NOT available migration IDs.
-- Add each missing column independently. Preserve all existing rows/lineage values.
-- No JSON-derived backfill, index, NOT NULL conversion, or modification of old columns.
-- Already executed the DDL? Run the preflight first; if BOTH columns are compatible,
-- no ALTER is necessary. If only one exists, this script adds only the missing one.
-- An incompatible existing column needs a separate operator-reviewed repair: do not
-- drop/re-add it or blindly change its type/default (that risks existing data).
SELECT COLUMN_NAME, COLUMN_TYPE, CHARACTER_MAXIMUM_LENGTH, IS_NULLABLE,
       COLUMN_DEFAULT, EXTRA, GENERATION_EXPRESSION
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'flow_runs'
  AND COLUMN_NAME IN ('parent_flow_id', 'root_flow_id')
ORDER BY COLUMN_NAME;

SET @flow_run_parent_ddl = IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'flow_runs' AND COLUMN_NAME = 'parent_flow_id'),
  'SELECT ''flow_runs.parent_flow_id already exists; verify compatibility'' AS migration_status',
  'ALTER TABLE flow_runs ADD COLUMN parent_flow_id VARCHAR(255) NULL DEFAULT NULL COMMENT ''Direct parent workflow run ID'''
);
PREPARE flow_run_parent_statement FROM @flow_run_parent_ddl;
EXECUTE flow_run_parent_statement;
DEALLOCATE PREPARE flow_run_parent_statement;

SET @flow_run_root_ddl = IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'flow_runs' AND COLUMN_NAME = 'root_flow_id'),
  'SELECT ''flow_runs.root_flow_id already exists; verify compatibility'' AS migration_status',
  'ALTER TABLE flow_runs ADD COLUMN root_flow_id VARCHAR(255) NULL DEFAULT NULL COMMENT ''Root ancestor workflow run ID'''
);
PREPARE flow_run_root_statement FROM @flow_run_root_ddl;
EXECUTE flow_run_root_statement;
DEALLOCATE PREPARE flow_run_root_statement;

-- Expected for both: varchar(255), nullable YES, SQL NULL default, not generated.
-- Wider existing VARCHAR columns (>=255) are compatible and remain unchanged.
SELECT COLUMN_NAME, COLUMN_TYPE, CHARACTER_MAXIMUM_LENGTH, IS_NULLABLE,
       COLUMN_DEFAULT, EXTRA, GENERATION_EXPRESSION
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'flow_runs'
  AND COLUMN_NAME IN ('parent_flow_id', 'root_flow_id')
ORDER BY COLUMN_NAME;
-- Stop on any unexpected DDL/storage error; do not continue or claim success.
-- Do NOT set schema_version blindly: MAX(version) gates all earlier migrations.
-- After provisioning, a normal DDL-enabled migration startup verifies compatible
-- columns and records v157 only after all earlier migrations have completed.
-- Managed CREATE-denied and external-schema startup ONLY validate columns: they
-- perform no lineage DDL and do not read/write the migration ledger for this guard.
