#!/bin/bash
# register.sh — Human-initiated bot registration + owner delete e2e tests.
#
# Closed loop: GET /register/token  ->  POST /register  ->  DELETE /bots/{id}.
#
#   GET  /register/token   routes::register::get_register_token
#   POST /register         routes::register::register_bot
#   DELETE /bots/{id}      routes::bots::leave_bot
#
# All three run under the mock human identity (X-Mock-User-Id: 001, set by the
# api_* helpers). GET /register/token mints a human-bound register token
# (human_001), POST /register consumes it to create a bot owned by that human,
# and DELETE /bots/{id} (owner delete) removes it — then GET /bots/{id} must
# 404, proving the deletion stuck and closing the loop.

# Test registration (consumed by e2e.sh)
E2E_TESTS_REGISTER=(
    "test_register_token"
    "test_register_full_flow"
)

# ============================================================================
# Tests
# ============================================================================

# GET /register/token — the mock human fetches a registration token.
test_register_token() {
    info "Register: GET /register/token (mock human)"
    api_get "/register/token"
    if [ "$HTTP_STATUS" != "200" ]; then
        fail "GET /register/token returned $HTTP_STATUS: $(printf '%s' "$RESPONSE" | head -c 160)"
        TESTS_FAILED=$((TESTS_FAILED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1)); return
    fi
    pass "GET /register/token returned 200"
    TESTS_PASSED=$((TESTS_PASSED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1))
    local token exp
    token=$(printf '%s' "$RESPONSE" | python3 -c "import json,sys; print(json.load(sys.stdin).get('token',''))" 2>/dev/null || echo "")
    exp=$(printf '%s' "$RESPONSE" | python3 -c "import json,sys; print(json.load(sys.stdin).get('expires_at',''))" 2>/dev/null || echo "")
    assert_not_empty "register token returned" "$token" || true
    assert_not_empty "register token expires_at returned" "$exp" || true
}

# GET /register/token -> POST /register -> DELETE /bots/{id} -> GET /bots/{id}
# (404). Verifies the bot_token is returned on register and that owner delete
# then removes the bot.
test_register_full_flow() {
    info "Register: GET token -> POST /register -> DELETE /bots/{id} -> verify gone"

    # 1. Mint a register token for the mock human.
    api_get "/register/token"
    if [ "$HTTP_STATUS" != "200" ]; then
        fail "setup GET /register/token returned $HTTP_STATUS"; TESTS_FAILED=$((TESTS_FAILED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1)); return
    fi
    local reg_token
    reg_token=$(printf '%s' "$RESPONSE" | python3 -c "import json,sys; print(json.load(sys.stdin).get('token',''))" 2>/dev/null || echo "")
    if [ -z "$reg_token" ]; then
        fail "register token empty"; TESTS_FAILED=$((TESTS_FAILED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1)); return
    fi

    # 2. POST /register?token=<enc>&bot-name=<unique>. Token may contain
    #    URL-special chars (+/=), so URL-encode both params. Use a unique name
    #    so repeated e2e runs don't collide on any name uniqueness.
    local bot_name enc_token enc_name
    bot_name="reg-e2e-$$-$(date +%s 2>/dev/null || echo $$)"
    # Truncate to a safe length and keep it within the 2-64 char rule.
    bot_name="${bot_name:0:40}"
    enc_token=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=''))" "$reg_token")
    enc_name=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=''))" "$bot_name")
    api_post "/register?token=${enc_token}&bot-name=${enc_name}"
    if [ "$HTTP_STATUS" != "200" ]; then
        fail "POST /register returned $HTTP_STATUS: $(printf '%s' "$RESPONSE" | head -c 200)"
        TESTS_FAILED=$((TESTS_FAILED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1)); return
    fi
    pass "POST /register returned 200"
    TESTS_PASSED=$((TESTS_PASSED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1))

    # 3. Credentials: bot_uuid + bot_token must both be present.
    local bot_uuid bot_token
    bot_uuid=$(printf '%s' "$RESPONSE" | python3 -c "import json,sys; print(json.load(sys.stdin).get('bot_uuid',''))" 2>/dev/null || echo "")
    bot_token=$(printf '%s' "$RESPONSE" | python3 -c "import json,sys; print(json.load(sys.stdin).get('bot_token',''))" 2>/dev/null || echo "")
    if [ -n "$bot_uuid" ] && [ -n "$bot_token" ]; then
        pass "register returned bot_uuid + bot_token"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        fail "register missing credentials (uuid='$bot_uuid' token_len=${#bot_token})"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    # Sanity: the just-registered bot is visible before deletion.
    api_get "/bots/${bot_uuid}"
    if [ "$HTTP_STATUS" = "200" ]; then
        pass "registered bot visible via GET /bots/{id} before delete"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        fail "registered bot not found before delete (GET $HTTP_STATUS)"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    # 4. Owner delete — same mock human identity owns the bot (created_by is
    #    human_001 from the register token). api_delete sends the mock headers.
    api_delete "/bots/${bot_uuid}"
    if [ "$HTTP_STATUS" = "200" ]; then
        pass "DELETE /bots/{id} returned 200 (owner delete ok)"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        fail "DELETE /bots/{id} returned $HTTP_STATUS: $(printf '%s' "$RESPONSE" | head -c 200)"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    # 5. Closed loop: bot must be gone after delete (soft-deleted -> 404).
    api_get "/bots/${bot_uuid}"
    if [ "$HTTP_STATUS" = "404" ]; then
        pass "GET /bots/{id} returns 404 after delete (deletion verified)"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        fail "GET /bots/{id} returned $HTTP_STATUS after delete (expected 404)"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
}