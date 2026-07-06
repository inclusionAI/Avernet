#!/bin/bash
# Phase 2 HTTP route smoke test for sessions/services/groups APIs.
#
# What this tests:
# - All 9 sessions routes (create, list, get, patch, complete×2 CAS, members add/remove/patch, messages 501)
# - 2 services routes (post_invocation, get)
# - PATCH /groups/{id}/settings with service_spec patch
# - Routing's group_strategy field surfaced in GET /groups/{id}
# - complete_if_running CAS short-circuit
#
# What this does NOT test:
# - Real bot WS protocol (use scripts/test-structured-routing/ for that)
# - Callback dispatch to AntDing (needs AntDing webhook)
# - Timeout scanner firing (needs to wait 10s + service_spec.timeout_seconds)
#
# Usage:
#   1. Start BCS server:
#        BCS_DATA_DIR=/tmp/bcs-test-data ./target/release/bcs -c /tmp/bcs-config
#      where /tmp/bcs-config/bcs-config.toml is a copy of configs/bcs-config-dev.toml
#   2. Run: bash scripts/test-phase2-routes.sh
#
# Exit code: 0 on all-pass, non-zero on first failure.

set -euo pipefail

BASE="${BCS_URL:-http://127.0.0.1:21000}"
PASS_COUNT=0
FAIL_COUNT=0

green() { printf '\033[0;32m%s\033[0m\n' "$1"; }
red() { printf '\033[0;31m%s\033[0m\n' "$1"; }
cyan() { printf '\033[0;36m%s\033[0m\n' "$1"; }

assert_eq() {
    local name="$1" got="$2" want="$3"
    if [ "$got" = "$want" ]; then
        green "  ✓ $name"
        PASS_COUNT=$((PASS_COUNT + 1))
    else
        red "  ✗ $name"
        red "    got:  $got"
        red "    want: $want"
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
}

assert_contains() {
    local name="$1" got="$2" needle="$3"
    if echo "$got" | grep -qF "$needle"; then
        green "  ✓ $name"
        PASS_COUNT=$((PASS_COUNT + 1))
    else
        red "  ✗ $name"
        red "    got:    $got"
        red "    needle: $needle"
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
}

curl_json() {
    local method="$1" path="$2" data="${3:-}" auth_token="${4:-}"
    local args=(-s -X "$method" "$BASE$path" -H 'Content-Type: application/json')
    if [ -n "$auth_token" ]; then
        args+=(-H "Authorization: Bearer $auth_token")
    fi
    if [ -n "$data" ]; then
        args+=(-d "$data")
    fi
    curl "${args[@]}"
}

cyan "=== Phase 2 HTTP Routes Smoke Test ==="
echo "BCS: $BASE"
echo

# Step 0: health check
cyan "Step 0: health check"
HEALTH=$(curl -s "$BASE/health")
assert_contains "GET /health → status:ok" "$HEALTH" '"status":"ok"'
echo

