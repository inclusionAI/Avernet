#!/usr/bin/env bash
# A run_in_background tool call refused before launch: no task_started, no
# async receipt — only an error tool_result. The turn must finalize on its
# single result instead of wedging on a task that never launched.
IFS= read -r _first
echo '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"toolu_denied","name":"Bash","input":{"command":"sleep 20","run_in_background":true}}]}}'
echo '{"type":"user","message":{"content":[{"type":"tool_use_id":"toolu_denied","type":"tool_result","content":"User declined the background command","is_error":true}]}}'
echo '{"type":"result","subtype":"success","result":"not launched"}'
sleep 5