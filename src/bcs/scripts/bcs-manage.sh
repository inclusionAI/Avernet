#!/bin/bash
# BCS 统一管理脚本
# 用法: ./scripts/bcs-manage.sh <command> [args...]
#
# 命令:
#   start bcs              - 启动 BCS 服务
#   stop bcs               - 停止 BCS 服务
#   start bot <name|all>   - 启动 bot (reviewer|database|legal|all)
#   stop bot <name|all>    - 停止 bot
#   restart bot <name>     - 重启 bot
#   onboard bot <name|all> - Bot 注册到 BCS
#   start all              - 启动 BCS + 所有 bot
#   stop all               - 停止 BCS + 所有 bot
#   clean                  - 清空 bcs_test_tmp 目录
#   status                 - 显示所有服务状态

set -euo pipefail

# ============================================================================
# Configuration
# ============================================================================

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE_DIR="$PROJECT_ROOT/bcs_test_tmp"
BCS_WSP="$BASE_DIR/bcs_wsp"
LOG_DIR="$BASE_DIR/logs"
PID_DIR="$BASE_DIR/.pids"

BCS_PORT="${MOLTIS_BCS_PORT:-21000}"
BCS_URL="http://localhost:${BCS_PORT}"
BCS_PID_FILE="$PID_DIR/bcs.pid"

MOLTIS_CLI="$PROJECT_ROOT/submodules/moltis/target/debug/moltis"
BCS_CLI="$PROJECT_ROOT/target/debug/bcs-cli"

# Bot names
BOT_NAMES="reviewer database legal"

# ============================================================================
# Colors
# ============================================================================

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BLUE='\033[0;34m'
NC='\033[0m'

pass() { echo -e "  ${GREEN}✓${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; }
info() { echo -e "  ${CYAN}→${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }
header() { echo -e "${BLUE}▶${NC} $1"; }

# ============================================================================
# Bot Configuration Functions
# ============================================================================

# Get bot info by name
# Usage: get_bot_info <name> <field>
# Fields: id, port, dingtalk_client_id, dingtalk_client_secret, dingtalk_robot_code, dingtalk_card_template_id, worker_id
get_bot_info() {
    local name="$1"
    local field="$2"

    case "$name" in
        reviewer)
            case "$field" in
                id) echo "Reviewer Bot" ;;
                port) echo "20091" ;;
                dingtalk_client_id) echo "${DINGTALK_REVIEWER_CLIENT_ID:-}" ;;
                dingtalk_client_secret) echo "${DINGTALK_REVIEWER_CLIENT_SECRET:-}" ;;
                dingtalk_robot_code) echo "${DINGTALK_REVIEWER_ROBOT_CODE:-}" ;;
                dingtalk_card_template_id) echo "${DINGTALK_REVIEWER_CARD_TEMPLATE_ID:-}" ;;
                worker_id) echo "11111111" ;;
            esac
            ;;
        database)
            case "$field" in
                id) echo "Database Bot" ;;
                port) echo "20111" ;;
                dingtalk_client_id) echo "${DINGTALK_DATABASE_CLIENT_ID:-}" ;;
                dingtalk_client_secret) echo "${DINGTALK_DATABASE_CLIENT_SECRET:-}" ;;
                dingtalk_robot_code) echo "${DINGTALK_DATABASE_ROBOT_CODE:-}" ;;
                dingtalk_card_template_id) echo "${DINGTALK_DATABASE_CARD_TEMPLATE_ID:-}" ;;
                worker_id) echo "12345678" ;;
            esac
            ;;
        legal)
            case "$field" in
                id) echo "Legal Bot" ;;
                port) echo "20101" ;;
                dingtalk_client_id) echo "${DINGTALK_LEGAL_CLIENT_ID:-}" ;;
                dingtalk_client_secret) echo "${DINGTALK_LEGAL_CLIENT_SECRET:-}" ;;
                dingtalk_robot_code) echo "${DINGTALK_LEGAL_ROBOT_CODE:-}" ;;
                dingtalk_card_template_id) echo "${DINGTALK_LEGAL_CARD_TEMPLATE_ID:-}" ;;
                worker_id) echo "12345678" ;;
            esac
            ;;
        *)
            return 1
            ;;
    esac
}

