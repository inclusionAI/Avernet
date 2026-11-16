CREATE TABLE IF NOT EXISTS ce_bot_diagnosis_recovery_config (
  id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
  bot_id VARCHAR(128) CHARACTER SET latin1 COLLATE latin1_bin NOT NULL COMMENT 'Bot 标识',
  entity_id VARCHAR(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL COMMENT 'Bot 目录实体标识',
  env VARCHAR(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL COMMENT '目标 Bot 环境',
  diagnosis_enabled BIGINT NOT NULL DEFAULT 0 COMMENT '诊断开关',
  recovery_enabled BIGINT NOT NULL DEFAULT 0 COMMENT '自愈开关',
  config_json TEXT NOT NULL COMMENT '通知等可扩展配置',
  version BIGINT NOT NULL DEFAULT 1 COMMENT '乐观锁版本',
  updated_by VARCHAR(190) DEFAULT NULL COMMENT '最近修改人',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  PRIMARY KEY (id),
  UNIQUE KEY uk_bot_diagnosis_recovery_target (bot_id, entity_id, env)
) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='Bot 诊断自愈配置';
