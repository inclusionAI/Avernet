#!/bin/bash
# friends.sh — Friend request/accept/reject/list e2e tests

# Test registration (consumed by e2e.sh)
E2E_TESTS_FRIENDS=(
    "test_friend_auto_accept_public"
    "test_friend_request_accept_protected"
    "test_friend_request_reject_protected"
    "test_friendship_insert_ignore_failure_is_visible_and_retryable"
    "test_list_friends"
    "test_friend_flow_via_cli"
)

# ============================================================================
# Local persistence fault fixture
# ============================================================================

_friendship_ignore_fixture() {
    local mode="$1" db_path="$2" bot_a="$3" bot_b="$4"
    python3 -c '
import sqlite3
import sys

mode, db_path, bot_a, bot_b = sys.argv[1:]
left_bot, right_bot = sorted((bot_a, bot_b))
trigger_name = "e2e_ignore_friendship_insert"

connection = sqlite3.connect(db_path, timeout=10)
try:
    connection.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")
    if mode == "reset":
        connection.execute(
            "DELETE FROM bcs_friendships WHERE left_bot = ? AND right_bot = ?",
            (left_bot, right_bot),
        )
        connection.execute(
            "DELETE FROM bcs_friend_requests "
            "WHERE (from_bot = ? AND to_bot = ?) OR (from_bot = ? AND to_bot = ?)",
            (bot_a, bot_b, bot_b, bot_a),
        )
        connection.execute(
            "DELETE FROM bcs_actor_relations "
            "WHERE is_creator = 0 AND "
            "((from_id = ? AND to_id = ?) OR (from_id = ? AND to_id = ?))",
            (bot_a, bot_b, bot_b, bot_a),
        )
    elif mode == "arm":
        quote = lambda value: "\x27" + value.replace("\x27", "\x27\x27") + "\x27"
        connection.execute(
            f"CREATE TRIGGER {trigger_name} "
            "BEFORE INSERT ON bcs_friendships "
            f"WHEN NEW.left_bot = {quote(left_bot)} AND NEW.right_bot = {quote(right_bot)} "
            "BEGIN SELECT RAISE(IGNORE); END"
        )
    elif mode != "disarm":
        raise ValueError(f"unsupported fixture mode: {mode}")
    connection.commit()
finally:
    connection.close()
' "$mode" "$db_path" "$bot_a" "$bot_b"
}

# ============================================================================
# Tests
# ============================================================================

# Public bot: friend request is auto-accepted — just verify they become friends.
test_friend_auto_accept_public() {
    info "Friends: add public bot as friend (auto-accept)"
    # 研发 should be public (default visibility from singlebox 5bots_profile)
    api_post "/friends/request" "{\"from_bot\":\"$BOT_CEO_UUID\",\"to_bot\":\"$BOT_ENG_UUID\"}"
    if [ "$HTTP_STATUS" = "200" ] || [ "$HTTP_STATUS" = "201" ]; then
        pass "send friend request to public bot returns $HTTP_STATUS"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        fail "send friend request to public bot returns $HTTP_STATUS (expected 200/201)"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    # Verify they are friends
    api_get "/bots/$BOT_CEO_UUID/friends"
    assert_eq "list CEO friends returns 200" "$HTTP_STATUS" "200"
    assert_contains "CEO is friends with 研发" "$RESPONSE" "$BOT_ENG_UUID"
}

