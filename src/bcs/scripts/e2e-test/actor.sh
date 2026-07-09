#!/bin/bash
# actor.sh — Actor directory + bot query/chat e2e tests.
#
# Covers previously-uncovered HTTP endpoints:
#   GET  /actors/list              routes::actors::list_actors
#   GET  /actors/search            routes::actors::search_actors
#   PUT  /actors/{aid}/status      routes::actors::put_actor_status
#   GET  /bots/my                  routes::bots::list_my_bots
#   GET  /bots/paged               routes::bots::list_bots_paged
#   POST /bots/query               routes::bots::query_bots
#   POST /bots/{id}/chat           routes::bot_chat::bot_chat   (sync 1:1)
# (GET /bots/{id}/groups is exercised in group.sh's test_bot_groups_of_bot.)

# Test registration (consumed by e2e.sh)
E2E_TESTS_ACTOR=(
    "test_actor_list"
    "test_actor_search"
    "test_actor_put_status"
    "test_bots_my"
    "test_bots_paged"
    "test_bots_query"
    "test_bot_chat_sync"
)

# A bot-token-authenticated request. The /actors/{aid}/status PUT and the sync
# /bots/{id}/chat resolve the caller bot from its session token (X-BCS-Bot-Token
# or Authorization: Bearer) — api_put/api_post only send the mock-human headers,
# so use the shared bot_request (see common.sh) which sends the bot token.
# Usage: _bot_request <METHOD> <PATH> <BODY> <BOT_NAME>
_bot_request() {
    bot_request "$1" "$2" "$4" "${3:-}"
}

# ============================================================================
# Tests
# ============================================================================

# GET /actors/list — list the actor directory from CEO's perspective.
# NOTE: the directory applies a visibility filter (public bots + the current
# bot's friends), so the result may legitimately be empty when CEO has no
# friends yet (e.g. running this suite standalone, before friends.sh runs).
# We assert the response shape, not a non-empty result.
test_actor_list() {
    info "Actors: GET /actors/list (from CEO)"
    api_get "/actors/list?current_bot_uuid=${BOT_CEO_UUID}&cooperatable_only=false&limit=20"
    if [ "$HTTP_STATUS" != "200" ]; then
        fail "GET /actors/list returned $HTTP_STATUS"; TESTS_FAILED=$((TESTS_FAILED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1)); return
    fi
    pass "GET /actors/list returned 200"
    TESTS_PASSED=$((TESTS_PASSED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1))
    # Response is { bots: [...], total: N }.
    local ok total
    read -r ok total <<EOF
$(printf '%s' "$RESPONSE" | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
    print('1' if isinstance(d.get('bots'),list) and isinstance(d.get('total'),int) else '0', d.get('total',0))
except Exception:
    print('0','0')
" 2>/dev/null)
EOF
    if [ "$ok" = "1" ]; then
        pass "actor list well-formed (total=$total)"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        fail "actor list response shape unexpected: $(printf '%s' "$RESPONSE" | head -c 120)"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
}

# GET /actors/search — keyword search over the actor directory.
test_actor_search() {
    info "Actors: GET /actors/search (q=产品, from CEO)"
    api_get "/actors/search?q=%E4%BA%A7%E5%93%81&current_bot_uuid=${BOT_CEO_UUID}&cooperatable_only=false"
    if [ "$HTTP_STATUS" != "200" ]; then
        fail "GET /actors/search returned $HTTP_STATUS"; TESTS_FAILED=$((TESTS_FAILED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1)); return
    fi
    pass "GET /actors/search returned 200"
    TESTS_PASSED=$((TESTS_PASSED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1))
    # Response is { bots: [...], context: {...} }. Search may fall back to the
    # registry when the recommend worker is unwired, so only assert the shape.
    local ok
    ok=$(printf '%s' "$RESPONSE" | python3 -c "import json,sys; d=json.load(sys.stdin); print('1' if 'bots' in d and 'context' in d else '0')" 2>/dev/null || echo 0)
    if [ "$ok" = "1" ]; then
        pass "actor search response has bots + context"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        fail "actor search response shape unexpected: $(printf '%s' "$RESPONSE" | head -c 120)"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
}

# PUT /actors/{aid}/status — a bot updates its own status (hidden, then back).
# Self-update (caller_actor_id == actor_id) is permitted without further checks.
test_actor_put_status() {
    info "Actors: PUT /actors/{CEO}/status hidden -> online (self, bot token)"
    if ! ensure_cli_token CEO; then
        skip_case "no CEO token; skipping actor PUT" || { TESTS_TOTAL=$((TESTS_TOTAL + 1)); return; }
    fi
    _bot_request PUT "/actors/${BOT_CEO_UUID}/status" '{"status":"hidden"}' CEO
    if [ "$HTTP_STATUS" != "200" ]; then
        fail "PUT status=hidden returned $HTTP_STATUS: $(printf '%s' "$RESPONSE" | head -c 160)"
        TESTS_FAILED=$((TESTS_FAILED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1)); return
    fi
    pass "PUT status=hidden returned 200"
    TESTS_PASSED=$((TESTS_PASSED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1))
    local status_val
    status_val=$(printf '%s' "$RESPONSE" | python3 -c "import json,sys; print(json.load(sys.stdin).get('data',{}).get('status',''))" 2>/dev/null || echo "")
    assert_eq "status reflected as hidden" "$status_val" "hidden" || true

    # Restore online so the bot stays collaboratable for the rest of the suite.
    _bot_request PUT "/actors/${BOT_CEO_UUID}/status" '{"status":"online"}' CEO
    if [ "$HTTP_STATUS" = "200" ]; then
        pass "PUT status=online restored (200)"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        fail "PUT status=online returned $HTTP_STATUS"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
}

# GET /bots/my — the mock human's owned bots (staff_no from mock identity).
test_bots_my() {
    info "Bots: GET /bots/my (mock human 001)"
    api_get "/bots/my?limit=20"
    if [ "$HTTP_STATUS" != "200" ]; then
        fail "GET /bots/my returned $HTTP_STATUS"; TESTS_FAILED=$((TESTS_FAILED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1)); return
    fi
    pass "GET /bots/my returned 200"
    TESTS_PASSED=$((TESTS_PASSED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1))
    local total
    total=$(printf '%s' "$RESPONSE" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('total',0))" 2>/dev/null || echo 0)
    if [ "$total" -ge 1 ]; then
        pass "/bots/my returned total=$total"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        fail "/bots/my returned no owned bots (mock human may own none)"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
}

# GET /bots/paged — paginated bot list.
test_bots_paged() {
    info "Bots: GET /bots/paged?limit=5"
    api_get "/bots/paged?limit=5&offset=0"
    if [ "$HTTP_STATUS" != "200" ]; then
        fail "GET /bots/paged returned $HTTP_STATUS"; TESTS_FAILED=$((TESTS_FAILED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1)); return
    fi
    pass "GET /bots/paged returned 200"
    TESTS_PASSED=$((TESTS_PASSED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1))
    local n
    n=$(printf '%s' "$RESPONSE" | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d.get('items',[])))" 2>/dev/null || echo 0)
    if [ "$n" -ge 1 ]; then
        pass "/bots/paged returned $n item(s)"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        fail "/bots/paged returned no items"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
}

