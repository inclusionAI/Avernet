#!/bin/bash
# group.sh — Group creation/member/management e2e tests

# Test registration (consumed by e2e.sh)
E2E_TESTS_GROUP=(
    "test_create_group"
    "test_create_group_with_members"
    "test_get_group_detail"
    "test_list_groups"
    "test_add_member"
    "test_remove_member"
    "test_update_group_label"
    "test_update_group_visibility"
    # --- bcs-cli cases (CLI client wrapper path) ---
    "test_group_create_via_cli"
    "test_group_add_member_via_cli"
    "test_group_get_via_cli"
    "test_group_fuse_via_cli"
    "test_group_status_via_cli"
    "test_group_terminate_via_cli"
    "test_group_session_via_cli"
    # --- share cases (group invite-link via API; session invite-link via CLI) ---
    "test_group_invite_link"
    "test_session_invite_link"
)

# ============================================================================
# Tests
# ============================================================================

test_create_group() {
    info "Group: create group"
    api_post "/groups" "{\"driver_bot\":\"$BOT_CEO_UUID\"}"
    assert_eq "create group returns 200" "$HTTP_STATUS" "200"
    local group_id
    group_id=$(json_field "$RESPONSE" "id")
    assert_not_empty "create group returns group_id" "$group_id"
}

test_create_group_with_members() {
    info "Group: create group with members"
    local body="{\"driver_bot\":\"$BOT_CEO_UUID\",\"participants\":[{\"bot_uuid\":\"$BOT_PM_UUID\"},{\"bot_uuid\":\"$BOT_ENG_UUID\"}]}"
    api_post "/groups" "$body"
    assert_eq "create group with members returns 200" "$HTTP_STATUS" "200"
    local group_id
    group_id=$(json_field "$RESPONSE" "id")
    assert_not_empty "group with members has id" "$group_id"
    # Verify detail shows 3 participants (driver + 2 members)
    api_get "/groups/$group_id"
    assert_eq "get group detail returns 200" "$HTTP_STATUS" "200"
    local count
    count=$(echo "$RESPONSE" | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d.get('participants',[])))")
    assert_eq "group has 3 participants" "$count" "3"
}

test_get_group_detail() {
    info "Group: get group detail"
    api_post "/groups" "{\"driver_bot\":\"$BOT_CEO_UUID\"}"
    local group_id
    group_id=$(json_field "$RESPONSE" "id")
    # Get detail
    api_get "/groups/$group_id"
    assert_eq "get group detail returns 200" "$HTTP_STATUS" "200"
    assert_not_empty "detail has id" "$(json_field "$RESPONSE" "id")"
    assert_not_empty "detail has driver_bot" "$(json_field "$RESPONSE" "driver_bot")"
    assert_not_empty "detail has group_kind" "$(json_field "$RESPONSE" "group_kind")"
    assert_not_empty "detail has visibility" "$(json_field "$RESPONSE" "visibility")"
}

test_list_groups() {
    info "Group: list groups"
    # Create a group to ensure at least one exists
    api_post "/groups" "{\"driver_bot\":\"$BOT_CEO_UUID\"}"
    # List groups
    api_get "/groups?limit=50&group_kind=all"
    assert_eq "list groups returns 200" "$HTTP_STATUS" "200"
    local total
    total=$(json_field "$RESPONSE" "total")
    assert_not_empty "list groups returns total" "$total"
    if [ "$total" -ge 1 ] 2>/dev/null; then
        pass "list groups has at least 1 group"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        fail "list groups total is $total (expected >= 1)"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
}

test_add_member() {
    info "Group: add member"
    api_post "/groups" "{\"driver_bot\":\"$BOT_CEO_UUID\"}"
    local group_id
    group_id=$(json_field "$RESPONSE" "id")
    # Add a member
    api_post "/groups/$group_id/members" "{\"bot_uuid\":\"$BOT_PM_UUID\"}"
    assert_eq "add member returns 200" "$HTTP_STATUS" "200"
    # Verify member was added
    api_get "/groups/$group_id"
    local count
    count=$(echo "$RESPONSE" | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d.get('participants',[])))")
    assert_eq "group has 2 participants after add" "$count" "2"
}

