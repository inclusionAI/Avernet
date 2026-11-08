#!/usr/bin/env bash
# Live policy transitions and owner isolation, with no database mutation by tests.
E2E_TESTS_STORIES+=(story_provider_owner_isolation)
story_provider_owner_isolation() {
    info "Story: Provider policy protects owner collaboration and private groups"
    local result=0
    python3 "$SCRIPT_DIR/provider_scope_story.py" --base-url "$BCS_API_BASE_URL" \
        --owner "$BCS_MOCK_USER_ID" || result=$?
    assert_eq "Provider owner boundary survives group/session/Workbench transitions" "$result" "0"
}
