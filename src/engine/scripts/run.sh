#!/bin/bash

# OpenClaw Enterprise Engine Adapter 启动脚本
# 用法: run.sh [选项]
#
# 选项:
#   -p, --port PORT       指定端口 (默认: 20003)
#   -d, --dev             开发模式 (启用热重载)
#   -e, --engine ENGINE   指定引擎类型: moltis 或 openclaw (默认: openclaw)
#   -l, --local           本地模式 (使用 openclaw gateway run --verbose 启动引擎)
#   -i, --install         启动前安装依赖
#   -h, --help            显示帮助
#
# 示例:
#   run.sh                    # 生产模式，端口 20003，默认 openclaw 引擎
#   run.sh -d                 # 开发模式，热重载
#   run.sh -p 8000 -d         # 开发模式，端口 8000
#   run.sh -e openclaw        # 使用 OpenClaw 引擎
#   run.sh -e openclaw -d     # OpenClaw 引擎 + 开发模式
#   run.sh -l                 # 本地模式，使用默认引擎
#   run.sh -e openclaw -l     # OpenClaw 引擎 + 本地模式
#   run.sh -i                 # 安装依赖后启动

set -e

# 脚本所在目录和项目根目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# 颜色输出 (使用 ANSI-C quoting)
RED=$'\033[0;31m'
GREEN=$'\033[0;32m'
YELLOW=$'\033[1;33m'
CYAN=$'\033[1;36m'
WHITE=$'\033[1;37m'
NC=$'\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step() { echo -e "${CYAN}[STEP]${NC} $1"; }

# 显示启动 Banner
show_banner() {
    local engine=$1
    local mode=$2
    local local_mode=$3

    echo ""
    echo "╔═══════════════════════════════════════════════════════════════╗"
    echo "║                                                               ║"
    echo -e "║                  ${CYAN}OpenClaw Enterprise${NC}                          ║"
    echo -e "║                  ${CYAN}Engine Adapter${NC}                               ║"
    echo "║                                                               ║"
    echo "╚═══════════════════════════════════════════════════════════════╝"
    echo ""
    printf "  ${GREEN}Engine:${NC}  ${YELLOW}%-10s${NC}\n" "$engine"
    printf "  ${GREEN}Mode:${NC}    ${YELLOW}%-10s${NC}\n" "$mode"
    if [ "$local_mode" == "true" ]; then
        printf "  ${GREEN}Local:${NC}   ${YELLOW}%-10s${NC}\n" "Yes (openclaw gateway run --verbose)"
    fi
    echo ""
}

# 默认配置
PORT="20003"
DEV_MODE="false"
INSTALL_DEPS="false"
CHAT_ENGINE="openclaw"
LOCAL_MODE="false"

# 解析参数
while [[ $# -gt 0 ]]; do
    case $1 in
        -p|--port)
            PORT="$2"
            shift 2
            ;;
        -d|--dev)
            DEV_MODE="true"
            shift
            ;;
        -e|--engine)
            CHAT_ENGINE="$2"
            shift 2
            ;;
        -i|--install)
            INSTALL_DEPS="true"
            shift
            ;;
        -l|--local)
            LOCAL_MODE="true"
            shift
            ;;
        -h|--help)
            # 显示 Banner
            show_banner "$CHAT_ENGINE" "Help" "false"
            echo "用法: run.sh [选项]"
            echo ""
            echo "选项:"
            echo "  -p, --port PORT       指定端口 (默认: 20003)"
            echo "  -d, --dev             开发模式 (启用热重载)"
            echo "  -e, --engine ENGINE   指定引擎: moltis 或 openclaw (默认: openclaw)"
            echo "  -l, --local           本地模式 (使用 openclaw gateway run --verbose)"
            echo "  -i, --install         启动前安装依赖"
            echo "  -h, --help            显示帮助"
            echo ""
            echo "示例:"
            echo "  run.sh                    # 生产模式，默认 openclaw 引擎"
            echo "  run.sh -d                 # 开发模式，热重载"
            echo "  run.sh -p 8000 -d         # 开发模式，端口 8000"
            echo "  run.sh -e openclaw        # 使用 OpenClaw 引擎"
            echo "  run.sh -e openclaw -d     # OpenClaw 引擎 + 开发模式"
            echo "  run.sh -l                 # 本地模式，使用默认引擎"
            echo "  run.sh -e openclaw -l     # OpenClaw 引擎 + 本地模式"
            echo "  run.sh -i                 # 安装依赖后启动"
            exit 0
            ;;
        *)
            # 兼容旧用法: 直接传端口号
            if [[ "$1" =~ ^[0-9]+$ ]]; then
                PORT="$1"
            else
                log_warn "未知参数: $1"
            fi
            shift
            ;;
    esac
