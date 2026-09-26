#!/bin/bash
# Test: Friend Request Flow
# Usage: ./test_friend.sh [request|accept|list|reject|flow|all]
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
# Setup: Connect two bots and get tokens + UUIDs
# ============================================================================
setup_two_bots() {
    info "Connecting Bot A to BCS..."
    CONNECT_A=$("$BCS_CLI" --url "$BCS_HTTP_URL" --json connect 2>/dev/null | extract_json)
    BOT_A_UUID=$(echo "$CONNECT_A" | json_field "bot_uuid")
    TOKEN_A=$(echo "$CONNECT_A" | json_field "token")

    info "Connecting Bot B to BCS..."
    CONNECT_B=$("$BCS_CLI" --url "$BCS_HTTP_URL" --json connect 2>/dev/null | extract_json)
    BOT_B_UUID=$(echo "$CONNECT_B" | json_field "bot_uuid")
    TOKEN_B=$(echo "$CONNECT_B" | json_field "token")

    if [ -z "$BOT_A_UUID" ] || [ -z "$TOKEN_A" ] || [ -z "$BOT_B_UUID" ] || [ -z "$TOKEN_B" ]; then
        fail "Failed to connect bots (is BCS running on port $BCS_PORT?)"
        exit 1
    fi
    pass "Bot A = $BOT_A_UUID"
    pass "Bot B = $BOT_B_UUID"
}

# ============================================================================
# Test: Send friend request
# ============================================================================
test_request() {
    echo ""
    echo "── Test: Send Friend Request ──"
    setup_two_bots

    info "Bot A sending friend request to Bot B..."
    REQUEST_RESULT=$("$BCS_CLI" --url "$BCS_HTTP_URL" --json friend --token "$TOKEN_A" request --bot-uuid "$BOT_B_UUID" 2>&1)
    if echo "$REQUEST_RESULT" | grep -qi "success\|sent\|Already"; then
        pass "Friend request sent"
    else
        fail "Failed to send friend request: $REQUEST_RESULT"
        return 1
    fi

    # Verify it appears in pending list
    info "Verifying request is pending for Bot B..."
    PENDING=$("$BCS_CLI" --url "$BCS_HTTP_URL" --json friend --token "$TOKEN_B" requests --direction received --status pending 2>&1)
    if echo "$PENDING" | grep -qi "$BOT_A_UUID\|pending"; then
        pass "Pending request visible to Bot B"
    else
        fail "Pending request not found: $PENDING"
        return 1
    fi

    pass "Send friend request test PASSED"
}

# ============================================================================
# Test: Accept friend request
# ============================================================================
test_accept() {
    echo ""
    echo "── Test: Accept Friend Request ──"
    setup_two_bots

    # Send request first
    info "Bot A sending friend request to Bot B..."
    "$BCS_CLI" --url "$BCS_HTTP_URL" --json friend --token "$TOKEN_A" request --bot-uuid "$BOT_B_UUID" >/dev/null 2>&1

    # Get request ID
    PENDING=$("$BCS_CLI" --url "$BCS_HTTP_URL" --json friend --token "$TOKEN_B" requests --direction received --status pending 2>&1)
    REQUEST_ID=$(echo "$PENDING" | python3 -c 'import sys,json; data=json.load(sys.stdin); print(data[0]["id"] if isinstance(data,list) and len(data)>0 else "")' 2>/dev/null)

    if [ -z "$REQUEST_ID" ]; then
        fail "No pending request found to accept"
        return 1
    fi

    info "Bot B accepting friend request ($REQUEST_ID)..."
    ACCEPT_RESULT=$("$BCS_CLI" --url "$BCS_HTTP_URL" --json friend --token "$TOKEN_B" accept --request-id "$REQUEST_ID" 2>&1)
    if echo "$ACCEPT_RESULT" | grep -qi "success\|true"; then
        pass "Friend request accepted"
    else
        fail "Failed to accept: $ACCEPT_RESULT"
        return 1
    fi

    pass "Accept friend request test PASSED"
}

# ============================================================================
# Test: Reject friend request
# ============================================================================
test_reject() {
    echo ""
    echo "── Test: Reject Friend Request ──"
    setup_two_bots

    # Send request first
    info "Bot A sending friend request to Bot B..."
    "$BCS_CLI" --url "$BCS_HTTP_URL" --json friend --token "$TOKEN_A" request --bot-uuid "$BOT_B_UUID" >/dev/null 2>&1

    # Get request ID
    PENDING=$("$BCS_CLI" --url "$BCS_HTTP_URL" --json friend --token "$TOKEN_B" requests --direction received --status pending 2>&1)
    REQUEST_ID=$(echo "$PENDING" | python3 -c 'import sys,json; data=json.load(sys.stdin); print(data[0]["id"] if isinstance(data,list) and len(data)>0 else "")' 2>/dev/null)

    if [ -z "$REQUEST_ID" ]; then
        fail "No pending request found to reject"
        return 1
    fi

    info "Bot B rejecting friend request ($REQUEST_ID)..."
    REJECT_RESULT=$("$BCS_CLI" --url "$BCS_HTTP_URL" --json friend --token "$TOKEN_B" reject --request-id "$REQUEST_ID" 2>&1)
    if echo "$REJECT_RESULT" | grep -qi "success\|true"; then
        pass "Friend request rejected"
    else
        fail "Failed to reject: $REJECT_RESULT"
        return 1
    fi

    pass "Reject friend request test PASSED"
}

