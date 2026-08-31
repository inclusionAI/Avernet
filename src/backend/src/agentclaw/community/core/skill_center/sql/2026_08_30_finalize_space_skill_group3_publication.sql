ALTER TABLE ac_skill_publication_attempt
  ADD COLUMN IF NOT EXISTS frozen_draft_locator VARCHAR(1028) NULL
    COMMENT 'immutable Draft Revision locator frozen by this Publication Attempt';

SET @drop_publish_request_index = IF(
  EXISTS(
    SELECT 1 FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA = DATABASE()
       AND TABLE_NAME = 'ac_skill_publication_attempt'
       AND INDEX_NAME = 'uk_publish_request'
  ),
  'ALTER TABLE ac_skill_publication_attempt DROP INDEX uk_publish_request',
  'SELECT 1'
);
PREPARE drop_publish_request_index_stmt FROM @drop_publish_request_index;
EXECUTE drop_publish_request_index_stmt;
DEALLOCATE PREPARE drop_publish_request_index_stmt;

ALTER TABLE ac_skill_publication_attempt
  ADD UNIQUE KEY uk_publish_request (avernet_tenant, env, request_id);
