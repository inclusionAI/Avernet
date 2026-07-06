#!/usr/bin/env bash
# End-to-end smoke test for the Provider Downlink stack in local mode.
#
# Coverage:
#   1. POST   /providers                     (provider 注册, static_bearer)
#   2. PATCH  /providers/{id}                (provider 编辑: name + webhook_url)
#   3. POST   /providers/{id}/bots           (provider bot 注册)
#   4. POST   /bots/{provider_bot}/chat      (provider bot 单聊 + auto-callback)
#   5. POST   /groups + /groups/{id}/chat    (拉群 + 群聊 @provider bot)
#
# Usage:
#   ./scripts/test_provider_e2e.sh                  # build, run, teardown
#   ./scripts/test_provider_e2e.sh --keep           # leave BCS + mock provider running on exit
#   BCS_PORT=22000 MOCK_PROVIDER_PORT=28181 ./scripts/test_provider_e2e.sh
#
# The script:
#   - rebuilds the BCS binary in debug mode
#   - launches BCS with SERVER_ENV=local + BCS_AUTH_MOCK=1
#   - launches scripts/mock_provider_downlink.py with --auto-callback after Step 3
#   - drives every step via curl and asserts on the server response or
#     mock-provider /requests, /sessions, /callbacks endpoints
#
# Exit codes:
#   0  all 5 steps passed
#   1  any step failed (logs and mock-provider state are dumped)

set -u -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BCS_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

BCS_PORT="${BCS_PORT:-21000}"
MOCK_PROVIDER_PORT="${MOCK_PROVIDER_PORT:-28080}"
KEEP_RUNNING=0

for arg in "$@"; do
    case "$arg" in
        --keep) KEEP_RUNNING=1 ;;
        -h|--help)
            sed -n '2,30p' "$0"
            exit 0
            ;;
        *)
            echo "Unknown argument: $arg" >&2
            exit 2
            ;;
    esac
done

WORK_DIR="$(mktemp -d -t bcs-provider-e2e.XXXXXX)"
LOG_DIR="$WORK_DIR/logs"
mkdir -p "$LOG_DIR"
BCS_DATA_DIR="$WORK_DIR/bcs_data"
mkdir -p "$BCS_DATA_DIR"
BCS_LOG="$LOG_DIR/bcs.log"
PROVIDER_LOG="$LOG_DIR/mock_provider.log"

BCS_URL="http://127.0.0.1:${BCS_PORT}"
MOCK_PROVIDER_URL="http://127.0.0.1:${MOCK_PROVIDER_PORT}"
MOCK_WEBHOOK_URL="${MOCK_PROVIDER_URL}/webhook"

# Caller identity for /bots/onboard, /bots/{id}/visibility, /bots/{id}/chat,
# /groups, /groups/{id}/chat. Same user_id is used as owner of both the
# driver bot and the provider bot so set_visibility passes the owner check.
MOCK_USER_ID="${MOCK_USER_ID:-12345678}"

BCS_PID=""
MOCK_PID=""

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

step()  { echo -e "${CYAN}==>${NC} $*"; }
pass()  { echo -e "  ${GREEN}✓${NC} $*"; }
fail()  { echo -e "  ${RED}✗${NC} $*" >&2; }
warn()  { echo -e "  ${YELLOW}⚠${NC} $*"; }

cleanup() {
    local exit_code=$?
    if [ "$KEEP_RUNNING" -eq 1 ] && [ "$exit_code" -eq 0 ]; then
        cat <<EOF

Leaving BCS and mock provider running (--keep):
  BCS:           ${BCS_URL}    PID=${BCS_PID}    log=${BCS_LOG}
  Mock Provider: ${MOCK_PROVIDER_URL}    PID=${MOCK_PID}    log=${PROVIDER_LOG}
  Workspace:     ${WORK_DIR}
EOF
        return
    fi
    if [ "$exit_code" -ne 0 ]; then
        echo
        echo "==== last 60 lines of BCS log (${BCS_LOG}) ===="
        tail -60 "$BCS_LOG" 2>/dev/null || true
        echo
        echo "==== last 30 lines of mock provider log (${PROVIDER_LOG}) ===="
        tail -30 "$PROVIDER_LOG" 2>/dev/null || true
        if [ -n "${PROVIDER_ID:-}" ]; then
            echo
            echo "==== mock provider /requests ===="
            curl -sS "${MOCK_PROVIDER_URL}/requests" 2>/dev/null || true
            echo
            echo "==== mock provider /callbacks ===="
            curl -sS "${MOCK_PROVIDER_URL}/callbacks" 2>/dev/null || true
        fi
    fi
    if [ -n "$MOCK_PID" ] && kill -0 "$MOCK_PID" 2>/dev/null; then
        kill "$MOCK_PID" 2>/dev/null || true
    fi
    if [ -n "$BCS_PID" ] && kill -0 "$BCS_PID" 2>/dev/null; then
        kill "$BCS_PID" 2>/dev/null || true
    fi
    if [ -d "$WORK_DIR" ] && [ "$KEEP_RUNNING" -ne 1 ]; then
        rm -rf "$WORK_DIR"
    fi
}
trap cleanup EXIT INT TERM

