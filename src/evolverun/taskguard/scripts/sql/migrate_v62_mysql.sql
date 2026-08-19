-- ============================================================================
-- Migration v62: Add P1-P5 enhancement columns to dev_workflow_phases (MySQL/ZDAS)
-- Purpose:
--   Support dev-workflow template enhancement (P1–P5):
--     P1: Each phase can specify which Bot to call (bot_id)
--     P2: Each phase can customize the Prompt sent to the Bot (prompt_template)
--     P3: Each phase can configure approval gates with approver lists
--     P4: Runtime editing of botId and prompt per phase
--     P5: Explicit "advance to next phase" button support
--
--   New columns:
--     bot_id               - Bound Bot ID for phase dispatching
--     prompt_template      - Prompt template with {{variable}} interpolation
--     prompt_variables_json - Declared variables for prompt template
--     approvers_json       - JSON array of approvers [{empId, name, role}]
--     approval_policy      - Approval strategy: any | all | majority
--     prompt_resolved      - Prompt with all variables resolved at runtime
--     gate_position        - When approval fires: pre-bot | post-bot
--     confirmed_by_json    - JSON array tracking who confirmed [{empId, name, confirmedAt}]
--
--   All columns are nullable for backward compatibility.
--   Computed flags (canEdit, canAdvance) are derived server-side, not stored.
-- ============================================================================

ALTER TABLE dev_workflow_phases ADD COLUMN bot_id VARCHAR(128) COMMENT '绑定的Bot ID，用于阶段调度';

ALTER TABLE dev_workflow_phases ADD COLUMN prompt_template TEXT COMMENT 'Prompt模板，支持{{变量}}插值';

ALTER TABLE dev_workflow_phases ADD COLUMN prompt_variables_json TEXT COMMENT 'Prompt变量声明JSON [{name, description, defaultValue}]';

ALTER TABLE dev_workflow_phases ADD COLUMN approvers_json TEXT COMMENT '审批人列表JSON [{empId, name, role}]';

ALTER TABLE dev_workflow_phases ADD COLUMN approval_policy VARCHAR(32) DEFAULT 'any' COMMENT '审批策略 any|all|majority';

ALTER TABLE dev_workflow_phases ADD COLUMN prompt_resolved TEXT COMMENT '运行时解析后的完整Prompt';

ALTER TABLE dev_workflow_phases ADD COLUMN gate_position VARCHAR(16) DEFAULT 'post-bot' COMMENT '审批关卡位置 pre-bot|post-bot';

ALTER TABLE dev_workflow_phases ADD COLUMN confirmed_by_json TEXT COMMENT '已确认人记录JSON [{empId, name, confirmedAt}]';