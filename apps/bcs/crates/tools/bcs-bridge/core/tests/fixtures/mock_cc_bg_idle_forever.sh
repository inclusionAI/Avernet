#!/usr/bin/env bash
# The 2026-10-14 hang, distilled: a background task is pending when the
# stream goes permanently quiet — the notification the turn waits for never
# arrives, and no result will ever come. The stream stays open (stdin is
# held, the engine idles) exactly like the production turn that sat silent
# until an operator noticed. Only the idle watchdog ends this turn.
IFS= read -r _first
echo '{"type":"system","subtype":"init","session_id":"cc-sess-idle"}'
echo '{"type":"system","subtype":"task_started","task_id":"t-a","tool_use_id":"toolu_a","task_type":"local_agent"}'
echo '{"type":"result","subtype":"success","result":"launched one finder"}'
sleep 600