ALTER TABLE bcs_group_contexts
    ADD COLUMN owners_json JSON NULL;

UPDATE bcs_group_contexts
SET owners_json = JSON_OBJECT('userIds', JSON_ARRAY(creator))
WHERE owners_json IS NULL;

ALTER TABLE bcs_group_contexts
    MODIFY COLUMN owners_json JSON NOT NULL;
