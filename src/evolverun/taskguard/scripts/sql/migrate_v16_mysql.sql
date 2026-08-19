-- Migration v16: Add flow_control_slots and flow_control_queue tables for flow control (蓄流).
-- Target: MySQL / ZDAS (OceanBase)
-- These tables support the three-scope flow control system, scoped per OpenClaw instance:
--   - global: instance-wide concurrent workflow limit
--   - workflow: per-workflow concurrent instance limit (within an instance)
--   - executor: per-executor-type concurrent node limit (within an instance)
-- instance_id = OWNER_ID + "_" + BOT_ID from ~/.credentials (e.g. "103892_20260402_mnpvqm6v"),
-- isolating flow control pools across different OpenClaw instances sharing the same DB.
-- Compliance: id BIGINT, gmt_create/gmt_modified TIMESTAMP with COMMENT,
--             gmt_modified ON UPDATE CURRENT_TIMESTAMP, indexes inline,
--             indexed columns use VARCHAR(255) not TEXT.
-- Note: acquired_at, enqueued_at, dispatch_after, expires_at are BIGINT (unix timestamp)
--       columns — the application stores integer timestamps.

CREATE TABLE IF NOT EXISTS flow_control_slots (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  instance_id VARCHAR(255) NOT NULL COMMENT '实例标识(OWNER_ID_BOT_ID，如103892_20260402_mnpvqm6v)',
  scope_key VARCHAR(255) NOT NULL COMMENT '作用域键(如global、workflow:risk-review、executor:baas-call)',
  flow_id VARCHAR(255) NOT NULL COMMENT '流程实例ID',
  node_id VARCHAR(255) COMMENT '节点ID(工作流/全局作用域为NULL，执行器作用域有值)',
  acquired_at BIGINT NOT NULL COMMENT '获取时间(unix时间戳)',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  UNIQUE INDEX uk_fc_slots_instance_scope_flow_node (instance_id, scope_key, flow_id, node_id),
  INDEX idx_fc_slots_instance_scope (instance_id, scope_key)
);

CREATE TABLE IF NOT EXISTS flow_control_queue (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  instance_id VARCHAR(255) NOT NULL COMMENT '实例标识(OWNER_ID_BOT_ID，如103892_20260402_mnpvqm6v)',
  scope_key VARCHAR(255) NOT NULL COMMENT '作用域键(如global、workflow:risk-review、executor:baas-call)',
  flow_id VARCHAR(255) NOT NULL COMMENT '流程实例ID',
  node_id VARCHAR(255) COMMENT '节点ID(工作流/全局作用域为NULL，执行器作用域有值)',
  priority INTEGER NOT NULL DEFAULT 0 COMMENT '优先级(数值越小优先级越高)',
  status VARCHAR(32) NOT NULL DEFAULT 'queued' COMMENT '状态(queued排队中|dispatched已派发|expired已过期)',
  enqueued_at BIGINT NOT NULL COMMENT '入队时间(unix时间戳)',
  dispatch_after BIGINT COMMENT '最早可派发时间(unix时间戳，用于退避调度)',
  expires_at BIGINT COMMENT '过期时间(unix时间戳，队列超时)',
  payload TEXT COMMENT '恢复执行所需的序列化上下文JSON',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  INDEX idx_fc_queue_instance_scope_status (instance_id, scope_key, status, priority, enqueued_at),
  INDEX idx_fc_queue_expires (expires_at, status),
  INDEX idx_fc_queue_instance_flow (instance_id, flow_id)
);