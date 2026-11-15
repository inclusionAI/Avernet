-- ClawWeb / ClawMind shared flow_runs, schema v152 (MySQL-compatible).
-- Operator-reviewed provisioning only; never run this from a web request.
-- Select the intended database explicitly before running. No credentials/host defaults.
-- Provision schema before either repository's ClawWeb receiver is deployed.
-- Existing columns are preserved (including explicit NULL and caller labels).
-- Review the preflight/postflight definitions; incompatible existing definitions
-- require an operator-reviewed repair, not a silent metadata overwrite.
SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'flow_runs'
  AND COLUMN_NAME IN ('env', 'tag')
ORDER BY COLUMN_NAME;

SET @flow_run_env_ddl = IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'flow_runs' AND COLUMN_NAME = 'env'),
  'SELECT ''flow_runs.env already exists'' AS migration_status',
  'ALTER TABLE flow_runs ADD COLUMN env VARCHAR(32) NULL DEFAULT ''prod'' COMMENT ''Run environment label; historical default is not provenance'''
);
PREPARE flow_run_env_statement FROM @flow_run_env_ddl;
EXECUTE flow_run_env_statement;
DEALLOCATE PREPARE flow_run_env_statement;

SET @flow_run_tag_ddl = IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'flow_runs' AND COLUMN_NAME = 'tag'),
  'SELECT ''flow_runs.tag already exists'' AS migration_status',
  'ALTER TABLE flow_runs ADD COLUMN tag VARCHAR(255) NULL DEFAULT '''' COMMENT ''Caller-supplied run tag'''
);
PREPARE flow_run_tag_statement FROM @flow_run_tag_ddl;
EXECUTE flow_run_tag_statement;
DEALLOCATE PREPARE flow_run_tag_statement;

-- Expected: env varchar(32), nullable YES, default prod;
--           tag varchar(255), nullable YES, default empty string.
SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'flow_runs'
  AND COLUMN_NAME IN ('env', 'tag')
ORDER BY COLUMN_NAME;
-- Do not set schema_version blindly: MAX(version) gates all earlier migrations.
-- Runtime migrations record v152 only after their prerequisites have completed.
-- No data backfill, filtering index, NOT NULL conversion, or purpose migration.
