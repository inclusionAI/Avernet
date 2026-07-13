-- Migration: BotRun 请求队列化改造（阶段一）
--
-- 设计取舍：**不改动热表 baas_bot_run**，新建独立的队列工作项表
-- baas_bot_run_queue。原因：
--   1. 规避对大/热表 baas_bot_run 的在线 DDL（加列+索引）风险；
--   2. 把队列的高频写 churn（claim / heartbeat）与其专用索引隔离到独立表，
--      不增加旧 chat()/查询路径的开销；
--   3. 队列工作项是瞬态的，DONE 后可按 TTL 清理，与持久结果记录解耦。
--
-- baas_bot_run（不动）   = 持久结果记录，GET /runs 读它，PENDING→RUNNING→COMPLETED/FAILED。
-- baas_bot_run_queue（新）= 队列工作项，Worker 专用，与 baas_bot_run 按 run_id 1:1。
--
-- 应用方式：与 sqls/ 下其他迁移一致，手工应用到 OceanBase（无 Alembic）。
-- SQLite 测试环境由 ORM Base.metadata.create_all() 自动建表，无需本文件。
-- 部署顺序：本迁移须在发布新版应用代码之前应用（新代码会读写本表）。

-- ---------------------------------------------------------------------------
-- 队列工作项表
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `baas_bot_run_queue` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `run_id` varchar(128) NOT NULL COMMENT '与 baas_bot_run.run_id 关联（1:1）',
  `bot_id` varchar(128) NOT NULL COMMENT 'Bot ID（<real_bot_id>:<entity_id>）',
  `session_id` varchar(128) DEFAULT NULL COMMENT '会话ID（session 串行锁 key）',
  `status` varchar(32) NOT NULL COMMENT '工作项状态：PENDING/RUNNING/DONE',
  `assigned_worker` varchar(64) DEFAULT NULL COMMENT '认领该工作项的 Worker 标识',
  `last_heartbeat` timestamp NULL DEFAULT NULL COMMENT '请求级心跳时间戳（宕机恢复判活）',
  `meta` text DEFAULT NULL COMMENT '工作项元数据 JSON（含 callback_function、bcn_callback_sent 等）',
  `env` varchar(32) DEFAULT NULL COMMENT '环境（dev/pre/prod）',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_bot_run_queue_run_id` (`run_id`),
  -- 发现/认领：WHERE status='PENDING' AND env=? ... bot_id ... ORDER BY gmt_create
  KEY `idx_q_status_env_bot_created` (`status`, `env`, `bot_id`, `gmt_create`),
  -- 宕机恢复扫描：WHERE status='RUNNING' AND last_heartbeat < ?
  KEY `idx_q_status_heartbeat` (`status`, `last_heartbeat`)
) AUTO_INCREMENT = 1 DEFAULT CHARSET = utf8mb4 COMMENT = 'BotRun 队列工作项';

-- ---------------------------------------------------------------------------
-- Bot QPM 配置表（按 bot 维度设置每分钟请求数上限）
-- 运营/管理端按 bot 维度设置 QPM；Worker 端 BotQpmManager 定期全量刷新缓存。
-- 未配置的 bot 使用代码默认值（60）。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `bot_qpm_config` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `bot_id` varchar(128) NOT NULL COMMENT 'Bot ID',
  `qpm` int(11) NOT NULL DEFAULT 60 COMMENT '每分钟请求数上限',
  `env` varchar(32) DEFAULT NULL COMMENT '环境（dev/pre/prod）',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_bot_qpm_bot_env` (`bot_id`, `env`)
) AUTO_INCREMENT = 1 DEFAULT CHARSET = utf8mb4 COMMENT = 'Bot QPM 限流配置';
