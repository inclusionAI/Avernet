#!/bin/bash
# stories.sh — User-story-oriented BCS Adapter API E2E suite.
#
# The public test list intentionally exposes stories, not one case per route.
# Each story follows a real user/operator journey and validates both response
# contracts and observable state transitions. The step functions from the
# older domain files remain reusable implementation details; they are not
# registered as standalone default cases.

E2E_TESTS_STORIES=(
    "story_user_prepares_agent_network"
    "story_user_builds_trusted_team"
    "story_user_operates_group_workspace"
    "story_user_runs_and_shares_sessions"
    "story_user_has_direct_agent_conversation"
    "story_user_runs_structured_collaboration"
    "story_provider_operator_publishes_agent"
    "story_user_validates_external_channel_setup"
    "story_operator_coordinates_with_cli"
    "story_cli_operator_creates_custom_collaboration"
    "story_cli_operator_builds_collaboration_team"
    "story_cli_operator_runs_sessions_and_services"
    "story_cli_operator_validates_channel_management"
    "story_session_file_workspace"
)
if [[ -n "${BCS_E2E_MOCK_BASE_URL:-}" ]]; then
    E2E_TESTS_STORIES+=("story_provider_callback_survives_slow_judge")
fi

require_status() {
    local desc="$1" expected="$2"
    assert_status "$desc" "$expected"
    if [[ "$HTTP_STATUS" != "$expected" ]]; then
        warn "response: $(printf '%s' "$RESPONSE" | head -c 300)"
    fi
    [[ "$HTTP_STATUS" = "$expected" ]]
}

