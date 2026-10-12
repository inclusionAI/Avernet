#!/usr/bin/env bash
IFS= read -r _first
cat <<'JSON'
{"type":"assistant","message":{"content":[{"type":"tool_use","id":"toolu_wrapped","name":"mcp__example__lookup","input":{"query":"example"}}]}}
{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"toolu_wrapped","content":"{\"result\":\"{\\\"items\\\":[1,2]}\"}"}]}}
{"type":"result","subtype":"success","result":"done"}
JSON
