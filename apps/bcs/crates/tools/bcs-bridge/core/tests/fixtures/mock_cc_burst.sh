#!/usr/bin/env bash
# Mock Claude Code engine for the re-attach buffered-replay regression (Task 16
# review fix round 1): read one stdin line, emit two `text_delta` lines
# IMMEDIATELY (突发一, 突二), then sleep 30s so the run stays active while a
# same-id retry re-attaches. The two deltas are pushed into the run's buffer
# (seq 1, 2) BEFORE the re-attach, so the re-attached stream's forwarder
# snapshots the buffer and replays them — verifying the buffer-replay leg of
# `forward_stream` (not just the live broadcast). `chat.abort` kills the
# 30s sleep; `kill_on_drop` reaps the subprocess on runtime teardown.
#
# Uses /bin/echo (external, flushes its stdio buffer on exit) so the driver
# observes each delta line before the script blocks on `sleep` — a bash
# `printf` builtin would block-buffer a non-tty stdout and the driver would
# deadlock waiting for the deltas while the mock sleeps.
IFS= read -r _first
/bin/echo '{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"突发一"}}}'
/bin/echo '{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"突发二"}}}'
sleep 30
