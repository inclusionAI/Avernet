#!/usr/bin/env bash
# Interleaved MCP calls: results arrive in a different order from starts.
IFS= read -r _first
cat <<'NDJSON'
{"type":"system","subtype":"init","session_id":"cc-mcp-session"}
{"type":"assistant","message":{"content":[{"type":"tool_use","id":"toolu_assign","name":"mcp__mcp_ant_agentclawscs_bcs_mcp__bcs_assign_task","input":{"task_id":"task-1"}},{"type":"tool_use","id":"toolu_send","name":"mcp__mcp_ant_agentclawscs_bcs_mcp__bcs_send_message","input":{"message":"done"}},{"type":"tool_use","id":"toolu_error","name":"mcp__mcp_ant_agentclawscs_bcs_mcp__bcs_assign_task","input":{"task_id":"task-error"}}]}}
{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"toolu_send","content":[{"type":"text","text":"{\"status\":\"stored\",\"intent_id\":\"intent-send\"}"}]},{"type":"tool_result","tool_use_id":"toolu_assign","is_error":false,"content":[{"type":"text","text":"{\"status\":\"stored\",\"intent_id\":\"intent-assign\"}"}]},{"type":"tool_result","tool_use_id":"toolu_error","is_error":true,"content":[{"type":"text","text":"permission denied"}]}]}}
{"type":"result","subtype":"success","result":"finished","session_id":"cc-mcp-session"}
NDJSON