# Step 1: register a bot to get a token
cyan "Step 1: register bot via /bots/connect"
CONNECT=$(curl_json POST /bots/connect '{"bot_id":"phase2-smoke-bot"}')
TOKEN=$(echo "$CONNECT" | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")
BOT_UUID=$(echo "$CONNECT" | python3 -c "import sys,json; print(json.load(sys.stdin)['bot_uuid'])")
assert_contains "POST /bots/connect returns token" "$CONNECT" '"token":"'
echo "  bot_uuid=$BOT_UUID"
echo

# Step 2: create a group
cyan "Step 2: create group"
GROUP=$(curl_json POST /groups "{\"driver_bot\":\"$BOT_UUID\",\"label\":\"phase2-smoke\",\"participants\":[{\"bot_uuid\":\"$BOT_UUID\",\"role\":\"driver\"}]}" "$TOKEN")
GID=$(echo "$GROUP" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
assert_contains "POST /groups → created" "$GROUP" '"created":true'
echo "  group_id=$GID"
echo

# Step 3: create a session via POST /groups/{id}/sessions
cyan "Step 3: POST /groups/{id}/sessions"
SESSION=$(curl_json POST "/groups/$GID/sessions" '{"session_title":"smoke-test"}' "$TOKEN")
SID=$(echo "$SESSION" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
assert_contains "session.id matches {group_id}:{8_hex}" "$SID" "$GID:"
assert_contains "session.status=running" "$SESSION" '"status":"running"'
assert_contains "session.session_kind=chat" "$SESSION" '"session_kind":"chat"'
echo "  session_id=$SID"
echo

# Step 4: GET /groups/{id}/sessions
cyan "Step 4: GET /groups/{id}/sessions"
LIST=$(curl_json GET "/groups/$GID/sessions" '' "$TOKEN")
assert_contains "list contains the new session_id" "$LIST" "$SID"
echo

# Step 5: GET /sessions/{sid}
cyan "Step 5: GET /sessions/{sid}"
GOT=$(curl_json GET "/sessions/$SID" '' "$TOKEN")
assert_contains "GET returns session" "$GOT" "\"id\":\"$SID\""
echo

# Step 6: PATCH /sessions/{sid}
cyan "Step 6: PATCH /sessions/{sid}"
PATCHED=$(curl_json PATCH "/sessions/$SID" '{"session_title":"smoke-renamed"}' "$TOKEN")
assert_contains "title updated" "$PATCHED" '"session_title":"smoke-renamed"'
echo

# Step 7: complete_if_running first call → completed
cyan "Step 7: POST /sessions/{sid}/complete (1st call)"
COMP1=$(curl_json POST "/sessions/$SID/complete" '{"output":"hello"}' "$TOKEN")
assert_contains "first complete returns status=completed" "$COMP1" '"status":"completed"'
echo

# Step 8: complete_if_running second call → CAS short-circuit
cyan "Step 8: POST /sessions/{sid}/complete (2nd call → CAS)"
COMP2=$(curl_json POST "/sessions/$SID/complete" '{"output":"hello"}' "$TOKEN")
assert_contains "second complete short-circuits" "$COMP2" '"already_completed":true'
echo

# Step 9: GET /sessions/{sid}/messages → 501
cyan "Step 9: GET /sessions/{sid}/messages (501)"
MSG=$(curl_json GET "/sessions/$SID/messages" '' "$TOKEN")
assert_contains "messages route returns 501 with delegated message" "$MSG" 'delegated to bot chat.history'
echo

# Step 10: GET /groups/{id} returns service_spec + latest_running_session_id + group_strategy
cyan "Step 10: GET /groups/{id}"
DETAIL=$(curl_json GET "/groups/$GID" '' "$TOKEN")
assert_contains "GET /groups/{id} contains group_strategy" "$DETAIL" '"group_strategy":"chat"'
assert_contains "GET /groups/{id} contains service_spec field" "$DETAIL" '"service_spec":'
assert_contains "GET /groups/{id} contains latest_running_session_id field" "$DETAIL" '"latest_running_session_id":'
echo

# Step 11: PATCH /groups/{id}/settings (validation + 501 apply)
cyan "Step 11: PATCH /groups/{id}/settings"
SETTINGS=$(curl_json PATCH "/groups/$GID/settings" '{"service_spec":{"timeout_seconds":60,"max_concurrency":8}}' "$TOKEN")
assert_contains "settings PATCH validation passes (501)" "$SETTINGS" 'apply deferred'
echo

# Step 12: POST /services/{group_id}/sessions (svc invocation; svc-key not yet enforced via middleware layer)
cyan "Step 12: POST /services/{gid}/sessions (service invocation)"
SVC=$(curl_json POST "/services/$GID/sessions" '{"input":{"q":"hi"}}')
SVC_SID=$(echo "$SVC" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))")
assert_contains "svc session created with session_kind=service_invocation" "$SVC" '"session_kind":"service_invocation"'
echo "  svc_session_id=$SVC_SID"
echo

# Step 13: GET /services/{gid}/sessions/{sid}
cyan "Step 13: GET /services/{gid}/sessions/{sid}"
SVC_GET=$(curl_json GET "/services/$GID/sessions/$SVC_SID")
assert_contains "service session lookup" "$SVC_GET" "\"id\":\"$SVC_SID\""
echo

# Step 14: add_session_participant
cyan "Step 14: POST /sessions/{sid}/members"
ADD=$(curl_json POST "/sessions/$SVC_SID/members" '{"bot_uuid":"phase2-extra","role":"consultant"}' "$TOKEN")
assert_contains "participant added" "$ADD" '"bot_uuid":"phase2-extra"'
echo

# Step 15: update_session_participant_mode
cyan "Step 15: PATCH /sessions/{sid}/members/{bot}"
MODE=$(curl_json PATCH "/sessions/$SVC_SID/members/phase2-extra" '{"mode":"muted"}' "$TOKEN")
assert_contains "mode updated to muted" "$MODE" '"mode":"muted"'
echo

# Step 16: remove_session_participant
cyan "Step 16: DELETE /sessions/{sid}/members/{bot}"
REM=$(curl_json DELETE "/sessions/$SVC_SID/members/phase2-extra" '' "$TOKEN")
assert_contains "remove returns updated session" "$REM" "\"id\":\"$SVC_SID\""
echo

# Summary
echo "════════════════════════════════════════"
if [ "$FAIL_COUNT" -eq 0 ]; then
    green "ALL $PASS_COUNT TESTS PASSED"
    exit 0
else
    red "PASS: $PASS_COUNT  FAIL: $FAIL_COUNT"
    exit 1
fi
