-- Preserve a bounded JSON snapshot of selected provider fields for imported Todos.
-- The column stays nullable at the DB layer: MySQL 5.x TEXT cannot carry a server
-- DEFAULT, so historical rows keep NULL while application writes always supply
-- the normalized "{}" value.

ALTER TABLE `ac_todo_source_binding`
    ADD COLUMN `source_metadata_json` text NULL
    COMMENT '来源重要字段白名单快照 JSON；历史行为 NULL，应用写入恒为合法 JSON'
    AFTER `source_updated_at`;