# Get bot SOUL content
get_bot_soul() {
    local name="$1"
    case "$name" in
        reviewer)
            echo "你是审理 Bot。
你负责审核文档、合同和规则符合性。"
            ;;
        database)
            echo "你是 DBA Bot。
你负责数据库故障排查、性能优化和数据库架构建议。"
            ;;
        legal)
            echo "你是法务 Bot。
你负责合规、条款风险和法律审查建议。"
            ;;
    esac
}

# Get bot RULES content
get_bot_rules() {
    local name="$1"
    case "$name" in
        reviewer)
            echo "- 不访问私有数据
- 审核时关注规则、条款和合规性"
            ;;
        database)
            echo "- 优先从数据库角度给出专业分析
- 关注死锁、锁等待、慢查询、事务冲突"
            ;;
        legal)
            echo "- 提供法律与合规建议
- 不提供无依据的业务承诺"
            ;;
    esac
}

# Get bot MEMORY content
get_bot_memory() {
    local name="$1"
    case "$name" in
        reviewer)
            echo "## 规则库
- 合同审核要点
- 合规性检查清单"
            ;;
        database)
            echo "## 数据库知识
- 常见死锁原因包括事务加锁顺序不一致
- 排查重点包括锁等待链、事务持锁时间、SQL执行路径"
            ;;
        legal)
            echo "## 重点
- 新支付功能需要关注合规风险和条款约束"
            ;;
    esac
}

# Get bot summary
get_bot_summary() {
    local name="$1"
    case "$name" in
        reviewer) echo "审核专家，负责文档、合同和规则符合性审核" ;;
        database) echo "数据库专家，负责数据库故障排查和性能优化" ;;
        legal) echo "法务顾问，负责合规、条款风险和法律审查" ;;
    esac
}

# Get bot skills
get_bot_skills() {
    local name="$1"
    case "$name" in
        reviewer) echo "review,compliance,audit" ;;
        database) echo "database,deadlock,performance" ;;
        legal) echo "legal,compliance,contract" ;;
    esac
}

# Get bot domains
get_bot_domains() {
    local name="$1"
    case "$name" in
        reviewer) echo "review,compliance,audit" ;;
        database) echo "database,deadlock,performance" ;;
        legal) echo "legal,compliance,contract" ;;
    esac
}

# Validate bot name
is_valid_bot() {
    local name="$1"
    for bot in $BOT_NAMES; do
        if [ "$bot" = "$name" ]; then
            return 0
        fi
    done
    return 1
}

# ============================================================================
# Utility Functions
# ============================================================================

ensure_dirs() {
    mkdir -p "$BASE_DIR" "$BCS_WSP" "$LOG_DIR" "$PID_DIR"
}

get_bot_pid_file() {
    local name="$1"
    echo "$PID_DIR/${name}.pid"
}

get_bot_dir() {
    local name="$1"
    local bot_id=$(get_bot_info "$name" id)
    echo "$BASE_DIR/$bot_id"
}

get_bot_log_file() {
    local name="$1"
    echo "$LOG_DIR/${name}.log"
}

# Get bot token from session file
get_bot_token() {
    local name="$1"
    local bot_dir=$(get_bot_dir "$name")
    local session_file="$bot_dir/.bcs/session.json"
    if [ -f "$session_file" ]; then
        local token=$(cat "$session_file" | grep -o '"token"[[:space:]]*:[[:space:]]*"[^"]*"' | grep -o '"[^"]*"$' | tr -d '"')
        echo "$token"
    else
        echo ""
    fi
}

# ============================================================================
# DingTalk Common Config
# ============================================================================

DINGTALK_DM_POLICY="open"
DINGTALK_ALLOWLIST='["*"]'
DINGTALK_REPLY_TO_MESSAGE="true"
DINGTALK_CARD_TEMPLATE_KEY="content"
DINGTALK_ENABLE_STREAMING_CARDS="true"

# ============================================================================
# BCS Management
# ============================================================================

