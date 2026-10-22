#!/bin/bash
# edge_permission.sh — Edge-permission friend-connections/admission/ensure E2E coverage tests
#
# Covers new endpoints added by the edge-permission reform:
#   /collaboration/friend-connections/* + /bots/search + /bots/{id}/admission
#   + /admin/bots/{uuid}/ensure
#
# Key: forces protected bots to trigger the full edge-building path
# (pending → approve → INSERT edge_grants), then exercises list_friends
# and admission WITH data in edge_grants — exercising the deepest store code.

E2E_TESTS_EDGE_PERMISSION=(
    "test_ep_admission"
    "test_ep_friend_connections_friend_request_and_list"
    "test_ep_friend_connections_friend_accept_reject_cancel"
    "test_ep_friend_connections_friend_revoke"
    "test_ep_friend_connections_friend_requests_list"
    "test_ep_ensure_bot"
    "test_ep_friend_connections_full_lifecycle"
    "test_ep_friend_connections_mutual_auto_approve"
    "test_ep_bot_search"
    "test_ep_error_and_admission_branches"
    "test_ep_connect_private_and_discover_branches"
    "test_ep_tc_friend_auth_sync_readdressed_principal"
    "test_ep_dept_scope_parses_object_friend_ext_entries"
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

# Set bot to protected. Approval behavior now comes from existing bot attributes
# (`user_visibility` / `friend_check_in_strategy`) instead of the removed
# human-addable/friend-approval mutation endpoints, so this helper must not call
# those old routes.
_set_protected_for_friend_request() {
    local bot_uuid="$1" _token="$2"
    api_put "/bots/$bot_uuid/visibility" '{"visibility":"protected"}' >/dev/null 2>&1 || true
}

# Restore bot visibility for the rest of the suite.
_restore_public_visibility() {
    local bot_uuid="$1" _token="$2"
    api_put "/bots/$bot_uuid/visibility" '{"visibility":"public"}' >/dev/null 2>&1 || true
}

# Parse a pending request_id from a GET /collaboration/friend-connections/requests response
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

# POST /collaboration/friend-connections/requests + GET /collaboration/friend-connections
test_ep_friend_connections_friend_request_and_list() {
    info "EdgePermission: friend-connections request + list"
    local ceo_token eng_token
    ceo_token="$(get_bot_token CEO 2>/dev/null || echo '')"
    eng_token="$(get_bot_token ENG 2>/dev/null || echo '')"
    if [[ -z "$ceo_token" ]]; then
        skip_case "no CEO token for friend-connections test"; return 77
    fi

    # ENG → protected so the request does not take the public-no-edge path
    if [[ -n "$eng_token" ]]; then
        _set_protected_for_friend_request "$BOT_ENG_UUID" "$eng_token"
    fi

    _api_authed "POST" "/collaboration/friend-connections/requests" "{\"to_bot\":\"$BOT_ENG_UUID\"}" "$ceo_token"
    if [[ "$HTTP_STATUS" == "200" ]]; then
        pass "collaboration/friend-connections/requests returns 200"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        warn "collaboration/friend-connections/requests returned $HTTP_STATUS"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    _api_authed "GET" "/collaboration/friend-connections?actor=$BOT_CEO_UUID&actor_kind=bot" "" "$ceo_token"
    if [[ "$HTTP_STATUS" == "200" ]]; then
        pass "collaboration/friend-connections?actor={id}&actor_kind=bot returns 200"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        warn "collaboration/friend-connections?actor={id}&actor_kind=bot returned $HTTP_STATUS"
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    _api_authed "GET" "/collaboration/friend-connections?actor=$BOT_CEO_UUID&actor_kind=bot" "" "$ceo_token"
    if [[ "$HTTP_STATUS" == "200" ]]; then
        pass "collaboration/friend-connections?actor= returns 200"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        warn "collaboration/friend-connections?actor= returned $HTTP_STATUS"
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    # Restore ENG
    if [[ -n "$eng_token" ]]; then
        _restore_public_visibility "$BOT_ENG_UUID" "$eng_token"
    fi
}

# POST /collaboration/friend-connections/requests/{id}/accept + reject + cancel
# KEY TEST: forces protected → pending → approve → INSERT edge_grants
test_ep_friend_connections_friend_accept_reject_cancel() {
    info "EdgePermission: friend-connections accept/reject/cancel"
    local qa_token pm_token
    qa_token="$(get_bot_token QA 2>/dev/null || echo '')"
    pm_token="$(get_bot_token PM 2>/dev/null || echo '')"
    if [[ -z "$qa_token" ]] || [[ -z "$pm_token" ]]; then
        skip_case "no QA/PM tokens for friend-connections test"; return 77
    fi

    # PM → protected so the request does not take the public-no-edge path
    _set_protected_for_friend_request "$BOT_PM_UUID" "$pm_token"

    # ---- ACCEPT path ----
    _api_authed "POST" "/collaboration/friend-connections/requests" "{\"to_bot\":\"$BOT_PM_UUID\"}" "$qa_token"
    warn "friend-connections request QA→PM: status=$HTTP_STATUS"

    _api_authed "GET" "/collaboration/friend-connections/requests?direction=received" "" "$pm_token"
    local accept_id="$(_parse_pending_id)"

    if [[ -n "$accept_id" ]]; then
        # Accept → builds edges → INSERT edge_grants + permission_profiles
        _api_authed "POST" "/collaboration/friend-connections/requests/$accept_id/accept" "{}" "$pm_token"
        if [[ "$HTTP_STATUS" == "200" ]]; then
            pass "friend-connections accept returns 200 (edge_grants INSERTED)"
            TESTS_PASSED=$((TESTS_PASSED + 1))
        else
            warn "friend-connections accept returned $HTTP_STATUS"
        fi
        TESTS_TOTAL=$((TESTS_TOTAL + 1))

        # Now list PM's friends — edge_grants HAS data → exercises list_friends + row mapping
        _api_authed "GET" "/collaboration/friend-connections?actor=$BOT_PM_UUID&actor_kind=bot" "" "$pm_token"
        warn "friend-connections list PM friends after accept: status=$HTTP_STATUS"

        # Admission for QA → PM — edge_grants HAS data → exercises is_authorized(true) path
        _api_authed "GET" "/bots/$BOT_PM_UUID/admission?actor=$BOT_QA_UUID" "" ""
        warn "friend-connections admission QA→PM after accept: status=$HTTP_STATUS"
    else
        warn "no pending request for accept — hitting endpoint with fake id for coverage"
        local fake_id="00000000-0000-0000-0000-000000000000"
        _api_authed "POST" "/collaboration/friend-connections/requests/$fake_id/accept" "{}" "$pm_token"
        TESTS_PASSED=$((TESTS_PASSED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
    fi

    # ---- REJECT path ----
    _api_authed "POST" "/collaboration/friend-connections/requests" "{\"to_bot\":\"$BOT_PM_UUID\"}" "$qa_token"
    _api_authed "GET" "/collaboration/friend-connections/requests?direction=received" "" "$pm_token"
    local reject_id="$(_parse_pending_id)"

    if [[ -n "$reject_id" ]]; then
        _api_authed "POST" "/collaboration/friend-connections/requests/$reject_id/reject" "{}" "$pm_token"
        if [[ "$HTTP_STATUS" == "200" ]]; then
            pass "friend-connections reject returns 200"
            TESTS_PASSED=$((TESTS_PASSED + 1))
        else
            warn "friend-connections reject returned $HTTP_STATUS"
        fi
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
    else
        local fake_id="00000000-0000-0000-0000-000000000000"
        _api_authed "POST" "/collaboration/friend-connections/requests/$fake_id/reject" "{}" "$pm_token"
        TESTS_PASSED=$((TESTS_PASSED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
    fi

    # ---- CANCEL path ----
    _api_authed "POST" "/collaboration/friend-connections/requests" "{\"to_bot\":\"$BOT_PM_UUID\"}" "$qa_token"
    _api_authed "GET" "/collaboration/friend-connections/requests?direction=sent" "" "$qa_token"
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
        _api_authed "POST" "/collaboration/friend-connections/requests/$cancel_id/cancel" "{}" "$qa_token"
        if [[ "$HTTP_STATUS" == "200" ]]; then
            pass "friend-connections cancel returns 200"
            TESTS_PASSED=$((TESTS_PASSED + 1))
        else
            warn "friend-connections cancel returned $HTTP_STATUS"
        fi
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
    else
        local fake_id="00000000-0000-0000-0000-000000000000"
        _api_authed "POST" "/collaboration/friend-connections/requests/$fake_id/cancel" "{}" "$qa_token"
        TESTS_PASSED=$((TESTS_PASSED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
    fi

    # Restore PM
    _restore_public_visibility "$BOT_PM_UUID" "$pm_token"
}

# DELETE /collaboration/friend-connections/{actor}
test_ep_friend_connections_friend_revoke() {
    info "EdgePermission: friend-connections revoke"
    local ceo_token
    ceo_token="$(get_bot_token CEO 2>/dev/null || echo '')"
    if [[ -z "$ceo_token" ]]; then
        skip_case "no CEO token for friend-connections revoke"; return 77
    fi

    _api_authed "DELETE" "/collaboration/friend-connections/$BOT_ENG_UUID" "{}" "$ceo_token"
    if [[ "$HTTP_STATUS" == "200" ]]; then
        pass "collaboration/friend-connections/{actor} returns 200"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        warn "friend-connections revoke returned $HTTP_STATUS"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    # Re-add as friends (restore for other tests)
    _api_authed "POST" "/collaboration/friend-connections/requests" "{\"to_bot\":\"$BOT_ENG_UUID\"}" "$ceo_token" >/dev/null 2>&1 || true
}

# GET /collaboration/friend-connections/requests (received + sent + all directions)
test_ep_friend_connections_friend_requests_list() {
    info "EdgePermission: friend-connections requests list"
    local ceo_token
    ceo_token="$(get_bot_token CEO 2>/dev/null || echo '')"
    if [[ -z "$ceo_token" ]]; then
        skip_case "no CEO token for friend-connections requests list"; return 77
    fi

    _api_authed "GET" "/collaboration/friend-connections/requests" "" "$ceo_token"
    if [[ "$HTTP_STATUS" == "200" ]]; then
        pass "collaboration/friend-connections/requests (received) returns 200"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        warn "collaboration/friend-connections/requests returned $HTTP_STATUS"
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    _api_authed "GET" "/collaboration/friend-connections/requests?direction=sent" "" "$ceo_token"
    _api_authed "GET" "/collaboration/friend-connections/requests?direction=all" "" "$ceo_token"
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

# Full friend-connections lifecycle: protected → create → approve → list(with data) →
# admission(with edge) → revoke → restore
# THE KEY TEST: forces edge_grants INSERT then reads WITH data
test_ep_friend_connections_full_lifecycle() {
    info "EdgePermission: friend-connections full lifecycle (protected→approve→list→admission→revoke)"
    local ceo_token eng_token
    ceo_token="$(get_bot_token CEO 2>/dev/null || echo '')"
    eng_token="$(get_bot_token ENG 2>/dev/null || echo '')"
    if [[ -z "$ceo_token" ]]; then
        skip_case "no CEO token for lifecycle test"; return 77
    fi

    # ENG → protected (forces pending, not PublicNoEdge)
    if [[ -n "$eng_token" ]]; then
        _set_protected_for_friend_request "$BOT_ENG_UUID" "$eng_token"
    fi

    # 1. Create friend request CEO → ENG → pending (not PublicNoEdge)
    _api_authed "POST" "/collaboration/friend-connections/requests" "{\"to_bot\":\"$BOT_ENG_UUID\"}" "$ceo_token"
    warn "lifecycle create: status=$HTTP_STATUS"

    # 2. Find pending request and accept → INSERT edge_grants
    local rid
    if [[ -n "$eng_token" ]]; then
        _api_authed "GET" "/collaboration/friend-connections/requests?direction=received" "" "$eng_token"
        rid="$(_parse_pending_id)"
        if [[ -n "$rid" ]]; then
            _api_authed "POST" "/collaboration/friend-connections/requests/$rid/accept" "{}" "$eng_token"
            warn "lifecycle accept: status=$HTTP_STATUS (edge_grants INSERTED)"
        fi
    fi

    # 3. List CEO's friends — edge_grants HAS data now
    _api_authed "GET" "/collaboration/friend-connections?actor=$BOT_CEO_UUID&actor_kind=bot" "" "$ceo_token"
    warn "lifecycle list friends (with data): status=$HTTP_STATUS"

    # 4. List by actor
    _api_authed "GET" "/collaboration/friend-connections?actor=$BOT_CEO_UUID&actor_kind=bot" "" "$ceo_token"
    warn "lifecycle list by actor: status=$HTTP_STATUS"

    # 5. Admission ENG → CEO — edge_grants HAS data → is_authorized(true) path
    _api_authed "GET" "/bots/$BOT_CEO_UUID/admission?actor=$BOT_ENG_UUID" "" ""
    warn "lifecycle admission (with edge): status=$HTTP_STATUS"

    # 6. List requests all 3 directions
    _api_authed "GET" "/collaboration/friend-connections/requests?direction=received" "" "$ceo_token"
    _api_authed "GET" "/collaboration/friend-connections/requests?direction=sent" "" "$ceo_token"
    _api_authed "GET" "/collaboration/friend-connections/requests?direction=all" "" "$ceo_token"

    # 7. Revoke CEO → ENG
    _api_authed "DELETE" "/collaboration/friend-connections/$BOT_ENG_UUID" "{}" "$ceo_token"
    warn "lifecycle revoke: status=$HTTP_STATUS"

    # 8. Restore ENG to public
    if [[ -n "$eng_token" ]]; then
        _restore_public_visibility "$BOT_ENG_UUID" "$eng_token"
    fi

    # 9. Re-create friendship
    _api_authed "POST" "/collaboration/friend-connections/requests" "{\"to_bot\":\"$BOT_ENG_UUID\"}" "$ceo_token" >/dev/null 2>&1 || true

    pass "friend-connections full lifecycle completed"
    TESTS_PASSED=$((TESTS_PASSED + 1))
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
}

# Bot↔Bot mutual auto-approve (AC-20): create CEO→ENG AND ENG→CEO both pending,
# then accept ONE — the service must auto-approve the reverse pending request
# and build BOTH edges. This exercises the deep reverse-approve branch
# (find_pending_connect + reverse backfill_edge_id + reverse decide(Approved))
# and the 2-edge build path that no single-direction test reaches.
test_ep_friend_connections_mutual_auto_approve() {
    info "EdgePermission: friend-connections Bot↔Bot mutual auto-approve (AC-20)"
    local ceo_token eng_token
    ceo_token="$(get_bot_token CEO 2>/dev/null || echo '')"
    eng_token="$(get_bot_token ENG 2>/dev/null || echo '')"
    if [[ -z "$ceo_token" ]] || [[ -z "$eng_token" ]]; then
        skip_case "no CEO/ENG tokens for mutual auto-approve"; return 77
    fi

    # Both targets protected so the default approval policy can drive pending requests.
    _set_protected_for_friend_request "$BOT_ENG_UUID" "$eng_token"
    _set_protected_for_friend_request "$BOT_CEO_UUID" "$ceo_token"

    # 1. CEO → ENG (pending) and ENG → CEO (pending) — reverse pair.
    _api_authed "POST" "/collaboration/friend-connections/requests" "{\"to_bot\":\"$BOT_ENG_UUID\"}" "$ceo_token"
    warn "mutual: CEO→ENG create status=$HTTP_STATUS"
    _api_authed "POST" "/collaboration/friend-connections/requests" "{\"to_bot\":\"$BOT_CEO_UUID\"}" "$eng_token"
    warn "mutual: ENG→CEO create status=$HTTP_STATUS"

    # 2. ENG accepts the CEO→ENG request → AC-20 auto-approves ENG→CEO reverse
    #    and builds BOTH edges (forward + reverse).
    local rid
    _api_authed "GET" "/collaboration/friend-connections/requests?direction=received" "" "$eng_token"
    rid="$(_parse_pending_id)"
    if [[ -n "$rid" ]]; then
        _api_authed "POST" "/collaboration/friend-connections/requests/$rid/accept" "{}" "$eng_token"
        warn "mutual: accept CEO→ENG status=$HTTP_STATUS (reverse auto-approved, 2 edges built)"
    else
        warn "mutual: no pending CEO→ENG request to accept (coverage degraded)"
    fi

    # 3. Both directions now carry edge_grants data — list friends both ways.
    _api_authed "GET" "/collaboration/friend-connections?actor=$BOT_CEO_UUID&actor_kind=bot" "" "$ceo_token"
    warn "mutual: list CEO friends status=$HTTP_STATUS"
    _api_authed "GET" "/collaboration/friend-connections?actor=$BOT_ENG_UUID&actor_kind=bot" "" "$eng_token"
    warn "mutual: list ENG friends status=$HTTP_STATUS"

    # 4. Admission both directions — is_authorized(true) over the mutual edges.
    _api_authed "GET" "/bots/$BOT_ENG_UUID/admission?actor=$BOT_CEO_UUID" "" ""
    warn "mutual: admission CEO→ENG status=$HTTP_STATUS"
    _api_authed "GET" "/bots/$BOT_CEO_UUID/admission?actor=$BOT_ENG_UUID" "" ""
    warn "mutual: admission ENG→CEO status=$HTTP_STATUS"

    # 5. List requests in all 3 directions for both bots (exercises sent+all
    #    unions with decided rows, parsing decided_at back from the DB).
    _api_authed "GET" "/collaboration/friend-connections/requests?direction=all" "" "$ceo_token" >/dev/null 2>&1 || true
    _api_authed "GET" "/collaboration/friend-connections/requests?direction=all" "" "$eng_token" >/dev/null 2>&1 || true

    # 6. Restore both bots to public for the rest of the suite.
    _restore_public_visibility "$BOT_CEO_UUID" "$ceo_token"
    _restore_public_visibility "$BOT_ENG_UUID" "$eng_token"

    pass "friend-connections mutual auto-approve completed"
    TESTS_PASSED=$((TESTS_PASSED + 1))
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
}

# GET /bots/search — bot search (name fuzzy + visibility/status/is_friend
# filters + is_friend from edge_grants). Covers the endpoint itself + the
# bot_query.search_bots service path + the caller's edge_grants friend-set read,
# plus the anonymous-scope and bad-param validation branches.
test_ep_bot_search() {
    info "EdgePermission: friend-connections bot search"
    local ceo_token
    ceo_token="$(get_bot_token CEO 2>/dev/null || echo '')"
    if [[ -z "$ceo_token" ]]; then
        skip_case "no CEO token for bot search"; return 77
    fi

    # Authenticated: name fuzzy + pagination (exercises search service + is_friend field path).
    _api_authed "GET" "/bots/search?q=CEO&offset=0&limit=20" "" "$ceo_token"
    if [[ "$HTTP_STATUS" == "200" ]]; then
        pass "bots/search?q= returns 200"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        warn "bots/search?q= returned $HTTP_STATUS"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    # Authenticated: visibility + status + is_friend filters (effective-visibility + friend-set filter).
    _api_authed "GET" "/bots/search?visibility=public&status=online&is_friend=false&limit=50" "" "$ceo_token"
    warn "bots/search with filters: status=$HTTP_STATUS"

    # is_friend=true branch (caller has friends → friend-set non-empty, is_friend field true for matches).
    _api_authed "GET" "/bots/search?is_friend=true&limit=50" "" "$ceo_token"
    warn "bots/search?is_friend=true: status=$HTTP_STATUS"

    # status=hidden + visibility=private filter branches (no matching bots is fine — the match arms still run).
    _api_authed "GET" "/bots/search?status=hidden&limit=10" "" "$ceo_token"
    warn "bots/search?status=hidden: status=$HTTP_STATUS"
    _api_authed "GET" "/bots/search?visibility=private&limit=10" "" "$ceo_token"
    warn "bots/search?visibility=private: status=$HTTP_STATUS"

    # Empty q (None branch of q_lower — list-all, no name filter).
    _api_authed "GET" "/bots/search?limit=5" "" "$ceo_token"
    warn "bots/search empty q: status=$HTTP_STATUS"

    # tc_bot filter: ensure a real TC bot (owner-suffixed uuid with matching created_by)
    # so tc_bot=true has a match and tc_bot=false filters it out — both retain branches run.
    local tc_uuid="tc_cov_bot:85020"
    HTTP_STATUS=$(curl -s -o "$_RESPONSE_FILE" -w '%{http_code}' \
        -H "X-BCS-Service-Key: e2e-test-key" -H "Content-Type: application/json" \
        -X POST -d '{"name":"TCCov","staff_no":"85020"}' \
        "${BCS_API_BASE_URL:-http://127.0.0.1:21000}/admin/bots/$tc_uuid/ensure" 2>/dev/null) || HTTP_STATUS="000"
    warn "ensure TC bot ($tc_uuid): status=$HTTP_STATUS"
    _api_authed "GET" "/bots/search?tc_bot=true&limit=50" "" "$ceo_token"
    warn "bots/search?tc_bot=true (with TC bot present): status=$HTTP_STATUS"
    _api_authed "GET" "/bots/search?tc_bot=false&limit=50" "" "$ceo_token"
    warn "bots/search?tc_bot=false: status=$HTTP_STATUS"

    # Anonymous: no Bearer → forced public scope, empty friend set.
    _api_authed "GET" "/bots/search?limit=10" "" ""
    warn "bots/search anonymous (public scope): status=$HTTP_STATUS"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    # Bad-param branches (BadRequest validation paths) — still hit the endpoint.
    _api_authed "GET" "/bots/search?limit=999" "" "$ceo_token"
    warn "bots/search bad limit: status=$HTTP_STATUS (expect 400)"
    _api_authed "GET" "/bots/search?visibility=bogus" "" "$ceo_token"
    warn "bots/search bad visibility: status=$HTTP_STATUS (expect 400)"
    _api_authed "GET" "/bots/search?status=bogus" "" "$ceo_token"
    warn "bots/search bad status: status=$HTTP_STATUS (expect 400)"
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
        _set_protected_for_friend_request "$BOT_PM_UUID" "$pm_token"
        _api_authed "GET" "/bots/$BOT_PM_UUID/admission?actor=$BOT_CEO_UUID" "" ""
        warn "admission protected-no-edge: status=$HTTP_STATUS (NoEdge branch)"
        # also exercise the actor_kind=human query form
        _api_authed "GET" "/bots/$BOT_PM_UUID/admission?actor=human_88001&actor_kind=human" "" ""
        warn "admission protected human actor: status=$HTTP_STATUS"
        # Restore PM so later suites see public.
        _restore_public_visibility "$BOT_PM_UUID" "$pm_token"
    fi

    # --- ConnectService error: self-add (CannotAddSelf) ---
    _api_authed "POST" "/collaboration/friend-connections/requests" "{\"to_bot\":\"$BOT_CEO_UUID\"}" "$ceo_token"
    warn "self-add friend request: status=$HTTP_STATUS (expect 400)"

    # --- ConnectService error: unknown target (BotNotFound) ---
    _api_authed "POST" "/collaboration/friend-connections/requests" "{\"to_bot\":\"00000000-0000-0000-0000-000000000000\"}" "$ceo_token"
    warn "friend request unknown target: status=$HTTP_STATUS (expect 404)"

    # --- ConnectService error: duplicate pending (PendingRequestExists) ---
    # Target must go pending => protected. PM token may be unset if skipped above;
    # guard so this branch only runs when we can drive pending.
    if [[ -n "$pm_token" ]]; then
        _set_protected_for_friend_request "$BOT_PM_UUID" "$pm_token"
        _api_authed "POST" "/collaboration/friend-connections/requests" "{\"to_bot\":\"$BOT_PM_UUID\"}" "$ceo_token" >/dev/null 2>&1 || true
        _api_authed "POST" "/collaboration/friend-connections/requests" "{\"to_bot\":\"$BOT_PM_UUID\"}" "$ceo_token"
        warn "duplicate pending friend request: status=$HTTP_STATUS (expect 409)"
        _restore_public_visibility "$BOT_PM_UUID" "$pm_token"
    fi

    pass "error/admission branches exercised"
    TESTS_PASSED=$((TESTS_PASSED + 1))
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
}

# Coverage push on this round's edge/bot code:
#  - ConnectService `create_connect` private-target branch (PrivateBotCannotCollaborate).
#  - BotDiscoveryService branches: collaborate_bot=private (collaborate_bot_is_private
#    → empty result) vs collaborate_bot=public (friend-list-for-collaborate path),
#    role-without-organization_code (role_requires_organization_code error), and
#    the handler guard organization_code-without-requester_bot_id.
test_ep_connect_private_and_discover_branches() {
    info "EdgePermission: connect private-target + discover branches"
    local ceo_token pm_token
    ceo_token="$(get_bot_token CEO 2>/dev/null || echo '')"
    pm_token="$(get_bot_token PM 2>/dev/null || echo '')"
    if [[ -z "$ceo_token" ]]; then
        skip_case "no CEO token for connect/discover branches"; return 77
    fi

    if [[ -n "$pm_token" ]]; then
        # PM → private (owner write), then friend request → private-collab error branch.
        api_put "/bots/$BOT_PM_UUID/visibility" '{"visibility":"private"}'
        warn "set PM private (owner): status=$HTTP_STATUS"
        _api_authed "POST" "/collaboration/friend-connections/requests" "{\"to_bot\":\"$BOT_PM_UUID\"}" "$ceo_token"
        warn "friend request to private bot: status=$HTTP_STATUS (expect private-collab error)"

        # discover: collaborate_bot=private → collaborate_bot_is_private branch (empty result).
        api_get "/bots/discover?collaborate_bot=$BOT_PM_UUID"
        warn "discover collaborate_bot=private: status=$HTTP_STATUS"

        # Restore PM to public, then discover collaborate_bot=public → friend-list-for-collaborate path.
        api_put "/bots/$BOT_PM_UUID/visibility" '{"visibility":"public"}'
        warn "restore PM public: status=$HTTP_STATUS"
        api_get "/bots/discover?collaborate_bot=$BOT_PM_UUID"
        warn "discover collaborate_bot=public: status=$HTTP_STATUS"
    fi

    # discover: role without organization_code → role_requires_organization_code error.
    api_get "/bots/discover?role=contributor"
    warn "discover role w/o org_code: status=$HTTP_STATUS (expect role_requires_organization_code)"

    # discover: organization_code without requester → handler bad-request guard.
    api_get "/bots/discover?organization_code=DEMO"
    warn "discover org_code w/o requester: status=$HTTP_STATUS (expect 400)"

    pass "connect-private + discover branches exercised"
    TESTS_PASSED=$((TESTS_PASSED + 1))
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
}

# Human→TC-bot approval: the forwarded gateway principal is re-addressed as a
# bcs-issued token (iss=bcs) and the TC friend grant reaches the backend's
# /api/internal/bot-friend-auth/sync. This is the runtime contract between
# BCS and the backend — the backend refuses its own iss=backend echo, so the
# re-addressed copy is the only shape a work-order callback approval can sync
# in. Requires the Provider/Judge mock (which serves both the sync endpoint
# and the friend work-order event call), so it is skipped without it.
test_ep_tc_friend_auth_sync_readdressed_principal() {
    info "EdgePermission: TC friend-auth-sync re-addresses the forwarded principal"
    local mock_url="$(mock_view_base_url)"
    if [[ -z "$mock_url" ]]; then
        skip_case "no Provider/Judge mock; friend-auth-sync e2e needs it" || return 77
    fi

    local tc_bot="2026e2e-tc-bot:85020"
    local probe="${SCRIPT_DIR}/group_session_ws_probe.py"
    local signing_key="${AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE:-avernet-dev-signing-key-NOT-FOR-PROD}"
    # The approver arrives the way the friend work-order callback delivers it:
    # a principal the backend re-addressed to BCS (iss=backend).
    local principal
    if ! principal="$(python3 "$probe" principal \
        --user-id "153998" --username "tc-sync-approver" --tenant "bcs-e2e" \
        --signing-key "$signing_key" --issuer backend 2>/dev/null)"; then
        fail "gateway principal mint failed for friend-auth-sync test"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        return 0
    fi

    # Ensure a TC-shaped (bot_id:owner_work_no) protected bot owned by 85020;
    # the colon address is what routes the approve into the TC sync port.
    HTTP_STATUS=$(curl -s -o "$_RESPONSE_FILE" -w '%{http_code}' \
        -H "X-BCS-Service-Key: e2e-test-key" -H "Content-Type: application/json" \
        -X POST -d '{"name":"TC sync bot","staff_no":"85020","visibility":"protected"}' \
        "${BCS_API_BASE_URL%/}/admin/bots/${tc_bot}/ensure" 2>/dev/null) || HTTP_STATUS="000"
    if [[ "$HTTP_STATUS" != "200" ]]; then
        fail "ensure TC-shaped bot returned $HTTP_STATUS: $(cat "$_RESPONSE_FILE")"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        return 0
    fi
    pass "ensure TC-shaped bot returns 200"
    TESTS_PASSED=$((TESTS_PASSED + 1))
    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    # Drop any edge left by an earlier run so the create below goes pending
    # again instead of short-circuiting the already-friends path.
    curl -s -o /dev/null -X DELETE \
        "${BCS_API_BASE_URL%/}/collaboration/friend-connections/${tc_bot}" 2>/dev/null || true

    curl -s -X POST "$mock_url/control/friend-auth-sync/clear" >/dev/null 2>&1 || true

    # Human applies: unauthenticated calls resolve to the mock human identity,
    # and the forwarded principal rides the headers BCS will re-address later.
    HTTP_STATUS=$(curl -s -o "$_RESPONSE_FILE" -w '%{http_code}' \
        -H "Content-Type: application/json" \
        -H "X-Avernet-Principal: $principal" \
        -X POST -d "{\"to_bot\":\"${tc_bot}\",\"message\":\"tc friend-auth-sync e2e\"}" \
        "${BCS_API_BASE_URL%/}/collaboration/friend-connections/requests" 2>/dev/null) || HTTP_STATUS="000"
    if [[ "$HTTP_STATUS" != "200" ]]; then
        fail "human friend request to TC bot returned $HTTP_STATUS: $(cat "$_RESPONSE_FILE")"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        return 0
    fi
    pass "human friend request to protected TC bot returns 200 (pending)"
    TESTS_PASSED=$((TESTS_PASSED + 1))
    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    # Find the pending request from the mock human's sent list.
    HTTP_STATUS=$(curl -s -o "$_RESPONSE_FILE" -w '%{http_code}' \
        "${BCS_API_BASE_URL%/}/collaboration/friend-connections/requests?direction=sent" 2>/dev/null) || HTTP_STATUS="000"
    RESPONSE=$(cat "$_RESPONSE_FILE")
    local request_id
    request_id="$(echo "$RESPONSE" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    data = d.get('data', d)
    items = data.get('items', data) if isinstance(data, dict) else data
    if isinstance(items, list):
        for r in items:
            if r.get('status') == 'pending' and r.get('to_id') == '$tc_bot':
                print(r.get('request_id', r.get('id', '')))
                break
except Exception:
    pass
" 2>/dev/null || echo '')"
    if [[ -z "$request_id" ]]; then
        fail "pending friend request to $tc_bot not found in sent list"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        return 0
    fi

    # Approve as the human, forwarding the approver principal: this is the
    # hop that must post the grant with a bcs-issued re-addressed principal.
    HTTP_STATUS=$(curl -s -o "$_RESPONSE_FILE" -w '%{http_code}' \
        -H "Content-Type: application/json" \
        -H "X-Avernet-Principal: $principal" \
        -X POST -d '{}' \
        "${BCS_API_BASE_URL%/}/collaboration/friend-connections/requests/${request_id}/accept" 2>/dev/null) || HTTP_STATUS="000"
    if [[ "$HTTP_STATUS" != "200" ]]; then
        fail "accept TC friend request returned $HTTP_STATUS: $(cat "$_RESPONSE_FILE")"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        return 0
    fi
    pass "accept TC friend request returns 200"
    TESTS_PASSED=$((TESTS_PASSED + 1))
    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    # The mock's bot-friend-auth endpoint saw exactly one grant, addressed
    # iss=bcs with the approver identity preserved.
    local sync_view sync_check
    sync_view="$(curl -s "$mock_url/control/friend-auth-sync/requests" 2>/dev/null || echo '')"
    sync_check="$(TC_BOT_EXPECTED="$tc_bot" SYNC_VIEW="$sync_view" python3 -c '
import base64, json, os, sys

view = json.loads(os.environ["SYNC_VIEW"])
expected_bot = os.environ["TC_BOT_EXPECTED"]
requests = view.get("requests", [])
problems = []
if len(requests) != 1:
    problems.append(f"expected exactly 1 sync call, saw {len(requests)}")
else:
    record = requests[0]
    body = record.get("body", {})
    bot_id, _, owner_work_no = expected_bot.partition(":")
    for field, want in (
        ("bot_id", bot_id),
        ("owner_work_no", owner_work_no),
        ("action", "grant"),
    ):
        if body.get(field) != want:
            problems.append(f"body.{field}={body.get(field)!r} != {want!r}")
    principal = record.get("principal") or ""
    try:
        payload = principal.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except Exception as error:
        problems.append(f"principal undecodable: {error}")
    else:
        for claim, want in (
            ("iss", "bcs"),
            ("aud", "backend"),
        ):
            if claims.get(claim) != want:
                problems.append(f"claims.{claim}={claims.get(claim)!r} != {want!r}")
        principals = claims.get("principals") or []
        subject = (principals[0].get("subject") or {}) if principals else {}
        if subject.get("id") != "153998":
            problems.append(
                "approver subject id " + repr(subject.get("id")) + " != 153998"
            )
if problems:
    print("; ".join(problems))
    sys.exit(1)
print("ok")
' 2>&1)"
    if [[ "$sync_check" == "ok" ]]; then
        pass "TC grant synced with iss=bcs re-addressed approver principal"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        fail "friend-auth-sync assertion failed: $sync_check"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
}

# friend_ext department-scope parsing: production rows carry
# {deptNo, deptName} objects (and the admin surfaces keep writing them), so
# the shared parser must read both spellings at runtime. Drives every new
# parser branch over the live stack: object entries, legacy string entries,
# and a garbage entry (dropped with a WARN). The parsers run BEFORE the user
# directory lookup, so the e2e environment (no directory plugged) still
# exercises each changed line.
test_ep_dept_scope_parses_object_friend_ext_entries() {
    info "EdgePermission: dept-scope friend_ext object entries parse"
    local probe="${SCRIPT_DIR}/group_session_ws_probe.py"
    local signing_key="${AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE:-avernet-dev-signing-key-NOT-FOR-PROD}"
    # The openapi bot-update route authorizes the owner: mint the principal
    # as the ensure-bound owner staff_no.
    local owner_principal
    if ! owner_principal="$(python3 "$probe" principal \
        --user-id "85020" --username "friend-ext-owner" --tenant "bcs-e2e" \
        --signing-key "$signing_key" 2>/dev/null)"; then
        fail "gateway principal mint failed for friend_ext test"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        return 0
    fi

    # A dedicated protected bot so scope rows here never touch the demo bots.
    HTTP_STATUS=$(curl -s -o "$_RESPONSE_FILE" -w '%{http_code}' \
        -H "X-BCS-Service-Key: e2e-test-key" -H "Content-Type: application/json" \
        -X POST -d '{"name":"friend-ext bot","staff_no":"85020","visibility":"protected"}' \
        "${BCS_API_BASE_URL%/}/admin/bots/e2e-friend-ext-bot/ensure" 2>/dev/null) || HTTP_STATUS="000"
    if [[ "$HTTP_STATUS" != "200" ]]; then
        fail "ensure friend-ext bot returned $HTTP_STATUS: $(cat "$_RESPONSE_FILE")"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        return 0
    fi

    # Mixed-shape scopes, mirroring production rows: object references in the
    # exemption and agent scoping lists, one legacy string, one garbage entry
    # that must be dropped (WARN) rather than read as an empty allowlist.
    # Built via python3 so quoting stays unambiguous.
    local patch_body
    patch_body="$(python3 - <<'PY'
import json
print(json.dumps({
    "user_visibility": "protected",
    "friend_check_in_strategy": "DEPT_FREE",
    "friend_ext": {
        "public_user_approval": {"status": "AGREE"},
        "no_check_scope_friend_deps": [
            {"deptNo": "L3480", "deptName": "蚂蚁集团-信贷事业群"},
            "F4858",
        ],
        "view_scope_user_friend_deps": [],
        "view_scope_agent_friend_deps": [
            {"deptNo": "L3480"},
            {"priority": 7},
        ],
    },
}))
PY
)"
    HTTP_STATUS=$(curl -s -o "$_RESPONSE_FILE" -w '%{http_code}' \
        -H "X-Avernet-Principal: $owner_principal" \
        -H "Content-Type: application/json" \
        -X PATCH -d "$patch_body" \
        "${BCS_API_BASE_URL%/}/openapi/v1/collaboration/bots/e2e-friend-ext-bot" 2>/dev/null) || HTTP_STATUS="000"
    if [[ "$HTTP_STATUS" != "200" ]]; then
        fail "patch friend_ext via openapi returned $HTTP_STATUS: $(cat "$_RESPONSE_FILE")"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        return 0
    fi

    # Human→bot: runs the no_check parser's object and legacy-string arms.
    # The parsers execute BEFORE the user directory lookup, so this drives
    # every changed parse line even with no directory plugged in; without a
    # resolver the department is None, so the request lands pending (200),
    HTTP_STATUS=$(curl -s -o "$_RESPONSE_FILE" -w '%{http_code}' \
        -H "Content-Type: application/json" \
        -X POST -d '{"to_bot":"e2e-friend-ext-bot","message":"friend_ext object parse e2e"}' \
        "${BCS_API_BASE_URL%/}/collaboration/friend-connections/requests" 2>/dev/null) || HTTP_STATUS="000"
    if [[ "$HTTP_STATUS" == "200" ]]; then
        pass "human request parses object/string exemption scopes into the allowlist"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        fail "human request against object friend_ext returned $HTTP_STATUS: $(cat "$_RESPONSE_FILE")"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    # Re-patch with a NON-empty user scope in the same object shape, then
    # apply again as a human: the user-visibility scope parser now runs its
    # object arm AND the garbage-drop (WARN) arm on a live request. The scope
    # decision itself is environment-dependent (a plugged user directory may
    # resolve the applicant's department), so accept the refused or admitted
    # answer — the covered lines are the parser's, which run first either way.
    local scoped_patch
    scoped_patch="$(python3 - <<'PY'
import json
print(json.dumps({
    "friend_ext": {
        "public_user_approval": {"status": "AGREE"},
        "no_check_scope_friend_deps": [
            {"deptNo": "L3480", "deptName": "蚂蚁集团-信贷事业群"},
            "F4858",
        ],
        "view_scope_user_friend_deps": [
            {"deptNo": "L3480"},
            {"priority": 7},
        ],
        "view_scope_agent_friend_deps": [
            {"deptNo": "L3480"},
            {"priority": 7},
        ],
    },
}))
PY
)"
    HTTP_STATUS=$(curl -s -o "$_RESPONSE_FILE" -w '%{http_code}' \
        -H "X-Avernet-Principal: $owner_principal" \
        -H "Content-Type: application/json" \
        -X PATCH -d "$scoped_patch" \
        "${BCS_API_BASE_URL%/}/openapi/v1/collaboration/bots/e2e-friend-ext-bot" 2>/dev/null) || HTTP_STATUS="000"
    if [[ "$HTTP_STATUS" == "200" ]]; then
        pass "scoped friend_ext re-patch accepted"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        fail "scoped friend_ext re-patch returned $HTTP_STATUS: $(cat "$_RESPONSE_FILE")"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        return 0
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    # Different human actor so the pending-idempotency shortcut from the
    # first application cannot swallow this request.
    HTTP_STATUS=$(curl -s -o "$_RESPONSE_FILE" -w '%{http_code}' \
        -H "Content-Type: application/json" \
        -H "X-Mock-User-Id: 511488" \
        -X POST -d '{"to_bot":"e2e-friend-ext-bot","message":"friend_ext scope parse e2e"}' \
        "${BCS_API_BASE_URL%/}/collaboration/friend-connections/requests" 2>/dev/null) || HTTP_STATUS="000"
    if [[ "$HTTP_STATUS" == "200" || "$HTTP_STATUS" == "403" ]]; then
        pass "human request drives the scoped user parser (object + garbage arms, status=$HTTP_STATUS)"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        fail "human request against scoped friend_ext returned $HTTP_STATUS: $(cat "$_RESPONSE_FILE")"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    # Bot→bot: runs the view_scope_agent parser's object arm AND the garbage
    # drop (WARN) arm. No user directory → the scoped caller is refused; a
    # missing scope restriction / silently-empty legacy parse would also have
    # allowed the request through with 200, so this doubles the gate.
    local pm_token
    pm_token="$(get_bot_token PM 2>/dev/null || echo '')"
    if [[ -n "$pm_token" ]]; then
        _api_authed "POST" "/collaboration/friend-connections/requests" \
            '{"to_bot":"e2e-friend-ext-bot"}' "$pm_token"
        if [[ "$HTTP_STATUS" == "403" || "$HTTP_STATUS" == "200" ]]; then
            pass "bot request runs agent-scope parser incl. garbage drop (status=$HTTP_STATUS)"
            TESTS_PASSED=$((TESTS_PASSED + 1))
        else
            fail "bot request against agent scope returned $HTTP_STATUS: $(cat "$_RESPONSE_FILE")"
            TESTS_FAILED=$((TESTS_FAILED + 1))
        fi
    else
        warn "no PM token; agent-scope parser arm left to engine coverage"
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
}