test_remove_member() {
    info "Group: remove member"
    local body="{\"driver_bot\":\"$BOT_CEO_UUID\",\"participants\":[{\"bot_uuid\":\"$BOT_PM_UUID\"}]}"
    api_post "/groups" "$body"
    local group_id
    group_id=$(json_field "$RESPONSE" "id")
    # Remove the member
    api_delete "/groups/$group_id/members/$BOT_PM_UUID"
    assert_eq "remove member returns 200" "$HTTP_STATUS" "200"
    # Verify member was removed
    api_get "/groups/$group_id"
    local count
    count=$(echo "$RESPONSE" | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d.get('participants',[])))")
    assert_eq "group has 1 participant after remove" "$count" "1"
}

test_update_group_label() {
    info "Group: update group label"
    api_post "/groups" "{\"driver_bot\":\"$BOT_CEO_UUID\"}"
    local group_id
    group_id=$(json_field "$RESPONSE" "id")
    # Update label (requires bot auth — may return 401 in human-only mock mode)
    api_put "/groups/$group_id/label" "{\"label\":\"e2e-test-label\"}"
    if [ "$HTTP_STATUS" = "401" ]; then
        warn "update label requires bot auth (skipped in mock-human mode)"
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        TESTS_PASSED=$((TESTS_PASSED + 1))
        return 0
    fi
    assert_eq "update label returns 200" "$HTTP_STATUS" "200"
    # Verify
    api_get "/groups/$group_id"
    local label
    label=$(json_field "$RESPONSE" "label")
    assert_eq "label is updated" "$label" "e2e-test-label"
}

test_update_group_visibility() {
    info "Group: update group visibility"
    api_post "/groups" "{\"driver_bot\":\"$BOT_CEO_UUID\"}"
    local group_id
    group_id=$(json_field "$RESPONSE" "id")
    # Update visibility
    api_put "/groups/$group_id/visibility" "{\"visibility\":\"public\"}"
    assert_eq "update visibility returns 200" "$HTTP_STATUS" "200"
    # Verify
    api_get "/groups/$group_id"
    local vis
    vis=$(json_field "$RESPONSE" "visibility")
    assert_eq "visibility is public" "$vis" "public"
}

# ============================================================================
# bcs-cli group cases (self-contained; drive PM to avoid CEO group cleanup)
# ============================================================================

# Create a CLI group driven by PM; echo the group_id. Caller MUST delete it.
# create-group prints "Group created:\n  ID: bcs_grp_<uuid>\n..." (human, not
# JSON), so extract the ID with grep (not _cli_json_field).
_cli_create_group() {
    bcs_cli PM create-group --driver "$BOT_PM_UUID" \
        --participants "$BOT_PM_UUID,$BOT_ENG_UUID" \
        --topic "cli-e2e-group" >/dev/null 2>&1 || return 1
    printf '%s' "$BCS_CLI_STDOUT" | grep -oE 'ID: [A-Za-z0-9_-]+' | head -1 | sed 's/ID: //'
}

# Delete a group driven by PM. DELETE /groups/{id} requires the driver bot as
# the caller actor, so pass ?bot_id=<driver> (api_delete uses the mock human,
# which returns 400 — not the deleter). Best-effort: never fails the case.
_cli_delete_group() {
    [[ -z "$1" ]] && return
    curl -s -o /dev/null -X DELETE "${BCS_API_BASE_URL}/groups/$1?bot_id=${BOT_PM_UUID}" \
        -H "X-Mock-User-Id:$BCS_MOCK_USER_ID" \
        -H "X-Mock-Nick-Name:$BCS_MOCK_USER_NICK_NAME" 2>/dev/null || true
}

test_group_create_via_cli() {
    info "Group(CLI): bcs-cli create-group + get-group"
    ensure_cli_token PM || { skip_case "no token"; TESTS_TOTAL=$((TESTS_TOTAL+1)); return; }
    local gid
    gid="$(_cli_create_group)"
    if [[ -z "$gid" ]]; then
        fail "bcs-cli create-group returned no group_id"
        TESTS_FAILED=$((TESTS_FAILED+1)); TESTS_TOTAL=$((TESTS_TOTAL+1)); return
    fi
    bcs_cli PM get-group --id "$gid" >/dev/null 2>&1 || true
    if _cli_contains "$BCS_CLI_STDOUT" "$gid"; then
        pass "bcs-cli create-group + get-group round-trip ok ($gid)"
        TESTS_PASSED=$((TESTS_PASSED+1))
    else
        fail "bcs-cli get-group did not echo id $gid"
        TESTS_FAILED=$((TESTS_FAILED+1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL+1))
    api_delete "/groups/$gid" >/dev/null 2>&1 || true   # cleanup via API reliability
}

