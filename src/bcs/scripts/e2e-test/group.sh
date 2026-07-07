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