done

# 导出环境变量
export SERVER_PORT="$PORT"
export SERVER_HOST="${SERVER_HOST:-0.0.0.0}"
export CHAT_ENGINE
export DEV_MODE
export PYTHONPATH="$PROJECT_ROOT/src:${PYTHONPATH:-}"
export NODE_EXTRA_CA_CERTS="/etc/ssl/certs/ca-bundle.crt"

# 本地模式：覆盖引擎启动命令
if [ "$LOCAL_MODE" == "true" ]; then
    if [ "$CHAT_ENGINE" == "openclaw" ]; then
        export ENGINE_OPENCLAW_PROCESS_START_CMD="openclaw gateway run --verbose"
        export ENGINE_OPENCLAW_PROCESS_STOP_CMD='pkill -f "openclaw gateway"'
    elif [ "$CHAT_ENGINE" == "claude_code" ] || [ "$CHAT_ENGINE" == "aicoding" ]; then
        log_info "Engine '${CHAT_ENGINE}' requires relay running at ws://localhost:18900"
    fi
fi

# 确定显示模式
if [ "$DEV_MODE" == "true" ]; then
    DISPLAY_MODE="Development (Hot Reload)"
else
    DISPLAY_MODE="Production"
fi

# 显示启动 Banner
show_banner "$CHAT_ENGINE" "$DISPLAY_MODE" "$LOCAL_MODE"

# 检查 Python 环境
log_step "检查 Python 环境..."
python3 --version || python --version

# 安装依赖
if [ "$INSTALL_DEPS" == "true" ]; then
    log_step "安装依赖..."
    pip3 install -q fastapi uvicorn aiofiles python-multipart websockets pydantic eval-type-backport python-dotenv || \
    pip install -q fastapi uvicorn aiofiles python-multipart websockets pydantic eval-type-backport python-dotenv
fi

# 显示配置信息
echo ""
log_info "启动配置:"
echo "  Port:       $PORT"
echo "  Host:       $SERVER_HOST"
echo "  Engine:     $CHAT_ENGINE"
echo "  Mode:       $DISPLAY_MODE"
echo "  Local:      $LOCAL_MODE"
echo "  PYTHONPATH: $PYTHONPATH"
echo ""
echo "  ${GREEN}Endpoints:${NC}"
echo "    WebSocket:    ws://$SERVER_HOST:$PORT/ws"
echo "    WebSocket:    ws://$SERVER_HOST:$PORT/api/$CHAT_ENGINE/ws"
echo "    Sessions:     http://$SERVER_HOST:$PORT/api/sessions"
echo "    Models:       http://$SERVER_HOST:$PORT/api/models"
echo "    Cron:         http://$SERVER_HOST:$PORT/api/cron"
echo "    Health:       http://$SERVER_HOST:$PORT/health"
echo "    API Docs:     http://$SERVER_HOST:$PORT/docs"
echo ""
echo -e "${YELLOW}Press Ctrl+C to stop${NC}"
echo ""

# 启动服务
cd "$PROJECT_ROOT/src"
LOG_CONFIG_PATH="$PROJECT_ROOT/config/uvicorn_log.json"
UVICORN_ARGS=(
    engine.community.api.app:app
    --host "$SERVER_HOST"
    --port "$PORT"
    --log-config "$LOG_CONFIG_PATH"
)
if [ "$DEV_MODE" == "true" ]; then
    UVICORN_ARGS+=(--reload --reload-dir engine)
fi

if [ "${SINGLEBOX_COVERAGE:-0}" == "1" ]; then
    COVERAGE_DIR="${SINGLEBOX_COVERAGE_DIR:-$PROJECT_ROOT/tmp/coverage}/engine"
    mkdir -p "$COVERAGE_DIR"
    export COVERAGE_FILE="$COVERAGE_DIR/.coverage"
    PYTHON_BIN="$(command -v python3 || command -v python)"
    uv pip install --python "$PYTHON_BIN" coverage >/dev/null
    log_info "Coverage mode: enabled ($COVERAGE_DIR)"
    "$PYTHON_BIN" -m coverage run \
        --parallel-mode \
        --save-signal=USR1 \
        --source="$PROJECT_ROOT/src" \
        -m uvicorn "${UVICORN_ARGS[@]}"
else
    python3 -m uvicorn "${UVICORN_ARGS[@]}" || \
    python -m uvicorn "${UVICORN_ARGS[@]}"
fi
