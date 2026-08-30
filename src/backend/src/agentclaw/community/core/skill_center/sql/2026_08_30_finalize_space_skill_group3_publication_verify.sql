SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'ac_skill_publication_attempt'
  AND COLUMN_NAME = 'frozen_draft_locator'
  AND COLUMN_TYPE = 'varchar(1028)'
  AND IS_NULLABLE = 'YES';

SELECT INDEX_NAME, GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) AS columns_in_order
FROM information_schema.STATISTICS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'ac_skill_publication_attempt'
  AND INDEX_NAME = 'uk_publish_request'
GROUP BY INDEX_NAME
HAVING columns_in_order = 'avernet_tenant,env,request_id';
