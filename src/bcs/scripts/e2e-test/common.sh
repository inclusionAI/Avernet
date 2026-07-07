#!/bin/bash
# common.sh — Shared library for BCS e2e tests. Sourced, not executed directly.

# ============================================================================
# Environment
# ============================================================================

BCS_API_BASE_URL="${BCS_API_BASE_URL:-http://127.0.0.1:21000}"
# Mock caller identity. Defaults must match singlebox's bcs/bots modules
# (scripts/modules/bcs.sh, scripts/modules/bots.sh), which start BCS with
# BCS_MOCK_USER_ID=001 / admin. Override via env if BCS was started with a
# different mock user (e.g. BCS_MOCK_USER_ID=xxx ./e2e.sh).
BCS_MOCK_USER_ID="${BCS_MOCK_USER_ID:-001}"
BCS_MOCK_USER_NICK_NAME="${BCS_MOCK_USER_NICK_NAME:-admin}"

# Bot IDs (must match the default 5bots_profile started by ./scripts/singlebox.sh --local start bcs_bots).
# Code names map to the 5bots_profile roles: CEO / 产品经理(PM) / 研发(ENG) / 验证(QA) / 客服(CS).
BOT_CEO_ID="${BOT_CEO_ID:-CEO}"
BOT_PM_ID="${BOT_PM_ID:-产品经理}"
BOT_ENG_ID="${BOT_ENG_ID:-研发}"
BOT_QA_ID="${BOT_QA_ID:-验证}"
BOT_CS_ID="${BOT_CS_ID:-客服}"

# ============================================================================
# Colors
# ============================================================================

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

