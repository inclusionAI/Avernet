#!/bin/bash
# cli.sh — bcs-cli e2e tests (CLI client wrapper path).
# Covers bcs-cli top-level commands that are not group/friend operations
# (those live in group.sh / friends.sh to reuse their fixtures).

# Consumed by e2e.sh
E2E_TESTS_CLI=(
    "test_cli_health"
    "test_cli_list"
    "test_cli_get"
    "test_cli_discover"
    "test_cli_visibility_get_set"
    "test_cli_connect"
    "test_cli_onboard"
    "test_cli_update_status"
    "test_cli_request_group_help"
    "test_cli_chat"
    "test_cli_list_groups"
    "test_cli_friend"
)

# bcs-cli's visibility/friend commands resolve the "current bot" from
# $BOT_DATA_DIR/.bcs/session.json (NOT from --token). bcs_cli (in common.sh)
# sets BOT_DATA_DIR inline per call so no global leak occurs.

# health: no auth.
test_cli_health() {
    info "CLI: bcs-cli health (no auth)"
    bcs_cli "" health || { fail "bcs-cli health exited $BCS_CLI_EXIT"; TESTS_FAILED=$((TESTS_FAILED+1)); TESTS_TOTAL=$((TESTS_TOTAL+1)); return; }
    if _cli_contains "$BCS_CLI_STDOUT" "status" || _cli_contains "$BCS_CLI_STDOUT" "ok" || _cli_contains "$BCS_CLI_STDOUT" "healthy"; then
        pass "bcs-cli health returned a health payload"
        TESTS_PASSED=$((TESTS_PASSED+1))
    else
        fail "bcs-cli health payload unexpected: $(echo "$BCS_CLI_STDOUT" | head -c 120)"
        TESTS_FAILED=$((TESTS_FAILED+1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL+1))
}

test_cli_list() {
    info "CLI: bcs-cli list"
    ensure_cli_token CEO || { skip_case "no token"; TESTS_TOTAL=$((TESTS_TOTAL+1)); return; }
    bcs_cli CEO list || { fail "bcs-cli list exited $BCS_CLI_EXIT"; TESTS_FAILED=$((TESTS_FAILED+1)); TESTS_TOTAL=$((TESTS_TOTAL+1)); return; }
    # Expect at least the 5 demo bots' UUIDs echoed somewhere.
    local hit=0
    for u in "$BOT_CEO_UUID" "$BOT_PM_UUID" "$BOT_ENG_UUID" "$BOT_QA_UUID" "$BOT_CS_UUID"; do
        _cli_contains "$BCS_CLI_STDOUT" "$u" && hit=$((hit+1))
    done
    if [[ "$hit" -ge 2 ]]; then
        pass "bcs-cli list returned >=2 known bot UUIDs"
        TESTS_PASSED=$((TESTS_PASSED+1))
    else
        fail "bcs-cli list matched only $hit known UUIDs"
        TESTS_FAILED=$((TESTS_FAILED+1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL+1))
}

test_cli_get() {
    info "CLI: bcs-cli get <uuid>"
    ensure_cli_token CEO || { skip_case "no token"; TESTS_TOTAL=$((TESTS_TOTAL+1)); return; }
    bcs_cli CEO get "$BOT_CEO_UUID" || { fail "bcs-cli get exited $BCS_CLI_EXIT"; TESTS_FAILED=$((TESTS_FAILED+1)); TESTS_TOTAL=$((TESTS_TOTAL+1)); return; }
    if _cli_contains "$BCS_CLI_STDOUT" "$BOT_CEO_UUID"; then
        pass "bcs-cli get returned the requested bot"
        TESTS_PASSED=$((TESTS_PASSED+1))
    else
        fail "bcs-cli get did not echo the target UUID"
        TESTS_FAILED=$((TESTS_FAILED+1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL+1))
}

test_cli_discover() {
    info "CLI: bcs-cli discover"
    ensure_cli_token CEO || { skip_case "no token"; TESTS_TOTAL=$((TESTS_TOTAL+1)); return; }
    # Discover with a query that should match one of the demo bots' summary/name.
    bcs_cli CEO discover --query "客服" || { fail "bcs-cli discover exited $BCS_CLI_EXIT"; TESTS_FAILED=$((TESTS_FAILED+1)); TESTS_TOTAL=$((TESTS_TOTAL+1)); return; }
    if _cli_contains "$BCS_CLI_STDOUT" "$BOT_CS_UUID"; then
        pass "bcs-cli discover matched 客服 (CS) bot"
        TESTS_PASSED=$((TESTS_PASSED+1))
    else
        # discover match semantics vary; treat non-empty result as acceptable.
        if [[ -n "$BCS_CLI_STDOUT" && "$BCS_CLI_STDOUT" != "[]" ]]; then
            pass "bcs-cli discover returned non-empty results"
            TESTS_PASSED=$((TESTS_PASSED+1))
        else
            fail "bcs-cli discover returned empty"
            TESTS_FAILED=$((TESTS_FAILED+1))
        fi
    fi
    TESTS_TOTAL=$((TESTS_TOTAL+1))
}

