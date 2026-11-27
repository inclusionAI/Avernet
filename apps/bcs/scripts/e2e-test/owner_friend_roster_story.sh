#!/bin/bash
# owner_friend_roster_story.sh — public API owner-initiated friend removal
#
# Covers the V1 (OpenAPI) bot friend roster: an authenticated human manages a
# Bot they own and removes that Bot's friends through
# `DELETE /openapi/v1/collaboration/friend-connections`:
#
#   - Bot→Bot removal (actor=bot, target=bot): both directions transition
#     atomically and the removed Bot's owner receives the BOT2BOT notice.
#   - Human→Bot removal (actor=bot, target=human): the human→bot edge and its
#     auth-sync teardown run; the removed human receives the HUMAN2BOT notice.
#   - Caller-side self-removal (no actor params) stays byte-identical.
#   - Idempotent re-removal reports `deleted=false` (no second notice).
#   - Input/authorization gates: both-or-none actor params (400), a non-Bot
#     actor (400), an unknown actor Bot (404), and a non-owner caller (403).
#
# Auth: every call carries the same Gateway Principal JWT
# (`X-Avernet-Principal`, signed with AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE)
# the public collaboration story uses, so `require_owner` is evaluated against
# the live ownership graph at request time.

# Test registration (consumed by e2e.sh)
E2E_TESTS_STORIES+=("story_owner_manages_bot_friend_roster")

# The secondary fixture human used only to prove the owner gate rejects a
# caller with no authority over the actor Bot.
_OFR_OUTSIDER="${BCS_E2E_OWNER_ROSTER_OUTSIDER:-002}"

# Request the public collaboration API as the given Gateway Principal.
# Sets HTTP_STATUS / RESPONSE like the api_* helpers.
_ofr_request() {
    local method="$1" path="$2" principal="$3" body="${4:-}"
    api_request_headers "$method" "/openapi/v1/collaboration${path}" "$body" \
        "X-Avernet-Principal: ${principal}"
}

# Create (and approve, when the target still asks) a friendship from the
# caller-default human — or from an explicit Bot actor — to a target Bot.
# Returns 0 only when the edge pair is live.
# Usage: _ofr_ensure_friend <from_actor_json|""> <to_bot> <principal>
_ofr_ensure_friend() {
    local from_actor="$1" to_bot="$2" principal="$3" body status request_id
    if [[ -n "$from_actor" ]]; then
        body="{\"from_actor\":${from_actor},\"to_actor\":{\"type\":\"bot\",\"id\":\"${to_bot}\"}}"
    else
        body="{\"to_actor\":{\"type\":\"bot\",\"id\":\"${to_bot}\"}}"
    fi
    _ofr_request POST "/friend-connections/requests" "$principal" "$body"
    if [[ "$HTTP_STATUS" != "201" ]]; then
        warn "friend connection create returned $HTTP_STATUS: $(printf '%s' "$RESPONSE" | head -c 300)"
        return 1
    fi
    status=$(json_path "$RESPONSE" "data.status")
    case "$status" in
        approved)
            return 0
            ;;
        pending)
            request_id=$(json_path "$RESPONSE" "data.request_ids.0")
            if [[ -z "$request_id" ]]; then
                warn "pending friend connection carries no decidable request id"
                return 1
            fi
            _ofr_request POST "/friend-connections/requests/${request_id}/accept" "$principal" "{}"
            if [[ "$HTTP_STATUS" != "200" ]]; then
                warn "friend connection accept returned $HTTP_STATUS: $(printf '%s' "$RESPONSE" | head -c 300)"
                return 1
            fi
            return 0
            ;;
        *)
            warn "friend connection create used unexpected status '${status}'"
            return 1
            ;;
    esac
}

# DELETE one friendship through the public roster surface.
# Usage: _ofr_delete <query> <principal>
_ofr_delete() {
    _ofr_request DELETE "/friend-connections?$1" "$2" ""
}

