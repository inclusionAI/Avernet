#!/bin/bash
# BCS 服务状态查看脚本

cd "$(dirname "$0")/.."

PID_FILE=".bcs.pid"

echo "============================================"
echo "BCS 服务状态"
echo "============================================"

if [ ! -f "$PID_FILE" ]; then
    echo "状态: 未运行"
    exit 0
fi

PID=$(cat "$PID_FILE")

if ps -p "$PID" > /dev/null 2>&1; then
    echo "状态: 运行中"
    echo "PID: $PID"
    echo ""
    echo "进程信息:"
    ps -p "$PID" -o pid,ppid,user,%cpu,%mem,etime,command 2>/dev/null || true
    echo ""
    echo "最近日志:"
    echo "---"
    tail -20 bcs.log 2>/dev/null || echo "(无日志文件)"
else
    echo "状态: 已停止 (PID 文件存在但进程不存在)"
    rm -f "$PID_FILE"
fi