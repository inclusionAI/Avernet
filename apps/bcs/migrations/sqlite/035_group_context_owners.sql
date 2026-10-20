ALTER TABLE bcs_group_contexts
    ADD COLUMN owners_json TEXT NOT NULL DEFAULT '{"userIds":[]}';

UPDATE bcs_group_contexts
SET owners_json = json_object('userIds', json_array(creator))
WHERE owners_json IS NULL OR owners_json = '{"userIds":[]}';