start_bcs() {
    ensure_dirs

    # Check if already running
    if [ -f "$BCS_PID_FILE" ]; then
        local old_pid=$(cat "$BCS_PID_FILE")
        if ps -p "$old_pid" > /dev/null 2>&1; then
            warn "BCS 服务已在运行 (PID: $old_pid)"
            return 0
        fi
    fi

    # Check port
    local port_pid=$(lsof -ti :$BCS_PORT 2>/dev/null || true)
    if [ -n "$port_pid" ]; then
        warn "端口 $BCS_PORT 已被占用 (PID: $port_pid)"
        return 1
    fi

    header "启动 BCS 服务..."

    export BCS_DATA_DIR="$BCS_WSP"
    export MOLTIS_BCS_URL="$BCS_URL"
    export RUST_LOG="${RUST_LOG:-info}"

    cargo run --package bcs &> "$LOG_DIR/bcs.log" &
    local pid=$!
    echo $pid > "$BCS_PID_FILE"

    # Wait for startup
    sleep 3
    if ps -p $pid > /dev/null 2>&1; then
        pass "BCS 服务已启动 (PID: $pid)"
        pass "日志: $LOG_DIR/bcs.log"
    else
        fail "BCS 服务启动失败"
        rm -f "$BCS_PID_FILE"
        return 1
    fi
}

stop_bcs() {
    header "停止 BCS 服务..."

    local pid=""

    # Find by PID file
    if [ -f "$BCS_PID_FILE" ]; then
        local file_pid=$(cat "$BCS_PID_FILE")
        if ps -p "$file_pid" > /dev/null 2>&1; then
            pid="$file_pid"
            info "通过 PID 文件找到进程 (PID: $pid)"
        fi
    fi

    # Find by port
    if [ -z "$pid" ]; then
        pid=$(lsof -ti :$BCS_PORT 2>/dev/null || true)
        if [ -n "$pid" ]; then
            info "通过端口 $BCS_PORT 找到进程 (PID: $pid)"
        fi
    fi

    # Find by process name
    if [ -z "$pid" ]; then
        pid=$(pgrep -f 'target/(debug|release)/bcs' 2>/dev/null | head -1 || true)
        if [ -n "$pid" ]; then
            info "通过进程名找到进程 (PID: $pid)"
        fi
    fi

    if [ -z "$pid" ]; then
        warn "没有找到运行中的 BCS 服务"
        rm -f "$BCS_PID_FILE"
        return 0
    fi

    info "停止进程 (PID: $pid)..."
    kill "$pid" 2>/dev/null || true

    # Wait for graceful shutdown
    for i in {1..10}; do
        if ! ps -p "$pid" > /dev/null 2>&1; then
            break
        fi
        sleep 1
    done

    # Force kill if needed
    if ps -p "$pid" > /dev/null 2>&1; then
        warn "进程未响应，强制终止..."
        kill -9 "$pid" 2>/dev/null || true
        sleep 1
    fi

    if ! ps -p "$pid" > /dev/null 2>&1; then
        rm -f "$BCS_PID_FILE"
        pass "BCS 服务已停止"
    else
        fail "无法停止 BCS 服务"
        return 1
    fi
}

check_bcs() {
    if [ -f "$BCS_PID_FILE" ]; then
        local pid=$(cat "$BCS_PID_FILE")
        if ps -p "$pid" > /dev/null 2>&1; then
            echo "running:$pid"
            return 0
        fi
    fi

    local pid=$(lsof -ti :$BCS_PORT 2>/dev/null | head -1 || true)
    if [ -n "$pid" ]; then
        echo "running:$pid"
        return 0
    fi

    echo "stopped"
}

# ============================================================================
# Bot Management
# ============================================================================