# Protected bot: friend request stays pending, must be manually accepted.
test_friend_request_accept_protected() {
    info "Friends: add protected bot as friend (manual accept)"
    # Set 产品经理 to protected
    api_put "/bots/$BOT_PM_UUID/visibility" "{\"visibility\":\"protected\"}"
    assert_eq "set 产品经理 to protected returns 200" "$HTTP_STATUS" "200"
    # Send friend request from 验证 to 产品经理
    api_post "/friends/request" "{\"from_bot\":\"$BOT_QA_UUID\",\"to_bot\":\"$BOT_PM_UUID\"}"
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
        warn "验证 and 产品经理 are already friends (idempotent)"
        pass "accept friend request (already friends)"
        TESTS_PASSED=$((TESTS_PASSED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
    else
        # Find the pending request
        api_get "/friends/requests?bot_uuid=$BOT_PM_UUID&direction=received&status=pending"
        assert_eq "list pending requests returns 200" "$HTTP_STATUS" "200"
        local request_id
        request_id=$(echo "$RESPONSE" | python3 -c "
import json, sys
d = json.load(sys.stdin)
data = d.get('data', d.get('items', []))
if isinstance(data, dict): data = data.get('items', [])
for r in data:
    if r.get('from_bot') == '$BOT_QA_UUID':
        print(r.get('id', ''))
        break
")
        assert_not_empty "pending request exists for 验证→产品经理" "$request_id"
        # Accept it
        if [ -n "$request_id" ]; then
            api_post "/friends/requests/$request_id/accept" '{}'
            assert_eq "accept friend request returns 200" "$HTTP_STATUS" "200"
        fi
    fi
    # Verify they are friends
    api_get "/bots/$BOT_QA_UUID/friends"
    assert_eq "list 验证 friends returns 200" "$HTTP_STATUS" "200"
    assert_contains "验证 is friends with 产品经理" "$RESPONSE" "$BOT_PM_UUID"
    # Restore 产品经理 to public
    api_put "/bots/$BOT_PM_UUID/visibility" "{\"visibility\":\"public\"}"
}

# Protected bot: friend request rejected — should NOT become friends.
test_friend_request_reject_protected() {
    info "Friends: reject friend request to protected bot"
    # Set 客服 to protected
    api_put "/bots/$BOT_CS_UUID/visibility" "{\"visibility\":\"protected\"}"
    assert_eq "set 客服 to protected returns 200" "$HTTP_STATUS" "200"
    # Send friend request from 研发 to 客服
    api_post "/friends/request" "{\"from_bot\":\"$BOT_ENG_UUID\",\"to_bot\":\"$BOT_CS_UUID\"}"
    if [ "$HTTP_STATUS" = "200" ] || [ "$HTTP_STATUS" = "201" ]; then
        pass "send friend request returns $HTTP_STATUS"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        fail "send friend request returns $HTTP_STATUS (expected 200/201)"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    # Find the pending request
    api_get "/friends/requests?bot_uuid=$BOT_CS_UUID&direction=received&status=pending"
    local request_id
    request_id=$(echo "$RESPONSE" | python3 -c "
import json, sys
d = json.load(sys.stdin)
data = d.get('data', d.get('items', []))
if isinstance(data, dict): data = data.get('items', [])
for r in data:
    if r.get('from_bot') == '$BOT_ENG_UUID':
        print(r.get('id', ''))
        break
")
    assert_not_empty "pending request exists for 研发→客服" "$request_id"
    # Reject it
    if [ -n "$request_id" ]; then
        api_post "/friends/requests/$request_id/reject" '{}'
        assert_eq "reject friend request returns 200" "$HTTP_STATUS" "200"
    fi
    # Verify they are NOT friends
    api_get "/bots/$BOT_ENG_UUID/friends"
    assert_eq "list 研发 friends returns 200" "$HTTP_STATUS" "200"
    # 客服 should NOT appear in 研发's friend list
    if [[ "$RESPONSE" == *"$BOT_CS_UUID"* ]]; then
        fail "研发 should NOT be friends with 客服 after reject"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    else
        pass "研发 is NOT friends with 客服 (correctly rejected)"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    # Restore 客服 to public
    api_put "/bots/$BOT_CS_UUID/visibility" "{\"visibility\":\"public\"}"
}

# Persistence failure: accepting a pending request must not report success when
# the friendship INSERT is ignored and no row exists. This case only runs
# against the explicitly provided standalone SQLite database; it never mutates
# a remote E2E target. The store unit test covers the real cross-env legacy-key
# setup, while this story verifies the HTTP error and retry journey end to end.
test_friendship_insert_ignore_failure_is_visible_and_retryable() {
    info "Friends: ignored friendship insert is visible and retryable"

    local db_path="${BCS_E2E_SQLITE_DB_PATH:-}"
    if [[ -z "$db_path" || ! -f "$db_path" ]]; then
        warn "SKIP: BCS_E2E_SQLITE_DB_PATH is not an available local database"
        return 0
    fi
    case "$BCS_API_BASE_URL" in
        http://127.0.0.1:*|http://localhost:*) ;;
        *)
            warn "SKIP: friendship persistence fault fixture requires a loopback BCS target"
            return 0
            ;;
    esac

    if ! _friendship_ignore_fixture reset "$db_path" "$BOT_QA_UUID" "$BOT_CS_UUID"; then
        fail "reset ignored-insert friendship fixture"
        TESTS_FAILED=$((TESTS_FAILED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1))
        return
    fi

    api_put "/bots/$BOT_CS_UUID/visibility" '{"visibility":"protected"}'
    if [[ "$HTTP_STATUS" != "200" ]]; then
        fail "set fault-fixture target to protected returns $HTTP_STATUS"
        TESTS_FAILED=$((TESTS_FAILED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1))
        _friendship_ignore_fixture reset "$db_path" "$BOT_QA_UUID" "$BOT_CS_UUID" || true
        return
    fi

    api_post "/friends/request" \
        "{\"from_bot\":\"$BOT_QA_UUID\",\"to_bot\":\"$BOT_CS_UUID\"}"
    if [[ "$HTTP_STATUS" != "200" && "$HTTP_STATUS" != "201" ]]; then
        fail "create fault-fixture friend request returns $HTTP_STATUS"
        TESTS_FAILED=$((TESTS_FAILED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1))
        _friendship_ignore_fixture reset "$db_path" "$BOT_QA_UUID" "$BOT_CS_UUID" || true
        api_put "/bots/$BOT_CS_UUID/visibility" '{"visibility":"public"}'
        return
    fi

    local request_id
    request_id=$(json_path "$RESPONSE" "data.id")
    if [[ -z "$request_id" ]]; then
        fail "fault-fixture friend request returns an id"
        TESTS_FAILED=$((TESTS_FAILED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1))
        _friendship_ignore_fixture reset "$db_path" "$BOT_QA_UUID" "$BOT_CS_UUID" || true
        api_put "/bots/$BOT_CS_UUID/visibility" '{"visibility":"public"}'
        return
    fi

    if ! _friendship_ignore_fixture arm "$db_path" "$BOT_QA_UUID" "$BOT_CS_UUID"; then
        fail "arm ignored-insert friendship fixture"
        TESTS_FAILED=$((TESTS_FAILED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1))
        _friendship_ignore_fixture reset "$db_path" "$BOT_QA_UUID" "$BOT_CS_UUID" || true
        api_put "/bots/$BOT_CS_UUID/visibility" '{"visibility":"public"}'
        return
    fi

    api_post "/friends/requests/$request_id/accept" '{}'
    assert_eq "ignored friendship insert returns 500" "$HTTP_STATUS" "500"
    assert_json_eq "ignored friendship insert reports failure" "$RESPONSE" "success" "false"
    assert_contains "ignored friendship insert returns a user-readable error" \
        "$RESPONSE" "添加好友失败，请稍后重试"

    api_get "/friends/requests?bot_uuid=$BOT_CS_UUID&direction=received&status=pending"
    assert_eq "failed acceptance keeps request queryable" "$HTTP_STATUS" "200"
    local pending_status
    pending_status=$(printf '%s' "$RESPONSE" | python3 -c '
import json
import sys

request_id = sys.argv[1]
data = json.load(sys.stdin).get("data", [])
request = next((item for item in data if item.get("id") == request_id), {})
print(request.get("status", ""))
' "$request_id")
    assert_eq "failed acceptance keeps request pending" "$pending_status" "pending"

    api_get "/bots/$BOT_QA_UUID/friends"
    assert_eq "friend list remains queryable after failed acceptance" "$HTTP_STATUS" "200"
    if [[ "$RESPONSE" == *"$BOT_CS_UUID"* ]]; then
        fail "failed acceptance must not expose a friendship"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    else
        pass "failed acceptance does not expose a friendship"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    if ! _friendship_ignore_fixture disarm "$db_path" "$BOT_QA_UUID" "$BOT_CS_UUID"; then
        fail "disarm ignored-insert friendship fixture"
        TESTS_FAILED=$((TESTS_FAILED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1))
    fi

    api_post "/friends/requests/$request_id/accept" '{}'
    assert_eq "friend request can be retried after persistence recovers" "$HTTP_STATUS" "200"
    api_get "/bots/$BOT_QA_UUID/friends"
    assert_eq "friend list is queryable after retry" "$HTTP_STATUS" "200"
    assert_contains "successful retry establishes the friendship" "$RESPONSE" "$BOT_CS_UUID"

    if ! _friendship_ignore_fixture reset "$db_path" "$BOT_QA_UUID" "$BOT_CS_UUID"; then
        fail "clean ignored-insert friendship fixture"
        TESTS_FAILED=$((TESTS_FAILED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1))
    fi
    api_put "/bots/$BOT_CS_UUID/visibility" '{"visibility":"public"}'
}

# List friends: verify known friendship exists.
test_list_friends() {
    info "Friends: list friends"
    # CEO and 研发 should be friends (from test_friend_auto_accept_public)
    api_get "/bots/$BOT_CEO_UUID/friends"
    assert_eq "list friends returns 200" "$HTTP_STATUS" "200"
    assert_contains "CEO's friend list contains 研发" "$RESPONSE" "$BOT_ENG_UUID"
}

# ============================================================================
# bcs-cli friend flow: request -> requests -> accept -> list
# (Covers the 'friend' sub-command family as one end-to-end CLI case.)
# ============================================================================
test_friend_flow_via_cli() {
    info "Friends(CLI): friend request/requests/accept/list"
    ensure_cli_token CEO || { skip_case "no CEO token"; TESTS_TOTAL=$((TESTS_TOTAL+1)); return; }
    # PM token needed to drive accept (PM is the receiver of CEO's request).
    local pm_token
    pm_token="$(get_bot_token PM)"
    if [[ -z "$pm_token" ]]; then
        skip_case "no PM token for accept step"; TESTS_TOTAL=$((TESTS_TOTAL+1)); return 77
    fi

    # 1) CEO requests friendship with PM (idempotent if already friends; standalone
    #    auto-accepts). 'request' subcommand prints "Friend request sent ...".
    bcs_cli CEO friend request --bot-uuid "$BOT_PM_UUID" >/dev/null 2>&1 \
        || warn "friend request returned $BCS_CLI_EXIT (may already exist)"

    # 2) PM lists received requests (covers 'requests'). Output is HUMAN:
    #    "Friend requests (N):\n  bot_x → bot_y [accepted] (id: <uuid>)"
    bcs_cli PM friend requests >/dev/null 2>&1 || true
    if ! _cli_contains "$BCS_CLI_STDOUT" "Friend request"; then
        fail "friend requests did not return a request list"
        TESTS_FAILED=$((TESTS_FAILED+1)); TESTS_TOTAL=$((TESTS_TOTAL+1)); return
    fi
    # Extract a request id to drive accept: "(id: <uuid>)".
    local rid
    rid="$(printf '%s' "$BCS_CLI_STDOUT" | grep -oE '\(id: [a-f0-9-]+\)' | head -1 | sed 's/(id: //;s/)//')"

    # 3) PM accepts the request (covers 'accept'). Accept is idempotent — accepting
    #    an already-accepted request still returns "✓ Friend request accepted".
    if [[ -n "$rid" ]]; then
        bcs_cli PM friend accept --request-id "$rid" >/dev/null 2>&1 \
            || warn "friend accept returned $BCS_CLI_EXIT"
    else
        warn "no request id parsed from 'friend requests'; accept step skipped"
    fi

    # 4) CEO lists friends (covers 'list'). Primary assertion: PM is in CEO's
    #    friend list (standalone auto-accepts, so the friendship exists).
    bcs_cli CEO friend list >/dev/null 2>&1 || true
    if _cli_contains "$BCS_CLI_STDOUT" "$BOT_PM_UUID"; then
        pass "friend flow via CLI ok (request/requests/accept/list)"
        TESTS_PASSED=$((TESTS_PASSED+1))
    else
        fail "PM not in CEO friend list after CLI friend flow"
        TESTS_FAILED=$((TESTS_FAILED+1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL+1))
}
