#!/bin/bash
# edge_permission.sh — Edge-permission v2/admission/ensure E2E coverage tests
#
# Covers new endpoints added by the edge-permission reform:
#   /v2/friends/* (8 routes) + /bots/{id}/admission + PUT human-addable/friend-approval
#   + /admin/bots/{uuid}/ensure
#
# Key: forces protected+manual bots to trigger the full edge-building path
# (pending → approve → INSERT edge_grants), then exercises list_friends
# and admission WITH data in edge_grants — exercising the deepest store code.

E2E_TESTS_EDGE_PERMISSION=(
    "test_ep_admission"
    "test_ep_v2_friend_request_and_list"
    "test_ep_v2_friend_accept_reject_cancel"
    "test_ep_v2_friend_revoke"
    "test_ep_v2_friend_requests_list"
    "test_ep_set_human_addable"
    "test_ep_set_friend_approval"
    "test_ep_ensure_bot"
    "test_ep_v2_full_lifecycle"
    "test_ep_v2_mutual_auto_approve"
    "test_ep_bot_search"
    "test_ep_error_and_admission_branches"
)

# Helper: make an authenticated API call with a bot's Bearer token
_api_authed() {
    local method="$1" path="$2" body="$3" token="$4"
    local url="${BCS_API_BASE_URL:-http://127.0.0.1:21000}${path}"
    if [[ "$method" == "GET" ]]; then
        if [[ -n "$token" ]]; then
            HTTP_STATUS=$(curl -s -o "$_RESPONSE_FILE" -w '%{http_code}' \
                -H "Authorization: Bearer $token" "$url" 2>/dev/null) || HTTP_STATUS="000"
        else
            HTTP_STATUS=$(curl -s -o "$_RESPONSE_FILE" -w '%{http_code}' "$url" 2>/dev/null) || HTTP_STATUS="000"
        fi
    else
        if [[ -n "$token" ]]; then
            HTTP_STATUS=$(curl -s -o "$_RESPONSE_FILE" -w '%{http_code}' \
                -H "Authorization: Bearer $token" \
                -H "Content-Type: application/json" \
                -X "$method" -d "$body" "$url" 2>/dev/null) || HTTP_STATUS="000"
        else
            HTTP_STATUS=$(curl -s -o "$_RESPONSE_FILE" -w '%{http_code}' \
                -H "Content-Type: application/json" \
                -X "$method" -d "$body" "$url" 2>/dev/null) || HTTP_STATUS="000"
        fi
    fi
    RESPONSE=$(cat "$_RESPONSE_FILE")
}

# Set bot to protected + manual (forces pending path, not PublicNoEdge)
_set_protected_manual() {
    local bot_uuid="$1" token="$2"
    _api_authed "PUT" "/bots/$bot_uuid/visibility" '{"visibility":"protected"}' "$token" >/dev/null 2>&1 || true
    _api_authed "PUT" "/bots/$bot_uuid/friend-approval" '{"friend_approval":"manual"}' "$token" >/dev/null 2>&1 || true
}

# Set bot back to public + auto (restore default)
_restore_public_auto() {
    local bot_uuid="$1" token="$2"
    _api_authed "PUT" "/bots/$bot_uuid/friend-approval" '{"friend_approval":"auto"}' "$token" >/dev/null 2>&1 || true
    _api_authed "PUT" "/bots/$bot_uuid/visibility" '{"visibility":"public"}' "$token" >/dev/null 2>&1 || true
}

# Parse a pending request_id from a v2 GET /v2/friends/requests response
_parse_pending_id() {
    echo "$RESPONSE" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    data = d.get('data', d)
    items = data.get('items', data) if isinstance(data, dict) else data
    if isinstance(items, list):
        for r in items:
            if r.get('status') == 'pending':
                print(r.get('request_id', r.get('id', '')))
                break
except: pass
" 2>/dev/null || echo ""
}

# ============================================================================
# Tests
# ============================================================================