setup_bot_dir() {
    local name="$1"
    local bot_id=$(get_bot_info "$name" id)
    local bot_port=$(get_bot_info "$name" port)
    local dingtalk_client_id=$(get_bot_info "$name" dingtalk_client_id)
    local dingtalk_client_secret=$(get_bot_info "$name" dingtalk_client_secret)
    local dingtalk_robot_code=$(get_bot_info "$name" dingtalk_robot_code)
    local dingtalk_card_template_id=$(get_bot_info "$name" dingtalk_card_template_id)

    local bot_dir=$(get_bot_dir "$name")

    mkdir -p "$bot_dir/config" "$bot_dir/workspace" "$bot_dir/skills/bcs-coordination" "$LOG_DIR" "$PID_DIR"

    # Write SOUL, RULES, MEMORY
    get_bot_soul "$name" > "$bot_dir/SOUL.md"
    get_bot_rules "$name" > "$bot_dir/RULES.md"
    get_bot_memory "$name" > "$bot_dir/MEMORY.md"

    cp "$bot_dir/SOUL.md" "$bot_dir/workspace/"
    cp "$bot_dir/RULES.md" "$bot_dir/workspace/"
    cp "$bot_dir/MEMORY.md" "$bot_dir/workspace/"

    # Copy provider keys if available
    if [ -f "$HOME/.config/moltis/provider_keys.json" ]; then
        cp "$HOME/.config/moltis/provider_keys.json" "$bot_dir/config/" 2>/dev/null || true
    fi

    # Copy the entire bcs-coordination skill directory (SKILL.md + references/)
    local skill_source_dir="$PROJECT_ROOT/crates/bcs-cli/bcs-coordination"
    if [ -d "$skill_source_dir" ]; then
        cp -r "$skill_source_dir" "$bot_dir/skills/"
        # Patch SKILL.md with bot-specific values
        if [ -f "$bot_dir/skills/bcs-coordination/SKILL.md" ]; then
            sed -i '' -e 's|./bcs-cli|bcs-cli|g' -e "s|<你的 Bot ID>|$bot_id|g" "$bot_dir/skills/bcs-coordination/SKILL.md"
        fi
    fi

    # Copy bcs-cli
    if [ -f "$BCS_CLI" ]; then
        cp "$BCS_CLI" "$bot_dir/skills/bcs-coordination/bcs-cli"
        chmod +x "$bot_dir/skills/bcs-coordination/bcs-cli"
    fi

    # Write moltis.toml
    cat > "$bot_dir/config/moltis.toml" << EOF
[server]
bind = "127.0.0.1"
port = $bot_port

[tls]
enabled = false
http_redirect_port = $((bot_port + 1000))

bots_base_dir = "$BASE_DIR"

[skills]
search_paths = ["$bot_dir/skills"]
auto_load = ["bcs-coordination"]

[tools.exec]
approval_mode = "never"
security_level = "permissive"

[providers."custom-llm-example-com"]
enabled = true

[providers.ollama]
enabled = false

[tools.exec.sandbox]
mode = "off"

[channels.bcn.my-bot]
url = "ws://127.0.0.1:$BCS_PORT/ws/bot"
bot_id = "$bot_id"
bot_name = "$bot_id"
dm_policy = "open"
enable_streaming = true
heartbeat_interval_secs = 60
reconnect_interval_secs = 5
connection_timeout_secs = 30

[channels.dingtalk.my_bot]
client_id = "$dingtalk_client_id"
client_secret = "$dingtalk_client_secret"
dm_policy = "$DINGTALK_DM_POLICY"
allowlist = $DINGTALK_ALLOWLIST
reply_to_message = $DINGTALK_REPLY_TO_MESSAGE
robot_code = "$dingtalk_robot_code"
card_template_id = "$dingtalk_card_template_id"
card_template_key = "$DINGTALK_CARD_TEMPLATE_KEY"
enable_streaming_cards = $DINGTALK_ENABLE_STREAMING_CARDS
EOF

    info "Bot 目录已设置: $name"
}

