-- Migration v15: Add approval_cards table for card-web delivery.
-- Target: MySQL / ZDAS (OceanBase)
-- This table is shared between ClawMind (writes on send, polls for resolution)
-- and ClawWeb (reads for display, writes on approve/reject actions).
-- Compliance: id BIGINT, gmt_create/gmt_modified TIMESTAMP with COMMENT,
--             gmt_modified ON UPDATE CURRENT_TIMESTAMP, indexes inline,
--             indexed columns use VARCHAR(255) not TEXT.
-- Note: created_at and resolved_at are BIGINT (unix timestamp) columns,
--       NOT TIMESTAMP — the application stores integer timestamps.

CREATE TABLE IF NOT EXISTS approval_cards (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  flow_id VARCHAR(255) NOT NULL COMMENT '流程实例ID',
  node_id VARCHAR(255) NOT NULL COMMENT '审批节点ID',
  workflow_id VARCHAR(255) NOT NULL COMMENT '工作流ID',
  workflow_title VARCHAR(255) COMMENT '工作流标题',
  approval_type VARCHAR(255) COMMENT '审批类型',
  message TEXT COMMENT '审批消息',
  card_fields_json TEXT COMMENT '审批卡片字段JSON',
  approver_ids TEXT NOT NULL COMMENT '审批人工号列表(逗号分隔)',
  approver_names TEXT COMMENT '审批人姓名列表(逗号分隔)',
  approval_policy VARCHAR(50) NOT NULL DEFAULT 'any' COMMENT '审批策略(any/all/majority)',
  approved_by VARCHAR(4000) NOT NULL DEFAULT '' COMMENT '已同意人工号(逗号分隔)',
  rejected_by VARCHAR(4000) NOT NULL DEFAULT '' COMMENT '已驳回人工号(逗号分隔)',
  status VARCHAR(50) NOT NULL DEFAULT 'pending' COMMENT '审批状态(pending/approved/rejected)',
  delivery_mode VARCHAR(50) NOT NULL DEFAULT 'card-web' COMMENT '投递方式(card-web)',
  created_at BIGINT NOT NULL COMMENT '创建时间(unix时间戳)',
  resolved_at BIGINT COMMENT '审批完成时间(unix时间戳)',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '记录创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '记录修改时间',
  INDEX idx_approval_cards_flow_node (flow_id, node_id),
  INDEX idx_approval_cards_status (status),
  INDEX idx_approval_cards_created (created_at)
);