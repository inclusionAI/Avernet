-- OceanBase/MySQL. Applied BY HAND, and BEFORE the code that reads it deploys:
-- there is no migration runner, so code-first writes a column that does not
-- exist and every Bot create fails.
--
-- Additive and nullable: existing rows are ordinary Bots (identity_bot_id
-- IS NULL), so no backfill is required. Application rollback may retain the column.
--
-- Not re-runnable: MySQL has no ADD COLUMN IF NOT EXISTS.
ALTER TABLE ac_bots
    ADD COLUMN identity_bot_id VARCHAR(64) NULL DEFAULT NULL
        COMMENT '继承其 AgentPass 身份的 Bot 的 bot_id；NULL 表示非继承 Bot';

-- The delete guard and the inherited-bot count both filter on this column.
CREATE INDEX idx_ac_bots_identity_bot_id
    ON ac_bots (identity_bot_id);