-- ============================================================================
-- Migration v33: Add cm_app_config table (MySQL/ZDAS)
-- Purpose:
--   Store non-bootstrap ClawMind application configuration as YAML fragments.
--   The local application.yaml is loaded first, then DB config overrides
--   matching sections in memory (files are never modified).
--
--   Each row stores one top-level YAML config section:
--     config_key   - top-level key name (e.g. "execution", "teclaw", "git")
--     config_yaml  - YAML fragment text for that section (no top-level key, no indent)
--     version      - optimistic lock version, incremented on each update
--     enabled      - 0=disabled, 1=enabled (only enabled rows are loaded)
--     updated_by   - user who last modified this config (clawweb UI)
--
--   Priority: env vars > cm_app_config DB > application.yaml > built-in defaults
-- ============================================================================

CREATE TABLE IF NOT EXISTS cm_app_config (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  config_key VARCHAR(64) NOT NULL COMMENT '配置段标识(对应YAML顶层键名,如execution/teclaw/git)',
  config_yaml MEDIUMTEXT NOT NULL COMMENT '该配置段的YAML原文(段内容,不含顶层key名)',
  version INTEGER NOT NULL DEFAULT 1 COMMENT '乐观锁版本号,每次更新+1',
  enabled INTEGER NOT NULL DEFAULT 1 COMMENT '是否启用(0禁用1启用)',
  description VARCHAR(512) COMMENT '配置段描述',
  updated_by VARCHAR(255) COMMENT '最后更新人(clawweb管理界面操作人)',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  UNIQUE INDEX uk_cm_app_config_key (config_key),
  INDEX idx_cm_app_config_enabled (enabled)
);
