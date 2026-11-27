-- Product runtime bindings are nullable so historical Template/Todo rows remain legacy.
-- No backfill is required or permitted by this migration.

ALTER TABLE `ac_todo_template_version`
  ADD COLUMN `product_profile_id` bigint(20) DEFAULT NULL
  COMMENT 'Immutable ProductProfile bound to this BotTeam revision';

CREATE INDEX `idx_todo_template_version_product_profile`
  ON `ac_todo_template_version`
  (`avernet_tenant`, `env`, `product_profile_id`);


ALTER TABLE `ac_todo`
  ADD COLUMN `product_collet_id` bigint(20) DEFAULT NULL
  COMMENT 'Product runtime Collet bound to this Todo; nullable for legacy Todos';

CREATE INDEX `idx_todo_product_collet`
  ON `ac_todo`
  (`avernet_tenant`, `env`, `product_collet_id`);