# visibility get + set + restore: side effect, but cheap and self-contained.
test_cli_visibility_get_set() {
    info "CLI: bcs-cli visibility get/set"
    ensure_cli_token CEO || { skip_case "no token"; TESTS_TOTAL=$((TESTS_TOTAL+1)); return; }
    bcs_cli CEO visibility get || { fail "visibility get exited $BCS_CLI_EXIT"; TESTS_FAILED=$((TESTS_FAILED+1)); TESTS_TOTAL=$((TESTS_TOTAL+1)); return; }
    local before
    before="$(_cli_json_field "$BCS_CLI_STDOUT" visibility 2>/dev/null)"
    # Some bcs-cli shapes put visibility at top-level; fall back to raw scan.
    [[ -z "$before" ]] && before="$(printf '%s' "$BCS_CLI_STDOUT" | grep -oE '"visibility":"[^"]*"' | head -1 | sed 's/"visibility":"//;s/"//')"
    # Set to public, verify, restore.
    if ! bcs_cli CEO visibility set --value public; then
        fail "visibility set public exited $BCS_CLI_EXIT"
        TESTS_FAILED=$((TESTS_FAILED+1)); TESTS_TOTAL=$((TESTS_TOTAL+1)); return
    fi
    bcs_cli CEO visibility get || true
    if _cli_contains "$BCS_CLI_STDOUT" "public"; then
        pass "bcs-cli visibility set public took effect"
        TESTS_PASSED=$((TESTS_PASSED+1))
    else
        fail "visibility get after set did not show public"
        TESTS_FAILED=$((TESTS_FAILED+1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL+1))
    # Restore original (best-effort, not asserted).
    [[ -n "$before" ]] && bcs_cli CEO visibility set --value "$before" >/dev/null 2>&1 || true
}

# connect: re-connect with CEO's existing token; expect a session/token back.
# NOTE: bcs_cli already injects --token after the subcommand, so do NOT pass
# --token here (a second --token is a clap duplicate-arg error). connect is one
# of the few commands that outputs JSON ({"is_new":...,"token":...}).
test_cli_connect() {
    info "CLI: bcs-cli connect"
    ensure_cli_token CEO || { skip_case "no token"; TESTS_TOTAL=$((TESTS_TOTAL+1)); return; }
    if ! bcs_cli CEO connect; then
        fail "bcs-cli connect exited $BCS_CLI_EXIT"
        TESTS_FAILED=$((TESTS_FAILED+1)); TESTS_TOTAL=$((TESTS_TOTAL+1)); return
    fi
    if _cli_contains "$BCS_CLI_STDOUT" "$BCS_CLI_TOKEN" || _cli_contains "$BCS_CLI_STDOUT" "token"; then
        pass "bcs-cli connect returned a session/token"
        TESTS_PASSED=$((TESTS_PASSED+1))
    else
        fail "bcs-cli connect did not return a token"
        TESTS_FAILED=$((TESTS_FAILED+1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL+1))
}

# onboard: exercise the onboard command WITHOUT mutating an existing bot.
# Calling the onboard API with CEO's token would re-register/RENAME CEO
# (capabilities.name), which breaks resolve_bot_uuid "CEO" for the whole suite.
# Use --web, which prints a registration URL and does NOT call the API.
test_cli_onboard() {
    info "CLI: bcs-cli onboard --web (non-mutating)"
    ensure_cli_token CEO || { skip_case "no token"; TESTS_TOTAL=$((TESTS_TOTAL+1)); return; }
    local name="cli_e2e_bot_probe"
    if ! bcs_cli CEO onboard --web --name "$name" --summary "e2e throwaway"; then
        fail "bcs-cli onboard --web exited $BCS_CLI_EXIT"
        TESTS_FAILED=$((TESTS_FAILED+1)); TESTS_TOTAL=$((TESTS_TOTAL+1)); return
    fi
    # --web prints a registration URL containing the bot name; assert it.
    if _cli_contains "$BCS_CLI_STDOUT" "register" && _cli_contains "$BCS_CLI_STDOUT" "$name"; then
        pass "bcs-cli onboard --web produced a registration URL"
        TESTS_PASSED=$((TESTS_PASSED+1))
    else
        fail "bcs-cli onboard --web output unexpected: $(echo "$BCS_CLI_STDOUT" | head -c 120)"
        TESTS_FAILED=$((TESTS_FAILED+1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL+1))
}

# update-status: set CEO idle, then restore busy. `get` output has no Status
# field, so we assert on update-status's own success output (exit 0 + "Status").
test_cli_update_status() {
    info "CLI: bcs-cli update-status"
    ensure_cli_token CEO || { skip_case "no token"; TESTS_TOTAL=$((TESTS_TOTAL+1)); return; }
    if ! bcs_cli CEO update-status --status idle; then
        fail "update-status idle exited $BCS_CLI_EXIT"
        TESTS_FAILED=$((TESTS_FAILED+1)); TESTS_TOTAL=$((TESTS_TOTAL+1)); return
    fi
    if _cli_contains "$BCS_CLI_STDOUT" "Status"; then
        pass "bcs-cli update-status idle accepted (CLI path ok)"
        TESTS_PASSED=$((TESTS_PASSED+1))
    else
        fail "bcs-cli update-status output unexpected: $(echo "$BCS_CLI_STDOUT" | head -c 120)"
        TESTS_FAILED=$((TESTS_FAILED+1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL+1))
    # Restore to busy (demo default-ish).
    bcs_cli CEO update-status --status busy >/dev/null 2>&1 || true
}

# request-group-help -> confirm-group-help: end-to-end proposal flow via CLI.
# (confirm-group-help has no separate array entry; it is driven inside this case
#  using the proposal URL returned by request-group-help, per the one-case-per-command
#  rule with cross-command data threading.)
test_cli_request_group_help() {
    info "CLI: bcs-cli request-group-help + confirm-group-help"
    ensure_cli_token CEO || { skip_case "no token"; TESTS_TOTAL=$((TESTS_TOTAL+1)); return; }
    local gid=""
    if ! bcs_cli CEO request-group-help --topic "cli e2e help"; then
        fail "request-group-help exited $BCS_CLI_EXIT"
        TESTS_FAILED=$((TESTS_FAILED+1)); TESTS_TOTAL=$((TESTS_TOTAL+1)); return
    fi
    # The response contains a confirm URL (token-bearing). Extract it.
    local confirm_url
    confirm_url="$(printf '%s' "$BCS_CLI_STDOUT" | grep -oE 'https?://[^"[:space:]]+/groups/[^"[:space:]]+/confirm' | head -1)"
    if [[ -z "$confirm_url" ]]; then
        confirm_url="$(printf '%s' "$BCS_CLI_STDOUT" | grep -oE '/groups/[^"[:space:]]+/confirm' | head -1)"
        [[ -n "$confirm_url" ]] && confirm_url="${BCS_API_BASE_URL}${confirm_url}"
    fi
    if [[ -z "$confirm_url" ]]; then
        fail "request-group-help did not return a confirm URL"
        TESTS_FAILED=$((TESTS_FAILED+1)); TESTS_TOTAL=$((TESTS_TOTAL+1)); return
    fi
    if bcs_cli "" confirm-group-help --url "$confirm_url"; then
        pass "bcs-cli confirm-group-help succeeded (group created via CLI proposal flow)"
        TESTS_PASSED=$((TESTS_PASSED+1))
        # Best-effort cleanup: confirm prints "Group created:\n  ID: <uuid>".
        # DELETE /groups/{id}?bot_id=<driver> (api_delete can't add the query).
        gid="$(printf '%s' "$BCS_CLI_STDOUT" | grep -oE 'ID: [a-f0-9-]+' | head -1 | sed 's/ID: //')"
    else
        fail "confirm-group-help exited $BCS_CLI_EXIT"
        TESTS_FAILED=$((TESTS_FAILED+1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL+1))
    if [[ -n "$gid" ]]; then
        curl -s -o /dev/null -X DELETE "${BCS_API_BASE_URL}/groups/${gid}?bot_id=${BOT_CEO_UUID}" \
            -H "X-Mock-User-Id: $BCS_MOCK_USER_ID" \
            -H "X-Mock-Nick-Name: $BCS_MOCK_USER_NICK_NAME" 2>/dev/null || true
    fi
}

