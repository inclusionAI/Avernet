#!/usr/bin/env bash
# engine-tee.sh — 引擎采集包装器(开发调试用,不进生产)
#
# bridge 配置里的 engine_bin 指向本脚本。本脚本把三方数据全部落盘后再转给
# 真正的引擎(REAL_ENGINE 指定,mock fixture 或真实引擎),逐行无缓冲透传:
#
#   bridge→引擎 stdin   $ENGINE_LOG_DIR/engine.stdin.jsonl   (bridge 写给引擎的行)
#   引擎→bridge stdout  $ENGINE_LOG_DIR/engine.stdout.ndjson (引擎原始事件,一行一个)
#   引擎 stderr         $ENGINE_LOG_DIR/engine.stderr.log
#   每次引擎启动        $ENGINE_LOG_DIR/runs.log             (时间/pid/args)
#
# 用法(bind 会通过环境变量自带):
#   REAL_ENGINE=/path/to/mock_cc.sh ENGINE_LOG_DIR=/tmp/bridge-dev \
#     ./scripts/engine-tee.sh --output-format stream-json ...
set -o pipefail

DIR="${ENGINE_LOG_DIR:-/tmp/bridge-dev}"
REAL="${REAL_ENGINE:?REAL_ENGINE not set — point it at the engine binary/script}"
mkdir -p "$DIR"

printf '[%s] engine pid=%s args=%s\n' "$(date '+%F %T')" "$$" "$*" >> "$DIR/runs.log"

tee -a "$DIR/engine.stdin.jsonl" \
  | python3 -c '
import fcntl
import os
import sys

for size in (1024 * 1024, 64 * 1024, 16 * 1024, 8 * 1024):
    try:
        actual = fcntl.fcntl(1, fcntl.F_SETPIPE_SZ, size)
    except (AttributeError, OSError):
        continue
    if actual >= 8 * 1024:
        break

os.execvp(sys.argv[1], sys.argv[1:])
' "$REAL" "$@" 2> >(tee -a "$DIR/engine.stderr.log" >&2) \
  | tee -a "$DIR/engine.stdout.ndjson"
exit $?