# ============================================================================
# Test: List friends
# ============================================================================
test_list() {
    echo ""
    echo "── Test: List Friends ──"
    setup_two_bots

    # Create friendship: request + accept
    info "Creating friendship between Bot A and Bot B..."
    "$BCS_CLI" --url "$BCS_HTTP_URL" --json friend --token "$TOKEN_A" request --bot-uuid "$BOT_B_UUID" >/dev/null 2>&1
    PENDING=$("$BCS_CLI" --url "$BCS_HTTP_URL" --json friend --token "$TOKEN_B" requests --direction received --status pending 2>&1)
    REQUEST_ID=$(echo "$PENDING" | python3 -c 'import sys,json; data=json.load(sys.stdin); print(data[0]["id"] if isinstance(data,list) and len(data)>0 else "")' 2>/dev/null)
    if [ -n "$REQUEST_ID" ]; then
        "$BCS_CLI" --url "$BCS_HTTP_URL" --json friend --token "$TOKEN_B" accept --request-id "$REQUEST_ID" >/dev/null 2>&1
    fi

    info "Listing Bot A's friends..."
    FRIENDS=$("$BCS_CLI" --url "$BCS_HTTP_URL" --json friend --token "$TOKEN_A" list --bot-uuid "$BOT_A_UUID" 2>&1)
    if echo "$FRIENDS" | grep -qi "$BOT_B_UUID"; then
        pass "Bot B appears in Bot A's friend list"
    else
        fail "Bot B not found in Bot A's friend list: $FRIENDS"
        return 1
    fi

    pass "List friends test PASSED"
}

# ============================================================================
# Test: Full flow (request → pending → accept → verify)
# ============================================================================
test_flow() {
    echo ""
    echo "── Test: Full Friend Request Flow ──"
    setup_two_bots

    # Step 1: Send friend request
    info "Step 1: Bot A sending friend request to Bot B..."
    REQUEST_RESULT=$("$BCS_CLI" --url "$BCS_HTTP_URL" --json friend --token "$TOKEN_A" request --bot-uuid "$BOT_B_UUID" 2>&1)
    if echo "$REQUEST_RESULT" | grep -qi "success\|sent\|Already"; then
        pass "Friend request sent"
    else
        fail "Failed to send friend request: $REQUEST_RESULT"
        return 1
    fi

    # Step 2: List pending requests
    info "Step 2: Listing pending requests for Bot B..."
    PENDING=$("$BCS_CLI" --url "$BCS_HTTP_URL" --json friend --token "$TOKEN_B" requests --direction received --status pending 2>&1)
    if echo "$PENDING" | grep -qi "$BOT_A_UUID\|pending"; then
        pass "Pending request visible to Bot B"
    else
        warn "Pending request not found (may already be friends)"
    fi

    # Step 3: Accept friend request
    info "Step 3: Bot B accepting friend request..."
    REQUEST_ID=$(echo "$PENDING" | python3 -c 'import sys,json; data=json.load(sys.stdin); print(data[0]["id"] if isinstance(data,list) and len(data)>0 else "")' 2>/dev/null)
    if [ -n "$REQUEST_ID" ]; then
        ACCEPT_RESULT=$("$BCS_CLI" --url "$BCS_HTTP_URL" --json friend --token "$TOKEN_B" accept --request-id "$REQUEST_ID" 2>&1)
        if echo "$ACCEPT_RESULT" | grep -qi "success\|true"; then
            pass "Friend request accepted"
        else
            fail "Failed to accept: $ACCEPT_RESULT"
            return 1
        fi
    else
        warn "No request ID found (may already be friends)"
    fi

    # Step 4: Verify friendship
    info "Step 4: Verifying friendship..."
    FRIENDS=$("$BCS_CLI" --url "$BCS_HTTP_URL" --json friend --token "$TOKEN_A" list --bot-uuid "$BOT_A_UUID" 2>&1)
    if echo "$FRIENDS" | grep -qi "$BOT_B_UUID"; then
        pass "Bot B appears in Bot A's friend list"
    else
        fail "Bot B not found in Bot A's friend list"
        return 1
    fi

    pass "Full friend request flow test PASSED"
}

# ============================================================================
# Main
# ============================================================================
COMMAND="${1:-all}"
FAILED=0

echo ""
echo "═══════════════════════════════════════════════════════"
echo " Friend Tests (BCS @ localhost:$BCS_PORT)"
echo "═══════════════════════════════════════════════════════"

case "$COMMAND" in
    request)  test_request  || FAILED=1 ;;
    accept)   test_accept   || FAILED=1 ;;
    reject)   test_reject   || FAILED=1 ;;
    list)     test_list     || FAILED=1 ;;
    flow)     test_flow     || FAILED=1 ;;
    all)
        test_request || FAILED=1
        test_accept  || FAILED=1
        test_reject  || FAILED=1
        test_list    || FAILED=1
        test_flow    || FAILED=1
        ;;
    *)
        echo "Usage: $0 {request|accept|reject|list|flow|all}"
        echo ""
        echo "Tests:"
        echo "  request  - Send a friend request"
        echo "  accept   - Send + accept a friend request"
        echo "  reject   - Send + reject a friend request"
        echo "  list     - Create friendship and list friends"
        echo "  flow     - Full flow: request → pending → accept → verify"
        echo "  all      - Run all tests (default)"
        exit 1
        ;;
esac

echo ""
if [ "$FAILED" -eq 0 ]; then
    pass "All friend tests PASSED ✓"
else
    fail "Some friend tests FAILED"
    exit 1
fi