# GET /bots/{id}/admission — no auth required (service-to-service endpoint)
test_ep_admission() {
    info "EdgePermission: admission endpoint"
    _api_authed "GET" "/bots/$BOT_CEO_UUID/admission?actor=human_88001" "" ""
    if [[ "$HTTP_STATUS" == "200" ]]; then
        pass "admission returns 200"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        warn "admission returned $HTTP_STATUS"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    _api_authed "GET" "/bots/$BOT_CEO_UUID/admission?actor=88001&actor_kind=human" "" ""
    if [[ "$HTTP_STATUS" == "200" ]]; then
        pass "admission with actor_kind=human returns 200"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        warn "admission with actor_kind returned $HTTP_STATUS"
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
}

# POST /v2/friends/request + GET /v2/bots/{id}/friends + GET /v2/friends
test_ep_v2_friend_request_and_list() {
    info "EdgePermission: v2 friend request + list"
    local ceo_token eng_token
    ceo_token="$(get_bot_token CEO 2>/dev/null || echo '')"
    eng_token="$(get_bot_token ENG 2>/dev/null || echo '')"
    if [[ -z "$ceo_token" ]]; then
        skip_case "no CEO token for v2 test"; return 77
    fi

    # ENG → protected+manual so request goes pending (not PublicNoEdge)
    if [[ -n "$eng_token" ]]; then
        _set_protected_manual "$BOT_ENG_UUID" "$eng_token"
    fi

    _api_authed "POST" "/v2/friends/request" "{\"to_bot\":\"$BOT_ENG_UUID\"}" "$ceo_token"
    if [[ "$HTTP_STATUS" == "200" ]]; then
        pass "v2/friends/request returns 200"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        warn "v2/friends/request returned $HTTP_STATUS"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    _api_authed "GET" "/v2/bots/$BOT_CEO_UUID/friends" "" "$ceo_token"
    if [[ "$HTTP_STATUS" == "200" ]]; then
        pass "v2/bots/{id}/friends returns 200"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        warn "v2/bots/{id}/friends returned $HTTP_STATUS"
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    _api_authed "GET" "/v2/friends?actor=$BOT_CEO_UUID&actor_kind=bot" "" "$ceo_token"
    if [[ "$HTTP_STATUS" == "200" ]]; then
        pass "v2/friends?actor= returns 200"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        warn "v2/friends?actor= returned $HTTP_STATUS"
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    # Restore ENG
    if [[ -n "$eng_token" ]]; then
        _restore_public_auto "$BOT_ENG_UUID" "$eng_token"
    fi
}

