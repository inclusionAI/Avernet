#!/bin/bash

WORK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

CONFIG_DIR="$WORK_DIR/configs"
VENV_DIR="$WORK_DIR/.venv"
# WORKSPACE_VENV_DIR="$(cd "$WORK_DIR"/../.. && pwd)/.venv"
APP_PORT="8888"
RUN_MODE=""
APP_LOG_DIR="$HOME/logs/secbaas"

mkdir -p "$WORK_DIR/tmp"
PID_FILE="$WORK_DIR/tmp/app.pid"
PORT_FILE="$WORK_DIR/tmp/app.port"
LOG_FILE="$WORK_DIR/tmp/app.log"

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1" | tee -a "$LOG_FILE"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1" | tee -a "$LOG_FILE"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1" | tee -a "$LOG_FILE"
}

log_usage() {
    echo -e "${BLUE}Usage:${NC} $0 {start|stop|restart|status} [options]"
    echo ""
    echo "Commands:"
    echo "  start              - 启动应用（默认启用覆盖率收集，--debug 禁用）"
    echo "  stop               - 停止应用（自动合并并输出覆盖率报告）"
    echo "  restart            - 重启应用"
    echo "  status             - 查看应用状态"
    echo ""
    echo "Options:"
    echo "  --singlebox - 单机模式（使用 singlebox-configs，端口 8890）"
    echo "  --mock      - 启用 PaaS Mock 模式（跳过真实设备创建）"
}

# 检查虚拟环境 — 优先使用工作区根 .venv，回退到社区包的 .venv
check_venv() {
    # 如果工作区根 .venv 存在且可用，直接使用
    # if [[ -d "$WORKSPACE_VENV_DIR" && -x "$WORKSPACE_VENV_DIR/bin/python" ]]; then
    #     VENV_DIR="$WORKSPACE_VENV_DIR"
    #     PYTHON_BIN="$VENV_DIR/bin/python"
    #     log_info "使用工作区虚拟环境: $VENV_DIR"
    #     return 0
    # fi

    if [[ ! -d "$VENV_DIR" ]]; then
        log_warn "虚拟环境不存在，正在使用 uv 创建..."
        if ! command -v uv &> /dev/null; then
            log_error "uv 未安装，请先安装 uv: curl -LsSf https://astral.sh/uv/install.sh | sh"
            return 1
        fi
        cd "$WORK_DIR" && uv sync
        if [[ $? -ne 0 ]]; then
            log_error "uv sync 失败"
            return 1
        fi
        log_info "虚拟环境创建成功"
    fi

    PYTHON_BIN="$VENV_DIR/bin/python"
    if [[ ! -x "$PYTHON_BIN" ]]; then
        log_error "Python 解释器不可用: $PYTHON_BIN"
        return 1
    fi
    return 0
}

# 检查配置目录
check_config() {
    if [[ ! -d "$CONFIG_DIR" ]]; then
        log_error "配置目录不存在: $CONFIG_DIR"
        return 1
    fi
    return 0
}

# 检查应用是否运行
is_running() {
    if [[ -f "$PID_FILE" ]]; then
        OLD_PID=$(cat "$PID_FILE")
        if kill -0 "$OLD_PID" 2>/dev/null; then
            return 0
        fi
    fi
    return 1
}

# 检查端口占用
check_port() {
    local port=$1
    # 只检查 LISTEN 状态。lsof -i :port 会把已经 CLOSED 的客户端连接也算进去,
    # 容易把本机浏览器/代理残留连接误判成端口被服务占用。
    if command -v lsof &> /dev/null; then
        if lsof -nP -iTCP:"$port" -sTCP:LISTEN &> /dev/null; then
            log_error "端口 $port 已被占用"
            lsof -nP -iTCP:"$port" -sTCP:LISTEN | tee -a "$LOG_FILE"
            return 1
        fi
    elif command -v netstat &> /dev/null; then
        if netstat -tuln | grep -q ":$port "; then
            log_error "端口 $port 已被占用"
            netstat -tuln | grep ":$port " | tee -a "$LOG_FILE"
            return 1
        fi
    else
        log_warn "无法检查端口占用 (未安装 lsof 或 netstat)"
    fi
    return 0
}