story_owner_manages_bot_friend_roster() {
    info "Story: a Bot Owner removes friends from the managed Bot roster"
    local signing_key principal outsider
    signing_key="${AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE:-avernet-dev-signing-key-NOT-FOR-PROD}"
    if ! principal=$(python3 "$SCRIPT_DIR/group_session_ws_probe.py" principal \
        --user-id "$BCS_MOCK_USER_ID" \
        --username "$BCS_MOCK_USER_NICK_NAME" \
        --tenant "bcs-e2e" \
        --signing-key "$signing_key"); then
        fail "Gateway Principal generation failed for the Bot Owner"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        return
    fi
    if ! outsider=$(python3 "$SCRIPT_DIR/group_session_ws_probe.py" principal \
        --user-id "$_OFR_OUTSIDER" \
        --username "$_OFR_OUTSIDER" \
        --tenant "bcs-e2e" \
        --signing-key "$signing_key"); then
        fail "Gateway Principal generation failed for the outsider human"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        return
    fi

    # 验证 (QA) owns the roster in this story; 客服 (CS) and the mock human
    # supply the two removable friend kinds.
    local owner_bot="$BOT_QA_UUID"
    local friend_bot="$BOT_CS_UUID"

    # 1. Bot→Bot: the owner removes another Bot from the managed Bot's roster.
    info "owner removes a Bot friend (both directions revoke atomically)"
    if ! _ofr_ensure_friend "{\"type\":\"bot\",\"id\":\"${owner_bot}\"}" "$friend_bot" "$principal"; then
        fail "could not establish the Bot↔Bot friendship under test"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        return
    fi
    _ofr_delete "actor_type=bot&actor_id=${owner_bot}&target_actor_type=bot&target_actor_id=${friend_bot}" "$principal"
    require_status "owner removes a Bot friend through the public API" "200" || return
    assert_json_eq "Bot friend removal reports the transition" "$RESPONSE" "data.deleted" "true"

    _ofr_delete "actor_type=bot&actor_id=${owner_bot}&target_actor_type=bot&target_actor_id=${friend_bot}" "$principal"
    require_status "repeated Bot friend removal stays idempotent" "200" || return
    assert_json_eq "repeated Bot friend removal transitions nothing" "$RESPONSE" "data.deleted" "false"

    # 2. Human→Bot: the owner removes a human friend, which also tears down
    #    the human→bot auth-sync mirror and notifies the human.
    info "owner removes a human friend (human→bot notice + auth-sync teardown)"
    if ! _ofr_ensure_friend "" "$owner_bot" "$principal"; then
        fail "could not establish the human↔Bot friendship under test"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        return
    fi
    _ofr_delete "actor_type=bot&actor_id=${owner_bot}&target_actor_type=human&target_actor_id=${BCS_MOCK_USER_ID}" "$principal"
    require_status "owner removes a human friend through the public API" "200" || return
    assert_json_eq "human friend removal reports the transition" "$RESPONSE" "data.deleted" "true"

    # 3. Self-removal (no actor params) keeps working alongside the owner path.
    info "caller-side self-removal keeps working (no actor params)"
    if ! _ofr_ensure_friend "" "$owner_bot" "$principal"; then
        fail "could not re-establish the human↔Bot friendship under test"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        return
    fi
    _ofr_delete "target_actor_type=bot&target_actor_id=${owner_bot}" "$principal"
    require_status "caller-side self-removal through the public API" "200" || return
    assert_json_eq "caller-side self-removal reports the transition" "$RESPONSE" "data.deleted" "true"

    # 4. Input and authority gates.
    info "owner roster validates its inputs and the live ownership edge"
    _ofr_delete "actor_type=bot&target_actor_type=bot&target_actor_id=${friend_bot}" "$principal"
    require_status "actor_type without actor_id is rejected" "400" || return
    assert_json_eq "half-specified owner params use the invalid_request code" \
        "$RESPONSE" "data.error_code" "invalid_request"

    _ofr_delete "actor_type=human&actor_id=${BCS_MOCK_USER_ID}&target_actor_type=bot&target_actor_id=${friend_bot}" "$principal"
    require_status "a human-typed actor is rejected for owner removal" "400" || return
    assert_json_eq "human-typed actor uses the invalid_request code" \
        "$RESPONSE" "data.error_code" "invalid_request"

    _ofr_delete "actor_type=bot&actor_id=&target_actor_type=bot&target_actor_id=${friend_bot}" "$principal"
    require_status "an empty actor id is rejected for owner removal" "400" || return
    assert_json_eq "empty actor id uses the invalid_request code" \
        "$RESPONSE" "data.error_code" "invalid_request"

    _ofr_delete "actor_type=bot&actor_id=00000000-0000-0000-0000-000000000000&target_actor_type=bot&target_actor_id=${friend_bot}" "$principal"
    require_status "an unknown actor Bot is not found" "404" || return
    assert_json_eq "unknown actor Bot uses the bot_not_found code" \
        "$RESPONSE" "data.error_code" "bot_not_found"

    _ofr_delete "actor_type=bot&actor_id=${owner_bot}&target_actor_type=bot&target_actor_id=${friend_bot}" "$outsider"
    require_status "a non-owner caller cannot remove the Bot's friends" "403" || return
    assert_json_eq "non-owner removal uses the forbidden code" \
        "$RESPONSE" "data.error_code" "forbidden"
}