-- REVIEW ONLY: NOT executed. Existing-table upgrade, OceanBase MySQL mode.
-- Check SHOW CREATE TABLE first; apply only if online_enabled is absent.
-- Apply BEFORE deploying code using production selection. Do not copy enabled:
-- existing service Bots have no trusted publication snapshot until republished.
-- Personal Bots continue to use enabled and do not depend on this initial value.
ALTER TABLE `ac_bot_knowledge_binding`
  ADD COLUMN `online_enabled` boolean NOT NULL DEFAULT FALSE
    COMMENT '服务Bot生产态是否启用该知识库' AFTER `enabled`;
-- No automatic rollback or column drop. Rolling back application code does not
-- require removing this additive column. No deployment DDL is run at startup.