# 启动应用
do_start() {
    local debug_port=""
    local env_name=""
    local paas_mock=""

    # 解析参数
    while [[ $# -gt 0 ]]; do
        case $1 in
            --debug)
                debug_port="${2:-5678}"
                shift 2
                ;;
            --singlebox)
                # RUN_MODE, CONFIG_DIR, APP_PORT 已在预解析阶段设置
                shift
                ;;
            --mode)
                APP_MODE="$2"
                shift 2
                ;;
            --mock)
                paas_mock="true"
                shift
                ;;
            --env)
                env_name="$2"
                shift 2
                ;;
            *)
                shift
                ;;
        esac
    done

    # 单机模式使用 singlebox-configs/application-dev.yaml；workspace 物理分区
    # 也由该 overlay 声明，不再通过额外环境变量覆盖。
    if [[ "$RUN_MODE" == "singlebox" ]]; then
        env_name="dev"
        log_info "单机模式: 已启用 (配置: $CONFIG_DIR, 端口: $APP_PORT)"
    fi

    # 根据环境设置配置文件
    local config_file="$CONFIG_DIR/application.yaml"
    if [[ -n "$env_name" ]]; then
        case "$env_name" in
            dev)
                config_file="$CONFIG_DIR/application-dev.yaml"
                ;;
            prepub)
                config_file="$CONFIG_DIR/application-prepub.yaml"
                ;;
            prod)
                config_file="$CONFIG_DIR/application.yaml"
                ;;
            *)
                log_error "未知环境: $env_name (支持: dev/prepub/prod)"
                exit 1
                ;;
        esac
    fi

    if [[ ! -f "$config_file" ]]; then
        log_error "配置文件不存在: $config_file"
        exit 1
    fi

    if is_running; then
        OLD_PID=$(cat "$PID_FILE")
        log_error "应用已在运行中 (PID: $OLD_PID)"
        log_info "如需重启，请先运行: $0 stop"
        exit 1
    fi

    # 检查 APP_PORT 端口占用
    check_port "$APP_PORT" || exit 1

    # 检查 debug 端口占用
    if [[ -n "$debug_port" ]]; then
        check_port "$debug_port" || exit 1
    fi

    # 清理遗留的 PID 文件
    if [[ -f "$PID_FILE" ]]; then
        log_warn "发现遗留 PID 文件，清理中..."
        rm -f "$PID_FILE"
    fi

    # 启动应用
    log_info "启动 secbaas 应用..."
    log_info "配置文件: $config_file"
    log_info "日志文件: $LOG_FILE"

    # 启用 PaaS Mock 模式
    if [[ "$paas_mock" == "true" ]]; then
        export PAAS_MOCK_MODE=true
        log_info "PaaS Mock 模式: 已启用 (跳过真实设备创建)"
    fi

    # 清理应用日志
    if [[ -d "$APP_LOG_DIR" ]]; then
        log_info "清理应用日志: $APP_LOG_DIR"
        rm -rf "$APP_LOG_DIR"/*
    fi

    if [[ -z "$APP_MODE" ]]; then
        APP_MODE="bare"
    fi
    # 用于持久化覆盖率状态，供 stop 时检测
    local coverage_marker="$WORK_DIR/tmp/.coverage_enabled"

    # 默认启用覆盖率收集（除非使用 --debug）
    if [[ -z "$debug_port" ]]; then
        export SINGLEBOX_COVERAGE=1
        : "${SINGLEBOX_COVERAGE_DIR:=$WORK_DIR/tmp/coverage}"
        mkdir -p "$(dirname "$coverage_marker")"
        : > "$coverage_marker"
    fi
    start_cmd=("$VENV_DIR/bin/python" src/secbaas/community/main.py -c "$CONFIG_DIR" --mode "$APP_MODE")
    if [[ -z "$debug_port" ]]; then
        coverage_dir="${SINGLEBOX_COVERAGE_DIR:-$WORK_DIR/tmp/coverage}/baas"
        mkdir -p "$coverage_dir"
        export COVERAGE_FILE="$coverage_dir/.coverage"
        start_cmd=(
            "$VENV_DIR/bin/python" -m coverage run
            --parallel-mode
            --save-signal=USR1
            --source="$WORK_DIR/src"
            --omit="*/stub/*,*/mock/*"
            src/secbaas/community/main.py
            -c "$CONFIG_DIR"
            --mode "$APP_MODE"
        )
        log_info "Coverage mode: enabled ($coverage_dir)"
    fi
    if [[ -n "$debug_port" ]]; then
        log_info "Debug 端口: $debug_port (不等待debugger，web服务立即启动)"
        SERVER_ENV="$env_name" APP_CONFIG_PATH="$config_file" nohup "$VENV_DIR/bin/python" -m debugpy --listen "0.0.0.0:$debug_port" "$WORK_DIR/src/secbaas/community/main.py" -c "$CONFIG_DIR" --mode "$APP_MODE" >> "$LOG_FILE" 2>&1 &
    else
        SERVER_ENV="$env_name" APP_CONFIG_PATH="$config_file" nohup "${start_cmd[@]}" >> "$LOG_FILE" 2>&1 &
    fi

    APP_PID=$!

    # 等待健康检查通过
    if wait_for_health "$APP_PID" "$APP_PORT"; then
        echo "$APP_PID" > "$PID_FILE"
        echo "$APP_PORT" > "$PORT_FILE"
        log_info "应用启动成功 (PID: $APP_PID)"
        log_info "健康检查通过: http://localhost:$APP_PORT/health"
        if [[ -n "$debug_port" ]]; then
            log_info "Debug 端口已开放: localhost:$debug_port"
        fi
        log_info "SOFAPy 运行日志: $APP_LOG_DIR"
        return 0
    else
        log_error "应用启动失败 (健康检查超时)，请查看日志: $LOG_FILE"
        # 清理失败的进程
        kill "$APP_PID" 2>/dev/null || true
        exit 1
    fi
}

# 等待健康检查通过
# 参数: $1 = 进程 PID, $2 = 端口
# 返回: 0 成功, 1 失败
wait_for_health() {
    local pid=$1
    local port=$2
    local max_attempts=30
    local attempt=1

    log_info "等待应用就绪..."

    while [ $attempt -le $max_attempts ]; do
        # 检查进程是否存活
        if ! kill -0 "$pid" 2>/dev/null; then
            log_error "应用进程意外退出，请查看日志: $LOG_FILE"
            if [[ -f "$LOG_FILE" ]]; then
                log_error "──── 最近错误日志 ────"
                tail -30 "$LOG_FILE" | while IFS= read -r line; do
                    echo -e "${RED}│${NC} $line"
                done
                log_error "──────────────────────"
            fi
            return 1
        fi

        # 检查健康检查端点
        if check_health_endpoint "$port"; then
            return 0
        fi

        sleep 2
        attempt=$((attempt + 1))
    done

    # 超时后显示日志
    if [[ -f "$LOG_FILE" ]]; then
        log_error "──── 最近错误日志 ────"
        tail -30 "$LOG_FILE" | while IFS= read -r line; do
            echo -e "${RED}│${NC} $line"
        done
        log_error "──────────────────────"
    fi
    return 1
}

# 根据端口获取进程 PID
get_pid_by_port() {
    local port=$1
    if command -v lsof &> /dev/null; then
        lsof -t -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | head -1
    elif command -v netstat &> /dev/null; then
        netstat -tuln 2>/dev/null | grep ":$port " | awk '{print $7}' | cut -d'/' -f1 | head -1
    fi
}

# 停止应用
do_stop() {
    local stopped=false
    local stop_port="$APP_PORT"

    local coverage_marker="$WORK_DIR/tmp/.coverage_enabled"

    # 优先从端口文件读取启动时的端口
    if [[ -f "$PORT_FILE" ]]; then
        stop_port=$(cat "$PORT_FILE")
    fi

    # 优先通过 PID 文件停止
    if is_running; then
        OLD_PID=$(cat "$PID_FILE")
        log_info "停止应用 (PID: $OLD_PID)..."

        # 如果启用了覆盖率，先发送 USR1 信号刷新覆盖率数据
        if [[ -f "$coverage_marker" ]]; then
            kill -USR1 "$OLD_PID" 2>/dev/null || true
            log_info "覆盖率数据已通过 SIGUSR1 刷新"
            sleep 2
        fi

        # 发送 SIGTERM 信号
        kill "$OLD_PID" 2>/dev/null

        # 等待进程结束
        for i in {1..10}; do
            if ! kill -0 "$OLD_PID" 2>/dev/null; then
                rm -f "$PID_FILE"
                log_info "应用已停止"
                stopped=true
                break
            fi
            sleep 1
        done

        # 强制杀死
        if [[ "$stopped" != "true" ]]; then
            log_warn "等待超时，强制杀死进程..."
            kill -9 "$OLD_PID" 2>/dev/null
            rm -f "$PID_FILE"
            log_info "应用已强制停止"
            stopped=true
        fi
    else
        rm -f "$PID_FILE"
    fi

    # 检查端口是否仍被占用，如果是则杀死占用进程
    PORT_PID=$(get_pid_by_port "$stop_port")
    if [[ -n "$PORT_PID" ]]; then
        log_warn "发现端口 $stop_port 仍被占用 (PID: $PORT_PID)，正在停止..."
        kill "$PORT_PID" 2>/dev/null
        sleep 1
        if kill -0 "$PORT_PID" 2>/dev/null; then
            kill -9 "$PORT_PID" 2>/dev/null
        fi
        log_info "已停止端口 $stop_port 上的进程 (PID: $PORT_PID)"
    elif [[ "$stopped" != "true" ]]; then
        log_warn "应用未在运行"
    fi

    rm -f "$PORT_FILE"

    # 如果启用了覆盖率，合并数据并生成报告
    if [[ -f "$coverage_marker" ]]; then
        local cov_dir="${SINGLEBOX_COVERAGE_DIR:-$WORK_DIR/tmp/coverage}/baas"
        shopt -s nullglob
        local cov_files=("$cov_dir"/.coverage.*)

        if [[ ${#cov_files[@]} -gt 0 ]]; then
            COVERAGE_FILE="$cov_dir/.coverage" uv run coverage combine "${cov_files[@]}" >/dev/null 2>&1 || true
            COVERAGE_FILE="$cov_dir/.coverage" uv run coverage html -i -d "$cov_dir/htmlcov" >/dev/null 2>&1 || true
            log_info "覆盖率报告: file://$cov_dir/htmlcov/index.html"

            if [[ -n "${COVERAGE_E2E_DIR:-}" ]]; then
                local session_label="${SESSION_LABEL:-session-$$}"
                local session_dir="$COVERAGE_E2E_DIR/$session_label"
                mkdir -p "$session_dir"
                cp "$cov_dir/.coverage" "$session_dir/"
                COVERAGE_FILE="$session_dir/.coverage" uv run coverage html -i -d "$session_dir/htmlcov" >/dev/null 2>&1 || true
                local summary
                summary=$(COVERAGE_FILE="$session_dir/.coverage" uv run coverage report --format=total 2>/dev/null | tail -1)
                log_info "[COVERAGE] $session_label: $summary → file://$session_dir/htmlcov/index.html"
            fi
        fi

        shopt -u nullglob
        rm -f "$coverage_marker"
    fi
}

# 查看状态
do_status() {
    local port="${APP_PORT}"

    # 优先从端口文件读取实际端口
    if [[ -f "$PORT_FILE" ]]; then
        port=$(cat "$PORT_FILE")
    fi

    if is_running; then
        OLD_PID=$(cat "$PID_FILE")
        # 检查健康状态
        if check_health_endpoint "$port"; then
            echo -e "${GREEN}● 应用运行中${NC} (PID: $OLD_PID, 健康检查: 通过)"
        else
            echo -e "${YELLOW}● 应用运行中${NC} (PID: $OLD_PID, 健康检查: 失败)"
        fi
    else
        echo -e "${RED}○ 应用未运行${NC}"
    fi
}

# 检查健康检查端点是否可访问
# 参数: $1 = 端口
# 返回: 0 成功, 1 失败
check_health_endpoint() {
    local port=$1
    curl --noproxy '*' -s "http://127.0.0.1:${port}/health" > /dev/null 2>&1
}

# 预解析 --singlebox 选项（需在 check_config 之前切换 CONFIG_DIR）
for arg in "$@"; do
    case "$arg" in
        --singlebox)
            RUN_MODE=singlebox
            CONFIG_DIR="$WORK_DIR/singlebox-configs"
            # 固定端口
            APP_PORT="8890"
            ;;
    esac
done

# 主命令处理
case "${1:-start}" in
    start)
        shift 2>/dev/null || true
        check_venv || exit 1
        check_config || exit 1
        do_start "$@"
        ;;
    stop)
        do_stop
        ;;
    restart)
        shift 2>/dev/null || true
        do_stop
        check_venv || exit 1
        check_config || exit 1
        do_start "$@"
        ;;
    status)
        do_status
        ;;
    *)
        log_error "未知命令: $1"
        log_usage
        exit 1
        ;;
esac