require_tool() {
    local tool="$1"
    if ! command -v "$tool" >/dev/null 2>&1; then
        fail "$tool is required but not on PATH"
        exit 2
    fi
}

require_tool curl
require_tool python3
require_tool jq
require_tool cargo

# ------------------------------------------------------------------
# Build BCS
# ------------------------------------------------------------------
step "Building BCS binary..."
(cd "$BCS_ROOT" && cargo build --package bcs) || { fail "BCS build failed"; exit 1; }
pass "BCS built"

# ------------------------------------------------------------------
# Start BCS
# ------------------------------------------------------------------
step "Starting BCS on port $BCS_PORT..."
BCS_DATA_DIR="$BCS_DATA_DIR" \
SERVER_ENV=local \
RUST_LOG=info \
BCS_AUTH_MOCK=1 \
BCS_MOCK_USER_ID="$MOCK_USER_ID" \
nohup "$BCS_ROOT/target/debug/bcs" >> "$BCS_LOG" 2>&1 &
BCS_PID=$!

for i in $(seq 1 30); do
    if curl -sS --noproxy '*' "$BCS_URL/health" >/dev/null 2>&1; then
        break
    fi
    sleep 0.5
done
if ! kill -0 "$BCS_PID" 2>/dev/null; then
    fail "BCS process died - check $BCS_LOG"
    exit 1
fi
if ! curl -sS --noproxy '*' "$BCS_URL/health" >/dev/null 2>&1; then
    fail "BCS /health not reachable - check $BCS_LOG"
    exit 1
fi
pass "BCS healthy at $BCS_URL"

# helpers
extract() { echo "$1" | jq -r "$2"; }
assert_eq() {
    if [ "$1" != "$2" ]; then
        fail "$3: expected '$2' but got '$1'"
        exit 1
    fi
}
assert_contains() {
    if ! echo "$1" | grep -q "$2"; then
        fail "$3: expected body to contain '$2' but got: $1"
        exit 1
    fi
}

# ===================================================================
# Step 1: Register Provider (static_bearer)
# ===================================================================
step "Step 1: Register provider (static_bearer)..."
REG_PROVIDER_RESP=$(curl -sS --noproxy '*' -X POST "$BCS_URL/providers" \
    -H 'Content-Type: application/json' \
    -H "X-Mock-User-Id: $MOCK_USER_ID" \
    -d '{"name":"E2E Mock Provider","webhook_url":"'"$MOCK_WEBHOOK_URL"'","auth":{"mode":"static_bearer"}}')
PROVIDER_ID=$(extract "$REG_PROVIDER_RESP" '.provider_id')
PROVIDER_ADMIN_TOKEN=$(extract "$REG_PROVIDER_RESP" '.provider_admin_token')
BCS_TO_PROVIDER_TOKEN=$(extract "$REG_PROVIDER_RESP" '.bcs_to_provider_token')

[ -n "$PROVIDER_ID" ] && [ "$PROVIDER_ID" != "null" ] || { fail "missing provider_id: $REG_PROVIDER_RESP"; exit 1; }
[ -n "$PROVIDER_ADMIN_TOKEN" ] && [ "$PROVIDER_ADMIN_TOKEN" != "null" ] || { fail "missing provider_admin_token"; exit 1; }
[ -n "$BCS_TO_PROVIDER_TOKEN" ] && [ "$BCS_TO_PROVIDER_TOKEN" != "null" ] || { fail "missing bcs_to_provider_token"; exit 1; }
pass "Provider registered: $PROVIDER_ID"

# Verify provider query does NOT leak admin token or bcs_to_provider_token
GET_PROVIDER=$(curl -sS --noproxy '*' -X GET "$BCS_URL/providers/$PROVIDER_ID" \
    -H "Authorization: Bearer $PROVIDER_ADMIN_TOKEN")
