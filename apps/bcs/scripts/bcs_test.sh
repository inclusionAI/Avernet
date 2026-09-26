#!/bin/bash
# BCS Scenario Tests: S1 & S2
#
# S1: 个人助理 (Personal Assistant)
#   Phase 1: BCN plugin connects to BCS, registers, onboards
#   Phase 2: User sends "我今天要做什么", bot responds using its MEMORY context
#
# S2: 专家咨询 (Expert Consultation)
#   Phase 1: 审理-Bot connects to BCS, registers, onboards
#   Phase 2a: User asks private data question → bot refuses (RULES enforcement)
#   Phase 2b: User asks for contract review → bot provides expert analysis
#
# Prerequisites:
#   cargo build -p bcs -p bcs-cli
#   cargo build --manifest-path submodules/moltis/Cargo.toml \
#     --package moltis --bin moltis \
#     --no-default-features --features "dingtalk,file-watcher,graphql,tls,bcn"
#
# Usage:
#   ./scripts/bcs_test.sh              # run all scenarios (S1 + S2)
#   ./scripts/bcs_test.sh s1           # run S1 only
#   ./scripts/bcs_test.sh s2           # run S2 only
#   ./scripts/bcs_test.sh start        # start BCS only
#   ./scripts/bcs_test.sh --no-cleanup # keep processes and temp dir after run

set -euo pipefail

# ============================================================================
# Config
# ============================================================================

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOTS_BASE_DIR="$PROJECT_ROOT/bcs_test_tmp"
BCS_DATA_DIR="$PROJECT_ROOT/bcs_test_tmp/bcs_data"
BCS_PORT=21000
BCS_URL="http://localhost:$BCS_PORT"
BCS_BIN="$PROJECT_ROOT/target/debug/bcs"
BCS_CLI="$PROJECT_ROOT/target/debug/bcs-cli"
MOLTIS_CLI="$PROJECT_ROOT/submodules/moltis/target/debug/moltis"

LOG_DIR="$BOTS_BASE_DIR/logs"

# S1 config
S1_BOT_ID="张三"
S1_BOT_PORT=20011
S1_BOT_DIR="$BOTS_BASE_DIR/$S1_BOT_ID"
S1_SESSION_FILE="$S1_BOT_DIR/.bcs/session.json"

# S2 config
S2_BOT_ID="审理"
S2_BOT_PORT=20041
S2_BOT_DIR="$BOTS_BASE_DIR/$S2_BOT_ID"
S2_SESSION_FILE="$S2_BOT_DIR/.bcs/session.json"

BCS_PID=""
S1_MOLTIS_PID=""
S2_MOLTIS_PID=""
TESTS_PASSED=0
TESTS_FAILED=0

# Parse arguments
NO_CLEANUP=0
CMD="all"
for arg in "$@"; do
    case "$arg" in
        --no-cleanup) NO_CLEANUP=1 ;;
        s1|s2|start|all) CMD="$arg" ;;
        --help|-h)
            echo "Usage: $(basename "$0") [COMMAND] [OPTIONS]"
            echo ""
            echo "Commands:"
            echo "  all      Run S1 + S2 (default)"
            echo "  s1       Run S1 only: 个人助理 (Personal Assistant)"
            echo "  s2       Run S2 only: 专家咨询 (Expert Consultation)"
            echo "  start    Start BCS server only"
            echo ""
            echo "Options:"
            echo "  --no-cleanup  Keep processes and temp dir after run"
            echo "  --help        Show this help"
            echo ""
            echo "Logs: $BOTS_BASE_DIR/logs/"
            exit 0
            ;;
        *) echo "Unknown argument: $arg"; exit 1 ;;
    esac
done

# ============================================================================
# Colors
# ============================================================================

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

