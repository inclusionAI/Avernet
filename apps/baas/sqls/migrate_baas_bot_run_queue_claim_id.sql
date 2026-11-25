-- One-time MySQL/MariaDB expansion; apply before starting the new BaaS binary.
ALTER TABLE `baas_bot_run_queue`
  ADD COLUMN `claim_id` VARCHAR(32) NULL COMMENT '当前队列认领批次';
