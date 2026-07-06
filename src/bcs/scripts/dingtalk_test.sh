#!/bin/bash
# DingTalk Scene Group Integration Test Script
# 钉钉场景群集成测试脚本
#
# 测试流程:
#   1. 创建张三bot、DBA bot（基于moltis），启动BCS
#   2. 张三bot、DBA bot注册到 BCS
#   3. bot 完成 onboarding
#   4. 完成拉群流程，并且绑定到场景群，场景群id固定 cidIRmxI8Dx8tO6HZ5m1UhOCw==
#   5. 基于BCS.md中群聊流程注入初始上下文
#   6. 用户可以在场景群中继续交互（在钉钉上用户操作）
#
# USAGE:
#   ./dingtalk_test.sh build         # Build all binaries
#   ./dingtalk_test.sh setup         # Setup test environment
#   ./dingtalk_test.sh start         # Start BCS and bots
#   ./dingtalk_test.sh onboard       # Complete bot onboarding
#   ./dingtalk_test.sh create-group  # Create group and bind to scene group
#   ./dingtalk_test.sh inject        # Inject initial context
#   ./dingtalk_test.sh full          # Run full test flow
#   ./dingtalk_test.sh stop          # Stop all processes
#   ./dingtalk_test.sh status        # Show status

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Test configuration
BCS_PORT=21000
BCS_URL="http://localhost:${BCS_PORT}"
WS_URL="ws://localhost:${BCS_PORT}"

# Bot configuration
ZHANGSAN_PORT=20011
DBA_PORT=20071

# Moltis binary path (from submodule)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MOLTIS_BIN="${PROJECT_ROOT}/submodules/moltis/target/release/moltis"
MOLTIS_DEBUG_BIN="${PROJECT_ROOT}/submodules/moltis/target/debug/moltis"

# Data directories
BCS_DATA_DIR="${BCS_DATA_DIR:-/tmp/bcs_test}"
ZHANGSAN_DATA_DIR="${BCS_DATA_DIR}/zhangsan"
DBA_DATA_DIR="${BCS_DATA_DIR}/dba"

# Scene group configuration (固定场景群ID)
SCENE_GROUP_ID="cidIRmxI8Dx8tO6HZ5m1UhOCw=="

# PID files
PIDS_DIR="/tmp/bcs_dingtalk_test_pids"
BCS_PID_FILE="${PIDS_DIR}/bcs.pid"
ZHANGSAN_PID_FILE="${PIDS_DIR}/zhangsan.pid"
DBA_PID_FILE="${PIDS_DIR}/dba.pid"

# Log files
LOGS_DIR="/tmp/bcs_dingtalk_test_logs"

# Helper functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Initialize directories
init_dirs() {
    mkdir -p "$PIDS_DIR" "$LOGS_DIR" "$BCS_DATA_DIR" "$ZHANGSAN_DATA_DIR" "$DBA_DATA_DIR"
}

# Cleanup function
cleanup() {
    log_info "Cleaning up..."
    stop_all
    rm -rf "$PIDS_DIR" "$LOGS_DIR"
}

# Check if a port is in use
port_in_use() {
    local port=$1
    if lsof -Pi :"$port" -sTCP:LISTEN -t >/dev/null 2>&1; then
        return 0
    else
        return 1
    fi
}

# Wait for a service to be ready
wait_for_service() {
    local url=$1
    local name=$2
    local max_attempts=${3:-30}
    local attempt=0

    log_info "Waiting for $name to be ready..."
    while [ $attempt -lt $max_attempts ]; do
        if curl -s "$url" >/dev/null 2>&1; then
            log_success "$name is ready!"
            return 0
        fi
        attempt=$((attempt + 1))
        sleep 1
    done
    log_error "$name failed to start after ${max_attempts}s"
    return 1
}