json_path() {
    local json="$1" path="$2"
    printf '%s' "$json" | python3 -c '
import json, sys
try:
    value = json.load(sys.stdin)
    for part in sys.argv[1].split("."):
        value = value[int(part)] if isinstance(value, list) else value[part]
    if value is None:
        print("")
    elif isinstance(value, (dict, list)):
        print(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    else:
        print(value)
except Exception:
    print("")
' "$path"
}

urlencode() {
    python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$1"
}

wait_for_mock_provider_method() {
    local method="$1" payload request
    for _ in $(seq 1 100); do
        payload=$(curl --noproxy '*' -fsS \
            "${BCS_E2E_MOCK_BASE_URL}/control/provider/requests" 2>/dev/null || true)
        request=$(printf '%s' "$payload" | python3 -c '
import json,sys
try:
    target=sys.argv[1]
    requests=json.load(sys.stdin).get("requests", [])
    match=next((item for item in requests if item.get("body", {}).get("method") == target), None)
    print(json.dumps(match, separators=(",", ":")) if match else "")
except Exception:
    print("")
' "$method")
        if [[ -n "$request" ]]; then
            printf '%s\n' "$request"
            return 0
        fi
        sleep 0.05
    done
    return 1
}

wait_for_mock_judge_start() {
    local payload started
    for _ in $(seq 1 100); do
        payload=$(curl --noproxy '*' -fsS \
            "${BCS_E2E_MOCK_BASE_URL}/control/judge/status" 2>/dev/null || true)
        started=$(json_path "$payload" "started")
        [[ "$started" == "True" || "$started" == "true" ]] && return 0
        sleep 0.05
    done
    return 1
}

state_machine_graph_node_field() {
    local graph="$1" node_id="$2" field="$3"
    printf '%s' "$graph" | python3 -c '
import json,sys
try:
    data=json.load(sys.stdin)
    node=next(item for item in data.get("nodes", []) if item.get("node_id") == sys.argv[1])
    value=node.get(sys.argv[2])
    print("" if value is None else value)
except Exception:
    print("")
' "$node_id" "$field"
}

wait_for_graph_node_sub_status() {
    local run_id="$1" node_id="$2" expected="$3" graph actual
    for _ in $(seq 1 100); do
        graph=$(curl --noproxy '*' -fsS \
            -H "X-Mock-User-Id: $BCS_MOCK_USER_ID" \
            -H "X-Mock-Nick-Name: $BCS_MOCK_USER_NICK_NAME" \
            "${BCS_API_BASE_URL}/state-machine-runs/${run_id}/graph" 2>/dev/null || true)
        actual=$(state_machine_graph_node_field "$graph" "$node_id" "sub_status")
        if [[ "$actual" == "$expected" ]]; then
            printf '%s\n' "$graph"
            return 0
        fi
        sleep 0.05
    done
    return 1
}

wait_for_state_machine_status() {
    local run_id="$1" expected="$2" view actual
    for _ in $(seq 1 100); do
        view=$(curl --noproxy '*' -fsS \
            -H "X-Mock-User-Id: $BCS_MOCK_USER_ID" \
            -H "X-Mock-Nick-Name: $BCS_MOCK_USER_NICK_NAME" \
            "${BCS_API_BASE_URL}/state-machine-runs/${run_id}" 2>/dev/null || true)
        actual=$(json_path "$view" "run.status")
        if [[ "$actual" == "$expected" ]]; then
            printf '%s\n' "$view"
            return 0
        fi
        sleep 0.05
    done
    return 1
}

provider_callback_with_timeout() {
    local provider_id="$1" runtime_token="$2" body="$3"
    local curl_args=(--noproxy '*' -s -o "$_RESPONSE_FILE" -D "$_RESPONSE_HEADERS_FILE"
        -w '%{http_code}' --max-time 1 -X POST
        -H "X-BCN-Provider-Id: ${provider_id}"
        -H "Authorization: Bearer ${runtime_token}"
        -H "Content-Type: application/json"
        -d "$body")
    HTTP_STATUS=$(curl "${curl_args[@]}" "${BCS_API_BASE_URL}/bot/events" 2>/dev/null) || HTTP_STATUS="000"
    RESPONSE=$(cat "$_RESPONSE_FILE")
    RESPONSE_HEADERS=$(cat "$_RESPONSE_HEADERS_FILE")
}

# User story: A signed-in user opens Avernet and prepares an owned agent for use.
#
# Flow:
#   Open the platform -> load advertised assets -> inspect the current identity
#   -> browse the agent directory -> register an agent -> onboard and review it
#   -> read it back -> remove the temporary agent.
#
# Critical assertions:
#   - Platform, manifest, asset, and identity contracts are complete and consistent.
#   - Registration returns usable credentials and onboarding persists capabilities.
#   - Administrative review is observable through an independent read-back.
#   - Removing the agent makes subsequent lookup return 404.
story_user_prepares_agent_network() {
    info "Story: user opens Avernet, inspects identity, and registers an owned agent"
    _story_platform_entrypoints || return
    test_actor_list
    test_actor_search
    test_actor_put_status
    test_bots_my
    test_bots_paged
    test_bots_query
    _story_register_and_onboard_owned_agent || return
}

_story_platform_entrypoints() {
    api_get "/health"
    require_status "platform health returns 200" "200" || return
    assert_json_eq "health status is ok" "$RESPONSE" "status" "ok"
    assert_json_eq "health service is bcs" "$RESPONSE" "service" "bcs"

    api_get "/manifest"
    require_status "manifest returns 200" "200" || return
    assert_json_eq "manifest schema version is 1" "$RESPONSE" "schema_version" "1"
    assert_json_not_empty "manifest environment is present" "$RESPONSE" "env"
    assert_json_not_empty "manifest exposes at least one bundle" "$RESPONSE" "bundles.0.name"
    local asset_url
    asset_url=$(json_path "$RESPONSE" "bundles.0.url")
    assert_not_empty "manifest bundle has an asset URL" "$asset_url"
    [[ -n "$asset_url" ]] || return

    api_get "$asset_url"
    require_status "advertised manifest asset is downloadable" "200" || return
    assert_contains "manifest asset uses JavaScript content type" "$RESPONSE_HEADERS" "application/javascript"
    local asset_bytes
    asset_bytes=$(printf '%s' "$RESPONSE" | wc -c | tr -d ' ')
    if [[ "$asset_bytes" -gt 1000 ]]; then
        pass "manifest asset is non-trivial (${asset_bytes} bytes)"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        fail "manifest asset is unexpectedly small (${asset_bytes} bytes)"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    api_get "/me"
    require_status "current identity returns 200" "200" || return
    assert_json_eq "current user_id matches mock identity" "$RESPONSE" "user_id" "$BCS_MOCK_USER_ID"
    assert_json_eq "current actor UUID is derived from staff id" "$RESPONSE" "actor_uuid" "human_${BCS_MOCK_USER_ID}"
    assert_json_eq "current nickname matches mock identity" "$RESPONSE" "nick_name" "$BCS_MOCK_USER_NICK_NAME"

    api_get "/me/repair-info"
    require_status "identity repair inspection returns 200" "200" || return
    assert_json_eq "repair inspection succeeds" "$RESPONSE" "ok" "true"
    assert_json_eq "repair inspection targets current actor" "$RESPONSE" "actor_uuid" "human_${BCS_MOCK_USER_ID}"

    api_get "/admin/secret/e2e-missing-secret"
    require_status "disabled local secret backend returns 503 without leaking a value" "503" || return
    assert_json_eq "disabled secret backend has stable error code" "$RESPONSE" "error" "unavailable"
}

_story_register_and_onboard_owned_agent() {
    api_get "/register/token"
    require_status "human obtains a register token" "200" || return
    local register_token expires_at
    register_token=$(json_path "$RESPONSE" "token")
    expires_at=$(json_path "$RESPONSE" "expires_at")
    assert_not_empty "register token is present" "$register_token"
    assert_not_empty "register token expiry is present" "$expires_at"
    [[ -n "$register_token" ]] || return

    local bot_name="story-agent-$$-$(date +%s)"
    local register_path="/register?token=$(urlencode "$register_token")&bot-name=$(urlencode "$bot_name")"
    api_post "$register_path"
    require_status "human registers a new agent" "200" || return
    local bot_uuid bot_token
    bot_uuid=$(json_path "$RESPONSE" "bot_uuid")
    bot_token=$(json_path "$RESPONSE" "bot_token")
    assert_not_empty "registration returns bot UUID" "$bot_uuid"
    assert_not_empty "registration returns bot token" "$bot_token"
    [[ -n "$bot_uuid" && -n "$bot_token" ]] || return

    api_request_headers POST "/bots/onboard" \
        "{\"name\":\"${bot_name}\",\"summary\":\"Story-owned support agent\",\"domains\":[\"support\"],\"skills\":[\"triage\"],\"scopes\":[\"local\"]}" \
        "X-BCS-Bot-Token: ${bot_token}"
    require_status "new agent onboards its capabilities" "200" || return
    assert_json_eq "onboard response identifies the new agent" "$RESPONSE" "bot_uuid" "$bot_uuid"
    assert_json_eq "onboard response confirms persistence" "$RESPONSE" "onboarded" "true"
    assert_json_eq "onboard response keeps the chosen name" "$RESPONSE" "name" "$bot_name"

    api_get "/onboard/url?token=$(urlencode "$bot_token")&name=$(urlencode "$bot_name")&summary=$(urlencode "Story-owned support agent")"
    require_status "user can obtain the browser onboarding URL" "200" || return
    assert_json_not_empty "browser onboarding URL is present" "$RESPONSE" "onboard_url"
    assert_contains "browser onboarding URL contains the registration path" "$RESPONSE" "/bcn/register"

    local admin_name="${bot_name}-admin"
    api_post "/admin/bots/onboard" \
        "{\"bot_id\":\"${bot_uuid}\",\"name\":\"${admin_name}\",\"summary\":\"Admin-reviewed agent\",\"domains\":[\"support\"],\"skills\":[\"triage\"],\"scopes\":[\"local\"]}"
    require_status "administrator can re-onboard the owned agent" "200" || return
    assert_json_eq "admin onboard targets the same bot" "$RESPONSE" "bot_uuid" "$bot_uuid"
    assert_json_eq "admin onboard persists the reviewed name" "$RESPONSE" "name" "$admin_name"

    api_get "/bots/${bot_uuid}"
    require_status "onboarded agent is queryable" "200" || return
    assert_json_eq "stored capability name matches admin review" "$RESPONSE" "capabilities.name" "$admin_name"

    api_delete "/bots/${bot_uuid}"
    require_status "owner can remove the temporary agent" "200" || return
    api_get "/bots/${bot_uuid}"
    require_status "removed agent is no longer visible" "404" || return
}

# User story: Agents establish and manage trusted collaboration relationships.
#
# Flow:
#   Befriend a public agent -> request access to a protected agent -> accept it
#   -> submit another protected request -> reject it -> inspect friends via API
#   and CLI.
#
# Critical assertions:
#   - Public friendship becomes visible without a pending approval step.
#   - Protected requests expose a concrete pending request that can be accepted.
#   - A rejected request never creates a friendship.
#   - API and CLI friend lists contain the expected agents after each decision.
story_user_builds_trusted_team() {
    info "Story: agents establish, accept, reject, and inspect trust relationships"
    test_friend_auto_accept_public
    test_friend_request_accept_protected
    test_friend_request_reject_protected
    test_list_friends
    test_friend_flow_via_cli
}

# User story: A user forms and operates an incident-response collaboration group.
#
# Flow:
#   Create a group -> manage members and visibility -> configure participant mode,
#   workspace, routing, and service settings -> exchange persistent and live messages
#   -> process an agent callback -> close the group -> confirm a proposed group.
#
# Critical assertions:
#   - Membership, labels, visibility, and participant identity changes are exact.
#   - Workspace and routing updates survive an independent read-back.
#   - Messages and callbacks produce routing or delivery results for the same group.
#   - Deleted groups return 404, while confirmed proposals create the requested group.
story_user_operates_group_workspace() {
    info "Story: a user forms a team, configures its workspace, and collaborates"
    # Reuse stable CRUD/CLI steps as implementation details of this story.
    test_create_group
    test_create_group_with_members
    test_get_group_detail
    test_list_groups
    test_add_member
    test_remove_member
    test_update_group_label
    test_update_group_visibility
    test_group_create_via_cli
    test_group_add_member_via_cli
    test_group_get_via_cli
    test_group_fuse_via_cli
    test_group_status_via_cli
    test_group_terminate_via_cli
    _story_group_workspace_round_trip || return
    _story_group_proposal_confirmation || return
}

_story_group_workspace_round_trip() {
    local body
    body="{\"driver_bot\":\"${BOT_CEO_UUID}\",\"label\":\"Incident response room\",\"participants\":[{\"bot_uuid\":\"${BOT_CEO_UUID}\",\"role\":\"driver\"},{\"bot_uuid\":\"${BOT_PM_UUID}\",\"role\":\"consultant\"},{\"bot_uuid\":\"${BOT_ENG_UUID}\",\"role\":\"consultant\"}]}"
    bot_post "/groups" CEO "$body"
    require_status "driver creates the incident response group" "200" || return
    local group_id
    group_id=$(json_path "$RESPONSE" "id")
    assert_not_empty "incident response group has an id" "$group_id"
    [[ -n "$group_id" ]] || return

    api_get "/groups/${group_id}"
    require_status "user reads the incident response group" "200" || return
    assert_json_eq "incident response group keeps its label" "$RESPONSE" "label" "Incident response room"

    bot_put "/groups/${group_id}/participants/${BOT_ENG_UUID}/mode" ENG '{"mode":"muted"}'
    require_status "participant mutes itself" "200" || return
    assert_json_eq "participant mode is muted" "$RESPONSE" "data.mode" "muted"
    assert_json_eq "participant mode response identifies the actor" "$RESPONSE" "data.actor_id" "$BOT_ENG_UUID"

    api_put "/groups/${group_id}/workspace" \
        '{"decisions":["ship the hotfix"],"notes":["customer impact contained"],"tasks":[],"audit_log":[]}'
    require_status "team updates its shared workspace" "200" || return
    assert_json_array_contains "workspace update stores the decision" "$RESPONSE" "workspace.decisions" "ship the hotfix"
    assert_json_array_contains "workspace update stores the note" "$RESPONSE" "workspace.notes" "customer impact contained"

    api_get "/groups/${group_id}/workspace"
    require_status "team reads the shared workspace" "200" || return
    assert_json_array_contains "workspace read returns the decision" "$RESPONSE" "decisions" "ship the hotfix"
    assert_json_array_contains "workspace read returns the note" "$RESPONSE" "notes" "customer impact contained"

    bot_put "/groups/${group_id}/routing-policy" CEO \
        "{\"mode\":\"structured\",\"default_bot_final_delivery\":\"inject_observers\",\"sender_routes\":{\"${BOT_CEO_UUID}\":[\"${BOT_PM_UUID}\"]}}"
    require_status "driver configures structured routing" "200" || return
    assert_json_eq "routing mode is persisted" "$RESPONSE" "routing_policy.mode" "structured"
    assert_json_eq "default delivery is persisted" "$RESPONSE" "routing_policy.default_bot_final_delivery" "inject_observers"

    api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
    require_status "group is exposed as a bounded service" "200" || return
    assert_json_eq "service group max concurrency is persisted" "$RESPONSE" "service_spec.max_concurrency" "2"
    assert_json_eq "group settings update returns stable status" "$RESPONSE" "status" "ok"

    api_post "/groups/${group_id}/messages" \
        "{\"sender\":\"${BOT_CEO_UUID}\",\"content\":\"Please validate the hotfix\",\"message_type\":\"bot\",\"role\":\"user\"}"
    require_status "human posts a persistent group message" "200" || return
    assert_json_not_empty "persistent message receives an id" "$RESPONSE" "message_id"
    assert_json_not_empty "persistent message records routing targets" "$RESPONSE" "routed_to"

    api_get "/groups/${group_id}/messages?limit=20"
    require_status "human reads group message history" "200" || return
    local history_is_array
    history_is_array=$(printf '%s' "$RESPONSE" | python3 -c 'import json,sys; print("1" if isinstance(json.load(sys.stdin), list) else "0")' 2>/dev/null || echo 0)
    assert_eq "group history response is a JSON array" "$history_is_array" "1"

    bot_post "/groups/${group_id}/chat" CEO \
        "{\"message\":\"@产品经理 please coordinate the release\",\"from\":\"${BOT_CEO_UUID}\"}"
    require_status "driver sends a live group chat message" "200" || return
    assert_json_eq "group chat response identifies the group" "$RESPONSE" "group_id" "$group_id"
    assert_json_not_empty "group chat reports delivery results" "$RESPONSE" "delivery_results"

    api_post "/groups/${group_id}/callback" \
        "{\"message\":\"Engineering validation finished\",\"mentions\":[\"${BOT_CEO_UUID}\"],\"metadata\":{\"source\":\"e2e\"}}"
    require_status "agent callback is delivered into the group" "200" || return
    assert_json_eq "callback response identifies the group" "$RESPONSE" "group_id" "$group_id"
    assert_json_not_empty "callback reports delivery results" "$RESPONSE" "delivery_results"

    api_delete "/groups/${group_id}?bot_id=${BOT_CEO_UUID}"
    require_status "driver closes the temporary incident group" "200" || return
    assert_json_eq "group deletion confirms the id" "$RESPONSE" "id" "$group_id"
    api_get "/groups/${group_id}"
    require_status "closed incident group is gone" "404" || return
}

_story_group_proposal_confirmation() {
    bot_post "/groups/request" CEO \
        "{\"topic\":\"Review release readiness\",\"suggested_participants\":[\"${BOT_PM_UUID}\",\"${BOT_QA_UUID}\"],\"suggested_driver\":\"${BOT_CEO_UUID}\"}"
    require_status "driver requests a release-readiness group" "200" || return
    assert_json_eq "group proposal is created" "$RESPONSE" "proposal_created" "true"
    local confirm_url confirm_path
    confirm_url=$(json_path "$RESPONSE" "confirm_url")
    assert_not_empty "group proposal returns a confirmation URL" "$confirm_url"
    [[ -n "$confirm_url" ]] || return
    confirm_path=$(python3 -c 'import sys,urllib.parse; u=urllib.parse.urlparse(sys.argv[1]); print(u.path)' "$confirm_url")

    api_get "$confirm_path"
    require_status "user previews the group confirmation page" "200" || return
    assert_contains "confirmation page shows the proposal topic" "$RESPONSE" "Review release readiness"
    assert_contains "confirmation page posts back to the same token" "$RESPONSE" "$confirm_path"

    api_post "$confirm_path"
    require_status "user confirms the proposed group" "200" || return
    assert_json_eq "confirmed proposal creates a group" "$RESPONSE" "created" "true"
    local group_id
    group_id=$(json_path "$RESPONSE" "group_id")
    assert_not_empty "confirmed proposal returns group id" "$group_id"
    [[ -n "$group_id" ]] || return

    api_get "/groups/${group_id}"
    require_status "confirmed group is queryable" "200" || return
    assert_json_eq "confirmed group uses the proposed driver" "$RESPONSE" "driver_bot" "$BOT_CEO_UUID"
    api_delete "/groups/${group_id}?bot_id=${BOT_CEO_UUID}"
    require_status "confirmed group is cleaned up" "200" || return
}

# User story: A team creates, shares, completes, and invokes collaboration sessions.
#
# Flow:
#   Create a group session -> list and inspect it -> share group/session invitations
#   -> join them -> manage session members and messages -> complete a chat session
#   -> expose the group as a service -> invoke and inspect a service session -> clean up
#   -> collect and uncollect a session and list collected sessions.
#
# Critical assertions:
#   - Invite tokens lead to the intended group or session and created sessions are queryable.
#   - Completion status and output survive an independent read-back.
#   - Service sessions preserve kind, group, title, and caller isolation.
#   - Cross-caller reads and use of the wrong completion API are rejected with 403.
#   - Collection (collect/uncollect/list) follows per-bot isolation and idempotency.
story_user_runs_and_shares_sessions() {
    info "Story: a team creates, shares, completes, and invokes collaboration sessions"
    test_group_session_via_cli
    test_group_invite_link
    test_session_invite_link
    test_bot_groups_of_bot
    test_session_invite_join
    test_session_lifecycle
    _story_complete_and_invoke_sessions || return
    # 收藏 (collection): a participant bot collects / uncollects a session and
    # lists collected sessions; non-participant collect is rejected. Exercises
    # POST/DELETE /sessions/{sid}/collect and the collected=true list filter.
    test_session_collection
}

_story_complete_and_invoke_sessions() {
    local group_body
    group_body="{\"driver_bot\":\"${BOT_PM_UUID}\",\"label\":\"Release service\",\"participants\":[{\"bot_uuid\":\"${BOT_PM_UUID}\",\"role\":\"driver\"},{\"bot_uuid\":\"${BOT_ENG_UUID}\",\"role\":\"worker\"}]}"
    bot_post "/groups" PM "$group_body"
    require_status "product manager creates a release service group" "200" || return
    local group_id
    group_id=$(json_path "$RESPONSE" "id")
    assert_not_empty "release service group has an id" "$group_id"
    [[ -n "$group_id" ]] || return

    local session_id
    session_id=$(_api_create_session "$group_id" "Release sign-off")
    assert_not_empty "release sign-off session is created" "$session_id"
    [[ -n "$session_id" ]] || return

    bot_post "/sessions/${session_id}/complete" PM \
        '{"output":{"summary":"release approved"}}'
    require_status "group driver completes the sign-off session" "200" || return
    assert_json_eq "completed session status is completed" "$RESPONSE" "status" "completed"
    assert_json_eq "completed session stores its output" "$RESPONSE" "output.summary" "release approved"

    api_get "/sessions/${session_id}"
    require_status "completed session can be read back" "200" || return
    assert_json_eq "read-back keeps completed status" "$RESPONSE" "status" "completed"
    assert_json_eq "read-back keeps completion output" "$RESPONSE" "output.summary" "release approved"

    api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
    require_status "release group is configured for service invocations" "200" || return
    assert_json_eq "service invocation concurrency is two" "$RESPONSE" "service_spec.max_concurrency" "2"

    bot_post "/services/${group_id}/sessions" PM \
        '{"caller_id":"release-pipeline-1","session_title":"Automated release audit","input":{"commit":"abc123"},"meta":{"source":"e2e"}}'
    require_status "release pipeline starts a service session" "202" || return
    local service_session_id
    service_session_id=$(json_path "$RESPONSE" "session_id")
    assert_not_empty "service invocation returns a session id" "$service_session_id"
    assert_json_eq "service invocation uses the service session kind" "$RESPONSE" "session_kind" "service_invocation"
    [[ -n "$service_session_id" ]] || return

    bot_get "/services/${group_id}/sessions/${service_session_id}" PM
    require_status "service caller reads its own session" "200" || return
    assert_json_eq "service session belongs to the release group" "$RESPONSE" "group_id" "$group_id"
    assert_json_eq "service session preserves the title" "$RESPONSE" "session_title" "Automated release audit"

    bot_get "/services/${group_id}/sessions/${service_session_id}" CEO
    require_status "different bot cannot read another caller's service session" "403" || return
    assert_json_eq "service session caller isolation returns forbidden" "$RESPONSE" "error" "forbidden"

    bot_post "/sessions/${service_session_id}/complete" PM '{"output":{"summary":"wrong endpoint"}}'
    require_status "service session rejects the chat-session completion API" "403" || return
    assert_json_eq "service completion guardrail returns forbidden" "$RESPONSE" "error" "forbidden"
    assert_contains "service completion guardrail identifies the wrong endpoint" "$RESPONSE" "service sessions cannot be completed via this endpoint"

    api_delete "/sessions/${session_id}?bot_id=${BOT_PM_UUID}"
    require_status "completed chat session is cleaned up" "200" || return
    api_delete "/sessions/${service_session_id}?bot_id=$(urlencode "bot:${BOT_PM_UUID}")"
    require_status "service session is cleaned up by its creator" "200" || return

    api_delete "/groups/${group_id}?bot_id=${BOT_PM_UUID}"
    require_status "release service group is cleaned up" "200" || return
}

# User story: One agent starts, observes, and cancels a direct conversation with another.
#
# Flow:
#   Attempt synchronous chat -> start an asynchronous chat -> inspect its run
#   -> cancel the run -> inspect the terminal state.
#
# Critical assertions:
#   - Synchronous chat accepts only delivery or the documented timeout contract.
#   - Async start returns stable run/session identifiers for the requested target agent.
#   - Run lookup preserves identity and exposes lifecycle state.
#   - Cancellation is acknowledged and remains terminal on a later read.
story_user_has_direct_agent_conversation() {
    info "Story: an agent starts a direct async conversation, inspects it, and cancels it"
    test_bot_chat_sync

    bot_post "/bots/${BOT_PM_UUID}/chat-async" CEO \
        '{"message":"Prepare a release checklist","timeout_ms":60000,"tags":["e2e","release"]}'
    require_status "agent starts an asynchronous direct conversation" "202" || return
    local run_id session_id
    run_id=$(json_path "$RESPONSE" "run_id")
    session_id=$(json_path "$RESPONSE" "session_id")
    assert_not_empty "async conversation returns a run id" "$run_id"
    assert_not_empty "async conversation returns a session id" "$session_id"
    assert_json_eq "async conversation targets product manager" "$RESPONSE" "bot_uuid" "$BOT_PM_UUID"
    [[ -n "$run_id" ]] || return

    bot_get "/chat/runs/${run_id}" CEO
    require_status "caller reads its direct chat run" "200" || return
    assert_json_eq "chat run read-back keeps run id" "$RESPONSE" "run_id" "$run_id"
    assert_json_not_empty "chat run exposes current state" "$RESPONSE" "state"

    bot_post "/chat/runs/${run_id}/cancel" CEO
    require_status "caller cancels the direct chat run" "200" || return
    assert_json_eq "chat run cancellation is acknowledged" "$RESPONSE" "cancelled" "true"
    assert_json_eq "cancelled chat run has cancelled state" "$RESPONSE" "state" "cancelled"

    bot_get "/chat/runs/${run_id}" CEO
    require_status "caller reads the cancelled chat run" "200" || return
    assert_json_eq "cancelled run stays cancelled" "$RESPONSE" "state" "cancelled"
    assert_json_eq "cancelled run is terminal" "$RESPONSE" "is_terminal" "true"
}

# User story: A user evolves and executes a template-based structured collaboration.
#
# Flow:
#   Browse templates -> open one -> create a state-machine group -> inspect and patch
#   its bound definition -> reject an unavailable upgrade -> start a run -> inspect
#   run, graph, and node state -> cancel the run -> remove the group.
#
# Critical assertions:
#   - Template identity, language, and authoring YAML match the request.
#   - Definition patches increment the version and preserve the revised instruction.
#   - Missing upgrades fail with 404 and identify the definition and target version.
#   - Run, graph, and node views refer to the same run, which remains aborted after cancel.
story_user_runs_structured_collaboration() {
    info "Story: user chooses a template, evolves a workflow, starts it, and cancels it"

    api_get "/collaboration/templates?lang=zh-CN"
    require_status "user lists collaboration templates" "200" || return
    assert_contains "template catalog includes guided answer" "$RESPONSE" "single-bot-guided-answer"

    api_get "/collaboration/templates/single-bot-guided-answer?lang=zh-CN&format=json"
    require_status "user opens a collaboration template" "200" || return
    assert_json_eq "template detail keeps requested id" "$RESPONSE" "id" "single-bot-guided-answer"
    assert_json_eq "template detail keeps requested language" "$RESPONSE" "lang" "zh-CN"
    assert_json_not_empty "template detail includes authoring YAML" "$RESPONSE" "yaml"

    local definition_yaml
    definition_yaml="name: E2E Guided Answer
participants:
  driver:
    bot_id: \"${BOT_CEO_UUID}\"
    required: true
runtime:
  kind: state_machine
  state_machine:
    graph_mode: acyclic
    nodes:
      answer:
        kind: bot_task
        display_name: Draft answer
        assignee:
          type: bot_binding
          binding: driver
        instruction: Draft a concise release recommendation.
        final_output: true"
    local create_body
    create_body=$(python3 -c '
import json,sys
print(json.dumps({
  "driver_bot": sys.argv[1],
  "label": "Structured release review",
  "participants": [
    {"bot_uuid": sys.argv[1]},
    {"bot_uuid": sys.argv[2]}
  ],
  "group_strategy": "state_machine",
  "collaboration_definition_yaml": sys.argv[3]
}, ensure_ascii=False))
' "$BOT_CEO_UUID" "$BOT_PM_UUID" "$definition_yaml")
    bot_post "/groups" CEO "$create_body"
    require_status "user creates a structured collaboration group" "200" || return
    local group_id
    group_id=$(json_path "$RESPONSE" "id")
    assert_not_empty "structured group has an id" "$group_id"
    [[ -n "$group_id" ]] || return

    api_get "/groups/${group_id}"
    require_status "user reads the structured collaboration group" "200" || return
    assert_json_eq "structured group records state-machine strategy" "$RESPONSE" "group_strategy" "state_machine"

    api_get "/groups/${group_id}/collaboration-definition"
    require_status "user reads the bound collaboration definition" "200" || return
    local definition_id base_version
    definition_id=$(json_path "$RESPONSE" "default_definition.id")
    base_version=$(json_path "$RESPONSE" "default_definition.version")
    assert_not_empty "bound definition has an id" "$definition_id"
    assert_not_empty "bound definition has a version" "$base_version"
    assert_json_eq "bound definition preserves original YAML" "$RESPONSE" "yaml_source" "original"
    [[ -n "$definition_id" && -n "$base_version" ]] || return

    local patched_yaml patch_body
    patched_yaml="${definition_yaml/Draft a concise release recommendation./Draft and verify a concise release recommendation.}"
    patch_body=$(python3 -c '
import json,sys
print(json.dumps({
  "base_definition": {"id": sys.argv[1], "version": int(sys.argv[2])},
  "definition_yaml": sys.argv[3]
}, ensure_ascii=False))
' "$definition_id" "$base_version" "$patched_yaml")
    api_patch "/groups/${group_id}/collaboration-definition" "$patch_body"
    require_status "user patches the collaboration definition" "200" || return
    local patched_version
    patched_version=$(json_path "$RESPONSE" "default_definition.version")
    assert_eq "definition patch increments version" "$patched_version" "$((base_version + 1))"
    assert_contains "definition patch persists revised instruction" "$RESPONSE" "Draft and verify"

    local missing_version upgrade_body
    missing_version=$((patched_version + 100))
    upgrade_body=$(python3 -c '
import json,sys
print(json.dumps({
  "base_definition": {"id": sys.argv[1], "version": int(sys.argv[2])},
  "target_definition": {"id": sys.argv[1], "version": int(sys.argv[3])}
}))
' "$definition_id" "$patched_version" "$missing_version")
    api_post "/groups/${group_id}/collaboration-definition/upgrade" "$upgrade_body"
    require_status "upgrade to an unpublished definition is rejected" "404" || return
    assert_contains "missing upgrade target identifies the definition" "$RESPONSE" "$definition_id"
    assert_contains "missing upgrade target identifies the version" "$RESPONSE" "@${missing_version}"

    api_post "/groups/${group_id}/state-machine-runs" \
        '{"input":{"question":"Is the release ready?"}}'
    require_status "user starts the bound state-machine workflow" "202" || return
    local run_id
    run_id=$(json_path "$RESPONSE" "run.run_id")
    assert_not_empty "state-machine start returns run id" "$run_id"
    assert_json_eq "state-machine run belongs to structured group" "$RESPONSE" "run.group_id" "$group_id"
    assert_json_not_empty "state-machine start returns node state" "$RESPONSE" "nodes.0.node_id"
    [[ -n "$run_id" ]] || return

    api_get "/state-machine-runs/${run_id}"
    require_status "user reads state-machine run status" "200" || return
    assert_json_eq "run status read-back keeps run id" "$RESPONSE" "run.run_id" "$run_id"
    assert_json_not_empty "run status exposes lifecycle state" "$RESPONSE" "run.status"

    api_get "/state-machine-runs/${run_id}/graph"
    require_status "user inspects state-machine graph" "200" || return
    assert_json_eq "graph identifies the active run" "$RESPONSE" "run.run_id" "$run_id"
    assert_json_eq "graph exposes the answer node" "$RESPONSE" "nodes.0.node_id" "answer"

    api_get "/state-machine-runs/${run_id}/nodes/answer"
    require_status "user inspects the active state-machine node" "200" || return
    assert_json_eq "node detail identifies answer node" "$RESPONSE" "node.node_id" "answer"
    assert_json_eq "node detail belongs to active run" "$RESPONSE" "node.run_id" "$run_id"

    api_post "/state-machine-runs/${run_id}/cancel" '{"reason":"E2E fixture completed"}'
    require_status "user cancels the state-machine run" "200" || return
    assert_json_eq "cancelled state-machine run is aborted" "$RESPONSE" "run.status" "aborted"

    api_get "/state-machine-runs/${run_id}"
    require_status "user reads cancelled state-machine run" "200" || return
    assert_json_eq "cancelled state-machine run remains aborted" "$RESPONSE" "run.status" "aborted"

    api_delete "/groups/${group_id}?bot_id=${BOT_CEO_UUID}"
    require_status "structured collaboration fixture is cleaned up" "200" || return
}

# Coverage-only user story: a Provider-backed state-machine node returns while
# its Judge is intentionally blocked. The callback must return before the
# caller's timeout, expose both running sub-statuses, and resume after release.
story_provider_callback_survives_slow_judge() {
    info "Story: an HTTP Provider callback survives a slow state-machine Judge"

    local control_status
    control_status=$(curl --noproxy '*' -s -o /dev/null -w '%{http_code}' \
        -X POST "${BCS_E2E_MOCK_BASE_URL}/control/reset" 2>/dev/null) || control_status="000"
    assert_eq "Provider/Judge mock resets before the story" "$control_status" "200"
    [[ "$control_status" == "200" ]] || return

    api_post "/providers" \
        "{\"name\":\"Slow Judge E2E Provider\",\"webhook_url\":\"${BCS_E2E_MOCK_BASE_URL}/provider/webhook\",\"auth\":{\"mode\":\"static_bearer\"},\"protocol_version\":\"2.0\",\"coordination\":{\"mode\":\"native_tool\"}}"
    require_status "operator registers the local HTTP Provider" "200" || return
    local provider_id admin_token bcs_token
    provider_id=$(json_path "$RESPONSE" "provider_id")
    admin_token=$(json_path "$RESPONSE" "provider_admin_token")
    bcs_token=$(json_path "$RESPONSE" "bcs_to_provider_token")
    assert_not_empty "local Provider receives an id" "$provider_id"
    assert_not_empty "local Provider receives an admin token" "$admin_token"
    [[ -n "$provider_id" && -n "$admin_token" ]] || return

    local provider_bot_ref="slow-judge-worker-$$-$(date +%s)"
    api_request_headers POST "/providers/${provider_id}/bots" \
        "{\"name\":\"Slow Judge Worker\",\"summary\":\"Returns before Judge completion\",\"owners\":[\"${BCS_MOCK_USER_ID}\"],\"provider_bot_ref\":\"${provider_bot_ref}\",\"domains\":[\"release\"],\"skills\":[\"review\"],\"scopes\":[\"local\"]}" \
        "Authorization: Bearer ${admin_token}"
    require_status "operator publishes the slow-Judge Provider bot" "200" || return
    local provider_bot_uuid runtime_token
    provider_bot_uuid=$(json_path "$RESPONSE" "bot_uuid")
    runtime_token=$(json_path "$RESPONSE" "bot_runtime_token")
    assert_not_empty "slow-Judge Provider bot receives a BCS id" "$provider_bot_uuid"
    assert_not_empty "slow-Judge Provider bot receives a runtime token" "$runtime_token"
    [[ -n "$provider_bot_uuid" && -n "$runtime_token" ]] || return

    # Group membership enforces the same trust boundary for Provider-backed
    # bots as it does for native bots. Make the fixture explicitly public and
    # establish its relationship with the driver before creating the group.
    api_put "/bots/${provider_bot_uuid}/visibility" '{"visibility":"public"}'
    require_status "operator makes the slow-Judge Provider bot discoverable" "200" || return
    api_post "/friends/request" \
        "{\"from_bot\":\"${BOT_CEO_UUID}\",\"to_bot\":\"${provider_bot_uuid}\"}"
    if [[ "$HTTP_STATUS" == "200" || "$HTTP_STATUS" == "201" ]]; then
        pass "driver befriends the slow-Judge Provider bot"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        fail "driver befriends the slow-Judge Provider bot (expected 200/201, actual=${HTTP_STATUS})"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    [[ "$HTTP_STATUS" == "200" || "$HTTP_STATUS" == "201" ]] || return
    api_get "/bots/${BOT_CEO_UUID}/friends"
    require_status "driver reads friends after Provider trust setup" "200" || return
    assert_contains "driver trust list contains the slow-Judge Provider bot" \
        "$RESPONSE" "$provider_bot_uuid"

    local definition_yaml create_body
    definition_yaml="name: HTTP Provider Slow Judge E2E
participants:
  worker:
    required: true
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    nodes:
      review:
        kind: bot_task
        display_name: Review
        assignee:
          type: bot_binding
          binding: worker
        instruction: Produce a candidate response.
        judge:
          type: llm
          criteria:
            - The candidate is suitable for publishing.
          outcomes: [approved]
        transitions:
          approved:
            targets: [publish]
      publish:
        kind: bot_task
        display_name: Publish
        assignee:
          type: bot_binding
          binding: worker
        instruction: Publish the approved response.
        final_output: true"
    create_body=$(python3 -c '
import json,sys
print(json.dumps({
  "driver_bot": sys.argv[1],
  "label": "HTTP Provider slow Judge E2E",
  "group_strategy": "state_machine",
  "participant_bindings": {
    "worker": {"source": "manual", "bot_ids": [sys.argv[2]]}
  },
  "participants": [
    {"bot_uuid": sys.argv[1]},
    {"bot_uuid": sys.argv[2]}
  ],
  "collaboration_definition_yaml": sys.argv[3]
}, ensure_ascii=False))
' "$BOT_CEO_UUID" "$provider_bot_uuid" "$definition_yaml")
    api_post "/groups" "$create_body"
    require_status "user creates a judged Provider state-machine group" "200" || return
    local group_id
    group_id=$(json_path "$RESPONSE" "id")
    assert_not_empty "judged Provider group receives an id" "$group_id"
    [[ -n "$group_id" ]] || return

    curl --noproxy '*' -fsS -X POST \
        "${BCS_E2E_MOCK_BASE_URL}/control/provider/clear" >/dev/null || return
    api_post "/groups/${group_id}/state-machine-runs" \
        '{"input":{"question":"verify asynchronous callback judging"}}'
    require_status "user starts the judged Provider workflow" "202" || return
    local run_id
    run_id=$(json_path "$RESPONSE" "run.run_id")
    assert_not_empty "judged Provider workflow returns a run id" "$run_id"
    [[ -n "$run_id" ]] || return

    local review_request review_provider_run_id graph
    review_request=$(wait_for_mock_provider_method "chat.send" || true)
    assert_not_empty "HTTP Provider receives the review task" "$review_request"
    [[ -n "$review_request" ]] || return
    review_provider_run_id=$(json_path "$review_request" "body.id")
    assert_not_empty "review task carries a Provider run id" "$review_provider_run_id"
    assert_json_eq "Provider delivery uses its callback credential" "$review_request" \
        "authorization" "Bearer ${bcs_token}"
    [[ -n "$review_provider_run_id" ]] || return

    graph=$(wait_for_graph_node_sub_status \
        "$run_id" "review" "awaiting_response" || true)
    assert_not_empty "running node exposes awaiting_response" "$graph"
    [[ -n "$graph" ]] || return
    assert_eq "review node remains durably running before its reply" \
        "$(state_machine_graph_node_field "$graph" "review" "status")" "running"

    curl --noproxy '*' -fsS -X POST \
        "${BCS_E2E_MOCK_BASE_URL}/control/provider/clear" >/dev/null || return
    provider_callback_with_timeout "$provider_id" "$runtime_token" \
        "{\"run_id\":\"${review_provider_run_id}\",\"state\":\"final\",\"message\":{\"text\":\"candidate awaiting slow judge\"}}"
    if ! require_status "Provider final callback returns before its one-second timeout" "200"; then
        curl --noproxy '*' -fsS -X POST \
            "${BCS_E2E_MOCK_BASE_URL}/control/judge/release" >/dev/null || true
        return
    fi

    if wait_for_mock_judge_start; then
        pass "slow Judge receives the candidate after callback acceptance"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        fail "slow Judge did not receive the candidate"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        return
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    graph=$(wait_for_graph_node_sub_status "$run_id" "review" "judging" || true)
    assert_not_empty "running node exposes judging while Judge is blocked" "$graph"
    [[ -n "$graph" ]] || return
    assert_eq "review node remains durably running during judging" \
        "$(state_machine_graph_node_field "$graph" "review" "status")" "running"

    api_get "/state-machine-runs/${run_id}/nodes/review"
    require_status "user reads the node while Judge is blocked" "200" || return
    assert_json_eq "node detail exposes judging" "$RESPONSE" \
        "sub_status" "judging"
    assert_json_eq "node detail persists the returned Provider artifact" "$RESPONSE" \
        "node.artifact_text" "candidate awaiting slow judge"

    curl --noproxy '*' -fsS -X POST \
        "${BCS_E2E_MOCK_BASE_URL}/control/judge/release" >/dev/null || return
    local publish_request publish_provider_run_id
    publish_request=$(wait_for_mock_provider_method "chat.send" || true)
    assert_not_empty "HTTP Provider receives the publish task after Judge release" "$publish_request"
    [[ -n "$publish_request" ]] || return
    publish_provider_run_id=$(json_path "$publish_request" "body.id")
    assert_not_empty "publish task carries a Provider run id" "$publish_provider_run_id"
    [[ -n "$publish_provider_run_id" ]] || return

    api_request_headers POST "/bot/events" \
        "{\"run_id\":\"${publish_provider_run_id}\",\"state\":\"final\",\"message\":{\"text\":\"published after slow judge\"}}" \
        "X-BCN-Provider-Id: ${provider_id}" \
        "Authorization: Bearer ${runtime_token}"
    require_status "publish callback is accepted" "200" || return

    local completed_view
    completed_view=$(wait_for_state_machine_status "$run_id" "completed" || true)
    assert_not_empty "Provider workflow completes after Judge release" "$completed_view"
    [[ -n "$completed_view" ]] || return
    assert_json_eq "completed workflow keeps the published output" "$completed_view" \
        "run.output" "published after slow judge"
    assert_json_eq "completed workflow records the approved Judge outcome" "$completed_view" \
        "judge_outputs.0.decision.outcome" "approved"

    api_delete "/groups/${group_id}?bot_id=${BOT_CEO_UUID}"
    require_status "slow-Judge Provider group is cleaned up" "200" || return
    api_request_headers DELETE "/providers/${provider_id}/bots/${provider_bot_ref}" "" \
        "Authorization: Bearer ${admin_token}"
    require_status "slow-Judge Provider bot is retired" "200" || return
}

# User story: A provider operator publishes, governs, and retires a provider-backed agent.
#
# Flow:
#   Register a provider -> configure stream rollout -> inspect and update metadata
#   -> publish and list an agent -> exercise runtime callbacks and guardrails
#   -> disable and re-enable the provider -> retire the agent -> restore rollout state.
#
# Critical assertions:
#   - Provider registration returns admin, callback, and runtime credentials as applicable.
#   - Provider and agent identities remain stable across update and list operations.
#   - Unknown callbacks and unauthorized delivery takeover fail with precise errors.
#   - Disable/enable and retirement are observable, and temporary rollout state is restored.
story_provider_operator_publishes_agent() {
    info "Story: provider operator registers a provider, publishes an agent, and retires it"

    api_post "/providers" \
        '{"name":"E2E Provider","webhook_url":"https://provider.example.com/bcs/webhook","auth":{"mode":"static_bearer"},"protocol_version":"2.0","coordination":{"mode":"native_tool"}}'
    require_status "operator registers a Provider 2.0 integration" "200" || return
    local provider_id admin_token bcs_token
    provider_id=$(json_path "$RESPONSE" "provider_id")
    admin_token=$(json_path "$RESPONSE" "provider_admin_token")
    bcs_token=$(json_path "$RESPONSE" "bcs_to_provider_token")
    assert_not_empty "provider registration returns provider id" "$provider_id"
    assert_not_empty "provider registration returns admin token" "$admin_token"
    assert_not_empty "provider registration returns BCS callback token" "$bcs_token"
    [[ -n "$provider_id" && -n "$admin_token" ]] || return

    api_get "/providers/stream-gray"
    require_status "operator reads provider streaming rollout" "200" || return
    assert_json_not_empty "stream rollout exposes enabled flag" "$RESPONSE" "enabled"

    api_put "/providers/stream-gray" \
        "{\"enabled\":true,\"created_by\":[\"${BCS_MOCK_USER_ID}\"]}"
    require_status "operator enables streaming for the current owner" "200" || return
    assert_json_eq "stream rollout is enabled" "$RESPONSE" "enabled" "true"
    assert_json_array_contains "stream rollout contains current owner" "$RESPONSE" "created_by" "$BCS_MOCK_USER_ID"

    api_request_headers GET "/providers/${provider_id}" "" \
        "Authorization: Bearer ${admin_token}"
    require_status "operator reads provider metadata" "200" || return
    assert_json_eq "provider metadata keeps id" "$RESPONSE" "provider_id" "$provider_id"
    assert_json_eq "provider metadata keeps protocol auth mode" "$RESPONSE" "auth_mode" "static_bearer"
    assert_json_eq "provider starts enabled" "$RESPONSE" "disabled" "false"

    api_request_headers PATCH "/providers/${provider_id}" \
        '{"name":"E2E Provider Updated","protocol_version":"2.0"}' \
        "Authorization: Bearer ${admin_token}"
    require_status "operator updates provider metadata" "200" || return
    assert_json_eq "provider name update is persisted" "$RESPONSE" "name" "E2E Provider Updated"
    assert_json_eq "provider id is stable across update" "$RESPONSE" "provider_id" "$provider_id"

    local provider_bot_ref="reviewer-$$-$(date +%s)"
    api_request_headers POST "/providers/${provider_id}/bots" \
        "{\"name\":\"Provider Review Agent\",\"summary\":\"Reviews release plans\",\"owners\":[\"${BCS_MOCK_USER_ID}\"],\"provider_bot_ref\":\"${provider_bot_ref}\",\"domains\":[\"release\"],\"skills\":[\"review\"],\"scopes\":[\"local\"]}" \
        "Authorization: Bearer ${admin_token}"
    require_status "operator publishes a provider-backed agent" "200" || return
    local provider_bot_uuid runtime_token
    provider_bot_uuid=$(json_path "$RESPONSE" "bot_uuid")
    runtime_token=$(json_path "$RESPONSE" "bot_runtime_token")
    assert_not_empty "provider agent receives BCS bot id" "$provider_bot_uuid"
    assert_not_empty "static-bearer agent receives runtime token" "$runtime_token"
    assert_json_eq "provider agent keeps provider ref" "$RESPONSE" "provider_bot_ref" "$provider_bot_ref"
    [[ -n "$provider_bot_uuid" && -n "$runtime_token" ]] || return

    _story_provider_manages_organization "$provider_id" "$admin_token" "$provider_bot_uuid" || return

    api_request_headers GET "/providers/${provider_id}/bots" "" \
        "Authorization: Bearer ${admin_token}"
    require_status "operator lists provider-backed agents" "200" || return
    local listed_bot
    listed_bot=$(printf '%s' "$RESPONSE" | python3 -c '
import json,sys
d=json.load(sys.stdin)
target=sys.argv[1]
print("1" if any(i.get("bot_uuid") == target for i in d.get("items", [])) else "0")
' "$provider_bot_uuid" 2>/dev/null || echo 0)
    assert_eq "published provider agent appears in provider list" "$listed_bot" "1"

    api_request_headers POST "/providers/agentpass/resolve" '{}' \
        "X-BCN-Provider-Id: ${provider_id}" \
        "Authorization: Bearer ${runtime_token}"
    require_status "agentpass lookup handles a non-agentpass credential safely" "200" || return
    assert_json_eq "non-agentpass credential resolves no agent code" "$RESPONSE" "agent_code" "null"
    assert_json_eq "non-agentpass credential resolves no provider binding" "$RESPONSE" "provider_bot_binding" "null"

    local missing_run="missing-provider-run-$$"
    api_request_headers POST "/bot/events" \
        "{\"run_id\":\"${missing_run}\",\"state\":\"final\",\"message\":{\"text\":\"late provider result\"}}" \
        "X-BCN-Provider-Id: ${provider_id}" \
        "Authorization: Bearer ${runtime_token}"
    require_status "late provider callback is rejected for an unknown run" "404" || return
    assert_json_eq "late provider callback reports run_not_found" "$RESPONSE" "error" "run_not_found"

    api_request_headers POST "/bot/events/coordination" \
        "{\"run_id\":\"${missing_run}\",\"tool_call_id\":\"tool-1\",\"kind\":\"coordination_intent\",\"intent\":{\"v\":1,\"tool\":\"bcs_send_task_message\",\"arguments\":{\"message\":\"done\"}}}" \
        "X-BCN-Provider-Id: ${provider_id}" \
        "Authorization: Bearer ${runtime_token}"
    require_status "late coordination callback is rejected for an unknown run" "404" || return
    assert_json_eq "late coordination callback reports run_not_found" "$RESPONSE" "error" "run_not_found"

    api_request_headers POST "/providers/${provider_id}/delivery/switch-bot" \
        "{\"bot_id\":\"${BOT_QA_UUID}\",\"provider_bot_ref\":\"${provider_bot_ref}\"}" \
        "Authorization: Bearer ${admin_token}"
    require_status "unapproved provider cannot take over an existing bot" "403" || return
    assert_contains "delivery switch guardrail names provider allow-list" "$RESPONSE" "not allowed to switch bot delivery"

    api_request_headers POST "/providers/${provider_id}/disable" "" \
        "Authorization: Bearer ${admin_token}"
    require_status "operator disables the provider" "200" || return
    assert_json_eq "provider disable is persisted" "$RESPONSE" "disabled" "true"

    api_request_headers POST "/providers/${provider_id}/enable" "" \
        "Authorization: Bearer ${admin_token}"
    require_status "operator re-enables the provider" "200" || return
    assert_json_eq "provider enable is persisted" "$RESPONSE" "disabled" "false"

    api_request_headers DELETE "/providers/${provider_id}/bots/${provider_bot_ref}" "" \
        "Authorization: Bearer ${admin_token}"
    require_status "operator retires the provider-backed agent" "200" || return
    assert_json_eq "provider agent deletion is acknowledged" "$RESPONSE" "deleted" "true"
    assert_json_eq "provider agent deletion keeps provider ref" "$RESPONSE" "provider_bot_ref" "$provider_bot_ref"

    api_request_headers GET "/providers/${provider_id}/bots" "" \
        "Authorization: Bearer ${admin_token}"
    require_status "operator verifies provider agent retirement" "200" || return
    assert_json_eq "provider agent list is empty after retirement" "$RESPONSE" "items" "[]"

    api_put "/providers/stream-gray" '{"enabled":false,"created_by":[]}'
    require_status "operator restores provider streaming rollout" "200" || return
    assert_json_eq "stream rollout is restored to disabled" "$RESPONSE" "enabled" "false"
    assert_json_eq "stream rollout owner list is cleared" "$RESPONSE" "created_by" "[]"
}

_story_provider_manages_organization() {
    local provider_id="$1" admin_token="$2" provider_bot_uuid="$3"
    local organization_code="e2e-org-$$-$(date +%s)"

    api_request_headers POST "/providers/${provider_id}/organizations" \
        "{\"organization_code\":\"${organization_code}\",\"name\":\"E2E release organization\",\"description\":\"provider-managed release team\"}" \
        "Authorization: Bearer ${admin_token}"
    require_status "provider creates an organization" "200" || return
    assert_json_eq "organization creation keeps its code" "$RESPONSE" "organization_code" "$organization_code"

    api_request_headers GET "/providers/${provider_id}/organizations" "" \
        "Authorization: Bearer ${admin_token}"
    require_status "provider lists its organizations" "200" || return
    local listed_organization
    listed_organization=$(printf '%s' "$RESPONSE" | python3 -c '
import json,sys
target=sys.argv[1]
print("1" if any(item.get("organization_code") == target for item in json.load(sys.stdin).get("organizations", [])) else "0")
' "$organization_code" 2>/dev/null || echo 0)
    assert_eq "organization list includes the created organization" "$listed_organization" "1"

    api_request_headers GET "/organizations/${organization_code}" "" \
        "Authorization: Bearer ${admin_token}"
    require_status "provider reads its organization" "200" || return
    assert_json_eq "organization read keeps its name" "$RESPONSE" "name" "E2E release organization"

    api_request_headers PATCH "/organizations/${organization_code}" \
        '{"name":"E2E release organization updated"}' \
        "Authorization: Bearer ${admin_token}"
    require_status "provider updates its organization" "200" || return
    assert_json_eq "organization update is persisted" "$RESPONSE" "name" "E2E release organization updated"

    api_request_headers GET "/providers/${provider_id}/organization-candidate-bots?organization_code=${organization_code}&q=Provider" "" \
        "Authorization: Bearer ${admin_token}"
    require_status "provider finds candidate bots for its organization" "200" || return
    local candidate_bot
    candidate_bot=$(printf '%s' "$RESPONSE" | python3 -c '
import json,sys
target=sys.argv[1]
print("1" if any(item.get("bot_uuid") == target for item in json.load(sys.stdin).get("bots", [])) else "0")
' "$provider_bot_uuid" 2>/dev/null || echo 0)
    assert_eq "organization candidates include the provider bot" "$candidate_bot" "1"

    api_request_headers GET "/providers/${provider_id}/organization-candidate-bots/${provider_bot_uuid}?organization_code=${organization_code}" "" \
        "Authorization: Bearer ${admin_token}"
    require_status "provider reads candidate bot details before adding it" "200" || return
    assert_json_eq "candidate detail reports a non-member before adding it" "$RESPONSE" "is_member" "false"
    assert_json_eq "candidate detail keeps the requested bot id" "$RESPONSE" "bot_uuid" "$provider_bot_uuid"

    api_request_headers PUT "/organizations/${organization_code}/members/${provider_bot_uuid}" \
        '{"role":"reviewer"}' \
        "Authorization: Bearer ${admin_token}"
    require_status "provider adds its bot to the organization" "200" || return
    assert_json_eq "organization member keeps its role" "$RESPONSE" "role" "reviewer"

    api_request_headers GET "/providers/${provider_id}/organization-candidate-bots?organization_code=${organization_code}&q=Provider" "" \
        "Authorization: Bearer ${admin_token}"
    require_status "provider lists candidates after adding the bot" "200" || return
    candidate_bot=$(printf '%s' "$RESPONSE" | python3 -c '
import json,sys
target=sys.argv[1]
print("1" if any(item.get("bot_uuid") == target for item in json.load(sys.stdin).get("bots", [])) else "0")
' "$provider_bot_uuid" 2>/dev/null || echo 0)
    assert_eq "organization candidates exclude the active member" "$candidate_bot" "0"

    api_request_headers GET "/providers/${provider_id}/organization-candidate-bots/${provider_bot_uuid}?organization_code=${organization_code}" "" \
        "Authorization: Bearer ${admin_token}"
    require_status "provider reads candidate bot details after adding it" "200" || return
    assert_json_eq "candidate detail reports an active member" "$RESPONSE" "is_member" "true"

    api_request_headers PATCH "/organizations/${organization_code}/members/${provider_bot_uuid}/profile" \
        '{"name":"E2E organization bot"}' \
        "Authorization: Bearer ${admin_token}"
    require_status "provider updates an organization member profile" "200" || return
    assert_json_eq "organization member profile update is persisted" "$RESPONSE" "profile.name" "E2E organization bot"

    api_request_headers GET "/organizations/${organization_code}/members/${provider_bot_uuid}" "" \
        "Authorization: Bearer ${admin_token}"
    require_status "provider reads an organization member" "200" || return
    assert_json_eq "organization member read keeps the bot id" "$RESPONSE" "bot_uuid" "$provider_bot_uuid"

    api_request_headers GET "/organizations/${organization_code}/members?role=reviewer" "" \
        "Authorization: Bearer ${admin_token}"
    require_status "provider lists organization members by role" "200" || return
    local listed_member
    listed_member=$(printf '%s' "$RESPONSE" | python3 -c '
import json,sys
target=sys.argv[1]
print("1" if any(item.get("bot_uuid") == target for item in json.load(sys.stdin).get("members", [])) else "0")
' "$provider_bot_uuid" 2>/dev/null || echo 0)
    assert_eq "organization member list includes the provider bot" "$listed_member" "1"

    api_request_headers POST "/organizations/${organization_code}/admin-runs" \
        "{\"target_bot_uuid\":\"${provider_bot_uuid}\",\"message\":{\"role\":\"user\",\"content\":[{\"type\":\"text\",\"text\":\"Review the E2E release plan\"}]},\"detach\":true}" \
        "Authorization: Bearer ${admin_token}" \
        "X-BCN-Provider-Id: ${provider_id}"
    require_status "provider starts an organization admin run" "202" || return
    local admin_run_id
    admin_run_id=$(json_path "$RESPONSE" "data.run_id")
    assert_not_empty "organization admin run returns a run id" "$admin_run_id"
    assert_json_eq "organization admin run keeps its organization" "$RESPONSE" "data.organization_code" "$organization_code"
    assert_json_eq "organization admin run keeps its target bot" "$RESPONSE" "data.target_bot_uuid" "$provider_bot_uuid"
    [[ -n "$admin_run_id" ]] || return

    api_request_headers GET "/organizations/${organization_code}/admin-runs/${admin_run_id}" "" \
        "Authorization: Bearer ${admin_token}" \
        "X-BCN-Provider-Id: ${provider_id}"
    require_status "provider reads the organization admin run" "200" || return
    assert_json_eq "organization admin run read keeps its run id" "$RESPONSE" "data.run_id" "$admin_run_id"
    assert_json_eq "organization admin run read keeps its target bot" "$RESPONSE" "data.target_bot_uuid" "$provider_bot_uuid"
    assert_json_not_empty "organization admin run read exposes status" "$RESPONSE" "data.status"

    api_request_headers DELETE "/organizations/${organization_code}/members/${provider_bot_uuid}" "" \
        "Authorization: Bearer ${admin_token}"
    require_status "provider removes its bot from the organization" "204" || return
}

# User story: A user validates channel behavior before an external provider is installed.
#
# Flow:
#   Attempt to create a binding -> inspect all and target-scoped bindings -> pause
#   and delete a missing binding through the disabled bridge -> inspect bindings again.
#
# Critical assertions:
#   - Binding creation fails with the explicit disabled-bridge bad-request contract.
#   - Full and target-scoped binding reads always expose a well-formed items array.
#   - Disabled-bridge pause and delete follow their documented no-op acknowledgements.
#   - No phantom binding appears after the rejected and no-op operations.
story_user_validates_external_channel_setup() {
    info "Story: user validates channel setup before a provider is installed"

    api_post "/channels/bindings" \
        "{\"channel_type\":\"e2e-uninstalled-channel\",\"account_ref\":\"account-$$\",\"target\":{\"bot\":{\"bot_id\":\"${BOT_CEO_UUID}\"}},\"outbound_visibility\":\"full_transcript\",\"env\":\"local\",\"config\":{\"mode\":\"story\"}}"
    require_status "binding an uninstalled channel provider is rejected" "400" || return
    assert_json_eq "disabled channel setup returns bad_request" "$RESPONSE" "code" "bad_request"
    assert_contains "channel setup error identifies disabled bridge" "$RESPONSE" "channel bridge is disabled"

    api_get "/channels/bindings"
    require_status "user can still inspect configured channel bindings" "200" || return
    local items_is_array
    items_is_array=$(printf '%s' "$RESPONSE" | python3 -c 'import json,sys; print("1" if isinstance(json.load(sys.stdin).get("items"), list) else "0")' 2>/dev/null || echo 0)
    assert_eq "channel binding list exposes an items array" "$items_is_array" "1"

    local encoded_bot_id
    encoded_bot_id=$(urlencode "$BOT_CEO_UUID")
    api_get "/channels/bindings/by-target?target_type=bot&target_id=${encoded_bot_id}&channel_type=e2e-uninstalled-channel"
    require_status "user can inspect bindings for one bot target" "200" || return
    assert_json_eq "target-scoped channel binding list remains empty" "$RESPONSE" "items" "[]"

    local missing_binding="missing-binding-$$"
    api_patch "/channels/bindings/${missing_binding}" '{"active":false}'
    require_status "disabled bridge accepts a no-op pause" "200" || return
    assert_json_eq "disabled bridge pause is acknowledged" "$RESPONSE" "ok" "true"

    api_delete "/channels/bindings/${missing_binding}"
    require_status "disabled bridge accepts a no-op delete" "200" || return
    assert_json_eq "disabled bridge delete is acknowledged" "$RESPONSE" "ok" "true"

    api_get "/channels/bindings"
    require_status "disabled bridge remains free of phantom bindings" "200" || return
    assert_json_eq "disabled bridge binding list remains empty" "$RESPONSE" "items" "[]"
}

# User story: An operator inspects and coordinates the agent network through bcs-cli.
#
# Flow:
#   Check health -> list, get, and discover agents -> change and restore visibility
#   -> reconnect and generate onboarding information -> update status -> propose a group
#   -> start a direct chat -> inspect groups and trusted relationships.
#
# Critical assertions:
#   - Directory commands return the exact seeded agents and requested identities.
#   - Visibility changes are read back and restored to the original value.
#   - Onboarding, group proposal, and chat commands return actionable URLs or handles.
#   - Group-list output has a consistent count/entry shape and friends include the expected agent.
story_operator_coordinates_with_cli() {
    info "Story: an operator uses bcs-cli to inspect and coordinate the agent network"
    test_cli_health
    test_cli_list
    test_cli_get
    test_cli_discover
    test_cli_visibility_get_set
    test_cli_connect
    test_cli_onboard
    _story_cli_direct_onboard || return
    test_cli_update_status
    test_cli_request_group_help
    test_cli_chat
    test_cli_list_groups
    test_cli_friend
}
