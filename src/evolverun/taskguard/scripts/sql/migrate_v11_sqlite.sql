-- Migration v11: Add system_context_json to node_executions
-- Records trigger evaluation, hook outcomes, retry context, executor details, and human actions
-- for debugging and analysis purposes.

ALTER TABLE node_executions ADD COLUMN system_context_json TEXT;