# Get bot token from session file
# Session file format: {"bot_uuid":"...","token":"...","bcs_url":"..."}
get_bot_token() {
    local data_dir=$1
    local session_file="$data_dir/.bcs/session.json"
    if [ -f "$session_file" ]; then
        # Try bot_uuid format first (BCN session format)
        local token=$(cat "$session_file" | grep -o '"token"[[:space:]]*:[[:space:]]*"[^"]*"' | grep -o '"[^"]*"$' | tr -d '"')
        echo "$token"
    else
        echo ""
    fi
}

# Get bot_id from session file
get_bot_id() {
    local data_dir=$1
    local session_file="$data_dir/.bcs/session.json"
    if [ -f "$session_file" ]; then
        # BCN uses bot_uuid as the bot identifier
        local bot_uuid=$(cat "$session_file" | grep -o '"bot_uuid"[[:space:]]*:[[:space:]]*"[^"]*"' | grep -o '"[^"]*"$' | tr -d '"')
        echo "$bot_uuid"
    else
        echo ""
    fi
}

# ============================================
# Build
# ============================================
cmd_build() {
    log_info "Building BCS and bcs-cli..."
    cargo build --release --package bcs --package bcs-cli

    log_info "Building moltis from submodule (debug mode)..."
    if [ -d "${PROJECT_ROOT}/submodules/moltis" ]; then
        (cd "${PROJECT_ROOT}/submodules/moltis" && cargo build)
    else
        log_error "moltis submodule not found at ${PROJECT_ROOT}/submodules/moltis"
        return 1
    fi

    log_success "Build completed!"
}

# ============================================
# Create Moltis Config for Bot
# ============================================
create_moltis_config() {
    local config_dir=$1
    local bot_name=$2
    local bot_id=$3
    local port=$4
    local summary=$5
    local skills=$6
    local domains=$7

    mkdir -p "$config_dir"

    cat > "$config_dir/moltis.toml" << EOF
# Moltis config for $bot_name
[server]
bind = "127.0.0.1"
port = $port

[identity]
name = "$bot_name"
emoji = "🤖"

[channels.bcn.$bot_id]
url = "ws://127.0.0.1:21000/ws/bot"
bot_id = "$bot_id"
bot_name = "$bot_name"
dm_policy = "open"
summary = "$summary"
domains = [$domains]
skills = [$skills]
scopes = ["production"]
enable_streaming = true
EOF

    log_info "Created moltis config for $bot_name at $config_dir/moltis.toml"
}

# ============================================
# Setup BCS Coordination Skill for Bot
# ============================================
setup_bcs_skill() {
    local data_dir=$1
    local bot_name=$2

    local skill_dir="$data_dir/skills/bcs-coordination"

    # Copy the entire bcs-coordination skill directory (SKILL.md + references/)
    local skill_source_dir="${PROJECT_ROOT}/crates/bcs-cli/bcs-coordination"
    if [ ! -d "$skill_source_dir" ]; then
        log_error "BCS skill directory not found at $skill_source_dir"
        return 1
    fi

    cp -r "$skill_source_dir" "$data_dir/skills/"

    # Patch SKILL.md: replace ./bcs-cli with bcs-cli (to use PATH)
    if [ -f "$skill_dir/SKILL.md" ]; then
        sed -i '' 's|./bcs-cli|bcs-cli|g' "$skill_dir/SKILL.md"
    fi
    log_info "Added bcs-coordination skill for $bot_name at $skill_dir"
}

