-- Read-only checks. Run against each intended ODC environment AND through
-- the ClawWeb service connection (during integration); compare the results.
-- Reuse the existing ClawWeb datasource; these checks validate new table rollout.
SELECT DATABASE() AS current_database;

SELECT TABLE_NAME
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME IN (
    'insight_failure_task',
    'insight_monitoring_diagnoses',
    'insight_monitoring_bot_checks'
  )
ORDER BY TABLE_NAME;

SHOW CREATE TABLE `insight_monitoring_diagnoses`;
SHOW CREATE TABLE `insight_monitoring_bot_checks`;

SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE,
       COLUMN_DEFAULT, CHARACTER_SET_NAME, COLLATION_NAME, EXTRA
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME IN ('insight_monitoring_diagnoses', 'insight_monitoring_bot_checks')
ORDER BY TABLE_NAME, ORDINAL_POSITION;

SELECT TABLE_NAME, INDEX_NAME, NON_UNIQUE, SEQ_IN_INDEX, COLUMN_NAME
FROM information_schema.STATISTICS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME IN ('insight_monitoring_diagnoses', 'insight_monitoring_bot_checks')
ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX;

-- Execute after the HTTP receiver exists and a mock event has been reported.
-- Empty results before the first report are normal, but do not prove write access.
SELECT event_id, bot_id, decision, human_intervention, occurred_at_ms, received_at_ms
FROM `insight_monitoring_diagnoses`
ORDER BY id DESC LIMIT 10;

SELECT bot_id, engine, status, checked_at_ms, last_successful_check_at_ms
FROM `insight_monitoring_bot_checks`
ORDER BY bot_id LIMIT 50;