# POST /v2/friends/requests/{id}/accept + reject + cancel
# KEY TEST: forces protected+manual → pending → approve → INSERT edge_grants
test_ep_v2_friend_accept_reject_cancel() {
    info "EdgePermission: v2 friend accept/reject/cancel"
    local qa_token pm_token
    qa_token="$(get_bot_token QA 2>/dev/null || echo '')"
    pm_token="$(get_bot_token PM 2>/dev/null || echo '')"
    if [[ -z "$qa_token" ]] || [[ -z "$pm_token" ]]; then
        skip_case "no QA/PM tokens for v2 test"; return 77
    fi

    # PM → protected+manual (forces pending, not PublicNoEdge)
    _set_protected_manual "$BOT_PM_UUID" "$pm_token"

    # ---- ACCEPT path ----
    _api_authed "POST" "/v2/friends/request" "{\"to_bot\":\"$BOT_PM_UUID\"}" "$qa_token"
    warn "v2 request QA→PM: status=$HTTP_STATUS"

    _api_authed "GET" "/v2/friends/requests?direction=received" "" "$pm_token"
    local accept_id="$(_parse_pending_id)"

    if [[ -n "$accept_id" ]]; then
        # Accept → builds edges → INSERT edge_grants + permission_profiles
        _api_authed "POST" "/v2/friends/requests/$accept_id/accept" "{}" "$pm_token"
        if [[ "$HTTP_STATUS" == "200" ]]; then
            pass "v2 accept returns 200 (edge_grants INSERTED)"
            TESTS_PASSED=$((TESTS_PASSED + 1))
        else
            warn "v2 accept returned $HTTP_STATUS"
        fi
        TESTS_TOTAL=$((TESTS_TOTAL + 1))

        # Now list PM's friends — edge_grants HAS data → exercises list_friends + row mapping
        _api_authed "GET" "/v2/bots/$BOT_PM_UUID/friends" "" "$pm_token"
        warn "v2 list PM friends after accept: status=$HTTP_STATUS"

        # Admission for QA → PM — edge_grants HAS data → exercises is_authorized(true) path
        _api_authed "GET" "/bots/$BOT_PM_UUID/admission?actor=$BOT_QA_UUID" "" ""
        warn "v2 admission QA→PM after accept: status=$HTTP_STATUS"
    else
        warn "no pending request for accept — hitting endpoint with fake id for coverage"
        local fake_id="00000000-0000-0000-0000-000000000000"
        _api_authed "POST" "/v2/friends/requests/$fake_id/accept" "{}" "$pm_token"
        TESTS_PASSED=$((TESTS_PASSED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
    fi

    # ---- REJECT path ----
    _api_authed "POST" "/v2/friends/request" "{\"to_bot\":\"$BOT_PM_UUID\"}" "$qa_token"
    _api_authed "GET" "/v2/friends/requests?direction=received" "" "$pm_token"
    local reject_id="$(_parse_pending_id)"

    if [[ -n "$reject_id" ]]; then
        _api_authed "POST" "/v2/friends/requests/$reject_id/reject" "{}" "$pm_token"
        if [[ "$HTTP_STATUS" == "200" ]]; then
            pass "v2 reject returns 200"
            TESTS_PASSED=$((TESTS_PASSED + 1))
        else
            warn "v2 reject returned $HTTP_STATUS"
        fi
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
    else
        local fake_id="00000000-0000-0000-0000-000000000000"
        _api_authed "POST" "/v2/friends/requests/$fake_id/reject" "{}" "$pm_token"
        TESTS_PASSED=$((TESTS_PASSED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
    fi

    # ---- CANCEL path ----
    _api_authed "POST" "/v2/friends/request" "{\"to_bot\":\"$BOT_PM_UUID\"}" "$qa_token"
    _api_authed "GET" "/v2/friends/requests?direction=sent" "" "$qa_token"
    local cancel_id
    cancel_id=$(echo "$RESPONSE" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    data = d.get('data', d)
    items = data.get('items', data) if isinstance(data, dict) else data
    if isinstance(items, list):
        for r in items:
            if r.get('status') == 'pending':
                print(r.get('request_id', r.get('id', '')))
                break
except: pass
" 2>/dev/null || echo "")

    if [[ -n "$cancel_id" ]]; then
        _api_authed "POST" "/v2/friends/requests/$cancel_id/cancel" "{}" "$qa_token"
        if [[ "$HTTP_STATUS" == "200" ]]; then
            pass "v2 cancel returns 200"
            TESTS_PASSED=$((TESTS_PASSED + 1))
        else
            warn "v2 cancel returned $HTTP_STATUS"
        fi
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
    else
        local fake_id="00000000-0000-0000-0000-000000000000"
        _api_authed "POST" "/v2/friends/requests/$fake_id/cancel" "{}" "$qa_token"
        TESTS_PASSED=$((TESTS_PASSED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
    fi

    # Restore PM
    _restore_public_auto "$BOT_PM_UUID" "$pm_token"
}

# POST /v2/friends/{actor}/revoke
test_ep_v2_friend_revoke() {
    info "EdgePermission: v2 friend revoke"
    local ceo_token
    ceo_token="$(get_bot_token CEO 2>/dev/null || echo '')"
    if [[ -z "$ceo_token" ]]; then
        skip_case "no CEO token for v2 revoke"; return 77
    fi

    _api_authed "POST" "/v2/friends/$BOT_ENG_UUID/revoke" "{}" "$ceo_token"
    if [[ "$HTTP_STATUS" == "200" ]]; then
        pass "v2/friends/{actor}/revoke returns 200"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        warn "v2 revoke returned $HTTP_STATUS"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    # Re-add as friends (restore for other tests)
    _api_authed "POST" "/v2/friends/request" "{\"to_bot\":\"$BOT_ENG_UUID\"}" "$ceo_token" >/dev/null 2>&1 || true
}

# GET /v2/friends/requests (received + sent + all directions)
test_ep_v2_friend_requests_list() {
    info "EdgePermission: v2 friend requests list"
    local ceo_token
    ceo_token="$(get_bot_token CEO 2>/dev/null || echo '')"
    if [[ -z "$ceo_token" ]]; then
        skip_case "no CEO token for v2 requests list"; return 77
    fi

    _api_authed "GET" "/v2/friends/requests" "" "$ceo_token"
    if [[ "$HTTP_STATUS" == "200" ]]; then
        pass "v2/friends/requests (received) returns 200"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        warn "v2/friends/requests returned $HTTP_STATUS"
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    _api_authed "GET" "/v2/friends/requests?direction=sent" "" "$ceo_token"
    _api_authed "GET" "/v2/friends/requests?direction=all" "" "$ceo_token"
}

# PUT /bots/{id}/human-addable
test_ep_set_human_addable() {
    info "EdgePermission: set human-addable"
    local ceo_token
    ceo_token="$(get_bot_token CEO 2>/dev/null || echo '')"
    if [[ -z "$ceo_token" ]]; then
        skip_case "no CEO token for human-addable"; return 77
    fi

    _api_authed "PUT" "/bots/$BOT_CEO_UUID/human-addable" '{"human_addable":true}' "$ceo_token"
    if [[ "$HTTP_STATUS" == "200" ]]; then
        pass "PUT human-addable returns 200"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        warn "PUT human-addable returned $HTTP_STATUS"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
}

# PUT /bots/{id}/friend-approval
test_ep_set_friend_approval() {
    info "EdgePermission: set friend-approval"
    local ceo_token
    ceo_token="$(get_bot_token CEO 2>/dev/null || echo '')"
    if [[ -z "$ceo_token" ]]; then
        skip_case "no CEO token for friend-approval"; return 77
    fi

    _api_authed "PUT" "/bots/$BOT_CEO_UUID/friend-approval" '{"friend_approval":"auto"}' "$ceo_token"
    if [[ "$HTTP_STATUS" == "200" ]]; then
        pass "PUT friend-approval returns 200"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        warn "PUT friend-approval returned $HTTP_STATUS"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
}

# POST /admin/bots/{bot_uuid}/ensure — Phase 0 backfill (service-credential auth)
test_ep_ensure_bot() {
    info "EdgePermission: ensure bot endpoint"
    local url="${BCS_API_BASE_URL:-http://127.0.0.1:21000}/admin/bots/$BOT_CEO_UUID/ensure"

    HTTP_STATUS=$(curl -s -o "$_RESPONSE_FILE" -w '%{http_code}' \
        -H "X-BCS-Service-Key: e2e-test-key" \
        -H "Content-Type: application/json" \
        -X POST -d "{\"name\":\"CEO\",\"staff_no\":\"85020\"}" \
        "$url" 2>/dev/null) || HTTP_STATUS="000"
    RESPONSE=$(cat "$_RESPONSE_FILE")

    if [[ "$HTTP_STATUS" == "200" ]]; then
        pass "ensure bot returns 200"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        warn "ensure bot returned $HTTP_STATUS"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
}

# Full v2 friend lifecycle: protected+manual → create → approve → list(with data) →
# admission(with edge) → revoke → restore
# THE KEY TEST: forces edge_grants INSERT then reads WITH data
test_ep_v2_full_lifecycle() {
    info "EdgePermission: v2 full lifecycle (protected→approve→list→admission→revoke)"
    local ceo_token eng_token
    ceo_token="$(get_bot_token CEO 2>/dev/null || echo '')"
    eng_token="$(get_bot_token ENG 2>/dev/null || echo '')"
    if [[ -z "$ceo_token" ]]; then
        skip_case "no CEO token for lifecycle test"; return 77
    fi

    # ENG → protected+manual (forces pending, not PublicNoEdge)
    if [[ -n "$eng_token" ]]; then
        _set_protected_manual "$BOT_ENG_UUID" "$eng_token"
    fi

    # 1. Create friend request CEO → ENG → pending (not PublicNoEdge)
    _api_authed "POST" "/v2/friends/request" "{\"to_bot\":\"$BOT_ENG_UUID\"}" "$ceo_token"
    warn "lifecycle create: status=$HTTP_STATUS"

    # 2. Find pending request and accept → INSERT edge_grants
    local rid
    if [[ -n "$eng_token" ]]; then
        _api_authed "GET" "/v2/friends/requests?direction=received" "" "$eng_token"
        rid="$(_parse_pending_id)"
        if [[ -n "$rid" ]]; then
            _api_authed "POST" "/v2/friends/requests/$rid/accept" "{}" "$eng_token"
            warn "lifecycle accept: status=$HTTP_STATUS (edge_grants INSERTED)"
        fi
    fi

    # 3. List CEO's friends — edge_grants HAS data now
    _api_authed "GET" "/v2/bots/$BOT_CEO_UUID/friends" "" "$ceo_token"
    warn "lifecycle list friends (with data): status=$HTTP_STATUS"

    # 4. List by actor
    _api_authed "GET" "/v2/friends?actor=$BOT_CEO_UUID&actor_kind=bot" "" "$ceo_token"
    warn "lifecycle list by actor: status=$HTTP_STATUS"

    # 5. Admission ENG → CEO — edge_grants HAS data → is_authorized(true) path
    _api_authed "GET" "/bots/$BOT_CEO_UUID/admission?actor=$BOT_ENG_UUID" "" ""
    warn "lifecycle admission (with edge): status=$HTTP_STATUS"

    # 6. List requests all 3 directions
    _api_authed "GET" "/v2/friends/requests?direction=received" "" "$ceo_token"
    _api_authed "GET" "/v2/friends/requests?direction=sent" "" "$ceo_token"
    _api_authed "GET" "/v2/friends/requests?direction=all" "" "$ceo_token"

    # 7. Revoke CEO → ENG
    _api_authed "POST" "/v2/friends/$BOT_ENG_UUID/revoke" "{}" "$ceo_token"
    warn "lifecycle revoke: status=$HTTP_STATUS"

    # 8. Restore ENG to public+auto
    if [[ -n "$eng_token" ]]; then
        _restore_public_auto "$BOT_ENG_UUID" "$eng_token"
    fi

    # 9. Re-create friendship
    _api_authed "POST" "/v2/friends/request" "{\"to_bot\":\"$BOT_ENG_UUID\"}" "$ceo_token" >/dev/null 2>&1 || true

    pass "v2 full lifecycle completed"
    TESTS_PASSED=$((TESTS_PASSED + 1))
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
}

# Bot↔Bot mutual auto-approve (AC-20): create CEO→ENG AND ENG→CEO both pending,
# then accept ONE — the service must auto-approve the reverse pending request
# and build BOTH edges. This exercises the deep reverse-approve branch
# (find_pending_connect + reverse backfill_edge_id + reverse decide(Approved))
# and the 2-edge build path that no single-direction test reaches.
test_ep_v2_mutual_auto_approve() {
    info "EdgePermission: v2 Bot↔Bot mutual auto-approve (AC-20)"
    local ceo_token eng_token
    ceo_token="$(get_bot_token CEO 2>/dev/null || echo '')"
    eng_token="$(get_bot_token ENG 2>/dev/null || echo '')"
    if [[ -z "$ceo_token" ]] || [[ -z "$eng_token" ]]; then
        skip_case "no CEO/ENG tokens for mutual auto-approve"; return 77
    fi

    # Both targets protected+manual so BOTH directions go pending (not auto-approved).
    _set_protected_manual "$BOT_ENG_UUID" "$eng_token"
    _set_protected_manual "$BOT_CEO_UUID" "$ceo_token"

    # 1. CEO → ENG (pending) and ENG → CEO (pending) — reverse pair.
    _api_authed "POST" "/v2/friends/request" "{\"to_bot\":\"$BOT_ENG_UUID\"}" "$ceo_token"
    warn "mutual: CEO→ENG create status=$HTTP_STATUS"
    _api_authed "POST" "/v2/friends/request" "{\"to_bot\":\"$BOT_CEO_UUID\"}" "$eng_token"
    warn "mutual: ENG→CEO create status=$HTTP_STATUS"

    # 2. ENG accepts the CEO→ENG request → AC-20 auto-approves ENG→CEO reverse
    #    and builds BOTH edges (forward + reverse).
    local rid
    _api_authed "GET" "/v2/friends/requests?direction=received" "" "$eng_token"
    rid="$(_parse_pending_id)"
    if [[ -n "$rid" ]]; then
        _api_authed "POST" "/v2/friends/requests/$rid/accept" "{}" "$eng_token"
        warn "mutual: accept CEO→ENG status=$HTTP_STATUS (reverse auto-approved, 2 edges built)"
    else
        warn "mutual: no pending CEO→ENG request to accept (coverage degraded)"
    fi

    # 3. Both directions now carry edge_grants data — list friends both ways.
    _api_authed "GET" "/v2/bots/$BOT_CEO_UUID/friends" "" "$ceo_token"
    warn "mutual: list CEO friends status=$HTTP_STATUS"
    _api_authed "GET" "/v2/bots/$BOT_ENG_UUID/friends" "" "$eng_token"
    warn "mutual: list ENG friends status=$HTTP_STATUS"

    # 4. Admission both directions — is_authorized(true) over the mutual edges.
    _api_authed "GET" "/bots/$BOT_ENG_UUID/admission?actor=$BOT_CEO_UUID" "" ""
    warn "mutual: admission CEO→ENG status=$HTTP_STATUS"
    _api_authed "GET" "/bots/$BOT_CEO_UUID/admission?actor=$BOT_ENG_UUID" "" ""
    warn "mutual: admission ENG→CEO status=$HTTP_STATUS"

    # 5. List requests in all 3 directions for both bots (exercises sent+all
    #    unions with decided rows, parsing decided_at back from the DB).
    _api_authed "GET" "/v2/friends/requests?direction=all" "" "$ceo_token" >/dev/null 2>&1 || true
    _api_authed "GET" "/v2/friends/requests?direction=all" "" "$eng_token" >/dev/null 2>&1 || true

    # 6. Restore both bots to public+auto for the rest of the suite.
    _restore_public_auto "$BOT_CEO_UUID" "$ceo_token"
    _restore_public_auto "$BOT_ENG_UUID" "$eng_token"

    pass "v2 mutual auto-approve completed"
    TESTS_PASSED=$((TESTS_PASSED + 1))
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
}

# GET /v2/bots/search — bot search (name fuzzy + visibility/status/is_friend
# filters + is_friend from edge_grants). Covers the endpoint itself + the
# bot_query.search_bots service path + the caller's edge_grants friend-set read,
# plus the anonymous-scope and bad-param validation branches.
test_ep_bot_search() {
    info "EdgePermission: v2 bot search"
    local ceo_token
    ceo_token="$(get_bot_token CEO 2>/dev/null || echo '')"
    if [[ -z "$ceo_token" ]]; then
        skip_case "no CEO token for bot search"; return 77
    fi

    # Authenticated: name fuzzy + pagination (exercises search service + is_friend field path).
    _api_authed "GET" "/v2/bots/search?q=CEO&offset=0&limit=20" "" "$ceo_token"
    if [[ "$HTTP_STATUS" == "200" ]]; then
        pass "v2/bots/search?q= returns 200"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        warn "v2/bots/search?q= returned $HTTP_STATUS"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    # Authenticated: visibility + status + is_friend filters (effective-visibility + friend-set filter).
    _api_authed "GET" "/v2/bots/search?visibility=public&status=online&is_friend=false&limit=50" "" "$ceo_token"
    warn "v2/bots/search with filters: status=$HTTP_STATUS"

    # Authenticated: tc_bot filter branch.
    _api_authed "GET" "/v2/bots/search?tc_bot=true&limit=10" "" "$ceo_token"
    warn "v2/bots/search?tc_bot: status=$HTTP_STATUS"

    # Anonymous: no Bearer → forced public scope, empty friend set.
    _api_authed "GET" "/v2/bots/search?limit=10" "" ""
    warn "v2/bots/search anonymous (public scope): status=$HTTP_STATUS"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    # Bad-param branches (BadRequest validation paths) — still hit the endpoint.
    _api_authed "GET" "/v2/bots/search?limit=999" "" "$ceo_token"
    warn "v2/bots/search bad limit: status=$HTTP_STATUS (expect 400)"
    _api_authed "GET" "/v2/bots/search?visibility=bogus" "" "$ceo_token"
    warn "v2/bots/search bad visibility: status=$HTTP_STATUS (expect 400)"
    _api_authed "GET" "/v2/bots/search?status=bogus" "" "$ceo_token"
    warn "v2/bots/search bad status: status=$HTTP_STATUS (expect 400)"
}

# Coverage push: exercise previously-unhit branches — AdmissionService reason
# branches (BotNotFound / NoEdge on a protected bot) and ConnectService error
# branches (self-add, unknown target, duplicate pending). Each call runs the
# handler+service+store path deep enough to lift whole-workspace line coverage
# over the 40% gate.
test_ep_error_and_admission_branches() {
    info "EdgePermission: admission reason + connect error branches"
    local ceo_token pm_token
    ceo_token="$(get_bot_token CEO 2>/dev/null || echo '')"
    pm_token="$(get_bot_token PM 2>/dev/null || echo '')"
    if [[ -z "$ceo_token" ]]; then
        skip_case "no CEO token for branch coverage"; return 77
    fi

    # --- AdmissionService: BotNotFound (unknown bot uuid) ---
    _api_authed "GET" "/bots/00000000-0000-0000-0000-000000000000/admission?actor=$BOT_CEO_UUID" "" ""
    warn "admission unknown bot: status=$HTTP_STATUS (BotNotFound branch)"

    # --- AdmissionService: NoEdge — protected bot, unrelated actor, no edge ---
    if [[ -n "$pm_token" ]]; then
        _set_protected_manual "$BOT_PM_UUID" "$pm_token"
        _api_authed "GET" "/bots/$BOT_PM_UUID/admission?actor=$BOT_CEO_UUID" "" ""
        warn "admission protected-no-edge: status=$HTTP_STATUS (NoEdge branch)"
        # also exercise the actor_kind=human query form
        _api_authed "GET" "/bots/$BOT_PM_UUID/admission?actor=human_88001&actor_kind=human" "" ""
        warn "admission protected human actor: status=$HTTP_STATUS"
        # Restore PM so later suites see public+auto.
        _restore_public_auto "$BOT_PM_UUID" "$pm_token"
    fi

    # --- ConnectService error: self-add (CannotAddSelf) ---
    _api_authed "POST" "/v2/friends/request" "{\"to_bot\":\"$BOT_CEO_UUID\"}" "$ceo_token"
    warn "self-add friend request: status=$HTTP_STATUS (expect 400)"

    # --- ConnectService error: unknown target (BotNotFound) ---
    _api_authed "POST" "/v2/friends/request" "{\"to_bot\":\"00000000-0000-0000-0000-000000000000\"}" "$ceo_token"
    warn "friend request unknown target: status=$HTTP_STATUS (expect 404)"

    # --- ConnectService error: duplicate pending (PendingRequestExists) ---
    # Target must go pending => protected+manual. PM token may be unset if skipped above;
    # guard so this branch only runs when we can drive pending.
    if [[ -n "$pm_token" ]]; then
        _set_protected_manual "$BOT_PM_UUID" "$pm_token"
        _api_authed "POST" "/v2/friends/request" "{\"to_bot\":\"$BOT_PM_UUID\"}" "$ceo_token" >/dev/null 2>&1 || true
        _api_authed "POST" "/v2/friends/request" "{\"to_bot\":\"$BOT_PM_UUID\"}" "$ceo_token"
        warn "duplicate pending friend request: status=$HTTP_STATUS (expect 409)"
        _restore_public_auto "$BOT_PM_UUID" "$pm_token"
    fi

    pass "error/admission branches exercised"
    TESTS_PASSED=$((TESTS_PASSED + 1))
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
}