pass() { echo -e "  ${GREEN}✓${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; }
info() { echo -e "  ${CYAN}→${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }

# ============================================================================
# Counters
# ============================================================================

TESTS_PASSED=0
TESTS_FAILED=0
TESTS_TOTAL=0
RESPONSE=""

# ============================================================================
# Assertion Helpers
# ============================================================================

assert_ok() {
    local desc="$1"; shift
    if "$@" &>/dev/null; then
        pass "$desc"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        fail "$desc"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
}

assert_eq() {
    local desc="$1" actual="$2" expected="$3"
    if [ "$actual" = "$expected" ]; then
        pass "$desc"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        fail "$desc (expected='$expected', actual='$actual')"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
}

assert_not_empty() {
    local desc="$1" value="$2"
    if [ -n "$value" ]; then
        pass "$desc"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        fail "$desc (value is empty)"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
}

assert_contains() {
    local desc="$1" haystack="$2" needle="$3"
    if [[ "$haystack" == *"$needle"* ]]; then
        pass "$desc"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        fail "$desc ('$needle' not found)"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
}

# ============================================================================
# Utility Helpers
# ============================================================================

json_field() {
    local json="$1" key="$2"
    python3 -c "
import json, sys
try:
    d = json.loads('''$json''')
    val = d.get('$key', '')
    print(val if val is not None else '')
except: print('')
"
}

json_field_default() {
    local json="$1" key="$2" default="${3:-}"
    local val
    val=$(json_field "$json" "$key")
    if [ -z "$val" ]; then
        echo "$default"
    else
        echo "$val"
    fi
}

wait_for_health() {
    local max_secs="${1:-30}"
    info "Waiting for BCS health (max ${max_secs}s)..."
    for i in $(seq 1 "$max_secs"); do
        if curl -sf "$BCS_API_BASE_URL/health" >/dev/null 2>&1; then
            pass "BCS is healthy"
            return 0
        fi
        sleep 1
    done
    fail "BCS not healthy after ${max_secs}s"
    return 1
}

ensure_human() {
    info "Ensuring mock human actor ($BCS_MOCK_USER_ID)..."
    api_post "/me/ensure-human" '{}'
    if [ "$HTTP_STATUS" = "200" ] || [ "$HTTP_STATUS" = "201" ]; then
        pass "Mock human actor ready"
        return 0
    fi
    fail "Failed to ensure human actor (HTTP $HTTP_STATUS)"
    return 1
}

summary() {
    echo ""
    info "=== Test Summary ==="
    pass "Passed: $TESTS_PASSED"
    if [ "$TESTS_FAILED" -gt 0 ]; then
        fail "Failed: $TESTS_FAILED"
    else
        info "Failed: 0"
    fi
    info "Total:  $TESTS_TOTAL"
}

# ============================================================================
# HTTP Helpers
# ============================================================================

# Temp file for response body (persists across subshell boundaries).
_RESPONSE_FILE=$(mktemp)
trap 'rm -f "$_RESPONSE_FILE"' EXIT

# Generic request helper. Sets globals:
#   HTTP_STATUS — the HTTP status code (e.g. "200")
#   RESPONSE   — the response body
# Does NOT print to stdout so callers don't need $().
_api_request() {
    local method="$1" path="$2" body="${3:-}"
    local url="${BCS_API_BASE_URL}${path}"
    local curl_args=(-s -o "$_RESPONSE_FILE" -w '%{http_code}' -X "$method"
        -H "X-Mock-User-Id: $BCS_MOCK_USER_ID"
        -H "X-Mock-Nick-Name: $BCS_MOCK_USER_NICK_NAME"
        -H "Content-Type: application/json")
    if [ -n "$body" ]; then
        curl_args+=(-d "$body")
    fi
    HTTP_STATUS=$(curl "${curl_args[@]}" "$url" 2>/dev/null) || HTTP_STATUS="000"
    RESPONSE=$(cat "$_RESPONSE_FILE")
}

api_get() {
    _api_request GET "$1"
}

api_post() {
    _api_request POST "$1" "${2:-}"
}

api_put() {
    _api_request PUT "$1" "${2:-}"
}

api_delete() {
    _api_request DELETE "$1"
}

api_patch() {
    _api_request PATCH "$1" "${2:-}"
}

# ============================================================================
# Bot UUID Resolution
# ============================================================================

# Resolve a bot name to its UUID by querying GET /bots.
# Usage: uuid=$(resolve_bot_uuid "CEO")
resolve_bot_uuid() {
    local name="$1"
    local url="${BCS_API_BASE_URL}/bots?limit=100"
    local body
    body=$(curl -s "$url") || true
    echo "$body" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    bots = data if isinstance(data, list) else data.get('bots', data.get('items', []))
    for b in bots:
        cap = b.get('capabilities', {})
        if cap.get('name') == '$name':
            print(b.get('bot_uuid', ''))
            sys.exit(0)
    print('')
except:
    print('')
"
}

# Resolve all bot UUIDs. Call after BCS is healthy and bots are onboarded.
# Sets BOT_CEO_UUID, BOT_PM_UUID, etc.
resolve_all_bot_uuids() {
    info "Resolving bot UUIDs..."
    BOT_CEO_UUID=$(resolve_bot_uuid "$BOT_CEO_ID")
    BOT_PM_UUID=$(resolve_bot_uuid "$BOT_PM_ID")
    BOT_ENG_UUID=$(resolve_bot_uuid "$BOT_ENG_ID")
    BOT_QA_UUID=$(resolve_bot_uuid "$BOT_QA_ID")
    BOT_CS_UUID=$(resolve_bot_uuid "$BOT_CS_ID")

    local failed=0
    for var in BOT_CEO_UUID BOT_PM_UUID BOT_ENG_UUID BOT_QA_UUID BOT_CS_UUID; do
        if [ -z "${!var}" ]; then
            fail "Could not resolve UUID for $var"
            failed=$((failed + 1))
        fi
    done
    if [ "$failed" -gt 0 ]; then
        fail "Some bot UUIDs not resolved. Are bots onboarded?"
        return 1
    fi
    pass "Bot UUIDs resolved (CEO=$BOT_CEO_UUID, PM=$BOT_PM_UUID, ...)"
}

# ============================================================================
# Group Cleanup
# ============================================================================

# Delete all normal groups driven by the given bot UUID. The BCS enforces a
# per-driver active-group cap (20), so leftover groups from previous e2e runs
# would make `POST /groups` return 400 ("already drives 20 active group(s)")
# and cascade-fail the member/label/visibility tests. Call this once during
# setup, after bot UUIDs are resolved, to start from a clean state.
cleanup_driver_groups() {
    local driver_uuid="$1"
    [ -n "$driver_uuid" ] || return 0
    info "Cleaning up existing groups driven by $driver_uuid..."
    api_get "/groups?limit=100&group_kind=all"
    [ "$HTTP_STATUS" = "200" ] || { warn "list groups returned $HTTP_STATUS; skip cleanup"; return 0; }
    local ids
    ids=$(echo "$RESPONSE" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    items = d.get('items', []) or []
    for g in items:
        if g.get('driver_bot') == '$driver_uuid':
            gid = g.get('id', '')
            if gid:
                print(gid)
except Exception:
    pass
")
    local count=0
    while IFS= read -r gid; do
        [ -n "$gid" ] || continue
        # DELETE /groups/{id} requires bot_id (the driver bot's UUID) as the
        # caller actor; api_delete doesn't take a query, so call curl directly.
        local code
        code=$(curl -s -o /dev/null -w '%{http_code}' -X DELETE \
            "${BCS_API_BASE_URL}/groups/${gid}?bot_id=${driver_uuid}" \
            -H "X-Mock-User-Id: $BCS_MOCK_USER_ID" \
            -H "X-Mock-Nick-Name: $BCS_MOCK_USER_NICK_NAME" 2>/dev/null) || code="000"
        if [ "$code" = "200" ]; then
            count=$((count + 1))
        fi
    done <<< "$ids"
    if [ "$count" -gt 0 ]; then
        pass "Cleaned up $count existing group(s) driven by $driver_uuid"
    else
        info "No existing groups to clean up"
    fi
}
