-- Read-only: run in the ClawWeb database.
SELECT MAX(version) AS current_version FROM schema_version;
SELECT version, description FROM schema_version WHERE version >= 120 ORDER BY version;
SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ('ce_stage_skill_implementations', 'ce_stage_extension_runs', 'ce_stage_interactions', 'ce_skill_assets', 'ce_skill_versions', 'ce_stage_developments', 'ce_skill_audit_events', 'ce_skill_events', 'ce_steps', 'workflow_specs', 'run_archive_records')
ORDER BY TABLE_NAME, ORDINAL_POSITION;
