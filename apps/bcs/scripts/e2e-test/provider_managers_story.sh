#!/usr/bin/env bash
E2E_TESTS_STORIES+=(story_provider_initializes_managers)
story_provider_initializes_managers() {
    info "Story: Provider initializes managers and preserves subsequent authority changes"
    local result=0
    python3 "$SCRIPT_DIR/provider_managers_story.py" --base-url "$BCS_API_BASE_URL" \
        --owner "$BCS_MOCK_USER_ID" || result=$?
    assert_eq "Provider initial manager authority journey" "$result" "0"
}
