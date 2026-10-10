#!/usr/bin/env bash
# Mock Claude Code engine for the UTF-8 regression (Task 16): read one stdin
# line (the user message), then emit 40 Chinese `text_delta` stream events
# followed by a terminal `result/success`. Each line is a complete JSON event
# the cc driver maps to a `chat_delta` SSE frame (spec §5/§6).
#
# `resp.text()` must observe valid UTF-8 throughout (no half-character byte
# slicing), and every `data:` line must parse as JSON. Uses /bin/echo
# (external, flushes its stdio buffer on exit) so the driver observes each
# delta line before the script exits — a bash `printf` builtin would
# block-buffer a non-tty stdout and deadlock the turn.
IFS= read -r _user
i=0
while [ "$i" -lt 40 ]; do
  /bin/echo '{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"中文增量"}}}'
  i=$((i + 1))
done
/bin/echo '{"type":"result","subtype":"success","result":"完成","session_id":"cc-utf8-1"}'
