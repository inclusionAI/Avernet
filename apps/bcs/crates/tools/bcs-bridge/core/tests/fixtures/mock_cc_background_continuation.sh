#!/usr/bin/env bash
# Mock Claude Code engine: a turn that launches a background agent, ends with
# `result` #1 ("LAUNCHED") while the task is still pending, then a
# task_notification completes it and the engine ends again with the real
# final `result` #2. Shapes are recorded from a live capture.
IFS= read -r _first
echo '{"type":"system","subtype":"init","session_id":"cc-sess-bg"}'
echo '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"toolu_bg","name":"Task","input":{"run_in_background":true}}]}}'
echo '{"type":"system","subtype":"task_started","task_id":"t-bg","tool_use_id":"toolu_bg","task_type":"local_agent"}'
echo '{"type":"user","message":{"content":[{"type":"tool_use_id":"toolu_bg","type":"tool_result","content":"Async agent launched successfully. agentId: t-bg","is_error":false}]}}'
echo '{"type":"result","subtype":"success","result":"LAUNCHED"}'
sleep 0.3
echo '{"type":"user","message":{"role":"user","content":"<task-notification>\n<task-id>t-bg</task-id>\n<tool-use-id>toolu_bg</tool-use-id>\n<status>completed</status>\n<summary>done</summary>\n</task-notification>"}}'
echo '{"type":"result","subtype":"success","result":"final answer after background agent"}'
sleep 30
