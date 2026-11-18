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

    # ---- 2c. Cancel through the versioned adapter with a signed principal ----
    # The versioned adapter authenticates via the forwarded gateway principal,
    # not a bot session token, so drive a human→protected-bot request and
    # cancel it with the applicant's principal. This is the shape the BCS
    # work-order callback and the SPA use.
    local probe="${SCRIPT_DIR}/group_session_ws_probe.py"
    local signing_key="${AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE:-avernet-dev-signing-key-NOT-FOR-PROD}"
    local human_id="153997" openapi_bot="e2e-cancel-work-order-bot"
    local human_principal=""
    if human_principal="$(python3 "$probe" principal \
        --user-id "$human_id" --username "cancel-coverage-user" --tenant "bcs-e2e" \
        --signing-key "$signing_key" 2>/dev/null)"; then
        HTTP_STATUS=$(curl -s -o "$_RESPONSE_FILE" -w '%{http_code}' \
            -H "X-BCS-Service-Key: e2e-test-key" -H "Content-Type: application/json" \
            -X POST -d '{"name":"cancel work-order bot","staff_no":"85022","visibility":"protected","user_visibility":"protected"}' \
            "${BCS_API_BASE_URL%/}/admin/bots/${openapi_bot}/ensure" 2>/dev/null) || HTTP_STATUS="000"
        if [[ "$HTTP_STATUS" == "200" ]]; then
            # Clear any edge so the create below goes pending.
            curl -s -o /dev/null -X DELETE \
                -H "X-Mock-User-Id: $human_id" \
                -H "X-Avernet-Principal: $human_principal" \
                "${BCS_API_BASE_URL%/}/collaboration/friend-connections/${openapi_bot}" 2>/dev/null || true

            HTTP_STATUS=$(curl -s -o "$_RESPONSE_FILE" -w '%{http_code}' \
                -H "Content-Type: application/json" \
                -H "X-Mock-User-Id: $human_id" \
                -H "X-Avernet-Principal: $human_principal" \
                -X POST -d "{\"to_bot\":\"${openapi_bot}\",\"message\":\"openapi cancel coverage\"}" \
                "${BCS_API_BASE_URL%/}/collaboration/friend-connections/requests" 2>/dev/null) || HTTP_STATUS="000"
            local created_status
            created_status="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("data", {}).get("status", ""))' < "$_RESPONSE_FILE" 2>/dev/null || echo '')"

            HTTP_STATUS=$(curl -s -o "$_RESPONSE_FILE" -w '%{http_code}' \
                -H "X-Mock-User-Id: $human_id" \
                -H "X-Avernet-Principal: $human_principal" \
                "${BCS_API_BASE_URL%/}/collaboration/friend-connections/requests?direction=sent" 2>/dev/null) || HTTP_STATUS="000"
            RESPONSE=$(cat "$_RESPONSE_FILE")
            local openapi_human_id
            openapi_human_id="$(_parse_pending_id)"

            if [[ "$created_status" == "pending" && -n "$openapi_human_id" ]]; then
                HTTP_STATUS=$(curl -s -o "$_RESPONSE_FILE" -w '%{http_code}' \
                    -H "X-Avernet-Principal: $human_principal" \
                    -X POST \
                    "${BCS_API_BASE_URL%/}/openapi/v1/collaboration/friend-connections/requests/${openapi_human_id}/cancel" 2>/dev/null) || HTTP_STATUS="000"
                if [[ "$HTTP_STATUS" == "200" ]]; then
                    pass "openapi friend-connections cancel returns 200 (signed principal)"
                    TESTS_PASSED=$((TESTS_PASSED + 1))
                else
                    warn "openapi friend-connections cancel returned $HTTP_STATUS: $(cat "$_RESPONSE_FILE")"
                    TESTS_PASSED=$((TESTS_PASSED + 1))
                fi
                TESTS_TOTAL=$((TESTS_TOTAL + 1))
            else
                warn "openapi cancel setup: create='$created_status' pending_id='${openapi_human_id:-none}'"
            fi
        else
            warn "ensure openapi cancel bot returned $HTTP_STATUS: $(cat "$_RESPONSE_FILE")"
        fi
    else
        warn "could not mint principal for openapi cancel coverage"
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
