-- Preserve a bounded JSON snapshot of selected provider fields for imported Todos.
-- The nullable-add/backfill/not-null sequence keeps existing source bindings valid.

ALTER TABLE `ac_todo_source_binding`
    ADD COLUMN IF NOT EXISTS `source_metadata_json` text NULL
    COMMENT '来源重要字段白名单快照 JSON'
    AFTER `source_updated_at`;

UPDATE `ac_todo_source_binding`
SET `source_metadata_json` = '{}'
WHERE `source_metadata_json` IS NULL;

ALTER TABLE `ac_todo_source_binding`
    MODIFY COLUMN `source_metadata_json` text NOT NULL
    COMMENT '来源重要字段白名单快照 JSON';
