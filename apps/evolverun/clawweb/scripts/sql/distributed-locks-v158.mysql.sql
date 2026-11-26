-- ClawWeb shared database lease locks, schema v158.
-- Operator-reviewed provisioning only; select the intended database explicitly.
-- Apply before deploying lock consumers. Never run from a request handler.
-- Existing tables are preserved by IF NOT EXISTS; incompatible definitions must
-- be reviewed, not dropped or silently repaired. Runtime validates id AUTO_INCREMENT
-- and the full unique lock_id index. No task or Workflow tables are modified.

CREATE TABLE IF NOT EXISTS clawweb_distributed_locks (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '自增主键ID',
  lock_id VARCHAR(64) NOT NULL COMMENT '环境场景租户资源四元组的SHA256',
  environment VARCHAR(16) NOT NULL COMMENT '控制面环境',
  scene VARCHAR(64) NOT NULL COMMENT '加锁业务场景',
  tenant_id VARCHAR(128) NOT NULL COMMENT '租户标识，全局场景使用__global__',
  resource_key VARCHAR(256) NOT NULL COMMENT '场景内业务资源标识',
  owner_token VARCHAR(64) DEFAULT NULL COMMENT '本次持有者随机令牌',
  lease_ms BIGINT NOT NULL DEFAULT 0 COMMENT '当前持有者的租期毫秒',
  expires_at_ms BIGINT NOT NULL DEFAULT 0 COMMENT '数据库时钟下的租约截止时间',
  next_acquire_at_ms BIGINT NOT NULL DEFAULT 0 COMMENT '数据库时钟下最早再次获取时间',
  version BIGINT NOT NULL DEFAULT 0 COMMENT '每次成功写入递增的版本',
  created_at_ms BIGINT NOT NULL COMMENT '数据库创建时间毫秒',
  updated_at_ms BIGINT NOT NULL COMMENT '数据库最后修改时间毫秒',
  UNIQUE INDEX uk_clawweb_distributed_lock (lock_id)
) COMMENT='ClawWeb公共分布式租约锁' ENGINE=InnoDB;

-- Inspect the result; id must be an auto-increment primary key and lock_id must
-- have its own full-column unique index (no prefix). Preserve existing rows.
SHOW CREATE TABLE clawweb_distributed_locks;
SHOW INDEX FROM clawweb_distributed_locks;
-- Do not advance schema_version manually; the normal migration registry records
-- v158 only after its prerequisites and this schema validation have succeeded.
