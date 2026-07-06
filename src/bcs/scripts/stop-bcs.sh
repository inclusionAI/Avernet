#!/bin/bash
# BCS 服务停止脚本

set -e

cd "$(dirname "$0")/.."

PID_FILE=".bcs.pid"
BCS_PORT="${BCS_PORT:-21000}"

# 通过端口查找 BCS 进程
find_bcs_pid_by_port() {
    lsof -ti :$BCS_PORT 2>/dev/null | head -1 || echo ""
}

# 通过进程名查找 BCS 进程（匹配 target/debug/bcs 或 target/release/bcs）
find_bcs_pid_by_name() {
    pgrep -f 'target/(debug|release)/bcs' 2>/dev/null | head -1 || echo ""
}

# 停止指定 PID 的进程
stop_pid() {
    local PID=$1
    if [ -z "$PID" ]; then
        return 1
    fi

    echo "🛑 停止 BCS 服务 (PID: $PID)..."
    kill "$PID" 2>/dev/null || true

    # 等待进程结束
    for i in {1..10}; do
        if ! ps -p "$PID" > /dev/null 2>&1; then
            break
        fi
        sleep 1
    done

    # 强制杀死
    if ps -p "$PID" > /dev/null 2>&1; then
        echo "⚠️  进程未响应，强制终止..."
        kill -9 "$PID" 2>/dev/null || true
        sleep 1
    fi

    # 验证是否已停止
    if ! ps -p "$PID" > /dev/null 2>&1; then
        return 0
    else
        return 1
    fi
}

# 查找 BCS 进程
PID=""

# 方式1: 通过 PID 文件查找
if [ -f "$PID_FILE" ]; then
    FILE_PID=$(cat "$PID_FILE")
    # 检查 PID 是否是 bcs 相关进程
    if ps -p "$FILE_PID" > /dev/null 2>&1; then
        CMD=$(ps -p "$FILE_PID" -o comm= 2>/dev/null || echo "")
        if [[ "$CMD" == *"cargo"* ]] || [[ "$CMD" == *"bcs"* ]]; then
            PID="$FILE_PID"
            echo "📍 通过 PID 文件找到进程 (PID: $PID)"
        fi
    fi
fi

# 方式2: 通过端口查找
if [ -z "$PID" ]; then
    PORT_PID=$(find_bcs_pid_by_port)
    if [ -n "$PORT_PID" ]; then
        PID="$PORT_PID"
        echo "📍 通过端口 $BCS_PORT 找到进程 (PID: $PID)"
    fi
fi

# 方式3: 通过进程名查找
if [ -z "$PID" ]; then
    NAME_PID=$(find_bcs_pid_by_name)
    if [ -n "$NAME_PID" ]; then
        PID="$NAME_PID"
        echo "📍 通过进程名找到进程 (PID: $PID)"
    fi
fi

# 没有找到进程
if [ -z "$PID" ]; then
    echo "⚠️  没有找到运行中的 BCS 服务"
    rm -f "$PID_FILE"
    exit 0
fi

# 停止进程
if stop_pid "$PID"; then
    rm -f "$PID_FILE"
    echo "✅ BCS 服务已停止"
else
    echo "❌ 无法停止 BCS 服务 (PID: $PID)"
    exit 1
fi