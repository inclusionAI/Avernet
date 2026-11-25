-- One-time MySQL/MariaDB expansion; apply before starting the new BaaS binary.
ALTER TABLE `baas_bot_run`
  ADD COLUMN `error_code` TEXT CHARACTER SET utf8mb4 NULL COMMENT '引擎原始错误码';