start_bot() {
    local name="$1"

    if ! is_valid_bot "$name"; then
        fail "未知的 bot: $name"
        echo "可用的 bot: $BOT_NAMES"
        return 1
    fi

    ensure_dirs

    local bot_id=$(get_bot_info "$name" id)
    local bot_port=$(get_bot_info "$name" port)
    local bot_dir=$(get_bot_dir "$name")
    local bot_log=$(get_bot_log_file "$name")
    local pid_file=$(get_bot_pid_file "$name")

    # Check if already running
    if [ -f "$pid_file" ]; then
        local old_pid=$(cat "$pid_file")
        if ps -p "$old_pid" > /dev/null 2>&1; then
            warn "Bot $name 已在运行 (PID: $old_pid)"
            return 0
        fi
    fi

    # Check port
    local port_pid=$(lsof -ti :$bot_port 2>/dev/null || true)
    if [ -n "$port_pid" ]; then
        warn "端口 $bot_port 已被占用 (PID: $port_pid)"
        return 1
    fi

    header "启动 Bot: $name..."

    # Setup directory
    setup_bot_dir "$name"

    # Check moltis binary
    if ! [ -f "$MOLTIS_CLI" ]; then
        fail "Moltis 二进制文件不存在: $MOLTIS_CLI"
        echo "运行: cargo build --manifest-path submodules/moltis/Cargo.toml --package moltis --bin moltis --no-default-features --features 'bcn,dingtalk,file-watcher,graphql,tls'"
        return 1
    fi

    # Start moltis
    MOLTIS_CONFIG_DIR="$bot_dir/config" \
    BOT_DATA_DIR="$bot_dir" \
    MOLTIS_WORKSPACE_PATH="$bot_dir/workspace" \
    MOLTIS_BCS_URL="$BCS_URL" \
    MOLTIS_BOT_ID="$bot_id" \
    MOLTIS_PORT="$bot_port" \
    PATH="$PROJECT_ROOT/target/debug:$PATH" \
        "$MOLTIS_CLI" --port "$bot_port" &> "$bot_log" &
    local pid=$!
    echo $pid > "$pid_file"

    # Wait for startup
    for i in $(seq 1 30); do
        if curl -s "http://localhost:$bot_port/health" > /dev/null 2>&1; then
            pass "Bot $name 已启动 (PID: $pid, 端口: $bot_port)"
            pass "日志: $bot_log"

            # Auto onboard
            info "等待 Bot 连接到 BCS..."
            sleep 3
            do_onboard_bot "$name"

            return 0
        fi
        sleep 1
    done

    fail "Bot $name 启动失败 (检查日志: $bot_log)"
    rm -f "$pid_file"
    return 1
}

stop_bot() {
    local name="$1"

    if ! is_valid_bot "$name"; then
        fail "未知的 bot: $name"
        echo "可用的 bot: $BOT_NAMES"
        return 1
    fi

    header "停止 Bot: $name..."

    local bot_port=$(get_bot_info "$name" port)
    local pid_file=$(get_bot_pid_file "$name")
    local redirect_port=$((bot_port + 1000))

    local pids=""

    # Find by PID file
    if [ -f "$pid_file" ]; then
        local file_pid=$(cat "$pid_file")
        if ps -p "$file_pid" > /dev/null 2>&1; then
            pids="$file_pid"
            info "通过 PID 文件找到进程 (PID: $file_pid)"
        fi
    fi

    # Find by port
    if [ -z "$pids" ]; then
        local port_pids=$(lsof -ti :$bot_port 2>/dev/null || true)
        local redirect_pids=$(lsof -ti :$redirect_port 2>/dev/null || true)
        pids=$(echo "$port_pids $redirect_pids" | xargs)

        if [ -n "$pids" ]; then
            info "通过端口找到进程: $pids"
        fi
    fi

    if [ -z "$pids" ]; then
        warn "没有找到运行中的 Bot: $name"
        rm -f "$pid_file"
        return 0
    fi

    # Kill processes
    info "停止进程: $pids"
    echo $pids | xargs kill 2>/dev/null || true
    sleep 1
    echo $pids | xargs kill -9 2>/dev/null || true

    rm -f "$pid_file"
    pass "Bot $name 已停止"
}

restart_bot() {
    local name="$1"
    stop_bot "$name"
    sleep 1
    start_bot "$name"
}

start_all_bots() {
    header "启动所有 Bot..."
    for name in $BOT_NAMES; do
        start_bot "$name" || true
    done
}

stop_all_bots() {
    header "停止所有 Bot..."
    for name in $BOT_NAMES; do
        stop_bot "$name" || true
    done
}

