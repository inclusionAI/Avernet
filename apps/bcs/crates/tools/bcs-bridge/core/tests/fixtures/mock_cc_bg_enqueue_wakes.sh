#!/usr/bin/env bash
# Real-engine capture (2026-10-14 production session + lab replay): this CLI
# never surfaces the <task-notification> user lines on stdout. Completion is
# signalled only by enqueue-time system lines, and the CLI delivers exactly
# ONE queued notification per auto-wake round (each round opens with a
# system/init line). C is enqueued before B's wake yet is only delivered a
# round later — a result may never finalize while anything a wake has not
# delivered is still pending.
IFS= read -r _first
echo '{"type":"system","subtype":"init","session_id":"cc-sess-wake"}'
echo '{"type":"system","subtype":"task_started","task_id":"t-a","tool_use_id":"toolu_a","task_type":"local_agent"}'
echo '{"type":"system","subtype":"task_started","task_id":"t-b","tool_use_id":"toolu_b","task_type":"local_agent"}'
echo '{"type":"system","subtype":"task_started","task_id":"t-c","tool_use_id":"toolu_c","task_type":"local_agent"}'
echo '{"type":"result","subtype":"success","result":"launched 3 finders"}'
sleep 0.2
# A completes and is delivered on the next wake.
echo '{"type":"system","subtype":"task_updated","task_id":"t-a","patch":{"status":"completed"}}'
echo '{"type":"system","subtype":"task_notification","task_id":"t-a","tool_use_id":"toolu_a","status":"completed"}'
echo '{"type":"system","subtype":"init","session_id":"cc-sess-wake"}'
echo '{"type":"result","subtype":"success","result":"got A, still waiting for B/C"}'
sleep 0.2
# B and C complete mid-round: enqueue-time lines only, neither delivered.
echo '{"type":"system","subtype":"task_updated","task_id":"t-b","patch":{"status":"completed"}}'
echo '{"type":"system","subtype":"task_notification","task_id":"t-b","tool_use_id":"toolu_b","status":"completed"}'
echo '{"type":"system","subtype":"task_updated","task_id":"t-c","patch":{"status":"completed"}}'
echo '{"type":"system","subtype":"task_notification","task_id":"t-c","tool_use_id":"toolu_c","status":"completed"}'
echo '{"type":"result","subtype":"success","result":"still waiting for B/C"}'
sleep 0.2
# The next wake delivers B alone; C stays queued — its round-end result is
# still intermediate.
echo '{"type":"system","subtype":"init","session_id":"cc-sess-wake"}'
echo '{"type":"result","subtype":"success","result":"got B, one left"}'
sleep 0.2
# C's wake: queue drained, so the round's result is the real final text.
echo '{"type":"system","subtype":"init","session_id":"cc-sess-wake"}'
echo '{"type":"result","subtype":"success","result":"summary table for all three finders"}'
sleep 30