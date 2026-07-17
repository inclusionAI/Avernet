#!/bin/bash
# cli-stories.sh — Complete bcs-cli coverage organized as user journeys.

_cli_story_run() {
    local desc="$1" bot="$2"; shift 2
    info "CLI story: bcs-cli $*"
    if bcs_cli_json "$bot" "$@"; then
        pass "$desc"
        TESTS_PASSED=$((TESTS_PASSED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        return 0
    fi
    fail "$desc (exit=$BCS_CLI_EXIT): $(printf '%s' "${BCS_CLI_STDERR:-$BCS_CLI_STDOUT}" | head -c 240)"
    TESTS_FAILED=$((TESTS_FAILED + 1))
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    return 1
}

_cli_json_array_has_object() {
    local json="$1" path="$2" key="$3" expected="$4" key2="${5:-}" expected2="${6:-}"
    printf '%s' "$json" | python3 -c '
import json, sys
try:
    value = json.load(sys.stdin)
    if sys.argv[1]:
        for part in sys.argv[1].split("."):
            value = value[int(part)] if isinstance(value, list) else value[part]
    found = any(
        isinstance(item, dict)
        and str(item.get(sys.argv[2], "")) == sys.argv[3]
        and (not sys.argv[4] or str(item.get(sys.argv[4], "")) == sys.argv[5])
        for item in value
    )
    print("1" if found else "0")
except Exception:
    print("0")
' "$path" "$key" "$expected" "$key2" "$expected2"
}

_cli_json_has_path() {
    local json="$1" path="$2"
    printf '%s' "$json" | python3 -c '
import json, sys
try:
    value = json.load(sys.stdin)
    for part in sys.argv[1].split("."):
        value = value[int(part)] if isinstance(value, list) else value[part]
    print("1")
except Exception:
    print("0")
' "$path"
}

_cli_json_is_array() {
    printf '%s' "$1" | python3 -c 'import json,sys; print("1" if isinstance(json.load(sys.stdin), list) else "0")' 2>/dev/null || echo 0
}

_cli_story_create_pm_group() {
    local topic="$1"
    CLI_STORY_GROUP_ID=""
    _cli_story_run "operator creates a CLI-managed group" PM create-group \
        --driver "$BOT_PM_UUID" \
        --participants "$BOT_PM_UUID,$BOT_ENG_UUID" \
        --context "Release coordination through bcs-cli" \
        --topic "$topic" || return
    CLI_STORY_GROUP_ID=$(printf '%s' "$BCS_CLI_STDOUT" | grep -oE 'ID: [A-Za-z0-9_-]+' | head -1 | sed 's/ID: //')
    assert_not_empty "CLI-created group returns an id" "$CLI_STORY_GROUP_ID"
    [[ -n "$CLI_STORY_GROUP_ID" ]]
}

_story_cli_direct_onboard() {
    api_get "/register/token"
    require_status "CLI onboarding fixture obtains a register token" "200" || return
    local register_token bot_name register_path bot_uuid bot_token
    register_token=$(json_path "$RESPONSE" "token")
    bot_name="cli-direct-agent-$$-$(date +%s)"
    assert_not_empty "CLI onboarding fixture has a register token" "$register_token"
    [[ -n "$register_token" ]] || return

    register_path="/register?token=$(urlencode "$register_token")&bot-name=$(urlencode "$bot_name")"
    api_post "$register_path"
    require_status "CLI onboarding fixture registers a temporary agent" "200" || return
    bot_uuid=$(json_path "$RESPONSE" "bot_uuid")
    bot_token=$(json_path "$RESPONSE" "bot_token")
    assert_not_empty "CLI onboarding fixture returns bot id" "$bot_uuid"
    assert_not_empty "CLI onboarding fixture returns bot token" "$bot_token"
    [[ -n "$bot_uuid" && -n "$bot_token" ]] || return

    _cli_story_run "operator directly onboards a temporary agent" "token:${bot_token}" onboard \
        --name "$bot_name" --summary "Direct CLI onboarding story" \
        --skills "triage,review" --domains "release" --scopes "local" || return
    assert_json_eq "direct CLI onboarding keeps the registered bot id" "$BCS_CLI_STDOUT" "bot_uuid" "$bot_uuid"
    assert_json_eq "direct CLI onboarding is persisted" "$BCS_CLI_STDOUT" "onboarded" "true"
    assert_json_eq "direct CLI onboarding keeps the selected name" "$BCS_CLI_STDOUT" "name" "$bot_name"

    api_get "/bots/${bot_uuid}"
    require_status "directly onboarded CLI agent is queryable" "200" || return
    assert_json_eq "CLI-onboarded capabilities are visible" "$RESPONSE" "capabilities.name" "$bot_name"
    api_delete "/bots/${bot_uuid}"
    require_status "temporary CLI-onboarded agent is removed" "200" || return
    api_get "/bots/${bot_uuid}"
    require_status "removed CLI-onboarded agent returns 404" "404" || return
}

# User story: An operator establishes trust and coordinates a release team entirely through bcs-cli.
#
# Flow:
#   Protect an agent -> request and reject friendship -> create and inspect a group
#   -> add a specialist -> list owned groups -> fuse context -> complete one group
#   -> terminate another group -> clean up all fixtures.
#
# Critical assertions:
#   - Rejected friendship never appears in the requester's friend list.
#   - Group identity, driver, and membership survive independent CLI reads.
#   - Context fusion returns a recommendation payload.
#   - Group status and termination are observable as completed states.
story_cli_operator_builds_collaboration_team() {
    info "Story: an operator establishes trust and coordinates a release team through bcs-cli"

    _cli_story_run "customer-service agent protects incoming friendship requests" CS visibility set --value protected || return
    assert_json_eq "protected visibility update succeeds" "$BCS_CLI_STDOUT" "success" "true"
    assert_json_eq "protected visibility is returned" "$BCS_CLI_STDOUT" "data.visibility" "protected"

    _cli_story_run "engineering requests a protected friendship" ENG friend request --bot-uuid "$BOT_CS_UUID" || return
    assert_json_eq "CLI friend request succeeds" "$BCS_CLI_STDOUT" "success" "true"

    _cli_story_run "customer-service lists pending requests" CS friend requests --direction received --status pending || return
    local request_id
    request_id=$(printf '%s' "$BCS_CLI_STDOUT" | python3 -c '
import json,sys
items=json.load(sys.stdin)
for item in items:
    if item.get("from_bot") == sys.argv[1] and item.get("to_bot") == sys.argv[2] and item.get("status") == "pending":
        print(item.get("id", "")); break
' "$BOT_ENG_UUID" "$BOT_CS_UUID" 2>/dev/null)
    assert_not_empty "pending CLI friend request is identifiable" "$request_id"
    [[ -n "$request_id" ]] || return

    _cli_story_run "customer-service rejects the friendship request" CS friend reject --request-id "$request_id" || return
    assert_json_eq "CLI friend rejection succeeds" "$BCS_CLI_STDOUT" "success" "true"

    _cli_story_run "engineering verifies the rejected relationship" ENG friend list || return
    local rejected_absent
    rejected_absent=$(_cli_json_array_has_object "$BCS_CLI_STDOUT" "" "bot_uuid" "$BOT_CS_UUID")
    assert_eq "rejected agent is absent from CLI friend list" "$rejected_absent" "0"

    _cli_story_run "customer-service restores public visibility" CS visibility set --value public || return
    assert_json_eq "public visibility restore succeeds" "$BCS_CLI_STDOUT" "data.visibility" "public"

    _cli_story_create_pm_group "CLI release coordination" || return
    local group_id="$CLI_STORY_GROUP_ID"

    _cli_story_run "operator reads the CLI-managed group" PM get-group --id "$group_id" || return
    assert_json_eq "CLI group read keeps its id" "$BCS_CLI_STDOUT" "id" "$group_id"
    assert_json_eq "CLI group read keeps its driver" "$BCS_CLI_STDOUT" "driver_bot" "$BOT_PM_UUID"

    _cli_story_run "operator adds verification to the group" PM add-member \
        --group "$group_id" --bot-uuid "$BOT_QA_UUID" --role consultant || return
    _cli_story_run "operator reads the expanded group" PM get-group --id "$group_id" || return
    assert_contains "CLI group read contains the added specialist" "$BCS_CLI_STDOUT" "$BOT_QA_UUID"

    _cli_story_run "operator lists groups containing itself" PM list-groups || return
    local cli_group_count api_group_count
    cli_group_count=$(printf '%s' "$BCS_CLI_STDOUT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["returned"])' 2>/dev/null)
    assert_not_empty "CLI current-bot group list exposes its result count" "$cli_group_count"
    assert_contains "CLI current-bot group list contains the managed group" "$BCS_CLI_STDOUT" "$group_id"
    api_get "/bots/${BOT_PM_UUID}/groups?include_session_groups=false"
    require_status "CLI group list can be checked against the formal bot-group API" "200" || return
    assert_contains "bot-group read-back contains the CLI-managed group" "$RESPONSE" "$group_id"
    api_group_count=$(printf '%s' "$RESPONSE" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(len(d.get("items", [])))' 2>/dev/null)
    assert_eq "CLI current-bot count matches the formal bot-group API" "$cli_group_count" "$api_group_count"

    _cli_story_run "operator fuses team perspectives" PM fuse --group "$group_id" \
        --question "Is the release ready?" \
        --participants "$BOT_PM_UUID,$BOT_ENG_UUID,$BOT_QA_UUID" \
        --focus "release risk" || return
    assert_contains "CLI fusion returns a recommendation" "$BCS_CLI_STDOUT" "recommendation"

    _cli_story_run "operator marks the coordinated group completed" PM group-status \
        --group "$group_id" --status completed --reason "release reviewed" || return
    _cli_story_run "operator verifies the completed group" PM get-group --id "$group_id" || return
    assert_json_eq "CLI group status persists completed" "$BCS_CLI_STDOUT" "status" "completed"

    api_delete "/groups/${group_id}?bot_id=${BOT_PM_UUID}"
    require_status "completed CLI group is cleaned up" "200" || return

    _cli_story_create_pm_group "CLI termination drill" || return
    local terminated_group_id="$CLI_STORY_GROUP_ID"
    _cli_story_run "operator terminates a CLI-managed group" PM terminate-group --group "$terminated_group_id" || return
    _cli_story_run "operator reads the terminated group" PM get-group --id "$terminated_group_id" || return
    assert_json_eq "terminated CLI group is completed" "$BCS_CLI_STDOUT" "status" "completed"
    api_delete "/groups/${terminated_group_id}?bot_id=${BOT_PM_UUID}"
    require_status "terminated CLI group is cleaned up" "200" || return
}

# User story: A release operator drives a full session and service lifecycle through bcs-cli.
#
# Flow:
#   Create and query a session -> patch it -> add and mute a participant -> chat
#   -> inspect messages -> create an invite -> remove the participant -> complete
#   the session -> invoke, inspect, and wait on a service session -> clean up.
#
# Critical assertions:
#   - Every session mutation is visible through a later CLI read.
#   - Chat history contains the submitted message and invite credentials are usable values.
#   - Completion preserves the exact output payload.
#   - Service status preserves identity and caller isolation; wait has a precise terminal or timeout result.
story_cli_operator_runs_sessions_and_services() {
    info "Story: a release operator runs sessions and services through bcs-cli"
    _cli_story_create_pm_group "CLI session and service lifecycle" || return
    local group_id="$CLI_STORY_GROUP_ID"

    _cli_story_run "operator creates a release session" PM session create \
        --group "$group_id" --title "CLI full session" \
        --input '{"release":"2026.07"}' --meta '{"source":"cli-story"}' || return
    local session_id
    session_id=$(json_path "$BCS_CLI_STDOUT" "session_id")
    assert_not_empty "CLI session creation returns an id" "$session_id"
    assert_json_eq "CLI session belongs to the managed group" "$BCS_CLI_STDOUT" "group_id" "$group_id"
    [[ -n "$session_id" ]] || return

    _cli_story_run "operator lists the created release session" PM session list \
        --group "$group_id" --status running --q "CLI full" \
        --participant "$BOT_PM_UUID" --offset 0 --limit 20 || return
    local listed
    listed=$(_cli_json_array_has_object "$BCS_CLI_STDOUT" "items" "session_id" "$session_id")
    assert_eq "CLI session list contains the created session" "$listed" "1"

    _cli_story_run "operator reads the release session" PM session get "$session_id" || return
    assert_json_eq "CLI session get keeps its id" "$BCS_CLI_STDOUT" "session_id" "$session_id"
    assert_json_eq "CLI session get keeps its title" "$BCS_CLI_STDOUT" "session_title" "CLI full session"

    _cli_story_run "operator renames the release session" PM session patch "$session_id" --title "CLI release sign-off" || return
    assert_json_eq "CLI session patch returns the new title" "$BCS_CLI_STDOUT" "session_title" "CLI release sign-off"

    _cli_story_run "operator adds verification to the session" PM session add-member "$session_id" \
        --bot-uuid "$BOT_QA_UUID" --role consultant || return
    local added
    added=$(_cli_json_array_has_object "$BCS_CLI_STDOUT" "participants" "bot_uuid" "$BOT_QA_UUID")
    assert_eq "CLI session add-member returns verification" "$added" "1"

    _cli_story_run "operator mutes verification in the session" PM session set-member-mode \
        "$session_id" "$BOT_QA_UUID" --mode muted || return
    _cli_story_run "operator reads the updated session membership" PM session get "$session_id" || return
    local muted
    muted=$(_cli_json_array_has_object "$BCS_CLI_STDOUT" "participants" "bot_uuid" "$BOT_QA_UUID" "mode" "muted")
    assert_eq "CLI session read shows verification muted" "$muted" "1"

    local chat_message="CLI release readiness check $$"
    _cli_story_run "operator chats in the release session" PM session chat \
        --session "$session_id" --message "$chat_message" || return
    assert_json_eq "CLI session chat keeps the session id" "$BCS_CLI_STDOUT" "session_id" "$session_id"
    assert_json_eq "CLI session chat keeps the group id" "$BCS_CLI_STDOUT" "group_id" "$group_id"
    assert_eq "CLI session chat exposes delivery results" "$(_cli_json_has_path "$BCS_CLI_STDOUT" "delivery_results")" "1"

    _cli_story_run "operator reads release-session messages" PM session messages "$session_id" \
        --view-bot "$BOT_PM_UUID" --limit 50 || return
    assert_eq "CLI session messages returns a JSON array" "$(_cli_json_is_array "$BCS_CLI_STDOUT")" "1"
    assert_contains "CLI session history contains the submitted message" "$BCS_CLI_STDOUT" "$chat_message"

    _cli_story_run "operator creates a session invite" PM session invite-link "$session_id" --ttl-seconds 300 || return
    assert_json_not_empty "CLI session invite returns a token" "$BCS_CLI_STDOUT" "invite_token"
    assert_json_not_empty "CLI session invite returns a join URL" "$BCS_CLI_STDOUT" "join_url"

    _cli_story_run "operator removes verification from the session" PM session remove-member \
        "$session_id" "$BOT_QA_UUID" || return
    _cli_story_run "operator verifies session membership removal" PM session get "$session_id" || return
    local removed
    removed=$(_cli_json_array_has_object "$BCS_CLI_STDOUT" "participants" "bot_uuid" "$BOT_QA_UUID")
    assert_eq "removed participant is absent from CLI session read" "$removed" "0"

    _cli_story_run "operator completes the release session" PM session complete "$session_id" \
        --output '{"summary":"approved by CLI"}' || return
    assert_json_eq "CLI session completion returns completed" "$BCS_CLI_STDOUT" "status" "completed"
    assert_json_eq "CLI session completion keeps its output" "$BCS_CLI_STDOUT" "output.summary" "approved by CLI"

    api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
    require_status "CLI service fixture enables group invocation" "200" || return

    _cli_story_run "release pipeline invokes the group service" PM service invoke \
        --group "$group_id" --input '{"commit":"cli123"}' \
        --meta '{"source":"cli-story"}' --caller-id "cli-release-pipeline" \
        --title "CLI automated release audit" --detach || return
    local service_session_id
    service_session_id=$(json_path "$BCS_CLI_STDOUT" "session_id")
    assert_not_empty "CLI service invoke returns a session id" "$service_session_id"
    assert_json_eq "CLI service invoke returns service kind" "$BCS_CLI_STDOUT" "session_kind" "service_invocation"
    [[ -n "$service_session_id" ]] || return

    _cli_story_run "release pipeline checks service status" PM service status "$service_session_id" || return
    assert_json_eq "CLI service status keeps its session id" "$BCS_CLI_STDOUT" "session_id" "$service_session_id"
    assert_json_eq "CLI service status keeps its group" "$BCS_CLI_STDOUT" "group_id" "$group_id"

    info "CLI story: bcs-cli service wait"
    if bcs_cli_json PM service wait "$service_session_id" --timeout-ms 1; then
        pass "CLI service wait returns a completed session"
        TESTS_PASSED=$((TESTS_PASSED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        assert_json_eq "completed CLI service wait keeps its session id" "$BCS_CLI_STDOUT" "session_id" "$service_session_id"
        assert_json_eq "completed CLI service wait is terminal" "$BCS_CLI_STDOUT" "status" "completed"
    else
        if [[ "$BCS_CLI_STDERR" == *"Timed out after"* && "$BCS_CLI_STDERR" == *"$service_session_id"* ]]; then
            pass "CLI service wait returns the precise bounded-timeout contract"
            TESTS_PASSED=$((TESTS_PASSED + 1))
        else
            fail "CLI service wait failed unexpectedly: $(printf '%s' "$BCS_CLI_STDERR" | head -c 240)"
            TESTS_FAILED=$((TESTS_FAILED + 1))
        fi
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
    fi

    api_delete "/sessions/${session_id}?bot_id=${BOT_PM_UUID}"
    require_status "completed CLI chat session is cleaned up" "200" || return
    api_delete "/sessions/${service_session_id}?bot_id=$(urlencode "bot:${BOT_PM_UUID}")"
    require_status "CLI service session is cleaned up" "200" || return
    api_delete "/groups/${group_id}?bot_id=${BOT_PM_UUID}"
    require_status "CLI session/service group is cleaned up" "200" || return
}

# User story: An operator validates channel-management behavior before a provider is installed.
#
# Flow:
#   Attempt to bind a channel -> list bindings -> unbind a missing binding -> list again.
#
# Critical assertions:
#   - Bind fails with the explicit disabled-bridge contract and never leaks the supplied secret.
#   - List returns a well-formed items array.
#   - Disabled-bridge unbind follows the documented no-op acknowledgement.
#   - No phantom binding appears after the rejected and no-op operations.
story_cli_operator_validates_channel_management() {
    info "Story: an operator validates channel management through bcs-cli"
    local account="cli-channel-$$" missing_id="cli-missing-binding-$$"

    info "CLI story: bcs-cli channel bind"
    if bcs_cli_json CEO channel bind \
        --account "$account" --target-kind bot --target-id "$BOT_CEO_UUID" \
        --visibility full_transcript --env local --robot-code "e2e-robot" \
        --client-id "e2e-client" --client-secret "not-a-real-secret" \
        --send-mode normal --message-type markdown; then
        fail "CLI channel bind unexpectedly succeeded without a channel provider"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        return
    fi
    if [[ "$BCS_CLI_STDERR" == *"channel bridge is disabled"* && "$BCS_CLI_STDERR" != *"not-a-real-secret"* ]]; then
        pass "CLI channel bind returns the redacted disabled-bridge contract"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        fail "CLI channel bind error is unexpected or leaked its secret: $(printf '%s' "$BCS_CLI_STDERR" | head -c 240)"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    _cli_story_run "operator lists channel bindings" CEO channel list || return
    assert_eq "CLI channel list exposes items" "$(_cli_json_has_path "$BCS_CLI_STDOUT" "items")" "1"

    _cli_story_run "operator unbinds a missing disabled-bridge binding" CEO channel unbind --id "$missing_id" || return
    assert_json_eq "CLI channel unbind acknowledges the no-op" "$BCS_CLI_STDOUT" "ok" "true"

    _cli_story_run "operator confirms no phantom channel binding exists" CEO channel list || return
    assert_json_eq "CLI channel list remains empty" "$BCS_CLI_STDOUT" "items" "[]"
}
