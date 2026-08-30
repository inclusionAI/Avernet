-- Phase 2 Group 3: one Idempotency-Key identifies one Publication intent in
-- a tenant/environment, including cross-Skill misuse.

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