assert_contains "$GET_PROVIDER" "\"provider_id\":\"$PROVIDER_ID\"" "get provider should return provider_id"
assert_contains "$GET_PROVIDER" "E2E Mock Provider" "get provider should return name"
GET_HAS_ADMIN=$(echo "$GET_PROVIDER" | grep -c "provider_admin_token" || true)
assert_eq "$GET_HAS_ADMIN" "0" "provider GET must not leak provider_admin_token"
GET_HAS_BCS_TOKEN=$(echo "$GET_PROVIDER" | grep -c "bcs_to_provider_token" || true)
assert_eq "$GET_HAS_BCS_TOKEN" "0" "provider GET must not leak bcs_to_provider_token"
pass "Provider query does not leak tokens"

# ===================================================================
# Step 2: Patch Provider (edit name)
# ===================================================================
step "Step 2: Patch provider..."
PATCH_RESP=$(curl -sS --noproxy '*' -X PATCH "$BCS_URL/providers/$PROVIDER_ID" \
    -H "Authorization: Bearer $PROVIDER_ADMIN_TOKEN" \
    -H "X-Mock-User-Id: $MOCK_USER_ID" \
    -H 'Content-Type: application/json' \
    -d '{"name":"E2E Mock Provider (patched)","webhook_url":"'"$MOCK_WEBHOOK_URL"'"}')
PATCHED_NAME=$(extract "$PATCH_RESP" '.name')
assert_eq "$PATCHED_NAME" "E2E Mock Provider (patched)" "patch name mismatch"
PATCHED_URL=$(extract "$PATCH_RESP" '.webhook_url')
assert_eq "$PATCHED_URL" "$MOCK_WEBHOOK_URL" "patch webhook_url mismatch"
pass "Provider patched: name=$PATCHED_NAME"

# Verify auth.mode cannot be changed
AUTH_MODE_PATCH=$(curl -sS --noproxy '*' -X PATCH "$BCS_URL/providers/$PROVIDER_ID" \
    -H "Authorization: Bearer $PROVIDER_ADMIN_TOKEN" \
    -H "X-Mock-User-Id: $MOCK_USER_ID" \
    -H 'Content-Type: application/json' \
    -d '{"auth":{"mode":"agentpass"}}' 2>&1 || true)
assert_contains "$AUTH_MODE_PATCH" "auth" "patching auth.mode should be rejected"
pass "auth.mode change rejected"

# ===================================================================
# Start mock provider
# ===================================================================
step "Starting mock provider on port $MOCK_PROVIDER_PORT..."
python3 "$SCRIPT_DIR/mock_provider_downlink.py" \
    --host 127.0.0.1 --port "$MOCK_PROVIDER_PORT" \
    --provider-id "$PROVIDER_ID" \
    --bcs-url "$BCS_URL" \
    --bcs-to-provider-token "$BCS_TO_PROVIDER_TOKEN" \
    --strict-auth \
    --verbose \
    >> "$PROVIDER_LOG" 2>&1 &
MOCK_PID=$!
sleep 1
if ! kill -0 "$MOCK_PID" 2>/dev/null; then
    fail "Mock provider process died - check $PROVIDER_LOG"
    exit 1
fi
if ! curl -sS --noproxy '*' "$MOCK_PROVIDER_URL/health" >/dev/null 2>&1; then
    fail "Mock provider /health not reachable"
    exit 1
fi
pass "Mock provider healthy at $MOCK_PROVIDER_URL"

# ===================================================================
# Step 3: Register Provider Bot
# ===================================================================
step "Step 3: Register provider bot..."
REG_BOT_RESP=$(curl -sS --noproxy '*' -X POST "$BCS_URL/providers/$PROVIDER_ID/bots" \
    -H "Authorization: Bearer $PROVIDER_ADMIN_TOKEN" \
    -H 'Content-Type: application/json' \
    -d '{"name":"E2E Provider Bot","summary":"provider e2e test bot","owners":["'"$MOCK_USER_ID"'"],"provider_bot_ref":"e2e-bot-v1"}')
PROVIDER_BOT_UUID=$(extract "$REG_BOT_RESP" '.bot_uuid')
BOT_RUNTIME_TOKEN=$(extract "$REG_BOT_RESP" '.bot_runtime_token')

[ -n "$PROVIDER_BOT_UUID" ] && [ "$PROVIDER_BOT_UUID" != "null" ] || { fail "missing bot_uuid: $REG_BOT_RESP"; exit 1; }
[ -n "$BOT_RUNTIME_TOKEN" ] && [ "$BOT_RUNTIME_TOKEN" != "null" ] || { fail "missing bot_runtime_token (static_bearer must return it)"; exit 1; }
pass "Provider bot registered: $PROVIDER_BOT_UUID"

