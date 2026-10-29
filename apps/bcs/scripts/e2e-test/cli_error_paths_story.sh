#!/bin/bash
# cli_error_paths_story.sh — Terminal-mode read flows and CLI-side
# validation/error contracts, all through the public bcs-cli entry:
#   ...runs_a_session_in_terminal_mode      — human-readable session
#       lifecycle plus malformed --input/--meta contracts.
#   ...validates_group_and_collaboration_config — collaboration YAML,
#       participant tags, role bindings, group-context policy JSON.
#   ...uses_channel_contracts_in_terminal   — binding payload guards, the
#       redacted disabled-bridge contract, terminal listings.
#   ...diagnoses_service_and_registration_in_terminal — service summaries,
#       sid inference, bounded waits, temp agents, endpoint diagnostics.
#   ...chats_and_shares_files_in_terminal    — chat contracts, collaboration
#       preview panels, session-file lifecycle.
# Assertion style: follow cli-stories.sh (bcs_cli/bcs_cli_json wrappers,
# assert_* tally helpers, cleanup of every fixture via the API helpers).

# Render a bcs-cli invocation for logging; `token:` credentials are redacted.
_cli_story_redact_args() {
    [[ "$#" -gt 0 ]] && printf '%s' "$*" | sed -E 's/token:[^ ]+/token:<redacted>/g'
}