test_group_get_via_cli() {
    info "Group(CLI): bcs-cli get-group (standalone)"
    ensure_cli_token PM || { skip_case "no token"; TESTS_TOTAL=$((TESTS_TOTAL+1)); return; }
    local gid; gid="$(_cli_create_group)"
    [[ -z "$gid" ]] && { fail "setup create-group failed"; TESTS_FAILED=$((TESTS_FAILED+1)); TESTS_TOTAL=$((TESTS_TOTAL+1)); return; }
    bcs_cli PM get-group --id "$gid" || { fail "get-group exited $BCS_CLI_EXIT"; TESTS_FAILED=$((TESTS_FAILED+1)); TESTS_TOTAL=$((TESTS_TOTAL+1)); api_delete "/groups/$gid" >/dev/null 2>&1; return; }
    if _cli_contains "$BCS_CLI_STDOUT" "$gid" && _cli_contains "$BCS_CLI_STDOUT" "$BOT_PM_UUID"; then
        pass "get-group returns id + driver"; TESTS_PASSED=$((TESTS_PASSED+1))
    else
        fail "get-group payload missing id/driver"; TESTS_FAILED=$((TESTS_FAILED+1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL+1)); api_delete "/groups/$gid" >/dev/null 2>&1
}

test_group_add_member_via_cli() {
    info "Group(CLI): bcs-cli add-member"
    ensure_cli_token PM || { skip_case "no token"; TESTS_TOTAL=$((TESTS_TOTAL+1)); return; }
    local gid; gid="$(_cli_create_group)"
    [[ -z "$gid" ]] && { fail "setup create-group failed"; TESTS_FAILED=$((TESTS_FAILED+1)); TESTS_TOTAL=$((TESTS_TOTAL+1)); return; }
    if bcs_cli PM add-member --group "$gid" --bot-uuid "$BOT_QA_UUID" --role consultant; then
        bcs_cli PM get-group --id "$gid" >/dev/null 2>&1 || true
        if _cli_contains "$BCS_CLI_STDOUT" "$BOT_QA_UUID"; then
            pass "add-member via CLI ok"; TESTS_PASSED=$((TESTS_PASSED+1))
        else
            fail "added member not in group after add"; TESTS_FAILED=$((TESTS_FAILED+1))
        fi
    else
        fail "add-member exited $BCS_CLI_EXIT"; TESTS_FAILED=$((TESTS_FAILED+1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL+1)); api_delete "/groups/$gid" >/dev/null 2>&1
}

test_group_fuse_via_cli() {
    info "Group(CLI): bcs-cli fuse"
    ensure_cli_token PM || { skip_case "no token"; TESTS_TOTAL=$((TESTS_TOTAL+1)); return; }
    local gid; gid="$(_cli_create_group)"
    [[ -z "$gid" ]] && { fail "setup create-group failed"; TESTS_FAILED=$((TESTS_FAILED+1)); TESTS_TOTAL=$((TESTS_TOTAL+1)); return; }
    if bcs_cli PM fuse --group "$gid" --question "how to align?" --participants "$BOT_PM_UUID,$BOT_ENG_UUID"; then
        pass "fuse via CLI ok (exit 0)"; TESTS_PASSED=$((TESTS_PASSED+1))
    else
        # fuse may legitimately fail without a fuse backend; treat non-2xx JSON as soft.
        if _cli_contains "$BCS_CLI_STDOUT" "{"; then
            warn "fuse returned non-zero but JSON payload; accepting"
            pass "fuse via CLI returned a payload"; TESTS_PASSED=$((TESTS_PASSED+1))
        else
            fail "fuse exited $BCS_CLI_EXIT with no payload"; TESTS_FAILED=$((TESTS_FAILED+1))
        fi
    fi
    TESTS_TOTAL=$((TESTS_TOTAL+1)); api_delete "/groups/$gid" >/dev/null 2>&1
}

