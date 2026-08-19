-- ============================================================================
-- Migration v62: Add P1-P5 enhancement columns to dev_workflow_phases (SQLite)
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
--
-- Note: SQLite ALTER TABLE ADD COLUMN does not support COMMENT clauses.
--       DEFAULT values must be literals (no function calls).
-- ============================================================================

ALTER TABLE dev_workflow_phases ADD COLUMN bot_id VARCHAR(128);

ALTER TABLE dev_workflow_phases ADD COLUMN prompt_template TEXT;

ALTER TABLE dev_workflow_phases ADD COLUMN prompt_variables_json TEXT;

ALTER TABLE dev_workflow_phases ADD COLUMN approvers_json TEXT;

ALTER TABLE dev_workflow_phases ADD COLUMN approval_policy VARCHAR(32) DEFAULT 'any';

ALTER TABLE dev_workflow_phases ADD COLUMN prompt_resolved TEXT;

ALTER TABLE dev_workflow_phases ADD COLUMN gate_position VARCHAR(16) DEFAULT 'post-bot';

ALTER TABLE dev_workflow_phases ADD COLUMN confirmed_by_json TEXT;