check_bot() {
    local name="$1"
    local bot_port=$(get_bot_info "$name" port)
    local pid_file=$(get_bot_pid_file "$name")

    if [ -f "$pid_file" ]; then
        local pid=$(cat "$pid_file")
        if ps -p "$pid" > /dev/null 2>&1; then
            echo "running:$pid"
            return 0
        fi
    fi

    if curl -s "http://localhost:$bot_port/health" > /dev/null 2>&1; then
        local pid=$(lsof -ti :$bot_port 2>/dev/null | head -1 || true)
        echo "running:$pid"
        return 0
    fi

    echo "stopped"
}

# ============================================================================
# Bot Onboarding
# ============================================================================

# Internal function to onboard a bot (without header, used by start_bot)
do_onboard_bot() {
    local name="$1"

    if ! is_valid_bot "$name"; then
        return 1
    fi

    # Check if BCS is running
    local bcs_status=$(check_bcs)
    if [[ "$bcs_status" != running:* ]]; then
        warn "BCS 未运行，跳过 $name 的 onboard"
        return 0
    fi

    local bot_id=$(get_bot_info "$name" id)
    local bot_dir=$(get_bot_dir "$name")

    # Get token
    local token=$(get_bot_token "$name")
    if [ -z "$token" ]; then
        info "等待 Bot 连接到 BCS..."
        sleep 5
        token=$(get_bot_token "$name")
    fi

    if [ -z "$token" ]; then
        warn "无法获取 $name 的 token，跳过 onboard"
        return 0
    fi

    # Check bcs-cli
    if ! [ -f "$BCS_CLI" ]; then
        warn "bcs-cli 不存在: $BCS_CLI，跳过 onboard"
        return 0
    fi

    # Get bot info
    local summary=$(get_bot_summary "$name")
    local domains=$(get_bot_domains "$name")
    local skills=$(get_bot_skills "$name")
    local worker_id=$(get_bot_info "$name" worker_id)

    # Build binding_channels JSON for antding
    local binding_channels=""
    if [ -n "$worker_id" ]; then
        binding_channels="{\"antding\":{\"binding_key\":\"$worker_id\"}}"
    fi

    info "执行 onboard: $bot_id..."
    if [ -n "$binding_channels" ]; then
        "$BCS_CLI" onboard \
            --token "$token" \
            --name "$bot_id" \
            --summary "$summary" \
            --domains "$domains" \
            --skills "$skills" \
            --scopes "production" \
            --binding-channels "$binding_channels" || {
            warn "$name onboard 失败"
            return 0
        }
    else
        "$BCS_CLI" onboard \
            --token "$token" \
            --name "$bot_id" \
            --summary "$summary" \
            --domains "$domains" \
            --skills "$skills" \
            --scopes "production" || {
            warn "$name onboard 失败"
            return 0
        }
    fi

    pass "$name onboard 成功!"
    return 0
}

# User command to onboard a bot (with header)
onboard_bot() {
    local name="$1"

    if ! is_valid_bot "$name"; then
        fail "未知的 bot: $name"
        echo "可用的 bot: $BOT_NAMES"
        return 1
    fi

    header "Onboard Bot: $name..."
    do_onboard_bot "$name"
    return $?
}

onboard_all_bots() {
    header "Onboard 所有 Bot..."

    # Wait for bots to connect
    info "等待 Bot 连接到 BCS..."
    sleep 5

    for name in $BOT_NAMES; do
        do_onboard_bot "$name" || true
    done

    pass "所有 Bot onboard 完成!"
}

# ============================================================================
# Global Commands
# ============================================================================

start_all() {
    header "启动所有服务..."
    start_bcs
    sleep 2
    start_all_bots
}

stop_all() {
    header "停止所有服务..."
    stop_all_bots
    stop_bcs
}

clean() {
    header "清空工作目录..."

    # Stop all services first
    stop_all_bots 2>/dev/null || true
    stop_bcs 2>/dev/null || true

    if [ -d "$BASE_DIR" ]; then
        rm -rf "$BASE_DIR"
        pass "已清空: $BASE_DIR"
    else
        info "目录不存在: $BASE_DIR"
    fi
}

