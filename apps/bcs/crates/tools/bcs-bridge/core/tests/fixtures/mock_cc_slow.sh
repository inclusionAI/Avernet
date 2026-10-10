#!/usr/bin/env bash
# Slow mock Claude Code engine: read one stdin line, sleep long enough to keep
# the first chat.send run active, then emit a terminal result. Used by the
# concurrent-send 429 test to guarantee the first run is still in flight when
# the second webhook arrives.
IFS= read -r _first
sleep 30
printf '{"type":"result","subtype":"success","result":"done","session_id":"sess-1"}\n'