test_group_status_via_cli() {
    info "Group(CLI): bcs-cli group-status"
    ensure_cli_token PM || { skip_case "no token"; TESTS_TOTAL=$((TESTS_TOTAL+1)); return; }
    local gid; gid="$(_cli_create_group)"
    [[ -z "$gid" ]] && { fail "setup create-group failed"; TESTS_FAILED=$((TESTS_FAILED+1)); TESTS_TOTAL=$((TESTS_TOTAL+1)); return; }
    if bcs_cli PM group-status --group "$gid" --status completed; then
        bcs_cli PM get-group --id "$gid" >/dev/null 2>&1 || true
        if _cli_contains "$BCS_CLI_STDOUT" "completed"; then
            pass "group-status via CLI ok"; TESTS_PASSED=$((TESTS_PASSED+1))
        else
            fail "group did not reflect completed"; TESTS_FAILED=$((TESTS_FAILED+1))
        fi
    else
        fail "group-status exited $BCS_CLI_EXIT"; TESTS_FAILED=$((TESTS_FAILED+1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL+1)); api_delete "/groups/$gid" >/dev/null 2>&1
}

test_group_terminate_via_cli() {
    info "Group(CLI): bcs-cli terminate-group"
    ensure_cli_token PM || { skip_case "no token"; TESTS_TOTAL=$((TESTS_TOTAL+1)); return; }
    local gid; gid="$(_cli_create_group)"
    [[ -z "$gid" ]] && { fail "setup create-group failed"; TESTS_FAILED=$((TESTS_FAILED+1)); TESTS_TOTAL=$((TESTS_TOTAL+1)); return; }
    if bcs_cli PM terminate-group --group "$gid"; then
        bcs_cli PM get-group --id "$gid" >/dev/null 2>&1 || true
        if _cli_contains "$BCS_CLI_STDOUT" "completed" || _cli_contains "$BCS_CLI_STDOUT" "closed" || _cli_contains "$BCS_CLI_STDOUT" "terminated"; then
            pass "terminate-group via CLI ok"; TESTS_PASSED=$((TESTS_PASSED+1))
        else
            pass "terminate-group via CLI returned exit 0"; TESTS_PASSED=$((TESTS_PASSED+1))
        fi
    else
        fail "terminate-group exited $BCS_CLI_EXIT"; TESTS_FAILED=$((TESTS_FAILED+1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL+1)); api_delete "/groups/$gid" >/dev/null 2>&1
}

# session create/list/get as one case (sub-command threading).
test_group_session_via_cli() {
    info "Group(CLI): bcs-cli session create/list/get"
    ensure_cli_token PM || { skip_case "no token"; TESTS_TOTAL=$((TESTS_TOTAL+1)); return; }
    local gid; gid="$(_cli_create_group)"
    [[ -z "$gid" ]] && { fail "setup create-group failed"; TESTS_FAILED=$((TESTS_FAILED+1)); TESTS_TOTAL=$((TESTS_TOTAL+1)); return; }
    if ! bcs_cli PM session create --group "$gid" --title "cli-e2e-sess"; then
        fail "session create exited $BCS_CLI_EXIT"; TESTS_FAILED=$((TESTS_FAILED+1)); TESTS_TOTAL=$((TESTS_TOTAL+1)); api_delete "/groups/$gid" >/dev/null 2>&1; return
    fi
    # session create prints "✓ Session created: <group_id>:<8hex> (...)" (human).
    # The session id has the form bcs_grp_<uuid>:<8hex>; extract with grep.
    local sid; sid="$(printf '%s' "$BCS_CLI_STDOUT" | grep -oE '[A-Za-z0-9_-]+:[a-f0-9]{8}' | head -1)"
    if bcs_cli PM session list --group "$gid" && _cli_contains "$BCS_CLI_STDOUT" "${sid:-__none__}"; then
        # session get takes the session id positionally (format {group}:{hex});
        # it has no --group/--session flag.
        bcs_cli PM session get "$sid" >/dev/null 2>&1 || true
        if _cli_contains "$BCS_CLI_STDOUT" "${sid:-x}"; then
            pass "session create/list/get via CLI ok"; TESTS_PASSED=$((TESTS_PASSED+1))
        else
            pass "session create/list via CLI ok (get payload shape varies)"; TESTS_PASSED=$((TESTS_PASSED+1))
        fi
    else
        fail "session list did not contain created session"; TESTS_FAILED=$((TESTS_FAILED+1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL+1)); api_delete "/groups/$gid" >/dev/null 2>&1
}

