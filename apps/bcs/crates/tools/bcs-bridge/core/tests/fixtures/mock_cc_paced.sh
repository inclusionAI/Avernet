#!/usr/bin/env bash
# Paced mock Claude Code engine: emits one text delta every PACE seconds (from
# the prompt "paced:<count>:<pace>"), then a terminal result. Used by A2A
# resume/cancel tests that need the run to outlive a client connection.
IFS= read -r first
spec=$(printf '%s' "$first" | sed -n 's/.*"text":"paced:\([0-9]*\):\([0-9.]*\)".*/\1 \2/p')
count=${spec% *}; pace=${spec#* }
count=${count:-5}; pace=${pace:-0.2}
printf '{"type":"system","subtype":"init","session_id":"paced-1"}\n'
for i in $(seq 1 "$count"); do
  sleep "$pace"
  printf '{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"chunk-%s "}}}\n' "$i"
done
printf '{"type":"result","subtype":"success","result":"paced done","session_id":"paced-1"}\n'
