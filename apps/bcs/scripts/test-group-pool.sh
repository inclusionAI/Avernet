#!/bin/bash
# Test script for BCS Group Pool Integration
#
# 测试 BCS 群池集成功能：
# 1. 注册 Bot
# 2. 发起 Proposal
# 3. 确认 Proposal (触发群池申请和成员管理)
# 4. 验证 Session 和 DingTalkGroupInfo
#
# USAGE:
#   ./test-group-pool.sh               # 运行完整测试
#   ./test-group-pool.sh --verbose     # 详细输出
#   ./test-group-pool.sh --no-mock     # 使用真实群池服务 (需要配置)
#
# Prerequisites:
#   1. BCS 服务已启动 (cargo run --package bcs)
#   2. 如果使用真实群池服务，需要配置 bcs.toml

set -e

# ============================================================================
# Verbose Mode
# ============================================================================

VERBOSE=0
USE_MOCK=1

for arg in "$@"; do
    if [ "$arg" = "--verbose" ] || [ "$arg" = "-v" ]; then
        VERBOSE=1
        set -x
    fi
    if [ "$arg" = "--no-mock" ]; then
        USE_MOCK=0
    fi
done

# ============================================================================
# Colors
# ============================================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD_CYAN='\033[1;36m'
NC='\033[0m'

# ============================================================================
# Configuration
# ============================================================================

BCS_URL="${MOLTIS_BCS_URL:-http://localhost:21000}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

BCS_CLI="${PROJECT_ROOT}/target/debug/bcs-cli"

# Test counters
TESTS_PASSED=0
TESTS_FAILED=0
TESTS_TOTAL=0

# ============================================================================
# Utility Functions
# ============================================================================

print_header() {
    echo -e ""
    echo -e "${BOLD_CYAN}╔═══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BOLD_CYAN}║ $1${NC}"
    echo -e "${BOLD_CYAN}╚═══════════════════════════════════════════════════════════════╝${NC}"
    echo -e ""
}

print_info() {
    echo -e "${CYAN}ℹ${NC} $1"
}

print_success() {
    echo -e "${GREEN}✓${NC} $1"
    TESTS_PASSED=$((TESTS_PASSED + 1))
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
}