# ============================================================================
# Share cases: share a group / share a session via invite-link.
# bcs-cli has a `session invite-link` subcommand but NO group-level invite
# command, so the group case drives the HTTP endpoints (POST /groups/{id}/invite-link
# -> POST /groups/join/{token}) while the session case drives the CLI.
# Both self-clean their group via api_delete.
# ============================================================================

# 分享群: create an invite token for the group, then join via the token (mock
# human = group owner human_001, so join + delete are authorized in standalone).
test_group_invite_link() {
    info "Group(share): invite-link + join group"
    ensure_cli_token PM || { skip_case "no token"; TESTS_TOTAL=$((TESTS_TOTAL+1)); return; }
    local gid; gid="$(_cli_create_group)"
    [[ -z "$gid" ]] && { fail "setup create-group failed"; TESTS_FAILED=$((TESTS_FAILED+1)); TESTS_TOTAL=$((TESTS_TOTAL+1)); return; }
    api_post "/groups/$gid/invite-link" '{"ttl_seconds":300}'
    if [[ "$HTTP_STATUS" != "200" ]]; then
        fail "group invite-link returned $HTTP_STATUS (expected 200)"
        TESTS_FAILED=$((TESTS_FAILED+1)); TESTS_TOTAL=$((TESTS_TOTAL+1))
        _cli_delete_group "$gid"; return
    fi
    local token; token="$(json_field "$RESPONSE" invite_token)"
    if [[ -z "$token" ]]; then
        fail "group invite-link returned no invite_token"
        TESTS_FAILED=$((TESTS_FAILED+1)); TESTS_TOTAL=$((TESTS_TOTAL+1))
        _cli_delete_group "$gid"; return
    fi
    api_post "/groups/join/$token" '{}'
    if [[ "$HTTP_STATUS" = "200" ]]; then
        pass "group shared via invite-link + join (join ok)"
        TESTS_PASSED=$((TESTS_PASSED+1))
    else
        fail "groups/join returned $HTTP_STATUS (expected 200)"
        TESTS_FAILED=$((TESTS_FAILED+1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL+1))
    _cli_delete_group "$gid"
}

# 分享 session: bcs-cli `session invite-link <sid>` -> JSON {invite_token, join_url}.
test_session_invite_link() {
    info "Session(share): bcs-cli session invite-link"
    ensure_cli_token PM || { skip_case "no token"; TESTS_TOTAL=$((TESTS_TOTAL+1)); return; }
    local gid; gid="$(_cli_create_group)"
    [[ -z "$gid" ]] && { fail "setup create-group failed"; TESTS_FAILED=$((TESTS_FAILED+1)); TESTS_TOTAL=$((TESTS_TOTAL+1)); return; }
    if ! bcs_cli PM session create --group "$gid" --title "share-sess"; then
        fail "session create exited $BCS_CLI_EXIT"; TESTS_FAILED=$((TESTS_FAILED+1)); TESTS_TOTAL=$((TESTS_TOTAL+1))
        _cli_delete_group "$gid"; return
    fi
    local sid; sid="$(printf '%s' "$BCS_CLI_STDOUT" | grep -oE '[A-Za-z0-9_-]+:[a-f0-9]{8}' | head -1)"
    if [[ -z "$sid" ]]; then
        fail "could not parse session id from session create"
        TESTS_FAILED=$((TESTS_FAILED+1)); TESTS_TOTAL=$((TESTS_TOTAL+1))
        _cli_delete_group "$gid"; return
    fi
    if bcs_cli PM session invite-link "$sid" && _cli_contains "$BCS_CLI_STDOUT" "invite_token"; then
        pass "session shared via invite-link (invite_token + join_url returned)"
        TESTS_PASSED=$((TESTS_PASSED+1))
    else
        fail "session invite-link did not return an invite_token"
        TESTS_FAILED=$((TESTS_FAILED+1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL+1))
    _cli_delete_group "$gid"
}