# Terminal-mode sibling of cli-stories.sh's _cli_story_run: same logging and
# tally contract, but through plain bcs_cli so commands render their
# documented human-readable output (the --json flag stays unset).
_cli_story_run_terminal() {
    local desc="$1"; shift
    info "CLI story: bcs-cli $(_cli_story_redact_args "$@")"
    if bcs_cli "$@"; then
        pass "$desc"
        TESTS_PASSED=$((TESTS_PASSED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        return 0
    fi
    fail "$desc (exit=$BCS_CLI_EXIT): $(printf '%s' "${BCS_CLI_STDERR:-}" | head -c 240)"
    TESTS_FAILED=$((TESTS_FAILED + 1))
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    return 1
}

# Assert a CLI call FAILS with the documented stderr snippet, without letting
# set -e stop the story. Usage:
#   _cli_story_expect_fail <desc> <stderr needle> <bot> <args...>
_cli_story_expect_fail() {
    local desc="$1" needle="$2"
    shift 2
    info "CLI story: bcs-cli $(_cli_story_redact_args "$@") (expected failure)"
    if bcs_cli "$@" 2>/dev/null; then
        fail "$desc (unexpected success)"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        return 1
    fi
    if [[ -n "$needle" && "${BCS_CLI_STDERR:-}" == *"$needle"* ]]; then
        pass "$desc"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        fail "$desc (stderr missing '$needle': $(printf '%s' "${BCS_CLI_STDERR:-}" | head -c 200))"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    return 0
}

# Run bcs_cli with temporary environment variables; a bash env-prefix on a
# FUNCTION call persists after the call, so snapshot and restore each variable
# (otherwise the overrides leak into later stories in the same e2e shell).
# Bash 3.2 compatible: plain arrays and ${!var} indirection only.
_cli_story_with_env() {
    local assigns=() names=() saved=() kv name i rc=0
    while [[ "$1" != "--" ]]; do
        assigns+=("$1")
        name="${1%%=*}"
        names+=("$name")
        saved+=("${!name:-__CLI_STORY_UNSET__}")
        shift
    done
    shift
    for kv in "${assigns[@]}"; do
        export "${kv%%=*}"="${kv#*=}"
    done
    bcs_cli "$@" || rc=$?
    i=0
    while [ "$i" -lt "${#names[@]}" ]; do
        name="${names[$i]}"
        if [[ "${saved[$i]}" == "__CLI_STORY_UNSET__" ]]; then
            unset "$name" 2>/dev/null || true
        else
            export "$name=${saved[$i]}"
        fi
        i=$((i + 1))
    done
    return "$rc"
}

# Best-effort scrub for failed story arms (never masks pending asserts).
# Optional args after the group id: a session id, then any temp files.
_cli_story_scrub_group() {
    local group_id="$1" session_id="${2:-}"; shift 2 2>/dev/null || shift "$#"
    [[ -n "$session_id" ]] && api_delete "/sessions/${session_id}?bot_id=${BOT_PM_UUID}" >/dev/null 2>&1
    api_delete "/groups/${group_id}?bot_id=${BOT_PM_UUID}" >/dev/null 2>&1
    for f in "$@"; do [[ -n "$f" ]] && rm -f "$f" 2>/dev/null; done
}

# Best-effort scrub of a detached service-invocation session (service sessions
# resolve their principal via the `bot:` id form, so they need their own delete)
# plus the enclosing PM group. _cli_story_scrub_service_session <sid> <group>.
_cli_story_scrub_service_session() {
    [[ -n "${1:-}" ]] && api_delete "/sessions/$1?bot_id=$(urlencode "bot:${BOT_PM_UUID}")" >/dev/null 2>&1
    [[ -n "${2:-}" ]] && _cli_story_scrub_group "$2"
}

# Register one temporary agent through the public /register flow into
# CLI_STORY_AGENT_UUID / CLI_STORY_AGENT_TOKEN (globals, not stdout: assert
# helpers print and would corrupt a captured value).
_cli_story_register_agent() {
    local name="$1"
    api_get "/register/token"
    require_status "terminal story obtains a register token" "200" || return 1
    local register_token
    register_token=$(json_path "$RESPONSE" "token")
    api_post "/register?token=$(urlencode "$register_token")&bot-name=$(urlencode "$name")"
    require_status "terminal story registers agent $name" "200" || return 1
    CLI_STORY_AGENT_UUID=$(json_path "$RESPONSE" "bot_uuid")
    CLI_STORY_AGENT_TOKEN=$(json_path "$RESPONSE" "bot_token")
    [[ -n "$CLI_STORY_AGENT_UUID" && -n "$CLI_STORY_AGENT_TOKEN" ]]
}

# Best-effort scrub of the two-agent story's temp dir and both agent bots.
_cli_story_scrub_agents() {
    [[ -n "${1:-}" ]] && rm -rf "$1"
    [[ -n "${2:-}" ]] && api_delete "/bots/$2" >/dev/null 2>&1
    [[ -n "${3:-}" ]] && api_delete "/bots/$3" >/dev/null 2>&1
}

# Extract the session id from the terminal-mode `session create` line.
_cli_story_terminal_sid() {
    printf '%s' "$BCS_CLI_STDOUT" | sed -n 's/.*Session created: \([^ ]*\) .*/\1/p' | head -1
}

# Write a minimal but valid collaboration definition with two required roles:
# enough to exercise every --binding rule in this file (including the
# missing-required-binding rejection).
_cli_story_write_planner_yaml() {
    printf '%s\n' "name: CLI terminal validation workflow
participants:
  planner:
    display_name: Planner
    required: true
  reviewer:
    display_name: Reviewer
    required: true
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    nodes:
      plan:
        kind: bot_task
        display_name: Plan
        assignee:
          type: bot_binding
          binding: planner
        instruction: Produce a plan.
        transitions:
          complete:
            targets: [review]
      review:
        kind: bot_task
        display_name: Review
        assignee:
          type: bot_binding
          binding: reviewer
        instruction: Review the plan.
        final_output: true" > "$1"
}

#
# Flow: group -> sessions in terminal mode through every mutation ->
# complete (twice) -> malformed --input/--meta literals -> unknown session.
# Critical assertions:
#   - Every terminal-mode print line matches the documented human contract.
#   - JSON read-back (bcs_cli_json) reflects the patched title.
#   - Invalid JSON literals and @file forms never reach the server.
story_cli_operator_runs_a_session_in_terminal_mode() {
    info "Story: an operator runs a chat session entirely in terminal mode"
    _cli_story_create_pm_group "CLI terminal-mode session story" || return 1
    local group_id="$CLI_STORY_GROUP_ID"

    _cli_story_run_terminal "operator creates a session in terminal mode" PM session create \
        --group "$group_id" --title "Terminal session story" \
        --input '{"source":"terminal"}' || { _cli_story_scrub_group "$group_id"; return 1; }
    local session_id
    session_id=$(_cli_story_terminal_sid)
    assert_not_empty "terminal session creation prints its id" "$session_id"
    [[ -n "$session_id" ]] || return 1
    assert_contains "terminal session creation reports kind and status" \
        "$BCS_CLI_STDOUT" "(kind=chat, status=running)"

    _cli_story_run_terminal "operator lists sessions in terminal mode" PM session list \
        --group "$group_id" --limit 10 || { _cli_story_scrub_group "$group_id" "$session_id"; return 1; }
    assert_contains "terminal session list header names the group" \
        "$BCS_CLI_STDOUT" "Sessions in group $group_id ("
    assert_contains "terminal session list shows the created session" \
        "$BCS_CLI_STDOUT" "$session_id [running]"

    _cli_story_run_terminal "operator reads the session in terminal mode" PM session get \
        "$session_id" || { _cli_story_scrub_group "$group_id" "$session_id"; return 1; }
    assert_contains "terminal session get pretty-prints the id" \
        "$BCS_CLI_STDOUT" "\"session_id\": \"$session_id\""

    _cli_story_run_terminal "operator patches the session title in terminal mode" PM session patch \
        "$session_id" --title "Terminal story retitled" || { _cli_story_scrub_group "$group_id" "$session_id"; return 1; }
    assert_contains "terminal session patch echoes the new title" \
        "$BCS_CLI_STDOUT" "✓ Patched: $session_id title=\"Terminal story retitled\""

    _cli_story_run "operator confirms the patch through structured read-back" PM \
        session get "$session_id" || { _cli_story_scrub_group "$group_id" "$session_id"; return 1; }
    assert_contains "structured read-back confirms the terminal patch" \
        "$BCS_CLI_STDOUT" "Terminal story retitled"

    local chat_line="Terminal readiness check $$"
    _cli_story_run_terminal "operator chats in terminal mode" PM session chat \
        --session "$session_id" --message "$chat_line" || { _cli_story_scrub_group "$group_id" "$session_id"; return 1; }
    assert_contains "terminal session chat reports delivery counts" \
        "$BCS_CLI_STDOUT" "✓ Delivered to 2 (failed 0)"

    _cli_story_run_terminal "operator reads session history in terminal mode" PM session messages \
        "$session_id" --limit 20 || { _cli_story_scrub_group "$group_id" "$session_id"; return 1; }
    assert_contains "terminal history header names the session" \
        "$BCS_CLI_STDOUT" "Messages in session $session_id ("
    assert_contains "terminal history contains the submitted message" \
        "$BCS_CLI_STDOUT" "$chat_line"

    _cli_story_run_terminal "operator adds verification in terminal mode" PM session add-member \
        "$session_id" --bot-uuid "$BOT_QA_UUID" --role consultant || { _cli_story_scrub_group "$group_id" "$session_id"; return 1; }
    assert_contains "terminal add-member reports the effective role" \
        "$BCS_CLI_STDOUT" "✓ Added member $BOT_QA_UUID to $session_id (role=consultant)"

    _cli_story_run_terminal "operator mutes verification in terminal mode" PM session set-member-mode \
        "$session_id" "$BOT_QA_UUID" --mode muted || { _cli_story_scrub_group "$group_id" "$session_id"; return 1; }
    assert_contains "terminal set-member-mode reports the transition" \
        "$BCS_CLI_STDOUT" "✓ Mode set $BOT_QA_UUID@$session_id -> muted"

    _cli_story_run_terminal "operator creates an invite link in terminal mode" PM session invite-link \
        "$session_id" --ttl-seconds 60 || { _cli_story_scrub_group "$group_id" "$session_id"; return 1; }
    assert_contains "terminal invite link exposes a token payload" \
        "$BCS_CLI_STDOUT" "invite_token"

    _cli_story_run_terminal "operator removes verification in terminal mode" PM session remove-member \
        "$session_id" "$BOT_QA_UUID" || { _cli_story_scrub_group "$group_id" "$session_id"; return 1; }
    assert_contains "terminal remove-member reports the removal" \
        "$BCS_CLI_STDOUT" "✓ Removed member $BOT_QA_UUID from $session_id"

    _cli_story_run_terminal "operator completes the session in terminal mode" PM session complete \
        "$session_id" --output '{"summary":"terminal approval"}' || { _cli_story_scrub_group "$group_id" "$session_id"; return 1; }
    assert_contains "terminal completion reports the completed status" \
        "$BCS_CLI_STDOUT" "✓ Completed: $session_id status=completed"

    _cli_story_run_terminal "operator re-completes the terminal session" PM session complete \
        "$session_id" || { _cli_story_scrub_group "$group_id" "$session_id"; return 1; }
    assert_contains "terminal re-completion follows the already-completed contract" \
        "$BCS_CLI_STDOUT" "↺ Already completed: $session_id"

    # Malformed input handling — no request may reach BCS for a bad literal.
    _cli_story_expect_fail \
        "terminal story rejects an invalid --input literal" \
        "Invalid --input JSON" \
        PM session create --group "$group_id" --input 'not-json'

    _cli_story_expect_fail \
        "terminal story rejects an invalid --meta literal" \
        "Invalid --meta JSON" \
        PM session create --group "$group_id" --meta '{oops'

    _cli_story_expect_fail \
        "terminal story reports unknown sessions precisely" \
        "Get session failed (404 Not Found)" \
        PM session get "no-such-session-$$"

    api_delete "/sessions/${session_id}?bot_id=${BOT_PM_UUID}"
    require_status "terminal session is cleaned up" "200"
    api_delete "/groups/${group_id}?bot_id=${BOT_PM_UUID}"
    require_status "terminal session group is cleaned up" "200"
}

#
# Critical assertions: documented client-side validation messages everywhere,
# manager-worker roles plus tags, invalid definitions rejected, validity
# windows round-tripping through get.
story_cli_operator_validates_group_and_collaboration_config() {
    info "Story: an operator validates collaboration and context inputs before persisting them"
    local yaml_file
    yaml_file="$(bcs_cli_mktemp cli-story-collab.XXXXXX)"
    _cli_story_write_planner_yaml "$yaml_file"

    _cli_story_expect_fail \
        "collaboration validate reports a missing file" \
        "Failed to read YAML file" \
        PM collaboration validate "/tmp/cli-story-missing-$$-workflow.yaml"

    _cli_story_expect_fail \
        "participant tags require a BOT_UUID=TAG pair" \
        "expected BOT_UUID=TAG" \
        PM create-group --driver "$BOT_PM_UUID" --participants "$BOT_PM_UUID,$BOT_ENG_UUID" \
        --participant-tag "garbage"

    _cli_story_expect_fail \
        "participant tags reject blank bot ids or tags" \
        "bot UUID and tag must not be empty" \
        PM create-group --driver "$BOT_PM_UUID" --participants "$BOT_PM_UUID,$BOT_ENG_UUID" \
        --participant-tag "${BOT_ENG_UUID}="

    _cli_story_expect_fail \
        "participant tags name a group participant" \
        "is not a group participant" \
        PM create-group --driver "$BOT_PM_UUID" --participants "$BOT_PM_UUID" \
        --participant-tag "${BOT_QA_UUID}=auditor"

    _cli_story_run_terminal "operator creates a manager-worker group with tags" PM create-group \
        --manager "$BOT_PM_UUID" --participants "$BOT_ENG_UUID,$BOT_QA_UUID" \
        --participant-tag "${BOT_ENG_UUID}=builder" --participant-tag "${BOT_QA_UUID}=verifier" \
        --context "Manager-worker validation $$" --topic "CLI terminal manager story" || {
        rm -f "$yaml_file"
        return 1
    }
    local manager_group_id
    manager_group_id=$(printf '%s\n' "$BCS_CLI_STDOUT" | sed -n 's/^  ID: //p' | head -1)
    assert_not_empty "manager-worker group creation prints its id" "$manager_group_id"
    assert_contains "manager-worker group keeps its manager" "$BCS_CLI_STDOUT" "Driver: $BOT_PM_UUID"
    if [[ -n "$manager_group_id" ]]; then
        _cli_story_run "operator verifies manager-worker roles" PM get-group \
            --id "$manager_group_id" || {
            rm -f "$yaml_file"
            _cli_story_scrub_group "$manager_group_id"
            return 1
        }
        assert_contains "manager-worker group assigns the manager role" "$BCS_CLI_STDOUT" '"manager"'
        assert_contains "manager-worker group assigns worker roles" "$BCS_CLI_STDOUT" '"worker"'
        assert_contains "manager-worker group persists participant tags" \
            "$BCS_CLI_STDOUT" '"builder"'
        api_delete "/groups/${manager_group_id}?bot_id=${BOT_PM_UUID}"
        require_status "manager-worker group is cleaned up" "200" || {
            rm -f "$yaml_file"
            return 1
        }
    fi

    _cli_story_expect_fail \
        "collaboration bindings reject a blank role" \
        "role and bot UUID must not be empty" \
        PM collaboration create "$yaml_file" "--binding==${BOT_PM_UUID}" --driver "$BOT_PM_UUID"

    _cli_story_expect_fail \
        "collaboration bindings require a ROLE=BOT_UUID pair" \
        "expected ROLE=BOT_UUID" \
        PM collaboration create "$yaml_file" "--binding=planmerequals" --driver "$BOT_PM_UUID"

    _cli_story_expect_fail \
        "collaboration bindings reject duplicate roles" \
        "Duplicate --binding role: planner" \
        PM collaboration create "$yaml_file" \
        "--binding=planner=${BOT_PM_UUID}" "--binding=planner=${BOT_ENG_UUID}" --driver "$BOT_PM_UUID"

    _cli_story_expect_fail \
        "collaboration bindings must reference declared roles" \
        "undeclared participant role: ghost" \
        PM collaboration create "$yaml_file" \
        "--binding=ghost=${BOT_PM_UUID}" "--binding=planner=${BOT_PM_UUID}" --driver "$BOT_PM_UUID"

    _cli_story_expect_fail \
        "required collaboration roles must be bound" \
        "Missing --binding for participant role: reviewer" \
        PM collaboration create "$yaml_file" "--binding=planner=${BOT_PM_UUID}" --driver "$BOT_PM_UUID"

    _cli_story_expect_fail \
        "the driver must appear in a collaboration binding" \
        "Driver bot must appear in at least one --binding" \
        PM collaboration create "$yaml_file" "--binding=planner=${BOT_ENG_UUID}" \
        "--binding=reviewer=${BOT_QA_UUID}" --driver "$BOT_PM_UUID"

    local invalid_yaml_file
    invalid_yaml_file="$(bcs_cli_mktemp cli-story-invalid.XXXXXX)"
    printf 'name: x\nruntime:\n  kind: state_machine\n  state_machine:\n    version: 1\n    nodes: {}' > "$invalid_yaml_file"
    info "CLI story: bcs-cli collaboration create (invalid definition, expected failure)"
    if bcs_cli_json PM collaboration create "$invalid_yaml_file" \
        "--binding=planner=${BOT_PM_UUID}" "--binding=reviewer=${BOT_ENG_UUID}" \
        --driver "$BOT_PM_UUID"; then
        fail "invalid collaboration definition was accepted (unexpected success)"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        rm -f "$invalid_yaml_file" "$yaml_file"
        return 1
    fi
    pass "invalid collaboration definition is refused"
    TESTS_PASSED=$((TESTS_PASSED + 1))
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    assert_contains "invalid definition surfaces server validation errors" \
        "$BCS_CLI_STDOUT" '"valid":false'
    assert_contains "invalid definition names the failing path" "$BCS_CLI_STDOUT" '"errors"'
    rm -f "$invalid_yaml_file"

    _cli_story_run "operator persists the validated collaboration" PM collaboration create \
        "$yaml_file" "--binding=planner=${BOT_PM_UUID}" "--binding=reviewer=${BOT_ENG_UUID}" \
        --driver "$BOT_PM_UUID" --context "Terminal validation success $$" \
        --topic "CLI terminal validation story" || {
        rm -f "$yaml_file"
        return 1
    }
    local collab_group_id
    collab_group_id=$(json_path "$BCS_CLI_STDOUT" "id")
    assert_not_empty "validated collaboration create returns a group id" "$collab_group_id"
    [[ -n "$collab_group_id" ]] || return 1
    rm -f "$yaml_file"

    # Group-context policy validation before any persistence.
    _cli_story_create_pm_group "CLI context validation $$" || return 1
    local group_id="$CLI_STORY_GROUP_ID"
    local ctx_name="cli_terminal_ctx_$$"
    # RFC3339 window end relative to the run date (python3: BSD/GNU date differ).
    local window_valid_to
    window_valid_to="$(python3 -c 'import datetime; print((datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ"))')"

    _cli_story_expect_fail \
        "group-context visible-to must be JSON" \
        "Invalid --visible-to JSON" \
        PM group-context add --group-id "$group_id" --name "$ctx_name" --scope-level group \
        --content "c" --visible-to 'not-json'

    _cli_story_expect_fail \
        "group-context visible-to must be an object" \
        "--visible-to must be a JSON object" \
        PM group-context add --group-id "$group_id" --name "$ctx_name" --scope-level group \
        --content "c" --visible-to '["array"]'

    _cli_story_expect_fail \
        "group-context collect-from must be JSON" \
        "Invalid --collect-from JSON" \
        PM group-context add --group-id "$group_id" --name "$ctx_name" --scope-level group \
        --content "c" --visible-to '{}' --collect-from 'oops['

    _cli_story_expect_fail \
        "group-context owners must be JSON" \
        "Invalid --owners JSON" \
        PM group-context update --group-id "$group_id" --name "$ctx_name" --scope-level group \
        --change-reason r --owners 'nope'

    _cli_story_expect_fail \
        "group-context owners must contain userIds" \
        "--owners must be a JSON object containing a non-empty userIds array" \
        PM group-context update --group-id "$group_id" --name "$ctx_name" --scope-level group \
        --change-reason r --owners '{}'

    _cli_story_expect_fail \
        "group-context owners.userIds must not be an empty array" \
        "--owners.userIds must not be empty" \
        PM group-context update --group-id "$group_id" --name "$ctx_name" --scope-level group \
        --change-reason r --owners '{"userIds":[]}'

    _cli_story_expect_fail \
        "group-context owners must list non-empty actor ids" \
        "--owners.userIds must contain non-empty string actor ids" \
        PM group-context update --group-id "$group_id" --name "$ctx_name" --scope-level group \
        --change-reason r --owners '{"userIds":["  "]}'

    _cli_story_expect_fail \
        "list cannot combine --scope-level and --scope-levels" \
        "--scope-level and --scope-levels cannot be used together" \
        PM group-context list --group-id "$group_id" --scope-level group --scope-levels group,session

    _cli_story_expect_fail \
        "get cannot combine --scope-level and --scope-levels" \
        "--scope-level and --scope-levels cannot be used together" \
        PM group-context get --group-id "$group_id" --name "$ctx_name" \
        --scope-level group --scope-levels round

    _cli_story_run_terminal "operator adds a context with a validity window" PM group-context add \
        --group-id "$group_id" --name "$ctx_name" --scope-level group \
        --content "terminal window content" \
        --visible-to "{\"userIds\":[\"$BOT_PM_UUID\"]}" \
        --collect-from "{\"userIds\":[\"$BOT_ENG_UUID\"]}" \
        --valid-from 123 --valid-to "$window_valid_to" || {
        _cli_story_scrub_group "$group_id"
        return 1
    }
    assert_json_eq "context add with a window returns ok" "$BCS_CLI_STDOUT" "status" "ok"

    _cli_story_run_terminal "operator updates the context and rewrites its window" PM group-context update \
        --group-id "$group_id" --name "$ctx_name" --scope-level group \
        --content "terminal window updated" --change-reason "window drill" \
        --valid-from 456 --valid-to "$window_valid_to" || {
        _cli_story_scrub_group "$group_id"
        return 1
    }
    assert_json_eq "context window update returns ok" "$BCS_CLI_STDOUT" "status" "ok"

    _cli_story_run_terminal "operator reads the updated context" PM group-context get \
        --group-id "$group_id" --name "$ctx_name" --scope-level group || {
        _cli_story_scrub_group "$group_id"
        return 1
    }
    assert_contains "context read-back shows the updated content" \
        "$BCS_CLI_STDOUT" "terminal window updated"
    assert_contains "context read-back preserves the collect-from audience" \
        "$BCS_CLI_STDOUT" "$BOT_ENG_UUID"

    api_delete "/groups/${group_id}?bot_id=${BOT_PM_UUID}"
    require_status "context validation group is cleaned up" "200" || return 1
    if [[ -n "$collab_group_id" ]]; then
        api_delete "/groups/${collab_group_id}?bot_id=${BOT_PM_UUID}"
        require_status "validated collaboration group is cleaned up" "200"
    fi
}

