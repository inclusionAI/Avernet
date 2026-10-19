#!/usr/bin/env bash
# A task_notification WITHOUT a tool_use_id (recorded pre-0.3.269 SDK shape);
# the completion must still release the wait via the task_id -> tool_use_id
# correlation that task_started established.
IFS= read -r _first
echo '{"type":"system","subtype":"init","session_id":"cc-sess-bg"}'
echo '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"toolu_leg","name":"Task","input":{"run_in_background":true}}]}}'
echo '{"type":"system","subtype":"task_started","task_id":"t-legacy","tool_use_id":"toolu_leg","task_type":"local_agent"}'
echo '{"type":"user","message":{"content":[{"type":"tool_use_id":"toolu_leg","type":"tool_result","content":"Command running in background with ID: t-legacy. You will be notified when it completes.","is_error":false}]}}'
echo '{"type":"result","subtype":"success","result":"PARTIAL"}'
sleep 0.2
echo '{"type":"user","message":{"role":"user","content":"<task-notification>\n<task-id>t-legacy</task-id>\n<status>completed</status>\n</task-notification>"}}'
echo '{"type":"result","subtype":"success","result":"released via task_id"}'
sleep 8