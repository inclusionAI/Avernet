#!/usr/bin/env bash
# Mid-round enqueue replay (production incident 2026-10-14): finder A is
# delivered immediately, B and C COMPLETE while the model is mid-round —
# their enqueue-time system lines arrive, and the model ends its round
# ("still waiting for B/C") BEFORE the CLI delivers B and C. The driver must
# swallow that round's result, keep the engine alive while the queued
# deliveries land, and only finalize on the final round's result with an
# empty pending set.
IFS= read -r _first
echo '{"type":"system","subtype":"init","session_id":"cc-sess-bg"}'
echo '{"type":"system","subtype":"task_started","task_id":"t-a","tool_use_id":"toolu_a","task_type":"local_agent"}'
echo '{"type":"system","subtype":"task_started","task_id":"t-b","tool_use_id":"toolu_b","task_type":"local_agent"}'
echo '{"type":"system","subtype":"task_started","task_id":"t-c","tool_use_id":"toolu_c","task_type":"local_agent"}'
echo '{"type":"result","subtype":"success","result":"launched 3 finders"}'
sleep 0.2
# A delivered while idle -> wake round 1.
echo '{"type":"user","message":{"role":"user","content":"<task-notification>\n<task-id>t-a</task-id>\n<tool-use-id>toolu_a</tool-use-id>\n<status>completed</status>\n</task-notification>"}}'
echo '{"type":"result","subtype":"success","result":"got A, still waiting for B/C"}'
sleep 0.2
# B and C COMPLETE mid-round: enqueue-time lines only, no delivery yet.
echo '{"type":"system","subtype":"task_notification","task_id":"t-b","tool_use_id":"toolu_b","status":"completed"}'
echo '{"type":"system","subtype":"task_notification","task_id":"t-c","tool_use_id":"toolu_c","status":"completed"}'
echo '{"type":"result","subtype":"success","result":"still waiting for B/C"}'
sleep 0.2
# Queued deliveries land after the round ends.
echo '{"type":"user","message":{"role":"user","content":"<task-notification>\n<task-id>t-b</task-id>\n<tool-use-id>toolu_b</tool-use-id>\n<status>completed</status>\n</task-notification>"}}'
echo '{"type":"result","subtype":"success","result":"got B, one left"}'
sleep 0.1
echo '{"type":"user","message":{"role":"user","content":"<task-notification>\n<task-id>t-c</task-id>\n<tool-use-id>toolu_c</tool-use-id>\n<status>completed</status>\n</task-notification>"}}'
echo '{"type":"result","subtype":"success","result":"summary table for all three finders"}'
sleep 30