# POST /bots/query — bulk-fetch bots by uuid.
test_bots_query() {
    info "Bots: POST /bots/query (CEO + PM)"
    api_post "/bots/query" "{\"bot_uuids\":[\"${BOT_CEO_UUID}\",\"${BOT_PM_UUID}\"]}"
    if [ "$HTTP_STATUS" != "200" ]; then
        fail "POST /bots/query returned $HTTP_STATUS"; TESTS_FAILED=$((TESTS_FAILED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1)); return
    fi
    pass "POST /bots/query returned 200"
    TESTS_PASSED=$((TESTS_PASSED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1))
    local n
    n=$(printf '%s' "$RESPONSE" | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d) if isinstance(d,list) else 0)" 2>/dev/null || echo 0)
    if [ "$n" -ge 2 ]; then
        pass "/bots/query resolved $n bot(s)"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        fail "/bots/query resolved $n bot(s) (expected >=2)"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
}

# POST /bots/{id}/chat — synchronous 1:1 bot chat (CEO -> PM). Blocks up to
# timeout_ms; accept 200 (delivered) or a timeout-family code (the demo bot may
# not reply within the small window, in which case BCS returns 500
# "Timeout waiting for bot response" — the endpoint is still exercised). Fail
# on auth/not-found/connection errors only.
test_bot_chat_sync() {
    info "Bots: POST /bots/{PM}/chat sync (from CEO, 6s timeout)"
    if ! ensure_cli_token CEO; then
        skip_case "no CEO token; skipping sync chat" || { TESTS_TOTAL=$((TESTS_TOTAL + 1)); return; }
    fi
    _bot_request POST "/bots/${BOT_PM_UUID}/chat" \
        '{"message":"ping","timeout_ms":6000}' CEO
    case "$HTTP_STATUS" in
        200)
            pass "sync bot chat returned 200 (delivered)"
            TESTS_PASSED=$((TESTS_PASSED + 1)) ;;
        # The chat route maps the blocking-chat timeout to HTTP 500 with body
        # "Timeout waiting for bot response" — accept that (and 408/502/504) as
        # the endpoint being exercised. But do NOT blanket-accept any 500: the
        # route also maps generic ServiceError::InternalError to 500, and treating
        # those as pass would mask a real regression. Only accept 500 when the
        # body carries the known timeout message.
        408|502|504)
            warn "sync bot chat returned $HTTP_STATUS (timeout family — endpoint exercised)"
            pass "sync bot chat endpoint reachable (status $HTTP_STATUS)"
            TESTS_PASSED=$((TESTS_PASSED + 1)) ;;
        500)
            if [[ "$RESPONSE" == *"Timeout waiting for bot response"* ]]; then
                warn "sync bot chat returned 500 (timeout waiting for bot response — endpoint exercised)"
                pass "sync bot chat endpoint reachable (timeout)"
                TESTS_PASSED=$((TESTS_PASSED + 1))
            else
                fail "sync bot chat returned 500 (non-timeout server error): $(printf '%s' "$RESPONSE" | head -c 200)"
                TESTS_FAILED=$((TESTS_FAILED + 1))
            fi
            ;;
        *)
            fail "sync bot chat returned $HTTP_STATUS: $(printf '%s' "$RESPONSE" | head -c 160)"
            TESTS_FAILED=$((TESTS_FAILED + 1)) ;;
    esac
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
}