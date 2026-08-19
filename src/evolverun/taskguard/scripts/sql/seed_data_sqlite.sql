-- ClawFlow Seed Data — Sample workflow runs and events for development/testing
-- Compatible with both MySQL and SQLite (uses common SQL subset)
-- Usage:
--   MySQL:  mysql -u <user> -p <database> < seed_data.sql
--   SQLite: sqlite3 engine.db < seed_data.sql

-- ── Sample Flow Run ──
-- A completed workflow run of the "full-demo" workflow

INSERT OR IGNORE INTO flow_runs (
  flow_id, workflow_id, workflow_title, status,
  node_count, succeeded_count, failed_count,
  total_duration_ms, total_token_usage,
  started_at, completed_at, gmt_create, gmt_modified
) VALUES (
  'flow-demo-001', 'full-demo', 'Full Demo Workflow', 'succeeded',
  6, 6, 0,
  4200, 1850,
  1747000000, 1747000004, 1747000000, 1747000004
);

-- ── Sample Node Executions ──

INSERT INTO node_executions (
  flow_id, workflow_id, node_id, executor_type, status, attempt,
  input_json, output_json, error_text, duration_ms, token_usage_json,
  started_at, completed_at, gmt_create
) VALUES (
  'flow-demo-001', 'full-demo', 'start-agent', 'embedded-agent', 'succeeded', 1,
  '{"request":"Process all items"}',
  '{"decision":"proceed","confidence":0.95}',
  NULL, 1200, '{"prompt_tokens":80,"completion_tokens":30,"total_tokens":110}',
  1747000000, 1747000001, 1747000001
);

INSERT INTO node_executions (
  flow_id, workflow_id, node_id, executor_type, status, attempt,
  input_json, output_json, error_text, duration_ms, token_usage_json,
  started_at, completed_at, gmt_create
) VALUES (
  'flow-demo-001', 'full-demo', 'fetch-data', 'action', 'succeeded', 1,
  '{"url":"https://api.example.com/data","method":"GET"}',
  '{"statusCode":200,"body":{"items":[{"id":1,"name":"Item A"},{"id":2,"name":"Item B"}]}}',
  NULL, 350, NULL,
  1747000001, 1747000002, 1747000002
);

INSERT INTO node_executions (
  flow_id, workflow_id, node_id, executor_type, status, attempt,
  input_json, output_json, error_text, duration_ms, token_usage_json,
  started_at, completed_at, gmt_create
) VALUES (
  'flow-demo-001', 'full-demo', 'review-decision', 'human', 'succeeded', 1,
  '{"data":"fetched items"}',
  '{"decision":"approve","reviewer":"admin@example.com"}',
  NULL, 850, NULL,
  1747000002, 1747000002, 1747000002
);

INSERT INTO node_executions (
  flow_id, workflow_id, node_id, executor_type, status, attempt,
  input_json, output_json, error_text, duration_ms, token_usage_json,
  started_at, completed_at, gmt_create
) VALUES (
  'flow-demo-001', 'full-demo', 'process-loop', 'loop-group', 'succeeded', 1,
  '{"items":[{"id":1,"name":"Item A"},{"id":2,"name":"Item B"}]}',
  '{"processed":[{"itemId":1,"result":"success"},{"itemId":2,"result":"success"}]}',
  NULL, 1100, NULL,
  1747000002, 1747000003, 1747000003
);

INSERT INTO node_executions (
  flow_id, workflow_id, node_id, executor_type, status, attempt,
  input_json, output_json, error_text, duration_ms, token_usage_json,
  started_at, completed_at, gmt_create
) VALUES (
  'flow-demo-001', 'full-demo', 'summarize', 'embedded-agent', 'succeeded', 1,
  '{"processedItems":2}',
  '{"summary":"All 2 items processed successfully","totalProcessed":2,"failedCount":0}',
  NULL, 600, '{"prompt_tokens":120,"completion_tokens":45,"total_tokens":165}',
  1747000003, 1747000004, 1747000004
);

INSERT INTO node_executions (
  flow_id, workflow_id, node_id, executor_type, status, attempt,
  input_json, output_json, error_text, duration_ms, token_usage_json,
  started_at, completed_at, gmt_create
) VALUES (
  'flow-demo-001', 'full-demo', 'notify-action', 'action', 'succeeded', 1,
  '{"channel":"email","recipients":["team@example.com"]}',
  '{"notificationId":"notif-123","status":"sent"}',
  NULL, 100, NULL,
  1747000004, 1747000004, 1747000004
);

-- ── Sample Flow Events ──