pass() { echo -e "  ${GREEN}✓${NC} $1"; TESTS_PASSED=$((TESTS_PASSED + 1)); }
fail() { echo -e "  ${RED}✗${NC} $1"; TESTS_FAILED=$((TESTS_FAILED + 1)); }
info() { echo -e "  ${CYAN}→${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }

# ============================================================================
# Environment Cleanup
# ============================================================================

# Kill any stale BCS/Moltis processes from previous runs
clean_env() {
    info "Cleaning stale processes..."

    # Kill stale BCS processes
    pkill -f "target/debug/bcs" 2>/dev/null || true

    # Kill stale Moltis processes on test ports (main + HTTP redirect ports)
    local s1_redirect=$((S1_BOT_PORT + 1000))
    local s2_redirect=$((S2_BOT_PORT + 1000))
    local stale_pids=$(lsof -ti :$S1_BOT_PORT,:$S2_BOT_PORT,:$BCS_PORT,:$s1_redirect,:$s2_redirect 2>/dev/null || true)
    if [ -n "$stale_pids" ]; then
        echo "$stale_pids" | xargs kill 2>/dev/null || true
        sleep 1
        echo "$stale_pids" | xargs kill -9 2>/dev/null || true
    fi

    # Clean temp directory
    if [ -d "$BOTS_BASE_DIR" ]; then
        rm -rf "$BOTS_BASE_DIR"
    fi

    pass "Environment cleaned"
}

# ============================================================================
# Cleanup
# ============================================================================

cleanup() {
    if [ "$NO_CLEANUP" -eq 1 ]; then
        echo ""
        info "Skipping cleanup (--no-cleanup). Processes still running:"
        [ -n "$BCS_PID" ]        && info "  BCS       PID=$BCS_PID        logs=$LOG_DIR/bcs.log"
        [ -n "$S1_MOLTIS_PID" ]  && info "  S1 Moltis PID=$S1_MOLTIS_PID  logs=$LOG_DIR/moltis_s1.log"
        [ -n "$S2_MOLTIS_PID" ]  && info "  S2 Moltis PID=$S2_MOLTIS_PID  logs=$LOG_DIR/moltis_s2.log"
        info "  BCS data dir: $BCS_DATA_DIR"
        return
    fi
    echo ""
    info "Cleaning up..."
    [ -n "$S1_MOLTIS_PID" ] && kill "$S1_MOLTIS_PID" 2>/dev/null || true
    [ -n "$S2_MOLTIS_PID" ] && kill "$S2_MOLTIS_PID" 2>/dev/null || true
    [ -n "$BCS_PID" ]       && kill "$BCS_PID"        2>/dev/null || true
    sleep 1
    [ -n "$S1_MOLTIS_PID" ] && kill -9 "$S1_MOLTIS_PID" 2>/dev/null || true
    [ -n "$S2_MOLTIS_PID" ] && kill -9 "$S2_MOLTIS_PID" 2>/dev/null || true
    [ -n "$BCS_PID" ]       && kill -9 "$BCS_PID"        2>/dev/null || true
}
trap cleanup EXIT INT TERM

# ============================================================================
# Start BCS
# ============================================================================

start_bcs() {
    if ! [ -f "$BCS_BIN" ]; then
        echo "BCS binary not found: $BCS_BIN"
        echo "Run: cargo build -p bcs"
        exit 1
    fi

    info "BCS data dir: $BCS_DATA_DIR"

    BCS_DATA_DIR="$BCS_DATA_DIR" \
    MOLTIS_BCS_PORT="$BCS_PORT" \
    RUST_LOG="info" \
        "$BCS_BIN" &> "$LOG_DIR/bcs.log" &
    BCS_PID=$!

    for i in $(seq 1 10); do
        if curl -sk "$BCS_URL/health" > /dev/null 2>&1; then
            pass "BCS started on port $BCS_PORT (PID $BCS_PID)"
            return 0
        fi
        sleep 1
    done

    fail "BCS failed to start (check $LOG_DIR/bcs.log)"
    return 1
}

# ============================================================================
# Setup & Start Moltis helpers
# ============================================================================

setup_bot_dir() {
    local bot_dir="$1"
    local bot_id="$2"
    local bot_port="$3"
    local soul="$4"
    local rules="$5"
    local memory="$6"
    local summary="$7"

    mkdir -p "$bot_dir/config" "$bot_dir/workspace" "$bot_dir/skills/bcs-coordination" "$LOG_DIR"

    printf '%s\n' "$soul"   > "$bot_dir/SOUL.md"
    printf '%s\n' "$rules"  > "$bot_dir/RULES.md"
    printf '%s\n' "$memory" > "$bot_dir/MEMORY.md"

    cp "$bot_dir/SOUL.md"   "$bot_dir/workspace/"
    cp "$bot_dir/RULES.md"  "$bot_dir/workspace/"
    cp "$bot_dir/MEMORY.md" "$bot_dir/workspace/"

    if [ -f "$HOME/.config/moltis/provider_keys.json" ]; then
        cp "$HOME/.config/moltis/provider_keys.json" "$bot_dir/config/" 2>/dev/null || true
    fi

    # Copy the entire bcs-coordination skill directory (SKILL.md + references/)
    local skill_source_dir="$PROJECT_ROOT/crates/bcs-cli/bcs-coordination"
    if [ -d "$skill_source_dir" ]; then
        cp -r "$skill_source_dir" "$bot_dir/skills/"
        # Patch SKILL.md with bot-specific values
        if [ -f "$bot_dir/skills/bcs-coordination/SKILL.md" ]; then
            sed -i '' "s/<你的Bot ID>/$bot_id/g" "$bot_dir/skills/bcs-coordination/SKILL.md"
        fi
    fi

    if [ -f "$BCS_CLI" ]; then
        cp "$BCS_CLI" "$bot_dir/skills/bcs-coordination/bcs-cli"
        chmod +x "$bot_dir/skills/bcs-coordination/bcs-cli"
    fi

    cat > "$bot_dir/config/moltis.toml" << EOF
[server]
bind = "127.0.0.1"
port = $bot_port

[tls]
enabled = false
http_redirect_port = $((bot_port + 1000))

bots_base_dir = "$BOTS_BASE_DIR"

[skills]
search_paths = ["$bot_dir/skills"]
auto_load = ["bcs-coordination"]

[tools.exec]
approval_mode = "never"
security_level = "permissive"

[providers."custom-antchat-alipay-com"]
enabled = true

[providers.ollama]
enabled = false

[channels.bcn.my-bot]
url = "ws://127.0.0.1:$BCS_PORT/ws/bot"
bot_id = "$bot_id"
bot_name = "$bot_id"
dm_policy = "open"
model = "Kimi-K2-Thinking"
enable_streaming = true
heartbeat_interval_secs = 60
reconnect_interval_secs = 5
connection_timeout_secs = 30
EOF
}

start_moltis() {
    local bot_dir="$1"
    local bot_id="$2"
    local bot_port="$3"
    local log_file="$4"

    if ! [ -f "$MOLTIS_CLI" ]; then
        echo "Moltis binary not found: $MOLTIS_CLI"
        echo "Run: cargo build --manifest-path submodules/moltis/Cargo.toml --package moltis --bin moltis --no-default-features --features 'bcn,dingtalk,file-watcher,graphql,tls'"
        exit 1
    fi

    info "Moltis data dir: $bot_dir"

    local pid
    MOLTIS_CONFIG_DIR="$bot_dir/config" \
    BOT_DATA_DIR="$bot_dir" \
    MOLTIS_WORKSPACE_PATH="$bot_dir/workspace" \
    MOLTIS_BCS_URL="$BCS_URL" \
    MOLTIS_BOT_ID="$bot_id" \
    MOLTIS_PORT="$bot_port" \
    PATH="$PROJECT_ROOT/target/debug:$PATH" \
        "$MOLTIS_CLI" --port "$bot_port" &> "$log_file" &
    pid=$!

    for i in $(seq 1 30); do
        if curl -s "http://localhost:$bot_port/health" > /dev/null 2>&1; then
            pass "Moltis ($bot_id) started on port $bot_port (PID $pid)"
            echo "$pid"
            return 0
        fi
        sleep 1
    done

    fail "Moltis ($bot_id) failed to start (check $log_file)"
    echo ""
    return 1
}

# ============================================================================
# Phase: Connection & Registration (shared)
# ============================================================================

check_connection() {
    local bot_id="$1"
    local session_file="$2"
    local token_out="$3"   # name of variable to store token
    local summary="$4"
    local skills="$5"

    # Wait for BCN to write session.json
    info "Waiting for $bot_id BCN to connect to BCS..."
    local connected=0
    for i in $(seq 1 30); do
        if [ -f "$session_file" ]; then
            if python3 -c "
import json, sys
d = json.load(open('$session_file'))
assert 'token' in d and 'bot_uuid' in d
" 2>/dev/null; then
                connected=1
                break
            fi
        fi
        sleep 1
    done

    if [ "$connected" -eq 0 ]; then
        fail "$bot_id: BCN did not connect to BCS within 30s (no valid session.json)"
        return 1
    fi

    sleep 3

    local bot_uuid token
    bot_uuid=$(python3 -c "import json; print(json.load(open('$session_file'))['bot_uuid'])" 2>/dev/null)
    token=$(python3 -c "import json; print(json.load(open('$session_file'))['token'])" 2>/dev/null)
    pass "$bot_id: BCN connected to BCS (bot_uuid: $bot_uuid)"

    # Onboard
    if ! [ -f "$BCS_CLI" ]; then
        fail "bcs-cli not found: $BCS_CLI"
        return 1
    fi

    local onboard_result
    onboard_result=$("$BCS_CLI" --json --url "$BCS_URL" onboard \
        --token "$token" \
        --name "$bot_id" \
        --summary "$summary" \
        --skills "$skills" 2>&1)

    if echo "$onboard_result" | grep -q '"bot_id"\|onboarded\|success'; then
        pass "$bot_id: Bot onboarded to BCS"
    else
        fail "$bot_id: Onboard failed: $onboard_result"
        return 1
    fi

    # Verify in registry
    local bots_result
    bots_result=$(curl -sk -H "Authorization: Bearer $token" "$BCS_URL/bots" 2>/dev/null)
    if echo "$bots_result" | grep -q "$bot_id\|$bot_uuid"; then
        pass "$bot_id: Bot visible in BCS registry"
    else
        warn "$bot_id: Bot not found in registry response: $bots_result"
    fi

    # Export token to caller
    eval "$token_out='$token'"
    return 0
}

# ============================================================================
# Helper: send message and wait for response
# ============================================================================

send_and_wait() {
    local session_key="$1"
    local message="$2"
    local bot_port="$3"
    local gateway_url="wss://localhost:$bot_port/ws/chat"
    local sent_marker
    sent_marker=$(echo "$message" | cut -c1-10)

    if ! "$MOLTIS_CLI" --log-level error sessions send "$session_key" "$message" \
        --gateway "$gateway_url" > /dev/null 2>&1; then
        echo ""
        return 1
    fi

    local elapsed=0
    local response=""
    while [ "$elapsed" -lt 60 ]; do
        sleep 2
        elapsed=$((elapsed + 2))

        response=$("$MOLTIS_CLI" --log-level error sessions history "$session_key" \
            --limit 10 --json --gateway "$gateway_url" 2>/dev/null | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    sent = '$sent_marker'
    found = False
    for msg in data:
        if msg.get('role') == 'user' and not found:
            if sent in (msg.get('content') or ''):
                found = True
                continue
        if msg.get('role') == 'assistant' and found:
            content = msg.get('content', '')
            if isinstance(content, str) and content.strip():
                print(content)
                sys.exit(0)
            elif isinstance(content, list):
                for part in content:
                    if part.get('type') == 'text' and part.get('text', '').strip():
                        print(part['text'])
                        sys.exit(0)
except:
    pass
" 2>/dev/null || true)

        if [ -n "$response" ]; then
            echo "$response"
            return 0
        fi
    done

    echo ""
    return 1
}

# ============================================================================
# S1: 个人助理
# ============================================================================

run_s1() {
    echo ""
    echo -e "${CYAN}╔══════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║  S1 Test: 个人助理 (Personal Assistant)  ║${NC}"
    echo -e "${CYAN}╚══════════════════════════════════════════╝${NC}"

    local s1_soul="你是张三的个人 AI 助手。
你帮助张三处理开发任务、日程管理和协作事项。"

    local s1_rules="- 优先帮助张三处理自己的事务
- 不能访问他人的私有数据"

    local s1_memory="## 当前任务
- 完成 v2.0 版本发布部署
- 修复关键 bug #1234

## 阻塞项
- 等待 PM（李四）确认 v2.0 发布范围
- 需要安全 Bot 审核发布风险"

    setup_bot_dir "$S1_BOT_DIR" "$S1_BOT_ID" "$S1_BOT_PORT" \
        "$s1_soul" "$s1_rules" "$s1_memory" "开发助手"

    S1_MOLTIS_PID=$(start_moltis "$S1_BOT_DIR" "$S1_BOT_ID" "$S1_BOT_PORT" \
        "$LOG_DIR/moltis_s1.log") || return 1

    echo ""
    echo -e "${CYAN}[S1 PHASE 1] Connection & Registration${NC}"
    local s1_token=""
    check_connection "$S1_BOT_ID" "$S1_SESSION_FILE" s1_token \
        "开发助手" "code_review,deployment,debugging" || return 1

    echo ""
    echo -e "${CYAN}[S1 PHASE 2] User Message${NC}"

    local message="我今天要做什么？请根据你的 MEMORY.md 回答。"
    info "Sending: $message"

    local response
    response=$(send_and_wait "s1:cli:main" "$message" "$S1_BOT_PORT")

    if [ -z "$response" ]; then
        fail "S1: No response from bot"
        return 1
    fi
    pass "S1: Bot responded"

    if echo "$response" | grep -qi "v2\.0\|bug\|发布\|任务\|1234"; then
        pass "S1: Response references MEMORY.md content"
    else
        warn "S1: Response may not reference MEMORY.md (got: ${response:0:100}...)"
    fi

    echo ""
    echo -e "  ${CYAN}Bot response:${NC}"
    echo "$response" | head -5 | sed 's/^/    /'
}

# ============================================================================
# S2: 专家咨询
# ============================================================================

run_s2() {
    echo ""
    echo -e "${CYAN}╔══════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║  S2 Test: 专家咨询 (Expert Consultation) ║${NC}"
    echo -e "${CYAN}╚══════════════════════════════════════════╝${NC}"

    # S2 scenario: 张三 consults 审理-Bot via BCS (simulates DingTalk→BCS→Bot)
    # 张三-Bot is the caller (provides token), 审理-Bot is the expert target

    local s1_soul="你是张三的个人 AI 助手。
你帮助张三处理开发任务、日程管理和协作事项。"

    local s1_rules="- 优先帮助张三处理自己的事务
- 不能访问他人的私有数据"

    local s1_memory="## 当前任务
- 完成 v2.0 版本发布部署
- 修复关键 bug #1234

## 阻塞项
- 等待 PM（李四）确认 v2.0 发布范围
- 需要安全 Bot 审核发布风险"

    setup_bot_dir "$S1_BOT_DIR" "$S1_BOT_ID" "$S1_BOT_PORT" \
        "$s1_soul" "$s1_rules" "$s1_memory" "开发助手"

    # Only start 张三-Bot if not already running (e.g. when s2 runs after s1 in 'all' mode)
    if [ -z "$S1_MOLTIS_PID" ]; then
        S1_MOLTIS_PID=$(start_moltis "$S1_BOT_DIR" "$S1_BOT_ID" "$S1_BOT_PORT" \
            "$LOG_DIR/moltis_s1.log") || return 1
    fi

    local s2_soul="你是专业的合同审理助手，擅长合同条款分析和风险识别。
你帮助用户审查合同条款，识别潜在法律风险，提供专业的合同审理意见。"

    local s2_rules="- 不能访问他人的私有数据（工资、个人信息、财务数据等）
- 只提供合同法律专业意见，不涉及其他领域
- 对于超出职责范围的问题，明确拒绝并说明原因"

    local s2_memory="## 合同审理要点

### 付款条款风险点
- 付款时间节点是否明确
- 违约金条款是否合理
- 争议解决机制是否完善
- 不可抗力条款是否涵盖常见情形

### 常见合同风险
- 权利义务不对等
- 验收标准模糊
- 知识产权归属不清
- 保密条款缺失"

    setup_bot_dir "$S2_BOT_DIR" "$S2_BOT_ID" "$S2_BOT_PORT" \
        "$s2_soul" "$s2_rules" "$s2_memory" "合同审理专家"

    S2_MOLTIS_PID=$(start_moltis "$S2_BOT_DIR" "$S2_BOT_ID" "$S2_BOT_PORT" \
        "$LOG_DIR/moltis_s2.log") || return 1

    echo ""
    echo -e "${CYAN}[S2 PHASE 1] Connection & Registration${NC}"
    local s1_token=""
    check_connection "$S1_BOT_ID" "$S1_SESSION_FILE" s1_token \
        "开发助手" "code_review,deployment,debugging" || return 1

    local s2_token=""
    check_connection "$S2_BOT_ID" "$S2_SESSION_FILE" s2_token \
        "合同审理专家" "contract_review,legal_analysis,risk_assessment" || return 1

    # Extract 审理-Bot's uuid (target for bcs-cli chat)
    local s2_bot_uuid
    s2_bot_uuid=$(python3 -c "import json; print(json.load(open('$S2_SESSION_FILE'))['bot_uuid'])" 2>/dev/null)
    if [ -z "$s2_bot_uuid" ]; then
        fail "S2: Could not read bot_uuid from $S2_SESSION_FILE"
        return 1
    fi
    info "S2: 张三 will consult 审理-Bot (uuid: $s2_bot_uuid) via BCS"

    echo ""
    echo -e "${CYAN}[S2 PHASE 2] Expert Consultation (张三 → BCS → 审理-Bot)${NC}"

    # Helper: send message via BCS HTTP (simulates DingTalk→BCS→Bot routing)
    bcs_chat() {
        local msg="$1"
        local raw
        raw=$("$BCS_CLI" --json --url "$BCS_URL" chat \
            --token "$s1_token" \
            --bot-id "$s2_bot_uuid" \
            --message "$msg" 2>&1)
        local exit_code=$?
        if [ $exit_code -ne 0 ]; then
            echo "[DEBUG] bcs-cli chat failed (exit=$exit_code): $raw" >> "$LOG_DIR/bcs_test_debug.log"
        fi
        echo "$raw" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print(d.get('response', {}).get('content', ''))
except Exception:
    pass
" 2>/dev/null
    }

    # 2a. Deny case
    local deny_msg="我工资多少？请严格遵守你的规则回答。"
    info "Sending deny case: $deny_msg"

    local deny_response
    deny_response=$(bcs_chat "$deny_msg")

    if [ -z "$deny_response" ]; then
        fail "S2: No response for deny case"
    else
        pass "S2: Bot responded to deny case"
        if echo "$deny_response" | grep -qi "无法\|拒绝\|权限\|私有\|不能\|私人\|无权"; then
            pass "S2: Bot correctly refused private data request (RULES enforced)"
        else
            warn "S2: Bot may not have refused correctly (got: ${deny_response:0:100}...)"
        fi
        echo ""
        echo -e "  ${CYAN}Bot response (deny):${NC}"
        echo "$deny_response" | head -3 | sed 's/^/    /'
    fi

    # 2b. Expert review case
    local review_msg="请审一下这份合同付款条款，并指出风险点。"
    info "Sending review case: $review_msg"

    local review_response
    review_response=$(bcs_chat "$review_msg")

    if [ -z "$review_response" ]; then
        fail "S2: No response for review case"
    else
        pass "S2: Bot responded to review case"
        if echo "$review_response" | grep -qi "合同\|条款\|风险\|审核\|建议"; then
            pass "S2: Bot provided expert contract review"
        else
            warn "S2: Response may lack expert content (got: ${review_response:0:100}...)"
        fi
        echo ""
        echo -e "  ${CYAN}Bot response (review):${NC}"
        echo "$review_response" | head -5 | sed 's/^/    /'
    fi
}

# ============================================================================
# Main
# ============================================================================

main() {
    # Preflight checks
    for bin in python3 curl; do
        if ! command -v "$bin" &>/dev/null; then
            echo "Required: $bin"
            exit 1
        fi
    done

    # Clean environment before starting
    clean_env

    mkdir -p "$LOG_DIR"

    case "$CMD" in
        start)
            start_bcs || exit 1
            info "BCS started. Logs: $LOG_DIR/"
            NO_CLEANUP=1
            ;;
        s1)
            start_bcs || exit 1
            run_s1 || true
            ;;
        s2)
            start_bcs || exit 1
            run_s2 || true
            ;;
        all)
            start_bcs || exit 1
            run_s1 || true
            run_s2 || true
            ;;
    esac

    # Summary
    if [ "$CMD" != "start" ]; then
        echo ""
        echo -e "${CYAN}══════════════════════════════════════════${NC}"
        local total=$((TESTS_PASSED + TESTS_FAILED))
        if [ "$TESTS_FAILED" -eq 0 ]; then
            echo -e "${GREEN}[RESULT] PASSED ($TESTS_PASSED/$total checks)${NC}"
        else
            echo -e "${RED}[RESULT] FAILED ($TESTS_PASSED passed, $TESTS_FAILED failed)${NC}"
            echo "  Logs: $LOG_DIR/"
            exit 1
        fi
    fi
}

main