# Verify duplicate provider_bot_ref is rejected
DUP_RESP=$(curl -sS --noproxy '*' -X POST "$BCS_URL/providers/$PROVIDER_ID/bots" \
    -H "Authorization: Bearer $PROVIDER_ADMIN_TOKEN" \
    -H 'Content-Type: application/json' \
    -d '{"name":"Dup","summary":"dup","owners":["'"$MOCK_USER_ID"'"],"provider_bot_ref":"e2e-bot-v1"}' 2>&1 || true)
assert_contains "$DUP_RESP" "already exists" "duplicate provider_bot_ref should be rejected"
pass "Duplicate provider_bot_ref rejected"

# Verify bot query does NOT leak bot_runtime_token
BOT_QUERY=$(curl -sS --noproxy '*' -X GET "$BCS_URL/providers/$PROVIDER_ID/bots" \
    -H "Authorization: Bearer $PROVIDER_ADMIN_TOKEN")
BOT_QUERY_HAS_RT=$(echo "$BOT_QUERY" | grep -c "bot_runtime_token" || true)
assert_eq "$BOT_QUERY_HAS_RT" "0" "provider bot list must not leak bot_runtime_token"
pass "Provider bot list does not leak runtime token"

# Restart mock provider with --auto-callback
step "Restarting mock provider with --auto-callback..."
kill "$MOCK_PID" 2>/dev/null || true
sleep 0.3
python3 "$SCRIPT_DIR/mock_provider_downlink.py" \
    --host 127.0.0.1 --port "$MOCK_PROVIDER_PORT" \
    --provider-id "$PROVIDER_ID" \
    --bcs-url "$BCS_URL" \
    --bcs-to-provider-token "$BCS_TO_PROVIDER_TOKEN" \
    --bot-runtime-token "$BOT_RUNTIME_TOKEN" \
    --auto-callback \
    --strict-auth \
    --callback-delay-ms 100 \
    --verbose \
    >> "$PROVIDER_LOG" 2>&1 &
MOCK_PID=$!
sleep 1
curl -sS --noproxy '*' "$MOCK_PROVIDER_URL/health" >/dev/null 2>&1 || { fail "mock provider restart failed"; exit 1; }
pass "Mock provider restarted with auto-callback"

# Set provider bot visibility to public (so driver can invite it to group)
step "Setting provider bot visibility to public..."
VIS_RESP=$(curl -sS --noproxy '*' -X PUT "$BCS_URL/bots/$PROVIDER_BOT_UUID/visibility" \
    -H "X-Mock-User-Id: $MOCK_USER_ID" \
    -H 'Content-Type: application/json' \
    -d '{"visibility":"public"}')
VIS_SUCCESS=$(extract "$VIS_RESP" '.success')
assert_eq "$VIS_SUCCESS" "true" "set visibility failed: $VIS_RESP"
pass "Provider bot visibility set to public"

# Register a driver bot (connect + onboard + set visibility=public)
step "Registering driver bot..."
CONNECT_RESP=$(curl -sS --noproxy '*' -X POST "$BCS_URL/bots/connect" \
    -H "X-Mock-User-Id: $MOCK_USER_ID" \
    -H 'Content-Type: application/json' -d '{}')
DRIVER_BOT_UUID=$(extract "$CONNECT_RESP" '.bot_uuid')
DRIVER_TOKEN=$(extract "$CONNECT_RESP" '.token')
[ -n "$DRIVER_BOT_UUID" ] && [ "$DRIVER_BOT_UUID" != "null" ] || { fail "driver connect failed: $CONNECT_RESP"; exit 1; }

ONBOARD_RESP=$(curl -sS --noproxy '*' -X POST "$BCS_URL/bots/onboard" \
    -H "X-Mock-User-Id: $MOCK_USER_ID" \
    -H "Authorization: Bearer $DRIVER_TOKEN" \
    -H 'Content-Type: application/json' \
    -d '{"name":"E2E Driver","summary":"e2e driver bot","domains":["test"],"skills":[{"name":"drive"}]}')
[ -n "$ONBOARD_RESP" ] || { fail "driver onboard failed"; exit 1; }

curl -sS --noproxy '*' -X PUT "$BCS_URL/bots/$DRIVER_BOT_UUID/visibility" \
    -H "X-Mock-User-Id: $MOCK_USER_ID" \
    -H 'Content-Type: application/json' \
    -d '{"visibility":"public"}' >/dev/null 2>&1
pass "Driver bot ready: $DRIVER_BOT_UUID"

# ===================================================================
# Step 4: 1:1 Chat (driver → provider bot)
# ===================================================================
step "Step 4: 1:1 chat (driver → provider bot)..."
curl -sS --noproxy '*' -X POST "$MOCK_PROVIDER_URL/reset" >/dev/null

