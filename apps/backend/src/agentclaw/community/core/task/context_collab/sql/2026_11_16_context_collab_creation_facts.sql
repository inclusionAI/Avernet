-- Replace the non-ACCS ContextSpec storage column with layer-owned facts.
-- Apply after 2026_11_13_context_collab_task.sql. Existing rows must be
-- migrated by the deployment owner: scope comes from context_spec.space and
-- creation_facts contains the remaining creation fields plus product_instance.
-- This migration intentionally does not create a new aggregate object.

ALTER TABLE `ac_context_collab_collet`
  ADD COLUMN `scope` mediumtext DEFAULT NULL COMMENT 'JSON: L4 Collet scope reference',
  ADD COLUMN `creation_facts` mediumtext DEFAULT NULL COMMENT 'JSON: plain creation facts and optional L6 ProductInstance';

UPDATE `ac_context_collab_collet`
SET `scope` = JSON_EXTRACT(`context_spec`, '$.space'),
    `creation_facts` = JSON_REMOVE(`context_spec`, '$.space', '$.principal_ref');

ALTER TABLE `ac_context_collab_collet`
  MODIFY COLUMN `scope` mediumtext NOT NULL,
  MODIFY COLUMN `creation_facts` mediumtext NOT NULL,
  DROP COLUMN `context_spec`;