# ============================================
# Setup
# ============================================
cmd_setup() {
    log_info "Setting up test environment..."

    init_dirs

    # Clean up previous data
    rm -rf "$BCS_DATA_DIR"/*
    rm -rf "$ZHANGSAN_DATA_DIR"/*
    rm -rf "$DBA_DATA_DIR"/*

    # Create moltis configs for each bot
    create_moltis_config "$ZHANGSAN_DATA_DIR/config" "张三" "zhangsan" "$ZHANGSAN_PORT" \
        "开发助手，负责协调技术问题" \
        '"code_review", "coordination"' \
        '"development"'

    create_moltis_config "$DBA_DATA_DIR/config" "DBA" "dba" "$DBA_PORT" \
        "数据库专家，负责数据库问题排查" \
        '"database_admin", "deadlock_analysis"' \
        '"database"'

    # Setup BCS coordination skill for each bot
    setup_bcs_skill "$ZHANGSAN_DATA_DIR" "张三"
    setup_bcs_skill "$DBA_DATA_DIR" "DBA"

    log_success "Test environment setup completed!"
    log_info "Data directories:"
    log_info "  BCS: $BCS_DATA_DIR"
    log_info "  张三: $ZHANGSAN_DATA_DIR"
    log_info "  DBA: $DBA_DATA_DIR"
}

# ============================================
# Start Services
# ============================================
start_bcs() {
    if [ -f "$BCS_PID_FILE" ] && kill -0 "$(cat "$BCS_PID_FILE")" 2>/dev/null; then
        log_warn "BCS is already running (PID: $(cat "$BCS_PID_FILE"))"
        return 0
    fi

    if port_in_use "$BCS_PORT"; then
        log_error "Port $BCS_PORT is already in use"
        return 1
    fi

    log_info "Starting BCS server on port $BCS_PORT..."

    export BCS_DATA_DIR="$BCS_DATA_DIR"
    # export RUST_LOG=debug

    cargo run --release --package bcs 2>&1 > "$LOGS_DIR/bcs.log" &
    echo $! > "$BCS_PID_FILE"

    sleep 2

    if wait_for_service "$BCS_URL/health" "BCS" 30; then
        log_success "BCS started successfully (PID: $(cat "$BCS_PID_FILE"))"
        return 0
    else
        log_error "Failed to start BCS"
        return 1
    fi
}

start_bot() {
    local bot_name=$1
    local bot_port=$2
    local data_dir=$3
    local pid_file=$4
    local log_file="$LOGS_DIR/${bot_name}.log"

    if [ -f "$pid_file" ] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
        log_warn "$bot_name is already running (PID: $(cat "$pid_file"))"
        return 0
    fi

    log_info "Starting $bot_name (moltis) on port $bot_port..."

    # Determine which moltis binary to use (default to debug)
    local moltis_cmd=""
    if [ -f "$MOLTIS_DEBUG_BIN" ]; then
        moltis_cmd="$MOLTIS_DEBUG_BIN"
    elif [ -f "$MOLTIS_BIN" ]; then
        log_warn "Debug binary not found, using release binary"
        moltis_cmd="$MOLTIS_BIN"
    else
        log_error "moltis binary not found at $MOLTIS_DEBUG_BIN or $MOLTIS_BIN"
        log_info "Please build moltis first: cd submodules/moltis && cargo build"
        return 1
    fi

    log_info "Using moltis binary: $moltis_cmd"

    # Determine bcs-cli binary path and add to PATH (prefer debug)
    local bcs_cli_dir=""
    if [ -f "${PROJECT_ROOT}/target/debug/bcs-cli" ]; then
        bcs_cli_dir="${PROJECT_ROOT}/target/debug"
        log_info "Using debug bcs-cli"
    elif [ -f "${PROJECT_ROOT}/target/release/bcs-cli" ]; then
        bcs_cli_dir="${PROJECT_ROOT}/target/release"
        log_info "Using release bcs-cli"
    else
        log_error "bcs-cli binary not found in target/debug or target/release"
        log_info "Please build bcs-cli first: cargo build --package bcs-cli"
        return 1
    fi

    # Start moltis bot with BCN plugin
    # Config is in config_dir, data is in data_dir
    local config_dir="$data_dir/config"

    BOT_DATA_DIR="$data_dir" \
    PATH="$bcs_cli_dir:$PATH" \
    "$moltis_cmd" --config-dir "$config_dir" --port "$bot_port" 2>&1 > "$log_file" &

    echo $! > "$pid_file"

    sleep 3

    # Check if bot process is running
    if kill -0 "$(cat "$pid_file")" 2>/dev/null; then
        log_success "$bot_name started successfully (PID: $(cat "$pid_file"))"
        return 0
    else
        log_error "Failed to start $bot_name"
        return 1
    fi
}

cmd_start() {
    init_dirs

    # Start BCS first
    start_bcs || exit 1

    # Wait for BCS to be fully ready
    sleep 2

    # Start bots
    start_bot "zhangsan" "$ZHANGSAN_PORT" "$ZHANGSAN_DATA_DIR" "$ZHANGSAN_PID_FILE" || exit 1
    start_bot "dba" "$DBA_PORT" "$DBA_DATA_DIR" "$DBA_PID_FILE" || exit 1

    log_success "All services started!"
    log_info "Use './dingtalk_test.sh status' to check status"
    log_info "Use './dingtalk_test.sh onboard' to complete bot onboarding"
}

# ============================================
# Stop Services
# ============================================
cmd_stop() {
    log_info "Stopping all services..."

    # Stop bots first
    for pid_file in "$DBA_PID_FILE" "$ZHANGSAN_PID_FILE"; do
        if [ -f "$pid_file" ]; then
            local pid=$(cat "$pid_file")
            if kill -0 "$pid" 2>/dev/null; then
                log_info "Stopping process $pid..."
                kill "$pid" 2>/dev/null || true
                sleep 1
                # Force kill if still running
                if kill -0 "$pid" 2>/dev/null; then
                    kill -9 "$pid" 2>/dev/null || true
                fi
            fi
            rm -f "$pid_file"
        fi
    done

    # Stop BCS
    if [ -f "$BCS_PID_FILE" ]; then
        local pid=$(cat "$BCS_PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            log_info "Stopping BCS (PID: $pid)..."
            kill "$pid" 2>/dev/null || true
            sleep 1
            if kill -0 "$pid" 2>/dev/null; then
                kill -9 "$pid" 2>/dev/null || true
            fi
        fi
        rm -f "$BCS_PID_FILE"
    fi

    # Clean up any remaining processes on test ports
    for port in "$BCS_PORT" "$ZHANGSAN_PORT" "$DBA_PORT"; do
        local pids=$(lsof -Pi :"$port" -sTCP:LISTEN -t 2>/dev/null || true)
        if [ -n "$pids" ]; then
            echo "$pids" | xargs kill -9 2>/dev/null || true
        fi
    done

    log_success "All services stopped!"
}

stop_all() {
    cmd_stop
}

# ============================================
# Status
# ============================================
cmd_status() {
    echo "========================================"
    echo "DingTalk Test Environment Status"
    echo "========================================"

    # Check BCS
    if [ -f "$BCS_PID_FILE" ] && kill -0 "$(cat "$BCS_PID_FILE")" 2>/dev/null; then
        echo -e "BCS Server: ${GREEN}Running${NC} (PID: $(cat "$BCS_PID_FILE"))"
        if curl -s "$BCS_URL/health" >/dev/null 2>&1; then
            echo -e "  Health: ${GREEN}OK${NC}"
        else
            echo -e "  Health: ${RED}Not Responding${NC}"
        fi
    else
        echo -e "BCS Server: ${RED}Stopped${NC}"
    fi

    # Check bots
    for bot_name in zhangsan dba; do
        local pid_file="${PIDS_DIR}/${bot_name}.pid"
        if [ -f "$pid_file" ] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
            echo -e "${bot_name}: ${GREEN}Running${NC} (PID: $(cat "$pid_file"))"
        else
            echo -e "${bot_name}: ${RED}Stopped${NC}"
        fi
    done

    echo ""
    echo "Data Directories:"
    echo "  BCS: $BCS_DATA_DIR"
    echo "  张三: $ZHANGSAN_DATA_DIR"
    echo "  DBA: $DBA_DATA_DIR"

    # Show bot tokens if available
    echo ""
    echo "Bot Tokens:"
    for bot_dir in "$ZHANGSAN_DATA_DIR" "$DBA_DATA_DIR"; do
        local bot_name=$(basename "$bot_dir")
        local token=$(get_bot_token "$bot_dir")
        if [ -n "$token" ]; then
            echo "  $bot_name: ${token:0:20}..."
        else
            echo "  $bot_name: Not available (need onboarding)"
        fi
    done
}

# ============================================
# Bot Onboarding
# ============================================
cmd_onboard() {
    log_info "Completing bot onboarding..."

    # Wait for bots to connect and get tokens
    log_info "Waiting for bots to connect to BCS..."
    sleep 5

    # Onboard 张三
    local zhangsan_token=$(get_bot_token "$ZHANGSAN_DATA_DIR")
    if [ -z "$zhangsan_token" ]; then
        log_warn "张三 token not found, checking session file..."
        # Try to find token in logs or wait longer
        sleep 5
        zhangsan_token=$(get_bot_token "$ZHANGSAN_DATA_DIR")
    fi

    if [ -n "$zhangsan_token" ]; then
        log_info "Onboarding 张三..."
        cargo run --release --package bcs-cli -- onboard \
            --token "$zhangsan_token" \
            --name "张三" \
            --summary "开发助手，负责协调技术问题" \
            --domains "development" \
            --skills "code_review,coordination" \
            --scopes "production" || {
            log_error "Failed to onboard 张三"
            return 1
        }
        log_success "张三 onboarded successfully!"
    else
        log_error "Cannot find 张三 token. Make sure bot is connected to BCS."
        return 1
    fi

    # Onboard DBA
    local dba_token=$(get_bot_token "$DBA_DATA_DIR")
    if [ -z "$dba_token" ]; then
        sleep 5
        dba_token=$(get_bot_token "$DBA_DATA_DIR")
    fi

    if [ -n "$dba_token" ]; then
        log_info "Onboarding DBA..."
        cargo run --release --package bcs-cli -- onboard \
            --token "$dba_token" \
            --name "DBA" \
            --summary "数据库专家，负责数据库问题排查" \
            --domains "database" \
            --skills "database_admin,deadlock_analysis" \
            --scopes "production" || {
            log_error "Failed to onboard DBA"
            return 1
        }
        log_success "DBA onboarded successfully!"
    else
        log_error "Cannot find DBA token. Make sure bot is connected to BCS."
        return 1
    fi

    log_success "All bots onboarded!"
}

# ============================================
# Create Group
# ============================================
cmd_create_group() {
    log_info "Creating BCS group..."

    # Get 张三 token
    local zhangsan_token=$(get_bot_token "$ZHANGSAN_DATA_DIR")
    if [ -z "$zhangsan_token" ]; then
        log_error "张三 token not found. Please run onboard first."
        return 1
    fi

    # Get bot IDs
    local zhangsan_id=$(get_bot_id "$ZHANGSAN_DATA_DIR")
    local dba_id=$(get_bot_id "$DBA_DATA_DIR")

    if [ -z "$zhangsan_id" ]; then
        log_error "张三 bot_id not found"
        return 1
    fi

    if [ -z "$dba_id" ]; then
        log_error "DBA bot_id not found"
        return 1
    fi

    log_info "Using bots: 张三($zhangsan_id), DBA($dba_id)"

    # Create group directly
    log_info "Creating group..."
    local create_response=$(cargo run --release --package bcs-cli -- create-group \
        --token "$zhangsan_token" \
        --driver "$zhangsan_id" \
        --participants "${zhangsan_id}:driver,${dba_id}:consultant" 2>&1)

    echo "$create_response"

    # Extract group ID from response
    local group_id=$(echo "$create_response" | grep -oE 'ID: ([0-9a-f-]+)' | head -1 | sed 's/ID: //')

    if [ -z "$group_id" ]; then
        log_error "Failed to create group or extract group ID"
        return 1
    fi

    log_success "Group created: $group_id"

    # Save group ID for later use
    echo "$group_id" > "$PIDS_DIR/last_group_id"

    log_success "Group created successfully!"
    log_info "Group ID: $group_id"

    return 0
}

# ============================================
# Inject Initial Context
# ============================================
cmd_inject() {
    log_info "Injecting initial context to group..."

    # Get group ID
    if [ ! -f "$PIDS_DIR/last_group_id" ]; then
        log_error "No group ID found. Please run create-group first."
        return 1
    fi

    local group_id=$(cat "$PIDS_DIR/last_group_id")
    log_info "Using group: $group_id"

    # Get 张三 token
    local zhangsan_token=$(get_bot_token "$ZHANGSAN_DATA_DIR")
    if [ -z "$zhangsan_token" ]; then
        log_error "张三 token not found"
        return 1
    fi

    # Inject group context message as system/initial context
    log_info "Sending initial context to group..."

    local context_message='[GROUP CONTEXT]
会话ID: '"$group_id"'
参与者: 张三(协调者), DBA(顾问)
主题: "数据库死锁问题排查协作"
目标: 诊断并解决数据库死锁问题
结束条件: 问题诊断完成，解决方案确认

[Bot 协作指引]
- 张三作为协调者，负责驱动讨论和汇总结论
- DBA作为数据库专家，负责技术分析
- 无 @mention 时，协调者应响应
- 被 @mention 的 Bot 必须响应
- 需要多视角时使用 bcs_fuse 融合上下文
[/GROUP CONTEXT]

欢迎加入协作群组！当前任务：数据库死锁问题排查。'

    local inject_response=$(curl -s -X POST \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $zhangsan_token" \
        -d "{
            \"from\": \"system\",
            \"message\": $(echo "$context_message" | jq -Rs .)
        }" \
        "$BCS_URL/groups/$group_id/chat" 2>&1)

    echo "Inject response: $inject_response"

    log_success "Initial context injected!"
    log_info ""
    log_info "========================================"
    log_info "DingTalk Scene Group Test Setup Complete"
    log_info "========================================"
    log_info ""
    log_info "Group ID: $group_id"
    log_info "Scene Group ID: $SCENE_GROUP_ID"
    log_info ""
    log_info "Next steps:"
    log_info "  1. Check bot logs: tail -f $LOGS_DIR/zhangsan.log $LOGS_DIR/dba.log"
    log_info "  2. Check BCS logs: tail -f $LOGS_DIR/bcs.log"
    log_info "  3. Test in DingTalk scene group: $SCENE_GROUP_ID"
    log_info "  4. Send messages in the scene group to see BCS routing"
    log_info ""
}

# ============================================
# Send Test Message
# ============================================
cmd_send() {
    local content="${1:-测试消息}"
    local from="${2:-zhangsan}"

    if [ ! -f "$PIDS_DIR/last_group_id" ]; then
        log_error "No group ID found. Please run create-group first."
        return 1
    fi

    local group_id=$(cat "$PIDS_DIR/last_group_id")
    local zhangsan_token=$(get_bot_token "$ZHANGSAN_DATA_DIR")

    log_info "Sending message to group $group_id..."

    local response=$(curl -s -X POST \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $zhangsan_token" \
        -d "{
            \"from\": \"$from\",
            \"content\": \"$content\",
            \"message_type\": \"text\"
        }" \
        "$BCS_URL/groups/$group_id/chat" 2>&1)

    echo "Response: $response"
}

# ============================================
# List Groups
# ============================================
cmd_list_groups() {
    log_info "Listing all groups..."

    local zhangsan_token=$(get_bot_token "$ZHANGSAN_DATA_DIR")
    if [ -z "$zhangsan_token" ]; then
        log_error "Token not found"
        return 1
    fi

    curl -s -H "Authorization: Bearer $zhangsan_token" "$BCS_URL/groups" | jq .
}

# ============================================
# Get Group Details
# ============================================
cmd_get_group() {
    local group_id="${1:-}"

    if [ -z "$group_id" ] && [ -f "$PIDS_DIR/last_group_id" ]; then
        group_id=$(cat "$PIDS_DIR/last_group_id")
    fi

    if [ -z "$group_id" ]; then
        log_error "No group ID specified or found"
        return 1
    fi

    log_info "Getting group details: $group_id"

    local zhangsan_token=$(get_bot_token "$ZHANGSAN_DATA_DIR")
    if [ -z "$zhangsan_token" ]; then
        log_error "Token not found"
        return 1
    fi

    curl -s -H "Authorization: Bearer $zhangsan_token" "$BCS_URL/groups/$group_id" | jq .
}

# ============================================
# Get Group Messages
# ============================================
cmd_messages() {
    local group_id="${1:-}"

    if [ -z "$group_id" ] && [ -f "$PIDS_DIR/last_group_id" ]; then
        group_id=$(cat "$PIDS_DIR/last_group_id")
    fi

    if [ -z "$group_id" ]; then
        log_error "No group ID specified or found"
        return 1
    fi

    log_info "Getting messages for group: $group_id"

    local zhangsan_token=$(get_bot_token "$ZHANGSAN_DATA_DIR")
    if [ -z "$zhangsan_token" ]; then
        log_error "Token not found"
        return 1
    fi

    curl -s -H "Authorization: Bearer $zhangsan_token" "$BCS_URL/groups/$group_id/messages" | jq .
}

# ============================================
# Full Test Flow
# ============================================
cmd_full() {
    log_info "Running full DingTalk test flow..."

    cmd_build
    cmd_setup
    cmd_start

    log_info "Waiting for services to stabilize..."
    sleep 5

    cmd_onboard
    cmd_create_group
    cmd_inject

    log_success "Full test flow completed!"
    log_info ""
    log_info "Current status:"
    cmd_status
}

# ============================================
# Main
# ============================================
main() {
    local cmd="${1:-help}"

    case "$cmd" in
        build)
            cmd_build
            ;;
        setup)
            cmd_setup
            ;;
        start)
            cmd_start
            ;;
        stop)
            cmd_stop
            ;;
        status)
            cmd_status
            ;;
        onboard)
            cmd_onboard
            ;;
        create-group)
            cmd_create_group
            ;;
        inject)
            cmd_inject
            ;;
        send)
            shift
            cmd_send "$@"
            ;;
        list-groups)
            cmd_list_groups
            ;;
        get-group)
            shift
            cmd_get_group "$@"
            ;;
        messages)
            shift
            cmd_messages "$@"
            ;;
        full)
            cmd_full
            ;;
        cleanup)
            cleanup
            ;;
        help|--help|-h)
            echo "DingTalk Scene Group Test Script"
            echo ""
            echo "USAGE:"
            echo "  ./dingtalk_test.sh <command>"
            echo ""
            echo "Commands:"
            echo "  build         Build all binaries (bcs, bcs-cli, moltis from submodule)"
            echo "  setup         Setup test environment (create directories)"
            echo "  start         Start BCS server and bots (using submodule moltis)"
            echo "  stop          Stop all services"
            echo "  status        Show current status of all services"
            echo "  onboard       Complete bot onboarding with BCS"
            echo "  create-group  Create BCS group and bind to scene group"
            echo "  inject        Inject initial context to group"
            echo "  send [msg]    Send a test message to the group"
            echo "  list-groups   List all groups"
            echo "  get-group [id] Get group details"
            echo "  messages [id] Get group messages"
            echo "  full          Run full test flow (build -> setup -> start -> onboard -> create-group -> inject)"
            echo "  cleanup       Stop services and clean up all data"
            echo "  help          Show this help message"
            echo ""
            echo "Configuration:"
            echo "  Moltis binary: $MOLTIS_BIN"
            echo "  Scene Group ID: $SCENE_GROUP_ID"
            echo ""
            echo "Quick Start:"
            echo "  ./dingtalk_test.sh full"
            echo ""
            echo "Step by Step:"
            echo "  ./dingtalk_test.sh build"
            echo "  ./dingtalk_test.sh setup"
            echo "  ./dingtalk_test.sh start"
            echo "  ./dingtalk_test.sh onboard"
            echo "  ./dingtalk_test.sh create-group"
            echo "  ./dingtalk_test.sh inject"
            ;;
        *)
            log_error "Unknown command: $cmd"
            echo "Use './dingtalk_test.sh help' for usage information"
            exit 1
            ;;
    esac
}

main "$@"
