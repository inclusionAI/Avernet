#!/bin/bash
# friends.sh — Friend request/accept/reject/list e2e tests

# Test registration (consumed by e2e.sh)
E2E_TESTS_FRIENDS=(
    "test_friend_auto_accept_public"
    "test_friend_request_accept_protected"
    "test_friend_request_reject_protected"
    "test_list_friends"
)

# ============================================================================
# Tests
# ============================================================================

# Public bot: friend request is auto-accepted — just verify they become friends.
test_friend_auto_accept_public() {
    info "Friends: add public bot as friend (auto-accept)"
    # 研发-Linus should be public (default visibility from singlebox 5bots_profile)
    api_post "/friends/request" "{\"from_bot\":\"$BOT_PMO_UUID\",\"to_bot\":\"$BOT_MENGCHANG_UUID\"}"
    if [ "$HTTP_STATUS" = "200" ] || [ "$HTTP_STATUS" = "201" ]; then
        pass "send friend request to public bot returns $HTTP_STATUS"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        fail "send friend request to public bot returns $HTTP_STATUS (expected 200/201)"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    # Verify they are friends
    api_get "/bots/$BOT_PMO_UUID/friends"
    assert_eq "list CEO-马斯克 friends returns 200" "$HTTP_STATUS" "200"
    assert_contains "CEO-马斯克 is friends with 研发-Linus" "$RESPONSE" "$BOT_MENGCHANG_UUID"
}

# Protected bot: friend request stays pending, must be manually accepted.
test_friend_request_accept_protected() {
    info "Friends: add protected bot as friend (manual accept)"
    # Set 产品-乔布斯 to protected
    api_put "/bots/$BOT_TANGHUA_UUID/visibility" "{\"visibility\":\"protected\"}"
    assert_eq "set 产品-乔布斯 to protected returns 200" "$HTTP_STATUS" "200"
    # Send friend request from 验证-图灵 to 产品-乔布斯
    api_post "/friends/request" "{\"from_bot\":\"$BOT_XIONGBING_UUID\",\"to_bot\":\"$BOT_TANGHUA_UUID\"}"
    if [ "$HTTP_STATUS" = "200" ] || [ "$HTTP_STATUS" = "201" ]; then
        pass "send friend request to protected bot returns $HTTP_STATUS"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        fail "send friend request to protected bot returns $HTTP_STATUS (expected 200/201)"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    # Check if already friends (idempotent on repeated runs)
    local already_friends
    already_friends=$(echo "$RESPONSE" | python3 -c "
import json, sys
d = json.load(sys.stdin)
print('yes' if d.get('message','') == 'Already friends' or 'already' in d.get('message','').lower() else 'no')
" 2>/dev/null || echo "no")
    if [ "$already_friends" = "yes" ]; then
        warn "验证-图灵 and 产品-乔布斯 are already friends (idempotent)"
        pass "accept friend request (already friends)"
        TESTS_PASSED=$((TESTS_PASSED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
    else
        # Find the pending request
        api_get "/friends/requests?bot_uuid=$BOT_TANGHUA_UUID&direction=received&status=pending"
        assert_eq "list pending requests returns 200" "$HTTP_STATUS" "200"
        local request_id
        request_id=$(echo "$RESPONSE" | python3 -c "
import json, sys
d = json.load(sys.stdin)
data = d.get('data', d.get('items', []))
if isinstance(data, dict): data = data.get('items', [])
for r in data:
    if r.get('from_bot') == '$BOT_XIONGBING_UUID':
        print(r.get('id', ''))
        break
")
        assert_not_empty "pending request exists for 验证-图灵→产品-乔布斯" "$request_id"
        # Accept it
        if [ -n "$request_id" ]; then
            api_post "/friends/requests/$request_id/accept" '{}'
            assert_eq "accept friend request returns 200" "$HTTP_STATUS" "200"
        fi
    fi
    # Verify they are friends
    api_get "/bots/$BOT_XIONGBING_UUID/friends"
    assert_eq "list 验证-图灵 friends returns 200" "$HTTP_STATUS" "200"
    assert_contains "验证-图灵 is friends with 产品-乔布斯" "$RESPONSE" "$BOT_TANGHUA_UUID"
    # Restore 产品-乔布斯 to public
    api_put "/bots/$BOT_TANGHUA_UUID/visibility" "{\"visibility\":\"public\"}"
}

# Protected bot: friend request rejected — should NOT become friends.
test_friend_request_reject_protected() {
    info "Friends: reject friend request to protected bot"
    # Set 客服-张勇 to protected
    api_put "/bots/$BOT_XIAHONG_UUID/visibility" "{\"visibility\":\"protected\"}"
    assert_eq "set 客服-张勇 to protected returns 200" "$HTTP_STATUS" "200"
    # Send friend request from 研发-Linus to 客服-张勇
    api_post "/friends/request" "{\"from_bot\":\"$BOT_MENGCHANG_UUID\",\"to_bot\":\"$BOT_XIAHONG_UUID\"}"
    if [ "$HTTP_STATUS" = "200" ] || [ "$HTTP_STATUS" = "201" ]; then
        pass "send friend request returns $HTTP_STATUS"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        fail "send friend request returns $HTTP_STATUS (expected 200/201)"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    # Find the pending request
    api_get "/friends/requests?bot_uuid=$BOT_XIAHONG_UUID&direction=received&status=pending"
    local request_id
    request_id=$(echo "$RESPONSE" | python3 -c "
import json, sys
d = json.load(sys.stdin)
data = d.get('data', d.get('items', []))
if isinstance(data, dict): data = data.get('items', [])
for r in data:
    if r.get('from_bot') == '$BOT_MENGCHANG_UUID':
        print(r.get('id', ''))
        break
")
    assert_not_empty "pending request exists for 研发-Linus→客服-张勇" "$request_id"
    # Reject it
    if [ -n "$request_id" ]; then
        api_post "/friends/requests/$request_id/reject" '{}'
        assert_eq "reject friend request returns 200" "$HTTP_STATUS" "200"
    fi
    # Verify they are NOT friends
    api_get "/bots/$BOT_MENGCHANG_UUID/friends"
    assert_eq "list 研发-Linus friends returns 200" "$HTTP_STATUS" "200"
    # 客服-张勇 should NOT appear in 研发-Linus's friend list
    if [[ "$RESPONSE" == *"$BOT_XIAHONG_UUID"* ]]; then
        fail "研发-Linus should NOT be friends with 客服-张勇 after reject"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    else
        pass "研发-Linus is NOT friends with 客服-张勇 (correctly rejected)"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    # Restore 客服-张勇 to public
    api_put "/bots/$BOT_XIAHONG_UUID/visibility" "{\"visibility\":\"public\"}"
}

# List friends: verify known friendship exists.
test_list_friends() {
    info "Friends: list friends"
    # CEO-马斯克 and 研发-Linus should be friends (from test_friend_auto_accept_public)
    api_get "/bots/$BOT_PMO_UUID/friends"
    assert_eq "list friends returns 200" "$HTTP_STATUS" "200"
    assert_contains "CEO-马斯克's friend list contains 研发-Linus" "$RESPONSE" "$BOT_MENGCHANG_UUID"
}