#
# Critical assertions: client-side payload guards, the disabled-bridge
# refusal never leaking the client secret, terminal listing output.
story_cli_operator_uses_channel_contracts_in_terminal() {
    info "Story: an operator prepares and validates channel bindings in terminal mode"
    local channel_secret="cli-terminal-secret-not-real"

    _cli_story_expect_fail \
        "channel binding rejects an unknown target kind" \
        "unsupported target-kind enterprise; expected group or bot" \
        CEO channel bind --account "cli-terminal-$$" --target-kind enterprise --target-id "x" \
        --visibility full_transcript --env local --robot-code r --client-id c \
        --client-secret "$channel_secret" --send-mode normal --message-type text

    _cli_story_expect_fail \
        "streaming-card channel bindings require a template" \
        "--card-template-id is required when --send-mode=streaming_card" \
        CEO channel bind --account "cli-terminal-$$" --target-kind bot --target-id "$BOT_CEO_UUID" \
        --visibility full_transcript --env local --robot-code r --client-id c \
        --client-secret "$channel_secret" --send-mode streaming_card --message-type text

    _cli_story_expect_fail \
        "channel binding rejects an unknown send mode" \
        "unsupported send-mode carrier-pigeon; expected normal or streaming_card" \
        CEO channel bind --account "cli-terminal-$$" --target-kind bot --target-id "$BOT_CEO_UUID" \
        --visibility full_transcript --env local --robot-code r --client-id c \
        --client-secret "$channel_secret" --send-mode carrier-pigeon --message-type text

    info "CLI story: bcs-cli channel bind per_sender streaming_card (bridge disabled, expected failure)"
    if bcs_cli CEO channel bind --account "cli-terminal-$$" --target-kind bot \
        --target-id "$BOT_CEO_UUID" --visibility full_transcript --env local \
        --robot-code r --client-id c --client-secret "$channel_secret" \
        --send-mode streaming_card --card-template-id tpl-cli-story \
        --message-type markdown --group-chat-scope per_sender; then
        fail "channel bind unexpectedly succeeded without a channel provider"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        return 1
    fi
    if [[ "$BCS_CLI_STDERR" == *"channel bridge is disabled"* && "$BCS_CLI_STDERR" != *"$channel_secret"* ]]; then
        pass "channel bind returns the redacted disabled-bridge contract"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        fail "channel bind error is unexpected or leaked its secret: $(printf '%s' "$BCS_CLI_STDERR" | head -c 240)"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    _cli_story_run_terminal "operator lists channel bindings in terminal mode" CEO channel list || return 1
    assert_contains "terminal channel list reports the binding count" \
        "$BCS_CLI_STDOUT" "Channel bindings ("

    _cli_story_create_pm_group "CLI channel conversation story" || return 1
    local group_id="$CLI_STORY_GROUP_ID"
    _cli_story_run_terminal "operator creates a session for conversation lookup" PM session create \
        --group "$group_id" --title "Channel lookup" || return 1
    local session_id
    session_id=$(_cli_story_terminal_sid)
    assert_not_empty "channel lookup session is created" "$session_id"
    [[ -n "$session_id" ]] || {
        _cli_story_scrub_group "$group_id"
        return 1
    }

    _cli_story_run_terminal "operator resolves conversations in terminal mode" PM channel \
        conversation-id --session "$session_id" || {
        _cli_story_scrub_group "$group_id" "$session_id"
        return 1
    }
    assert_contains "terminal conversation lookup names the session" \
        "$BCS_CLI_STDOUT" "DingTalk conversations for session $session_id ("

    _cli_story_run_terminal "operator unbinds a missing binding in terminal mode" CEO channel unbind \
        --id "cli-terminal-missing-$$" || return 1
    assert_contains "terminal unbind acknowledges the no-op" \
        "$BCS_CLI_STDOUT" "✓ Channel binding deleted: cli-terminal-missing-$$"

    api_delete "/sessions/${session_id}?bot_id=${BOT_PM_UUID}"
    require_status "channel conversation session is cleaned up" "200" || return 1
    api_delete "/groups/${group_id}?bot_id=${BOT_PM_UUID}"
    require_status "channel conversation group is cleaned up" "200"
}