print_error() {
    echo -e "${RED}✗${NC} $1"
    TESTS_FAILED=$((TESTS_FAILED + 1))
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

# ============================================================================
# Helper Functions
# ============================================================================

# Check if BCS is running
check_bcs() {
    curl -s "$BCS_URL/health" > /dev/null 2>&1
}

# Ensure bcs-cli is built
ensure_cli() {
    if [ ! -f "$BCS_CLI" ]; then
        print_info "Building bcs-cli..."
        cargo build --package bcs-cli 2>&1 | tail -5
    fi
}

# Extract JSON field using Python
extract_field() {
    local json="$1"
    local field="$2"
    echo "$json" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    value = data.get('$field', '')
    if isinstance(value, str):
        print(value)
    else:
        print(json.dumps(value))
except:
    print('')
"
}

# Extract nested JSON field
extract_nested_field() {
    local json="$1"
    local field="$2"
    echo "$json" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    # Support nested field like 'dingtalk_group.conversation_id'
    parts = '$field'.split('.')
    value = data
    for part in parts:
        if isinstance(value, dict):
            value = value.get(part)
        else:
            value = None
            break
    if value is None:
        print('')
    elif isinstance(value, str):
        print(value)
    else:
        print(json.dumps(value))
except:
    print('')
"
}

# Check if field exists in JSON
field_exists() {
    local json="$1"
    local field="$2"
    echo "$json" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    parts = '$field'.split('.')
    value = data
    for part in parts:
        if isinstance(value, dict) and part in value:
            value = value.get(part)
        else:
            value = None
            break
    print('true' if value is not None else 'false')
except:
    print('false')
"
}

# Make HTTP request to BCS
bcs_request() {
    local method="$1"
    local endpoint="$2"
    local data="$3"

    if [ -n "$data" ]; then
        curl -s -X "$method" "$BCS_URL$endpoint" \
            -H "Content-Type: application/json" \
            -d "$data"
    else
        curl -s -X "$method" "$BCS_URL$endpoint"
    fi
}

# ============================================================================
# Test Functions
# ============================================================================

# Test 1: Health Check
test_health() {
    print_header "Step 1: Health Check"

    if check_bcs; then
        print_success "BCS is healthy at $BCS_URL"
    else
        print_error "BCS is not running at $BCS_URL"
        print_info "Please start BCS first: cargo run --package bcs"
        exit 1
    fi
}

# Test 2: Register Bots
test_register_bots() {
    print_header "Step 2: Register Bots"

    # Register 张三
    print_info "Registering zhangsan bot..."
    local result1
    result1=$(bcs_request POST "/bots/register" '{
        "bot_id": "zhangsan",
        "bot_name": "张三助理",
        "process_url": "http://localhost:20001",
        "capabilities": {
            "name": "张三助理",
            "summary": "张三的个人 AI 助手",
            "skills": [],
            "domains": [],
            "scopes": []
        }
    }')

    if echo "$result1" | grep -q '"registered":true'; then
        print_success "zhangsan registered successfully"
    else
        print_error "Failed to register zhangsan: $result1"
    fi

    # Register DBA
    print_info "Registering dba bot..."
    local result2
    result2=$(bcs_request POST "/bots/register" '{
        "bot_id": "dba",
        "bot_name": "DBA Bot",
        "process_url": "http://localhost:20061",
        "capabilities": {
            "name": "DBA Bot",
            "summary": "数据库专家，负责数据库架构、性能优化、故障排查",
            "skills": ["sql_analysis", "deadlock_debugging", "performance_tuning"],
            "domains": ["database", "mysql", "postgresql"],
            "scopes": []
        }
    }')

    if echo "$result2" | grep -q '"registered":true'; then
        print_success "dba registered successfully"
    else
        print_error "Failed to register dba: $result2"
    fi

    # List bots
    print_info "Listing registered bots..."
    local bots
    bots=$(bcs_request GET "/bots")
    local count=$(echo "$bots" | python3 -c "import sys, json; print(len(json.load(sys.stdin)))")
    print_info "Total bots registered: $count"
}

# Test 3: Create Proposal
test_create_proposal() {
    print_header "Step 3: Create Proposal"

    print_info "Creating proposal for skill gap..."
    local result
    result=$(bcs_request POST "/proposals/evaluate" '{
        "bot_id": "zhangsan",
        "gap_type": "skill",
        "description": "生产环境出现死锁，需要数据库专家协助排查",
        "suggested_participants": ["dba"]
    }')

    if echo "$result" | grep -q '"proposal_created":true'; then
        print_success "Proposal created successfully"
    else
        print_error "Failed to create proposal: $result"
        return 1
    fi

    # Extract confirm_url
    PROPOSAL_CONFIRM_URL=$(extract_field "$result" "confirm_url")

    if [ -n "$PROPOSAL_CONFIRM_URL" ]; then
        print_success "Confirm URL extracted: $PROPOSAL_CONFIRM_URL"
    else
        print_error "Failed to extract confirm_url"
        return 1
    fi

    # Extract mode
    local mode=$(extract_field "$result" "mode")
    print_info "Proposal mode: $mode"

    # Extract participants
    local participants=$(extract_field "$result" "participants")
    print_info "Proposed participants: $participants"
}

# Test 4: Confirm Proposal (with Group Pool Integration)
test_confirm_proposal() {
    print_header "Step 4: Confirm Proposal"

    if [ -z "$PROPOSAL_CONFIRM_URL" ]; then
        print_error "No confirm URL available"
        return 1
    fi

    # Ensure URL has http:// prefix
    local full_url="$PROPOSAL_CONFIRM_URL"
    if [[ ! "$full_url" =~ ^http:// ]]; then
        full_url="http://$full_url"
    fi
    print_info "Confirming proposal: $full_url"

    # Use curl directly (bcs-cli has URL parsing issues)
    local result
    result=$(curl -s -X POST "$full_url")

    # bcs-cli outputs friendly format like "Group created:\n  ID: xxx"
    # or JSON format with "created":true
    if echo "$result" | grep -qE '"created":true|Group created:'; then
        print_success "Proposal confirmed, session created"
    else
        print_error "Failed to confirm proposal: $result"
        return 1
    fi

    # Extract session_id
    # bcs-cli format: "  ID: xxx" or JSON: "session_id": "xxx"
    SESSION_ID=$(echo "$result" | grep -oE 'ID: [a-f0-9-]+' | head -1 | awk '{print $2}')
    if [ -z "$SESSION_ID" ]; then
        SESSION_ID=$(extract_field "$result" "session_id")
    fi

    if [ -n "$SESSION_ID" ]; then
        print_success "Session ID: $SESSION_ID"
    else
        print_error "Failed to extract session_id"
        return 1
    fi

    # Check for dingtalk_conversation_id
    # bcs-cli format: "  dingtalk_conversation_id: xxx" or JSON format
    local dingtalk_cid=$(echo "$result" | grep -oE 'dingtalk_conversation_id":"[^"]*"' | head -1 | sed 's/.*":"\([^"]*\)".*/\1/')
    if [ -z "$dingtalk_cid" ]; then
        dingtalk_cid=$(echo "$result" | grep -oE '"dingtalk_conversation_id":"[^"]*"' | head -1 | sed 's/.*":"\([^"]*\)".*/\1/')
    fi

    if [ -n "$dingtalk_cid" ] && [ "$dingtalk_cid" != "null" ] && [ "$dingtalk_cid" != "" ]; then
        print_success "DingTalk conversation_id: $dingtalk_cid"
    else
        if [ "$USE_MOCK" -eq 1 ]; then
            print_warning "No DingTalk conversation_id (expected without real group pool service)"
        else
            print_error "Expected DingTalk conversation_id but got none"
        fi
    fi
}

# Test 5: Verify Session Details
test_verify_session() {
    print_header "Step 5: Verify Session Details"

    if [ -z "$SESSION_ID" ]; then
        print_error "No session ID available"
        return 1
    fi

    print_info "Fetching session details..."
    local session
    session=$(bcs_request GET "/groups/$SESSION_ID")

    if [ -n "$session" ] && ! echo "$session" | grep -q '"error"'; then
        print_success "Session fetched successfully"
    else
        print_error "Failed to fetch session: $session"
        return 1
    fi

    # Verify mode
    local mode=$(extract_field "$session" "mode")
    if [ "$mode" = "agent" ]; then
        print_success "Session mode is agent"
    else
        print_error "Expected mode 'agent', got '$mode'"
    fi

    # Verify driver_bot
    # For skill gap type, BCS chooses the specialist as driver
    local driver=$(extract_field "$session" "driver_bot")
    if [ "$driver" = "dba" ]; then
        print_success "Driver bot is dba (specialist for skill gap)"
    else
        print_error "Expected driver 'dba' (specialist), got '$driver'"
    fi

    # Verify participants
    if echo "$session" | grep -q '"dba"'; then
        print_success "Session includes dba participant"
    else
        print_error "Session missing dba participant"
    fi

    # Check for dingtalk_group field
    local has_dingtalk=$(field_exists "$session" "dingtalk_group")

    if [ "$has_dingtalk" = "true" ]; then
        print_success "Session has dingtalk_group field"

        # Extract dingtalk_group details
        local dt_binding_id=$(extract_nested_field "$session" "dingtalk_group.binding_id")
        local dt_conversation_id=$(extract_nested_field "$session" "dingtalk_group.conversation_id")
        local dt_status=$(extract_nested_field "$session" "dingtalk_group.status")

        if [ -n "$dt_binding_id" ] && [ "$dt_binding_id" != "null" ]; then
            print_info "  binding_id: $dt_binding_id"
        fi
        if [ -n "$dt_conversation_id" ] && [ "$dt_conversation_id" != "null" ]; then
            print_info "  conversation_id: $dt_conversation_id"
        fi
        if [ -n "$dt_status" ] && [ "$dt_status" != "null" ]; then
            print_info "  status: $dt_status"
        fi
    else
        if [ "$USE_MOCK" -eq 1 ]; then
            print_warning "No dingtalk_group field (expected without real group pool service)"
        else
            print_error "Expected dingtalk_group field"
        fi
    fi
}

# Test 6: Cleanup
test_cleanup() {
    print_header "Step 6: Cleanup"

    # Release group back to pool if we have a session
    if [ -n "$SESSION_ID" ]; then
        local session
        session=$(bcs_request GET "/groups/$SESSION_ID")

        # Extract binding_id from dingtalk_group
        local binding_id=$(echo "$session" | grep -oE '"binding_id":[0-9]+' | head -1 | sed 's/"binding_id"://')

        if [ -n "$binding_id" ] && [ "$binding_id" != "null" ]; then
            print_info "Releasing group (binding_id: $binding_id) back to pool..."

            local release_result
            release_result=$(curl -s -X POST "http://localhost:8888/api/v1/group-pools/$binding_id/release" \
                -H "Content-Type: application/json" \
                -d '{"release_reason":"测试完成，释放群"}')

            if echo "$release_result" | grep -q '"success":true'; then
                print_success "Group released back to pool"
            else
                print_warning "Failed to release group: $release_result"
            fi
        fi
    fi

    print_info "Unregistering test bots..."

    bcs_request DELETE "/bots/zhangsan" > /dev/null 2>&1 || true
    bcs_request DELETE "/bots/dba" > /dev/null 2>&1 || true

    print_success "Cleanup completed"
}

# ============================================================================
# Main
# ============================================================================

print_banner() {
    echo ""
    echo -e "${BOLD_CYAN}"
    echo "╔═══════════════════════════════════════════════════════════════╗"
    echo "║        BCS Group Pool Integration Test Suite                  ║"
    echo "╚═══════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
    echo ""
    echo "  BCS URL: $BCS_URL"
    echo "  Mock Mode: $([ $USE_MOCK -eq 1 ] && echo 'Yes (no real group pool)' || echo 'No (using real group pool)')"
    echo ""
    if [ "$USE_MOCK" -eq 0 ]; then
        echo "  ${YELLOW}注意: 使用真实群池模式需要配置 bcs.toml${NC}"
        echo "  配置文件位置 (按顺序搜索):"
        echo "    1. ./bcs.toml"
        echo "    2. ~/.config/bcs/bcs.toml"
        echo "    3. /etc/bcs/bcs.toml"
        echo "    4. MOLTIS_BCS_CONFIG 环境变量指定路径"
        echo ""
        echo "  参考 bcs.toml.example 模板"
    fi
    echo ""
}

print_summary() {
    echo ""
    print_header "Test Summary"
    echo -e "  Total:  ${TESTS_TOTAL}"
    echo -e "  ${GREEN}Passed: ${TESTS_PASSED}${NC}"
    echo -e "  ${RED}Failed: ${TESTS_FAILED}${NC}"
    echo ""

    if [ $TESTS_FAILED -eq 0 ]; then
        echo -e "${GREEN}✓ All tests passed!${NC}"
        exit 0
    else
        echo -e "${RED}✗ Some tests failed${NC}"
        exit 1
    fi
}

# Run all tests
main() {
    print_banner
    ensure_cli

    test_health
    test_register_bots
    test_create_proposal
    test_confirm_proposal
    test_verify_session
    test_cleanup

    print_summary
}

main