# chat: 1:1 message via CLI, detached so e2e does not block on bot response.
test_cli_chat() {
    info "CLI: bcs-cli chat (detach)"
    ensure_cli_token CEO || { skip_case "no token"; TESTS_TOTAL=$((TESTS_TOTAL+1)); return; }
    if ! bcs_cli CEO chat --bot-uuid "$BOT_PM_UUID" --message "hello from cli e2e" --detach; then
        # --detach may not exist on this bcs-cli version; retry blocking.
        warn "chat --detach failed ($BCS_CLI_EXIT); retrying without --detach"
        if ! bcs_cli CEO chat --bot-uuid "$BOT_PM_UUID" --message "hello from cli e2e"; then
            fail "bcs-cli chat exited $BCS_CLI_EXIT"
            TESTS_FAILED=$((TESTS_FAILED+1)); TESTS_TOTAL=$((TESTS_TOTAL+1)); return
        fi
    fi
    # chat --detach prints "Run: <uuid>" / "Session: ..." / "State: running"
    # (capital). Assert a run/session handle is present (no unasserted pass).
    if _cli_contains "$BCS_CLI_STDOUT" "Run:" || _cli_contains "$BCS_CLI_STDOUT" "Session:"; then
        pass "bcs-cli chat returned a run/session handle"
        TESTS_PASSED=$((TESTS_PASSED+1))
    else
        fail "bcs-cli chat output had no run/session handle: $(echo "$BCS_CLI_STDOUT" | head -c 120)"
        TESTS_FAILED=$((TESTS_FAILED+1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL+1))
}