#
# Critical assertions:
#   - Terminal service summaries match the documented human lines.
#   - sid inference refuses missing/empty group prefixes with one message.
#   - connect enforces the actor-matching guard and persists the session file
#     (bot id + websocket URL) under BOT_DATA_DIR.
#   - The friend exchange finishes accepted and listed; a fresh agent starts
#     with an empty roster.
#   - Health for a reachable stack is structured-healthy; an unreachable
#     endpoint is structured-unhealthy with a non-zero exit.
story_cli_operator_diagnoses_service_and_registration_in_terminal() {
    info "Story: an operator reads service state and registers agents through the terminal"
    _cli_story_create_pm_group "CLI service terminal story" || return 1
    local group_id="$CLI_STORY_GROUP_ID"

    # --input accepts a literal JSON object or a `@file` reference; probe the
    # documented marker forms. The bad/marker cases fail client-side.
    local good_json_file bad_json_file
    good_json_file="$(bcs_cli_mktemp cli-story-svc-good.XXXXXX)"
    printf '{"k":"file"}' > "$good_json_file"
    bad_json_file="$(bcs_cli_mktemp cli-story-svc-bad.XXXXXX)"
    printf 'this is not json' > "$bad_json_file"

    _cli_story_expect_fail \
        "service invoke rejects a bare @ marker" \
        "'@' must be followed by a file path" \
        PM service invoke --group "$group_id" --input '@' --caller-id cli-story

    _cli_story_expect_fail \
        "service invoke rejects a missing --input file" \
        "Failed to read" \
        PM service invoke --group "$group_id" --input "@/tmp/cli-story-missing-$$.json" --caller-id cli-story

    _cli_story_expect_fail \
        "service invoke rejects an unreadable JSON file" \
        "Invalid JSON in $bad_json_file" \
        PM service invoke --group "$group_id" --input "@$bad_json_file" --caller-id cli-story

    _cli_story_expect_fail \
        "service invoke rejects a bad --meta file reference" \
        "--meta: Invalid JSON in $bad_json_file" \
        PM service invoke --group "$group_id" --input "@$good_json_file" \
        --meta "@$bad_json_file" --caller-id cli-story

    _cli_story_run_terminal "operator submits a service invocation in terminal mode" PM service invoke \
        --group "$group_id" --input "@$good_json_file" --meta '{"source":"terminal"}' \
        --caller-id "cli-terminal-story" --title "CLI terminal invocation" --detach || {
        rm -f "$good_json_file" "$bad_json_file"
        _cli_story_scrub_group "$group_id"
        return 1
    }
    rm -f "$good_json_file" "$bad_json_file"
    local service_sid
    service_sid=$(printf '%s' "$BCS_CLI_STDOUT" | sed -n 's/^Invocation submitted: //p' | head -1)
    assert_not_empty "terminal service invocation prints its sid" "$service_sid"
    assert_contains "terminal invocation summary names the group" \
        "$BCS_CLI_STDOUT" "Group:    $group_id"
    assert_contains "terminal invocation summary reports its kind" \
        "$BCS_CLI_STDOUT" "Kind:     service_invocation"
    [[ -n "$service_sid" ]] || {
        _cli_story_scrub_group "$group_id"
        return 1
    }

    _cli_story_run_terminal "operator reads the service summary in terminal mode" PM service status \
        "$service_sid" || { _cli_story_scrub_service_session "$service_sid" "$group_id"; return 1; }
    assert_contains "terminal service status names the session" \
        "$BCS_CLI_STDOUT" "Service session: $service_sid"

    _cli_story_run_terminal "operator resolves the service with an explicit --group" PM service status \
        "$service_sid" --group "$group_id" || { _cli_story_scrub_service_session "$service_sid" "$group_id"; return 1; }
    assert_contains "explicit --group keeps the canonical service identity" \
        "$BCS_CLI_STDOUT" "Service session: $service_sid"

    _cli_story_expect_fail \
        "service status rejects a sid without a group prefix" \
        "Cannot infer group from session id" \
        PM service status "no-colon-sid-$$"

    _cli_story_expect_fail \
        "service status rejects an empty group prefix" \
        "Cannot infer group from session id" \
        PM service status ":missing-group-$$"

    info "CLI story: bcs-cli service wait (bounded timeout)"
    if bcs_cli PM service wait "$service_sid" --timeout-ms 2500; then
        pass "service wait finished before the bounded timeout"
        TESTS_PASSED=$((TESTS_PASSED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
    else
        if [[ "$BCS_CLI_STDERR" == *"Timed out after 2500 ms waiting for service session $service_sid"* ]]; then
            pass "terminal service wait reports its precise bounded timeout"
            TESTS_PASSED=$((TESTS_PASSED + 1))
        else
            fail "terminal service wait failed unexpectedly: $(printf '%s' "$BCS_CLI_STDERR" | head -c 200)"
            TESTS_FAILED=$((TESTS_FAILED + 1))
        fi
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
    fi

    _cli_story_expect_fail \
        "status updates refuse invalid bot tokens" \
        "Status update failed (401 Unauthorized)" \
        "token:totally-bogus-token-$$" update-status --status active

    _cli_story_run_terminal "operator idles the status in terminal mode" PM update-status \
        --status idle --summary "terminal story idle" || { _cli_story_scrub_service_session "$service_sid" "$group_id"; return 1; }
    assert_eq "terminal status update is acknowledged" "$BCS_CLI_STDOUT" "✓ Status updated"

    _cli_story_run_terminal "operator reads the CEO profile in terminal mode" PM get "$BOT_CEO_UUID" || { _cli_story_scrub_service_session "$service_sid" "$group_id"; return 1; }
    assert_contains "terminal bot profile names the bot" "$BCS_CLI_STDOUT" "Bot: $BOT_CEO_UUID"
    assert_contains "terminal bot profile lists domains" "$BCS_CLI_STDOUT" "  Domains: "

    local agent_a_uuid agent_a_token agent_b_uuid agent_b_token temp_dir
    _cli_story_register_agent "cli-terminal-agent-$$-a" || { _cli_story_scrub_service_session "$service_sid" "$group_id"; return 1; }
    agent_a_uuid="$CLI_STORY_AGENT_UUID"
    agent_a_token="$CLI_STORY_AGENT_TOKEN"
    _cli_story_register_agent "cli-terminal-agent-$$-b" || {
        _cli_story_scrub_service_session "$service_sid" "$group_id"
        _cli_story_scrub_agents "" "$agent_a_uuid"
        return 1
    }
    agent_b_uuid="$CLI_STORY_AGENT_UUID"
    agent_b_token="$CLI_STORY_AGENT_TOKEN"

    info "CLI story: bcs-cli connect refuses a mismatched actor (expected failure)"
    if _cli_story_with_env "BCN_ACTOR_BOT_ID=$agent_a_uuid" -- \
        "token:$agent_b_token" connect --bot-id "$agent_b_uuid"; then
        fail "connect accepted a bot id that does not match BCN_ACTOR_BOT_ID"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        _cli_story_scrub_service_session "$service_sid" "$group_id"
        _cli_story_scrub_agents "" "$agent_a_uuid" "$agent_b_uuid"
        return 1
    fi
    if [[ "${BCS_CLI_STDERR:-}" == *"--bot-id must match BCN_ACTOR_BOT_ID when connecting"* ]]; then
        pass "connect refuses a bot id that does not match the actor"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        fail "connect mismatch error was unexpected: $(printf '%s' "${BCS_CLI_STDERR:-}" | head -c 200)"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    temp_dir="$(mktemp -d)"
    info "CLI story: bcs-cli connect persisted the local session contract"
    if _cli_story_with_env "BOT_DATA_DIR=$temp_dir" -- "token:$agent_a_token" connect; then
        pass "connect accepts a freshly registered agent"
        TESTS_PASSED=$((TESTS_PASSED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
    else
        fail "connect failed for a registered agent: $(printf '%s' "${BCS_CLI_STDERR:-}" | head -c 200)"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        _cli_story_scrub_service_session "$service_sid" "$group_id"
        _cli_story_scrub_agents "$temp_dir" "$agent_a_uuid" "$agent_b_uuid"
        return 1
    fi
    local session_file="$temp_dir/.bcs/session.json"
    if [[ -f "$session_file" ]]; then
        pass "connect persists a session file under BOT_DATA_DIR"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        fail "connect did not persist BOT_DATA_DIR/.bcs/session.json"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    if [[ -f "$session_file" ]]; then
        assert_contains "persisted session keeps the bot identity" \
            "$(cat "$session_file")" "$agent_a_uuid"
        assert_contains "persisted session keeps the websocket URL" \
            "$(cat "$session_file")" "ws://"
    fi

    _cli_story_run_terminal "operator protects the agent in terminal mode" \
        "token:$agent_a_token" visibility set --value protected || { _cli_story_scrub_service_session "$service_sid" "$group_id"; _cli_story_scrub_agents "$temp_dir" "$agent_a_uuid" "$agent_b_uuid"; return 1; }
    assert_contains "terminal visibility change is acknowledged" \
        "$BCS_CLI_STDOUT" "✓ Visibility set to 'protected'"

    _cli_story_run_terminal "fresh agent has no friends yet" "token:$agent_b_token" friend list || { _cli_story_scrub_service_session "$service_sid" "$group_id"; _cli_story_scrub_agents "$temp_dir" "$agent_a_uuid" "$agent_b_uuid"; return 1; }
    assert_contains "terminal friend list renders an empty roster" \
        "$BCS_CLI_STDOUT" "Friends (0):"

    _cli_story_run_terminal "peer requests friendship through the terminal" \
        "token:$agent_b_token" friend request --bot-uuid "$agent_a_uuid" || { _cli_story_scrub_service_session "$service_sid" "$group_id"; _cli_story_scrub_agents "$temp_dir" "$agent_a_uuid" "$agent_b_uuid"; return 1; }
    assert_contains "terminal friend request reports the target" \
        "$BCS_CLI_STDOUT" "✓ Friend request sent to $agent_a_uuid"
    local request_id
    request_id=$(printf '%s' "$BCS_CLI_STDOUT" | sed -n 's/^  Request ID: //p' | head -1)
    assert_not_empty "terminal friend request prints its Request ID" "$request_id"
    [[ -n "$request_id" ]] || {
        _cli_story_scrub_service_session "$service_sid" "$group_id"
        _cli_story_scrub_agents "$temp_dir" "$agent_a_uuid" "$agent_b_uuid"
        return 1
    }

    _cli_story_run_terminal "protected agent sees the pending request" "token:$agent_a_token" \
        friend requests --direction received || { _cli_story_scrub_service_session "$service_sid" "$group_id"; _cli_story_scrub_agents "$temp_dir" "$agent_a_uuid" "$agent_b_uuid"; return 1; }
    assert_contains "terminal pending list shows the request pair" \
        "$BCS_CLI_STDOUT" "$agent_b_uuid → $agent_a_uuid [pending] (id: $request_id)"
    assert_contains "terminal pending list carries the count header" \
        "$BCS_CLI_STDOUT" "Friend requests (1):"

    _cli_story_run_terminal "protected agent accepts the request in terminal mode" \
        "token:$agent_a_token" friend accept --request-id "$request_id" || { _cli_story_scrub_service_session "$service_sid" "$group_id"; _cli_story_scrub_agents "$temp_dir" "$agent_a_uuid" "$agent_b_uuid"; return 1; }
    assert_contains "terminal friend acceptance is acknowledged" \
        "$BCS_CLI_STDOUT" "✓ Friend request accepted"

    _cli_story_run_terminal "protected agent sees its new friend in terminal mode" \
        "token:$agent_a_token" friend list || { _cli_story_scrub_service_session "$service_sid" "$group_id"; _cli_story_scrub_agents "$temp_dir" "$agent_a_uuid" "$agent_b_uuid"; return 1; }
    assert_contains "terminal friend list shows the accepted peer" \
        "$BCS_CLI_STDOUT" "($agent_b_uuid)"

    # Endpoint diagnostics. _cli_story_with_env isolates the overrides so they
    # cannot leak into later stories in the same e2e shell.
    _cli_story_run_terminal "operator checks endpoint health" "" health || { _cli_story_scrub_service_session "$service_sid" "$group_id"; _cli_story_scrub_agents "$temp_dir" "$agent_a_uuid" "$agent_b_uuid"; return 1; }
    assert_contains "health reports the structured healthy status" \
        "$BCS_CLI_STDOUT" '"status":"healthy"'

    local down_rc=0
    _cli_story_with_env "BCS_API_BASE_URL=http://127.0.0.1:59999" -- "" health || down_rc=$?
    if [[ "$down_rc" -ne 0 && "${BCS_CLI_STDOUT:-}" == *'"status":"unhealthy"'* ]]; then
        pass "unreachable endpoints surface the structured unhealthy contract"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        fail "unreachable endpoint must report structured unhealthy (exit=$down_rc)"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    # Robustness on the healthy endpoint: a malformed TRACEPARENT and cookie
    # header must not change the health verdict.
    local robust_rc=0
    _cli_story_with_env "TRACEPARENT=garbage-not-a-trace" "BCS_COOKIE=cli-story-cookie" -- \
        "" health || robust_rc=$?
    if [[ "$robust_rc" -eq 0 && "${BCS_CLI_STDOUT:-}" == *'"status":"healthy"'* ]]; then
        pass "health tolerates a malformed TRACEPARENT and a cookie header"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        fail "health should tolerate malformed tracing/cookie input (exit=$robust_rc, stdout=$(printf '%s' "${BCS_CLI_STDOUT:-}" | head -c 120))"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    rm -rf "$temp_dir"
    api_delete "/bots/$agent_a_uuid"
    require_status "terminal story agent A is removed" "200"
    api_delete "/bots/$agent_b_uuid"
    require_status "terminal story agent B is removed" "200"

    api_delete "/sessions/${service_sid}?bot_id=$(urlencode "bot:${BOT_PM_UUID}")"
    require_status "terminal service session is cleaned up" "200" || return 1
    api_delete "/groups/${group_id}?bot_id=${BOT_PM_UUID}"
    require_status "terminal service group is cleaned up" "200"
}

#
# Critical assertions:
#   - Delivered chat exposes the completed state and the engine response.
#   - Cancellation of a terminal run is a documented no-op.
#   - The unknown-bot failure is a structured submit_failed result, exit 1.
#   - File lifecycle lines all match the terminal-mode contract, and the
#     downloaded bytes match the uploaded payload verbatim.
story_cli_operator_chats_and_shares_files_in_terminal() {
    info "Story: an operator chats and shares session files through the terminal"

    local marker="terminal direct chat $$"
    _cli_story_run "operator delivers a direct chat" PM chat \
        --bot-uuid "$BOT_ENG_UUID" --message "$marker" || return 1
    local delivered_run_id
    delivered_run_id=$(json_path "$BCS_CLI_STDOUT" "run_id")
    assert_not_empty "delivered chat returns a run id" "$delivered_run_id"
    assert_json_eq "delivered chat completes" "$BCS_CLI_STDOUT" "state" "completed"
    assert_contains "delivered chat carries the engine response" \
        "$BCS_CLI_STDOUT" "[from OpenAI-compatible Mock Model Server]"

    _cli_story_run "operator re-reads the delivered run" PM chat-run status \
        --run-id "$delivered_run_id" || return 1
    assert_contains "chat-run status keeps the run identity" \
        "$BCS_CLI_STDOUT" "\"run_id\":\"$delivered_run_id\""

    info "CLI story: bcs-cli chat-run cancel (terminal run no-op)"
    if bcs_cli PM chat-run cancel --run-id "$delivered_run_id"; then
        pass "cancelling a completed run follows the no-op contract"
        TESTS_PASSED=$((TESTS_PASSED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
    else
        fail "cancel of a completed run must not fail: $(printf '%s' "$BCS_CLI_STDERR" | head -c 160)"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        return 1
    fi
    assert_json_eq "completed-run cancellation stays completed" "$BCS_CLI_STDOUT" "state" "completed"

    _cli_story_run "operator opens a bounded wait-until chat" PM chat \
        --bot-uuid "$BOT_ENG_UUID" --message "terminal wait check $$" \
        --wait-until running --timeout-ms 15000 || return 1
    local waiting_run_id
    waiting_run_id=$(json_path "$BCS_CLI_STDOUT" "run_id")
    assert_not_empty "wait-until chat returns a run id" "$waiting_run_id"
    [[ -n "$waiting_run_id" ]] || return 1

    _cli_story_run "operator cancels the observed running chat" PM chat-run cancel \
        --run-id "$waiting_run_id" || return 1
    assert_json_eq "running chat is cancelled" "$BCS_CLI_STDOUT" "state" "cancelled"

    info "CLI story: bcs-cli chat unknown bot (expected failure)"
    if bcs_cli PM chat --bot-uuid "bot_cli_story_unknown_$$" --message "anyone?"; then
        fail "chat with an unknown bot unexpectedly succeeded"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        return 1
    fi
    if [[ "$BCS_CLI_EXIT" -eq 1 && "$BCS_CLI_STDOUT" == *'"submit_failed"'* && "$BCS_CLI_STDOUT" == *'chat_async failed (404)'* ]]; then
        pass "chat with an unknown bot returns the structured submit-failure contract"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        fail "unknown-bot chat must surface submit_failed and exit 1 (exit=$BCS_CLI_EXIT, stdout=$(printf '%s' "$BCS_CLI_STDOUT" | head -c 160))"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    local yaml_file
    yaml_file="$(bcs_cli_mktemp cli-story-preview.XXXXXX)"
    _cli_story_write_planner_yaml "$yaml_file"
    _cli_story_run "operator previews the definition panel" PM collaboration preview "$yaml_file" || {
        rm -f "$yaml_file"
        return 1
    }
    assert_contains "terminal preview renders the definition card" \
        "$BCS_CLI_STDOUT" "bcsPanel.CollaborationDefinitionCard"
    _cli_story_run "operator previews the definition in structured mode" PM collaboration preview \
        "$yaml_file" --title "Terminal preview story" || {
        rm -f "$yaml_file"
        return 1
    }
    assert_contains "structured preview keeps the snapshot" "$BCS_CLI_STDOUT" '"snapshot"'
    rm -f "$yaml_file"

    _cli_story_create_pm_group "CLI file sharing story" || return 1
    local group_id="$CLI_STORY_GROUP_ID"
    _cli_story_run_terminal "operator creates a file-sharing session" PM session create \
        --group "$group_id" --title "Terminal file story" || return 1
    local session_id
    session_id=$(_cli_story_terminal_sid)
    assert_not_empty "file story session is created" "$session_id"
    [[ -n "$session_id" ]] || {
        _cli_story_scrub_group "$group_id"
        return 1
    }

    local upload_file download_file file_id file_tail
    upload_file="$(bcs_cli_mktemp cli-story-upload.XXXXXX)"
    file_tail="terminal story upload payload $$"
    printf '%s' "$file_tail" > "$upload_file"
    _cli_story_run_terminal "operator uploads a session file" PM session file upload \
        --session "$session_id" --path "$upload_file" --mime text/plain || { _cli_story_scrub_group "$group_id" "$session_id" "$upload_file"; return 1; }
    file_id=$(printf '%s' "$BCS_CLI_STDOUT" | sed -n 's/.*Uploaded: \([^ ]*\) (.*$/\1/p' | head -1)
    assert_not_empty "terminal upload prints a file id" "$file_id"
    [[ -n "$file_id" ]] || { _cli_story_scrub_group "$group_id" "$session_id" "$upload_file"; return 1; }

    _cli_story_run_terminal "operator lists session files" PM session file list \
        --session "$session_id" || { _cli_story_scrub_group "$group_id" "$session_id" "$upload_file"; return 1; }
    assert_contains "terminal file list shows the upload" "$BCS_CLI_STDOUT" "$file_id"
    assert_contains "terminal file list reports sizes and status" \
        "$BCS_CLI_STDOUT" "bytes) [Ready]"

    download_file="$(bcs_cli_mktemp cli-story-download.XXXXXX)"
    _cli_story_run_terminal "operator downloads the file" PM session file download \
        --session "$session_id" --file-id "$file_id" --out "$download_file" || { _cli_story_scrub_group "$group_id" "$session_id" "$upload_file" "$download_file"; return 1; }
    assert_contains "terminal download names its output path" \
        "$BCS_CLI_STDOUT" "✓ Downloaded to"
    assert_contains "terminal download writes the payload verbatim" \
        "$(cat "$download_file")" "$file_tail"

    _cli_story_run_terminal "operator shares the file" PM session file share \
        --session "$session_id" --file-id "$file_id" --ttl 60 || { _cli_story_scrub_group "$group_id" "$session_id" "$upload_file" "$download_file"; return 1; }
    assert_contains "terminal share link announcement" "$BCS_CLI_STDOUT" "✓ Share link:"
    assert_contains "terminal share link carries a tokenized URL" \
        "$BCS_CLI_STDOUT" "shared-file/content?token="

    _cli_story_run_terminal "operator reads storage capabilities" PM session file capabilities \
        --session "$session_id" || { _cli_story_scrub_group "$group_id" "$session_id" "$upload_file" "$download_file"; return 1; }
    assert_contains "terminal capabilities report storage" "$BCS_CLI_STDOUT" '"storage"'

    _cli_story_run_terminal "operator deletes the file" PM session file delete \
        --session "$session_id" --file-id "$file_id" || { _cli_story_scrub_group "$group_id" "$session_id" "$upload_file" "$download_file"; return 1; }
    assert_contains "terminal delete acknowledges the file id" \
        "$BCS_CLI_STDOUT" "✓ Deleted: $file_id"

    rm -f "$upload_file" "$download_file"
    api_delete "/sessions/${session_id}?bot_id=${BOT_PM_UUID}"
    require_status "file story session is cleaned up" "200" || return 1
    api_delete "/groups/${group_id}?bot_id=${BOT_PM_UUID}"
    require_status "file story group is cleaned up" "200"
}

E2E_TESTS_STORIES+=(story_cli_operator_runs_a_session_in_terminal_mode)
E2E_TESTS_STORIES+=(story_cli_operator_validates_group_and_collaboration_config)
E2E_TESTS_STORIES+=(story_cli_operator_uses_channel_contracts_in_terminal)
E2E_TESTS_STORIES+=(story_cli_operator_diagnoses_service_and_registration_in_terminal)
E2E_TESTS_STORIES+=(story_cli_operator_chats_and_shares_files_in_terminal)