INSERT INTO flow_events (event_id, flow_id, workflow_id, node_id, event_type, attempt, time, data_json, error_text, gmt_create)
VALUES
  ('evt-001', 'flow-demo-001', 'full-demo', NULL, 'flow_started', NULL, 1747000000, '{"params":{}}', NULL, 1747000000),
  ('evt-002', 'flow-demo-001', 'full-demo', 'start-agent', 'node_started', 1, 1747000000, NULL, NULL, 1747000000),
  ('evt-003', 'flow-demo-001', 'full-demo', 'start-agent', 'node_succeeded', 1, 1747000001, '{"output":{"decision":"proceed"}}', NULL, 1747000001),
  ('evt-004', 'flow-demo-001', 'full-demo', 'fetch-data', 'node_started', 1, 1747000001, NULL, NULL, 1747000001),
  ('evt-005', 'flow-demo-001', 'full-demo', 'fetch-data', 'node_succeeded', 1, 1747000002, NULL, NULL, 1747000002),
  ('evt-006', 'flow-demo-001', 'full-demo', 'review-decision', 'node_started', 1, 1747000002, NULL, NULL, 1747000002),
  ('evt-007', 'flow-demo-001', 'full-demo', 'review-decision', 'node_succeeded', 1, 1747000002, NULL, NULL, 1747000002),
  ('evt-008', 'flow-demo-001', 'full-demo', 'process-loop', 'node_started', 1, 1747000002, NULL, NULL, 1747000002),
  ('evt-009', 'flow-demo-001', 'full-demo', 'process-loop', 'node_succeeded', 1, 1747000003, NULL, NULL, 1747000003),
  ('evt-010', 'flow-demo-001', 'full-demo', 'summarize', 'node_started', 1, 1747000003, NULL, NULL, 1747000003),
  ('evt-011', 'flow-demo-001', 'full-demo', 'summarize', 'node_succeeded', 1, 1747000004, NULL, NULL, 1747000004),
  ('evt-012', 'flow-demo-001', 'full-demo', 'notify-action', 'node_started', 1, 1747000004, NULL, NULL, 1747000004),
  ('evt-013', 'flow-demo-001', 'full-demo', 'notify-action', 'node_succeeded', 1, 1747000004, NULL, NULL, 1747000004),
  ('evt-014', 'flow-demo-001', 'full-demo', NULL, 'flow_succeeded', NULL, 1747000004, NULL, NULL, 1747000004);

-- ── Sample Flow Metrics ──

INSERT INTO flow_metrics (flow_id, workflow_id, node_id, metric_name, metric_value, time, labels_json, gmt_create)
VALUES
  ('flow-demo-001', 'full-demo', 'start-agent', 'token_usage', 110, 1747000001, '{"type":"total_tokens"}', 1747000001),
  ('flow-demo-001', 'full-demo', 'fetch-data', 'duration_ms', 350, 1747000002, '{"type":"action_duration"}', 1747000002),
  ('flow-demo-001', 'full-demo', 'summarize', 'token_usage', 165, 1747000004, '{"type":"total_tokens"}', 1747000004);

-- ── Sample Failed Flow Run (for testing error states) ──

INSERT OR IGNORE INTO flow_runs (
  flow_id, workflow_id, workflow_title, status,
  node_count, succeeded_count, failed_count,
  total_duration_ms, total_token_usage,
  started_at, completed_at, gmt_create, gmt_modified
) VALUES (
  'flow-demo-002', 'full-demo', 'Full Demo Workflow', 'failed',
  3, 2, 1,
  1800, 500,
  1747100000, 1747100002, 1747100000, 1747100002
);

INSERT INTO node_executions (
  flow_id, workflow_id, node_id, executor_type, status, attempt,
  input_json, output_json, error_text, duration_ms, token_usage_json,
  started_at, completed_at, gmt_create
) VALUES (
  'flow-demo-002', 'full-demo', 'start-agent', 'embedded-agent', 'succeeded', 1,
  '{"request":"Process items"}',
  '{"decision":"proceed","confidence":0.7}',
  NULL, 900, '{"prompt_tokens":75,"completion_tokens":25,"total_tokens":100}',
  1747100000, 1747100001, 1747100001
);

INSERT INTO node_executions (
  flow_id, workflow_id, node_id, executor_type, status, attempt,
  input_json, output_json, error_text, duration_ms, token_usage_json,
  started_at, completed_at, gmt_create
) VALUES (
  'flow-demo-002', 'full-demo', 'fetch-data', 'action', 'failed', 3,
  '{"url":"https://api.example.com/data","method":"GET"}',
  NULL,
  'Connection refused after 3 attempts',
  900, NULL,
  1747100001, 1747100002, 1747100002
);

INSERT INTO flow_events (event_id, flow_id, workflow_id, node_id, event_type, attempt, time, data_json, error_text, gmt_create)
VALUES
  ('evt-020', 'flow-demo-002', 'full-demo', NULL, 'flow_started', NULL, 1747100000, '{"params":{}}', NULL, 1747100000),
  ('evt-021', 'flow-demo-002', 'full-demo', 'fetch-data', 'node_failed', 3, 1747100002, NULL, 'Connection refused after 3 attempts', 1747100002),
  ('evt-022', 'flow-demo-002', 'full-demo', NULL, 'flow_failed', NULL, 1747100002, NULL, 'Node fetch-data failed after retries', 1747100002);