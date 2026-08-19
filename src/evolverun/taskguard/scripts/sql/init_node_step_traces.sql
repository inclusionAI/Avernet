-- node_step_traces DDL — MySQL / ZDAS
-- Aligned with ClawMind src/db/schema.ts migration v15
-- Compliance: id BIGINT, gmt_create/gmt_modified TIMESTAMP, COMMENT on all columns,
--             gmt_modified ON UPDATE CURRENT_TIMESTAMP, no FLOAT/DOUBLE
--             Indexed columns use VARCHAR(255) not TEXT (OceanBase can't index TEXT)
-- Note: Indexes defined inline within CREATE TABLE to avoid ZDAS parser issues
--       with separate CREATE INDEX statements.
-- Usage: mysql -u <user> -p <database> < init_node_step_traces.sql

CREATE TABLE IF NOT EXISTS node_step_traces (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  flow_id VARCHAR(255) NOT NULL COMMENT '流程实例ID',
  node_id VARCHAR(255) NOT NULL COMMENT '节点ID',
  attempt INTEGER NOT NULL DEFAULT 1 COMMENT '执行次数',
  step_seq INTEGER NOT NULL COMMENT '步骤序号(1-based)',
  step_type VARCHAR(32) NOT NULL COMMENT '步骤类型(tool_call/tool_result/assistant_text)',
  skill_name VARCHAR(255) COMMENT 'skill名称(无skill时为NULL)',
  tool_name VARCHAR(255) COMMENT '工具名称(tool_call/tool_result时有值)',
  tool_use_id VARCHAR(255) COMMENT '工具调用ID(关联tool_call↔tool_result)',
  tool_input_json TEXT COMMENT '工具输入参数JSON(截断到2000字符)',
  tool_output_text TEXT COMMENT '工具输出文本(截断到5000字符)',
  is_error INTEGER NOT NULL DEFAULT 0 COMMENT '是否报错(0正常1报错)',
  text_content TEXT COMMENT 'assistant_text类型输出(截断到5000字符)',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  INDEX idx_nst_flow_node (flow_id, node_id, attempt),
  INDEX idx_nst_flow_id (flow_id),
  INDEX idx_nst_skill_name (skill_name),
  INDEX idx_nst_created (gmt_create)
);