CHAT_RESP=$(curl -sS --noproxy '*' -X POST "$BCS_URL/bots/$PROVIDER_BOT_UUID/chat" \
    -H "X-Mock-User-Id: $MOCK_USER_ID" \
    -H "Authorization: Bearer $DRIVER_TOKEN" \
    -H 'Content-Type: application/json' \
    -d '{"message":"hello from driver","from":"driver"}')
CHAT_DELIVERED=$(extract "$CHAT_RESP" '.delivered')
assert_eq "$CHAT_DELIVERED" "true" "1:1 chat delivery failed: $CHAT_RESP"

# Wait for mock provider to receive webhook + callback
sleep 1
REQUESTS=$(curl -sS --noproxy '*' "$MOCK_PROVIDER_URL/requests")
assert_contains "$REQUESTS" "chat.send" "mock provider should receive chat.send"
CALLBACKS=$(curl -sS --noproxy '*' "$MOCK_PROVIDER_URL/callbacks")
CALLBACK_OK=$(extract "$CALLBACKS" '.callbacks[0].ok')
assert_eq "$CALLBACK_OK" "true" "callback should succeed: $CALLBACKS"
SESSIONS=$(curl -sS --noproxy '*' "$MOCK_PROVIDER_URL/sessions")
assert_contains "$SESSIONS" "hello from driver" "session should contain user message"
assert_contains "$SESSIONS" "mock provider final" "session should contain assistant final"
pass "1:1 chat OK: webhook received + callback final + session recorded"

# ===================================================================
# Step 5: Group creation + group chat (driver @provider bot)
# ===================================================================
step "Step 5: Create group and send @mention message..."

curl -sS --noproxy '*' -X POST "$MOCK_PROVIDER_URL/reset" >/dev/null

GROUP_RESP=$(curl -sS --noproxy '*' -X POST "$BCS_URL/groups" \
    -H "X-Mock-User-Id: $MOCK_USER_ID" \
    -H "Authorization: Bearer $DRIVER_TOKEN" \
    -H 'Content-Type: application/json' \
    -d '{"driver_bot":"'"$DRIVER_BOT_UUID"'","participants":[{"bot_uuid":"'"$DRIVER_BOT_UUID"'","role":"driver"},{"bot_uuid":"'"$PROVIDER_BOT_UUID"'","role":"consultant"}]}')
GROUP_ID=$(extract "$GROUP_RESP" '.id')
[ -n "$GROUP_ID" ] && [ "$GROUP_ID" != "null" ] || { fail "group create failed: $GROUP_RESP"; exit 1; }
pass "Group created: $GROUP_ID"

# Provider bot receives chat.inject on group creation. Clear for clean assertions.
curl -sS --noproxy '*' -X POST "$MOCK_PROVIDER_URL/reset" >/dev/null

# Send @mention group chat
GC_RESP=$(curl -sS --noproxy '*' -X POST "$BCS_URL/groups/$GROUP_ID/chat" \
    -H "X-Mock-User-Id: $MOCK_USER_ID" \
    -H "Authorization: Bearer $DRIVER_TOKEN" \
    -H 'Content-Type: application/json' \
    -d '{"message":"@'"$PROVIDER_BOT_UUID"' please review this PR","from":"'"$DRIVER_BOT_UUID"'"}')
GC_DELIVERED=$(extract "$GC_RESP" '.delivered')
assert_eq "$GC_DELIVERED" "true" "group chat delivery failed: $GC_RESP"
pass "Group message sent"

# Wait for webhook delivery + auto-callback
sleep 2
REQUESTS=$(curl -sS --noproxy '*' "$MOCK_PROVIDER_URL/requests")
assert_contains "$REQUESTS" "chat.send" "provider should receive chat.send for @mention"
CALLBACKS=$(curl -sS --noproxy '*' "$MOCK_PROVIDER_URL/callbacks")
CALLBACK_OK=$(extract "$CALLBACKS" '.callbacks[0].ok')
assert_eq "$CALLBACK_OK" "true" "group callback should succeed: $CALLBACKS"
SESSIONS=$(curl -sS --noproxy '*' "$MOCK_PROVIDER_URL/sessions")
assert_contains "$SESSIONS" "please review this PR" "session should contain group message"
assert_contains "$SESSIONS" "mock provider final" "session should contain assistant final"
pass "Group chat OK: chat.send received + callback final + session recorded"

# ===================================================================
# Done
# ===================================================================
echo
pass "All 5 steps passed!"
exit 0
