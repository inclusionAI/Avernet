#!/usr/bin/env bash
# mock engine：按行读 stdin；每读一行回显 "ack:<line>"；收到 "quit" 时输出终态并退出
while IFS= read -r line; do
  if [ "$line" = "quit" ]; then
    printf '{"type":"result","subtype":"success","result":"done","session_id":"sess-1"}\n'
    exit 0
  fi
  printf 'ack:%s\n' "$line"
done
