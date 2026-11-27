-- Persist scheduling hints consumed by the external Dima auto-import Agent.

ALTER TABLE `ac_todo_source_import_rule`
  ADD COLUMN `scheduler_bot_id` varchar(256) NULL
    COMMENT '创建者名下用于执行定时自动化的 Bot 标识；历史规则可为空'
    AFTER `bot_id`,
  ADD COLUMN `trigger_interval_seconds` int NOT NULL DEFAULT 1800
    COMMENT '自动化触发间隔秒数'
    AFTER `scheduler_bot_id`,
  ADD COLUMN `cron_expression` varchar(128) NOT NULL DEFAULT '*/30 * * * *'
    COMMENT '外部 Agent 使用的五段 Cron 调度表达式'
    AFTER `trigger_interval_seconds`;
