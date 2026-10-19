#!/usr/bin/env bash
# Claude Code 2.1.199 auto-backgrounds a long-running FOREGROUND Bash (the
# model did not ask for run_in_background). Recorded live (repro6, sleep 25):
# task_started, enqueue-time task_notification, then the real output delivered
# as an ORDINARY block-array tool_result carrying the same tool_use_id — no
# `<task-notification>` user line, no second `system/init` wake — then the
# single `result`. The turn must finalize on that result: the inline delivery
# already released the wait, so `result` is not swallowed.
IFS= read -r _first
echo '{"type":"system","subtype":"init","session_id":"cc-sess-inline"}'
echo '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"toolu_bg","name":"Bash","input":{"command":"sleep 25; echo DONE","timeout":120000}}]}}'
echo '{"type":"system","subtype":"task_started","task_id":"t-bg","tool_use_id":"toolu_bg","task_type":"local_bash"}'
echo '{"type":"system","subtype":"task_notification","task_id":"t-bg","tool_use_id":"toolu_bg","status":"completed"}'
echo '{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"toolu_bg","is_error":false,"content":[{"type":"text","text":"DONE"}]}]}}'
echo '{"type":"result","subtype":"success","result":"the background command printed DONE"}'
sleep 30
