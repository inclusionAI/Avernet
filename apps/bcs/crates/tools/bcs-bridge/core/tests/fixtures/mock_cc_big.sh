#!/usr/bin/env bash
# Mock Claude Code engine for the oversize-frame regression (Task 16): read one
# stdin line, then emit a single `text_delta` whose text exceeds the 8 MiB SSE
# frame cap (MAX_FRAME_BYTES = 8 * 1024 * 1024). The run loop's `push_frame`
# converts `FrameTooLarge` into a terminal `chat/error` frame — the test
# asserts that `state:"error"` is present, `state:"final"` is absent, and no
# oversize frame reaches the wire (body < 9 MiB).
#
# The 9,000,000-char text is generated inline (`head -c /dev/zero | tr` to
# 'x') and embedded in a single NDJSON line: prefix + 9M 'x' + suffix, with
# no interior newline, so the cc driver reads it as one `stream_event` line
# (codex/cc drivers read NDJSON lines; a 9 MiB single line is fine).
IFS= read -r _user
printf '{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"'
head -c 9000000 /dev/zero | tr '\0' 'x'
printf '"}}}\n'
/bin/echo '{"type":"result","subtype":"success","result":"done","session_id":"cc-big-1"}'
