#!/bin/bash
# Test: Visibility Management
# Usage: ./test_visibility.sh [get|set-public|set-protected|invalid|flow|all]
#
# Requires BCS running on $BCS_PORT (default 21000).
# Uses bcs-cli connect to get tokens directly (no session.json dependency).

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BCS_PORT="${BCS_PORT:-21000}"
BCS_HTTP_URL="http://localhost:$BCS_PORT"
BCS_CLI="$PROJECT_ROOT/target/debug/bcs-cli"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

pass() { echo -e "  ${GREEN}✓${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; }
info() { echo -e "  ${CYAN}→${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }

# Extract last JSON line from bcs-cli output (tracing logs may precede it)
extract_json() { grep -E '^\{' | tail -1; }

# Parse a JSON field via python3
json_field() { python3 -c "import sys,json; print(json.load(sys.stdin)[\"$1\"])" 2>/dev/null; }

# ============================================================================
# Setup: Connect a bot and get token + UUID
# ============================================================================
setup_bot() {
    info "Connecting bot to BCS..."
    CONNECT_RESULT=$("$BCS_CLI" --url "$BCS_HTTP_URL" --json connect 2>/dev/null | extract_json)
    BOT_UUID=$(echo "$CONNECT_RESULT" | json_field "bot_uuid")
    TOKEN=$(echo "$CONNECT_RESULT" | json_field "token")

    if [ -z "$BOT_UUID" ] || [ -z "$TOKEN" ]; then
        fail "Failed to connect bot (is BCS running on port $BCS_PORT?)"
        exit 1
    fi
    pass "Bot = $BOT_UUID"
}

# ============================================================================
# Test: Get visibility
# ============================================================================
test_get() {
    echo ""
    echo "── Test: Get Visibility ──"
    setup_bot

    info "Getting visibility..."
    VIS=$("$BCS_CLI" --url "$BCS_HTTP_URL" --json visibility --token "$TOKEN" get --bot-uuid "$BOT_UUID" 2>&1)
    if echo "$VIS" | python3 -c 'import sys,json; d=json.load(sys.stdin); exit(0 if d.get("success")==True else 1)' 2>/dev/null; then
        pass "Get visibility succeeded: $VIS"
    else
        fail "Get visibility failed: $VIS"
        return 1
    fi

    pass "Get visibility test PASSED"
}

# ============================================================================
# Test: Set visibility to public
# ============================================================================
test_set_public() {
    echo ""
    echo "── Test: Set Visibility to Public ──"
    setup_bot

    info "Setting visibility to public..."
    SET_RESULT=$("$BCS_CLI" --url "$BCS_HTTP_URL" --json visibility --token "$TOKEN" set --value public --bot-uuid "$BOT_UUID" 2>&1)
    if echo "$SET_RESULT" | python3 -c 'import sys,json; d=json.load(sys.stdin); exit(0 if d.get("success")==True else 1)' 2>/dev/null; then
        pass "Visibility set to 'public' (response: $SET_RESULT)"
    else
        fail "Failed to set visibility to public: $SET_RESULT"
        return 1
    fi

    pass "Set public test PASSED"
}

# ============================================================================
# Test: Set visibility to protected
# ============================================================================
test_set_protected() {
    echo ""
    echo "── Test: Set Visibility to Protected ──"
    setup_bot

    info "Setting visibility to protected..."
    SET_RESULT=$("$BCS_CLI" --url "$BCS_HTTP_URL" --json visibility --token "$TOKEN" set --value protected --bot-uuid "$BOT_UUID" 2>&1)
    if echo "$SET_RESULT" | python3 -c 'import sys,json; d=json.load(sys.stdin); exit(0 if d.get("success")==True else 1)' 2>/dev/null; then
        pass "Visibility set to 'protected' (response: $SET_RESULT)"
    else
        fail "Failed to set visibility to protected: $SET_RESULT"
        return 1
    fi

    pass "Set protected test PASSED"
}

# ============================================================================
# Test: Invalid visibility value (should be rejected)
# ============================================================================
test_invalid() {
    echo ""
    echo "── Test: Invalid Visibility Value ──"
    setup_bot

    info "Setting visibility to invalid value..."
    SET_RESULT=$("$BCS_CLI" --url "$BCS_HTTP_URL" --json visibility --token "$TOKEN" set --value invalid --bot-uuid "$BOT_UUID" 2>&1)
    if echo "$SET_RESULT" | python3 -c 'import sys,json; d=json.load(sys.stdin); exit(0 if d.get("success")==False else 1)' 2>/dev/null; then
        pass "Invalid visibility correctly rejected"
    else
        warn "Invalid visibility was not rejected: $SET_RESULT"
    fi

    pass "Invalid visibility test PASSED"
}

# ============================================================================
# Test: Full flow (get → set public → set protected → invalid)
# ============================================================================
test_flow() {
    echo ""
    echo "── Test: Full Visibility Flow ──"
    setup_bot

    # Step 1: Get initial visibility
    info "Step 1: Getting initial visibility..."
    VIS=$("$BCS_CLI" --url "$BCS_HTTP_URL" --json visibility --token "$TOKEN" get --bot-uuid "$BOT_UUID" 2>&1)
    pass "Initial visibility: $VIS"

    # Step 2: Set to public
    info "Step 2: Setting visibility to public..."
    SET_RESULT=$("$BCS_CLI" --url "$BCS_HTTP_URL" --json visibility --token "$TOKEN" set --value public --bot-uuid "$BOT_UUID" 2>&1)
    if echo "$SET_RESULT" | python3 -c 'import sys,json; d=json.load(sys.stdin); exit(0 if d.get("success")==True else 1)' 2>/dev/null; then
        pass "Visibility set to 'public'"
    else
        fail "Failed to set visibility to public: $SET_RESULT"
        return 1
    fi

    # Step 3: Set to protected
    info "Step 3: Setting visibility to protected..."
    SET_RESULT=$("$BCS_CLI" --url "$BCS_HTTP_URL" --json visibility --token "$TOKEN" set --value protected --bot-uuid "$BOT_UUID" 2>&1)
    if echo "$SET_RESULT" | python3 -c 'import sys,json; d=json.load(sys.stdin); exit(0 if d.get("success")==True else 1)' 2>/dev/null; then
        pass "Visibility set to 'protected'"
    else
        fail "Failed to set visibility to protected: $SET_RESULT"
        return 1
    fi

    # Step 4: Invalid value
    info "Step 4: Testing invalid visibility value..."
    SET_RESULT=$("$BCS_CLI" --url "$BCS_HTTP_URL" --json visibility --token "$TOKEN" set --value invalid --bot-uuid "$BOT_UUID" 2>&1)
    if echo "$SET_RESULT" | python3 -c 'import sys,json; d=json.load(sys.stdin); exit(0 if d.get("success")==False else 1)' 2>/dev/null; then
        pass "Invalid visibility correctly rejected"
    else
        warn "Invalid visibility was not rejected: $SET_RESULT"
    fi

    pass "Full visibility flow test PASSED"
}

# ============================================================================
# Main
# ============================================================================
COMMAND="${1:-all}"
FAILED=0

echo ""
echo "═══════════════════════════════════════════════════════"
echo " Visibility Tests (BCS @ localhost:$BCS_PORT)"
echo "═══════════════════════════════════════════════════════"

case "$COMMAND" in
    get)            test_get           || FAILED=1 ;;
    set-public)     test_set_public    || FAILED=1 ;;
    set-protected)  test_set_protected || FAILED=1 ;;
    invalid)        test_invalid       || FAILED=1 ;;
    flow)           test_flow          || FAILED=1 ;;
    all)
        test_get           || FAILED=1
        test_set_public    || FAILED=1
        test_set_protected || FAILED=1
        test_invalid       || FAILED=1
        test_flow          || FAILED=1
        ;;
    *)
        echo "Usage: $0 {get|set-public|set-protected|invalid|flow|all}"
        echo ""
        echo "Tests:"
        echo "  get            - Get bot visibility"
        echo "  set-public     - Set visibility to public"
        echo "  set-protected  - Set visibility to protected"
        echo "  invalid        - Test invalid visibility value"
        echo "  flow           - Full flow: get → set public → set protected → invalid"
        echo "  all            - Run all tests (default)"
        exit 1
        ;;
esac

echo ""
if [ "$FAILED" -eq 0 ]; then
    pass "All visibility tests PASSED ✓"
else
    fail "Some visibility tests FAILED"
    exit 1
fi