show_status() {
    echo ""
    header "服务状态:"
    echo ""

    # BCS status
    local bcs_status=$(check_bcs)
    if [[ "$bcs_status" == running:* ]]; then
        local bcs_pid="${bcs_status#running:}"
        pass "BCS (端口 $BCS_PORT): 运行中 (PID: $bcs_pid)"
    else
        warn "BCS (端口 $BCS_PORT): 未运行"
    fi

    # Bot status
    for name in $BOT_NAMES; do
        local bot_port=$(get_bot_info "$name" port)
        local bot_status=$(check_bot "$name")
        if [[ "$bot_status" == running:* ]]; then
            local bot_pid="${bot_status#running:}"
            pass "$name (端口 $bot_port): 运行中 (PID: $bot_pid)"
        else
            warn "$name (端口 $bot_port): 未运行"
        fi
    done

    echo ""
}

# ============================================================================
# Main
# ============================================================================

usage() {
    echo "用法: $0 <command> [args...]"
    echo ""
    echo "BCS 管理:"
    echo "  start bcs              启动 BCS 服务"
    echo "  stop bcs               停止 BCS 服务"
    echo ""
    echo "Bot 管理:"
    echo "  start bot <name|all>   启动 bot (reviewer|database|legal|all)"
    echo "  stop bot <name|all>    停止 bot"
    echo "  restart bot <name>     重启 bot"
    echo "  onboard bot <name|all> Bot 注册到 BCS"
    echo ""
    echo "全局管理:"
    echo "  start all              启动 BCS + 所有 bot"
    echo "  stop all               停止 BCS + 所有 bot"
    echo "  clean                  清空 bcs_test_tmp 目录"
    echo "  status                 显示所有服务状态"
    echo ""
    echo "可用的 Bot: $BOT_NAMES"
    exit 1
}

# Parse command
if [ $# -lt 1 ]; then
    usage
fi

CMD="$1"
shift

case "$CMD" in
    start)
        if [ $# -lt 1 ]; then
            fail "缺少参数"
            usage
        fi
        TARGET="$1"
        shift

        case "$TARGET" in
            bcs) start_bcs ;;
            bot)
                if [ $# -lt 1 ]; then
                    fail "缺少 bot 名称"
                    usage
                fi
                BOT_NAME="$1"
                if [ "$BOT_NAME" = "all" ]; then
                    start_all_bots
                else
                    start_bot "$BOT_NAME"
                fi
                ;;
            all) start_all ;;
            *) fail "未知目标: $TARGET"; usage ;;
        esac
        ;;

    stop)
        if [ $# -lt 1 ]; then
            fail "缺少参数"
            usage
        fi
        TARGET="$1"
        shift

        case "$TARGET" in
            bcs) stop_bcs ;;
            bot)
                if [ $# -lt 1 ]; then
                    fail "缺少 bot 名称"
                    usage
                fi
                BOT_NAME="$1"
                if [ "$BOT_NAME" = "all" ]; then
                    stop_all_bots
                else
                    stop_bot "$BOT_NAME"
                fi
                ;;
            all) stop_all ;;
            *) fail "未知目标: $TARGET"; usage ;;
        esac
        ;;

    restart)
        if [ $# -lt 2 ]; then
            fail "缺少参数"
            usage
        fi
        TARGET="$1"
        BOT_NAME="$2"

        if [ "$TARGET" != "bot" ]; then
            fail "restart 仅支持 bot"
            usage
        fi

        if [ "$BOT_NAME" = "all" ]; then
            stop_all_bots
            sleep 1
            start_all_bots
        else
            restart_bot "$BOT_NAME"
        fi
        ;;

    onboard)
        if [ $# -lt 2 ]; then
            fail "缺少参数"
            usage
        fi
        TARGET="$1"
        BOT_NAME="$2"

        if [ "$TARGET" != "bot" ]; then
            fail "onboard 仅支持 bot"
            usage
        fi

        if [ "$BOT_NAME" = "all" ]; then
            onboard_all_bots
        else
            onboard_bot "$BOT_NAME"
        fi
        ;;

    clean) clean ;;
    status) show_status ;;
    *) fail "未知命令: $CMD"; usage ;;
esac
