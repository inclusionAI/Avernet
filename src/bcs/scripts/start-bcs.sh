#!/bin/bash
# BCS 服务启动脚本
# 用法: ./scripts/start-bcs.sh [direct|gateway]

set -e

# 进入项目根目录
cd "$(dirname "$0")/.."

# 默认使用 direct 模式配置
MODE="${1:-direct}"
LOG_LEVEL="${RUST_LOG:-debug}"

# 根据模式选择配置文件
case "$MODE" in
    direct)
        CONFIG_FILE="configs/bcs-config.toml"
        ;;
    gateway)
        CONFIG_FILE="configs/bcs-config.toml"
        ;;
    *)
        echo "用法: $0 [direct|gateway]"
        echo "  direct  - 直连钉钉 Stream API (默认)"
        echo "  gateway - 通过企业网关连接"
        exit 1
        ;;
esac

# 检查配置文件是否存在
if [ ! -f "$CONFIG_FILE" ]; then
    echo "❌ 配置文件不存在: $CONFIG_FILE"
    exit 1
fi

echo "============================================"
echo "BCS 服务启动"
echo "模式: $MODE"
echo "配置: $CONFIG_FILE"
echo "日志级别: $LOG_LEVEL"
echo "============================================"

# 设置环境变量
export MOLTIS_BCS_CONFIG="$CONFIG_FILE"
export RUST_LOG="$LOG_LEVEL"

# 检查进程是否已运行
PID_FILE=".bcs.pid"
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if ps -p "$OLD_PID" > /dev/null 2>&1; then
        echo "⚠️  BCS 服务已在运行 (PID: $OLD_PID)"
        echo "   使用 './scripts/stop-bcs.sh' 停止后再启动"
        exit 1
    fi
fi

# 启动服务
echo "🚀 启动 BCS 服务..."
cargo run --package bcs 2>&1 | tee bcs.log &
PID=$!
echo $PID > "$PID_FILE"

echo "✅ BCS 服务已启动 (PID: $PID)"
echo "   日志文件: bcs.log"
echo "   停止服务: ./scripts/stop-bcs.sh"
echo ""
echo "等待连接建立..."

# 等待几秒后检查服务是否正常运行
sleep 3
if ps -p $PID > /dev/null 2>&1; then
    echo "✅ 服务运行正常"
    echo ""
    echo "查看实时日志:"
    echo "  tail -f bcs.log"
    echo ""
    echo "测试钉钉订阅:"
    echo "  在钉钉中向机器人发送消息，观察日志输出"
else
    echo "❌ 服务启动失败，请检查日志:"
    echo "  cat bcs.log"
    rm -f "$PID_FILE"
    exit 1
fi