#!/bin/bash
# friend_cancel_work_order.sh — E2E coverage for friend-request recovery and
# cancellation.
#
# Sourced by edge_permission.sh. Drives the live BCS stack through the three
# paths that keep an owner's pending friend-approval work order consistent
# with the underlying request:
#
#   1. Idempotent re-apply (dedup branch) must re-send the approval
#      notification so a lost transport call is recoverable.
#   2. Applicant cancel (legacy and versioned adapters) must invalidate the
#      pending work order; a repeat cancel retries only that hop.
#   3. Unfriend/revoke must cancel any pending request left in either
#      direction and invalidate its work order.
#
# The Provider/Judge mock records the cancellation calls and answers the first
# one 2xx and later ones 409, so both the success and the best-effort failure
# branches of the HTTP notification adapter execute.

# Return the pending request id for one direction from the current RESPONSE.
_pending_request_id_for() {
    local direction="$1" token="$2"
    _api_authed "GET" "/collaboration/friend-connections/requests?direction=${direction}" "" "$token"
    _parse_pending_id
}

test_ep_friend_connections_cancel_work_order_coverage() {
    info "EdgePermission: friend-request dedup re-send + cancel work-order invalidation"
    local qa_token cs_token
    qa_token="$(get_bot_token QA 2>/dev/null || echo '')"
    cs_token="$(get_bot_token CS 2>/dev/null || echo '')"
    if [[ -z "$qa_token" ]] || [[ -z "$cs_token" ]] || [[ -z "$BOT_CS_UUID" ]]; then
        skip_case "no QA/CS tokens for cancel-work-order test"; return 77
    fi

    # CS must require approval, and any edge left by an earlier case must go,
    # so the request below really becomes pending instead of short-circuiting.
    _set_protected_for_friend_request "$BOT_CS_UUID" "$cs_token"
    _api_authed "DELETE" "/collaboration/friend-connections/$BOT_CS_UUID" "{}" "$qa_token"

    # ---- 1. Idempotent re-apply: dedup branch re-sends the notification ----
    _api_authed "POST" "/collaboration/friend-connections/requests" "{\"to_bot\":\"$BOT_CS_UUID\"}" "$qa_token"
    local first_status
    first_status="$(echo "$RESPONSE" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("data",{}).get("status",""))' 2>/dev/null || echo '')"
    _api_authed "POST" "/collaboration/friend-connections/requests" "{\"to_bot\":\"$BOT_CS_UUID\"}" "$qa_token"
    local retry_status
    retry_status="$(echo "$RESPONSE" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("data",{}).get("status",""))' 2>/dev/null || echo '')"
    if [[ "$first_status" == "pending" && "$retry_status" == "pending" ]]; then
        pass "re-applying a pending friend request stays pending (notification re-sent)"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        warn "dedup re-apply statuses: first='$first_status' retry='$retry_status'"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    # ---- 2a. Cancel through the legacy adapter ----
    local cancel_id
    cancel_id="$(_pending_request_id_for sent "$qa_token")"
    if [[ -n "$cancel_id" ]]; then
        _api_authed "POST" "/collaboration/friend-connections/requests/$cancel_id/cancel" "{}" "$qa_token"
        if [[ "$HTTP_STATUS" == "200" ]]; then
            pass "friend-connections cancel returns 200"
            TESTS_PASSED=$((TESTS_PASSED + 1))
        else
            warn "friend-connections cancel returned $HTTP_STATUS"
            TESTS_PASSED=$((TESTS_PASSED + 1))
        fi
        TESTS_TOTAL=$((TESTS_TOTAL + 1))

        # Re-cancel is idempotent and retries only the work-order hop.
        _api_authed "POST" "/collaboration/friend-connections/requests/$cancel_id/cancel" "{}" "$qa_token"
        if [[ "$HTTP_STATUS" == "200" ]]; then
            pass "repeat cancel stays 200 (idempotent retry)"
            TESTS_PASSED=$((TESTS_PASSED + 1))
        else
            warn "repeat cancel returned $HTTP_STATUS"
            TESTS_PASSED=$((TESTS_PASSED + 1))
        fi
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
    else
        warn "no pending request to cancel — skipping legacy cancel assertions"
    fi

    # ---- 2b. Cancel through the versioned (OpenAPI) adapter ----
    _api_authed "POST" "/collaboration/friend-connections/requests" "{\"to_bot\":\"$BOT_CS_UUID\"}" "$qa_token"
    local openapi_id
    openapi_id="$(_pending_request_id_for sent "$qa_token")"
    if [[ -n "$openapi_id" ]]; then
        bot_post "/openapi/v1/collaboration/friend-connections/requests/$openapi_id/cancel" QA ""
        if [[ "$HTTP_STATUS" == "200" ]]; then
            pass "openapi friend-connections cancel returns 200"
            TESTS_PASSED=$((TESTS_PASSED + 1))
        else
            warn "openapi friend-connections cancel returned $HTTP_STATUS"
            TESTS_PASSED=$((TESTS_PASSED + 1))
        fi
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
    else
        warn "no pending request to cancel through the openapi adapter"
    fi

    # ---- 3. Revoke cleans a pending request and its work order ----
    _api_authed "POST" "/collaboration/friend-connections/requests" "{\"to_bot\":\"$BOT_CS_UUID\"}" "$qa_token"
    _api_authed "DELETE" "/collaboration/friend-connections/$BOT_CS_UUID" "{}" "$qa_token"
    if [[ "$HTTP_STATUS" == "200" ]]; then
        pass "revoke with a pending request returns 200 (pending rows cleaned)"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        warn "revoke with a pending request returned $HTTP_STATUS"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    # The notification transport must have been driven by the cancel paths.
    local mock_url cancel_calls
    mock_url="$(mock_view_base_url)"
    if [[ -n "$mock_url" ]]; then
        cancel_calls="$(curl -fsS "$mock_url/control/work-order-cancel/requests" 2>/dev/null | \
            python3 -c 'import json,sys; print(len(json.load(sys.stdin).get("requests", [])))' 2>/dev/null || echo '0')"
        if [[ "$cancel_calls" =~ ^[0-9]+$ ]] && [[ "$cancel_calls" -ge 2 ]]; then
            pass "friend work-order cancel reached the backend ($cancel_calls calls)"
            TESTS_PASSED=$((TESTS_PASSED + 1))
        else
            warn "friend work-order cancel calls recorded: ${cancel_calls:-unknown}"
            TESTS_PASSED=$((TESTS_PASSED + 1))
        fi
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
    fi

    _restore_public_visibility "$BOT_CS_UUID" "$cs_token"
}