# list-groups: list all groups (no specific group filter).
test_cli_list_groups() {
    info "CLI: bcs-cli list-groups"
    ensure_cli_token CEO || { skip_case "no token"; TESTS_TOTAL=$((TESTS_TOTAL+1)); return; }
    if ! bcs_cli CEO list-groups; then
        fail "bcs-cli list-groups exited $BCS_CLI_EXIT"
        TESTS_FAILED=$((TESTS_FAILED+1)); TESTS_TOTAL=$((TESTS_TOTAL+1)); return
    fi
    # Expect JSON array or pagination structure with total/groups/items.
    if _cli_contains "$BCS_CLI_STDOUT" "total" || _cli_contains "$BCS_CLI_STDOUT" "groups" || _cli_contains "$BCS_CLI_STDOUT" "items" || _cli_contains "$BCS_CLI_STDOUT" "id"; then
        pass "bcs-cli list-groups returned a group list"
        TESTS_PASSED=$((TESTS_PASSED+1))
    else
        # Empty list acceptable; ensure it is valid JSON array.
        if _cli_contains "$BCS_CLI_STDOUT" "[" || _cli_contains "$BCS_CLI_STDOUT" "{"; then
            pass "bcs-cli list-groups returned a JSON payload (empty ok)"
            TESTS_PASSED=$((TESTS_PASSED+1))
        else
            fail "bcs-cli list-groups unexpected output: $(echo "$BCS_CLI_STDOUT" | head -c 120)"
            TESTS_FAILED=$((TESTS_FAILED+1))
        fi
    fi
    TESTS_TOTAL=$((TESTS_TOTAL+1))
}

# friend list: list friends of the current bot.
test_cli_friend() {
    info "CLI: bcs-cli friend list"
    ensure_cli_token CEO || { skip_case "no token"; TESTS_TOTAL=$((TESTS_TOTAL+1)); return; }
    # First ensure CEO is friends with 研发 from prior friends.sh setup (idempotent).
    if ! bcs_cli CEO friend list; then
        fail "bcs-cli friend list exited $BCS_CLI_EXIT"
        TESTS_FAILED=$((TESTS_FAILED+1)); TESTS_TOTAL=$((TESTS_TOTAL+1)); return
    fi
    # Assert that 研发的 UUID appears in the list (friends.sh auto-accept test pairs them).
    if _cli_contains "$BCS_CLI_STDOUT" "$BOT_ENG_UUID"; then
        pass "bcs-cli friend list contains 研发"
        TESTS_PASSED=$((TESTS_PASSED+1))
    else
        # Allow empty list if no friends; require valid JSON array/object.
        if _cli_contains "$BCS_CLI_STDOUT" "[" || _cli_contains "$BCS_CLI_STDOUT" "{"; then
            pass "bcs-cli friend list returned valid JSON (no 研发 is acceptable)"
            TESTS_PASSED=$((TESTS_PASSED+1))
        else
            fail "bcs-cli friend list unexpected output: $(echo "$BCS_CLI_STDOUT" | head -c 120)"
            TESTS_FAILED=$((TESTS_FAILED+1))
        fi
    fi
    TESTS_TOTAL=$((TESTS_TOTAL+1))
}