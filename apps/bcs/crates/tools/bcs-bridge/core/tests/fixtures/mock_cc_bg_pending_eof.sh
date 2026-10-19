#!/usr/bin/env bash
# Mock Claude Code engine: the turn ends with `result` #1 while a background
# task is still pending, then the engine exits (stdout EOF) without ever
# completing it — the driver must report the pending work, not a success.
IFS= read -r _first
echo '{"type":"system","subtype":"init","session_id":"cc-sess-bg"}'
echo '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"toolu_bg","name":"Task","input":{"run_in_background":true}}]}}'
echo '{"type":"system","subtype":"task_started","task_id":"t-bg","tool_use_id":"toolu_bg","task_type":"local_agent"}'
echo '{"type":"user","message":{"content":[{"type":"tool_use_id":"toolu_bg","type":"tool_result","content":"Async agent launched successfully. agentId: t-bg","is_error":false}]}}'
echo '{"type":"result","subtype":"success","result":"LAUNCHED"}'
sleep 0.3
exit 0