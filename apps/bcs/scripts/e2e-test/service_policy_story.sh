#!/usr/bin/env bash
# Legacy policy compatibility through live HTTP; no external model required.
E2E_TESTS_STORIES+=(story_retired_service_policy)

story_retired_service_policy() {
    info "Story: retired service policies are ignored while Session lifecycle guards remain"
    local result=0
    python3 "$SCRIPT_DIR/service_policy_story.py" --base-url "$BCS_API_BASE_URL" || result=$?
    assert_eq "Legacy service_spec is ignored and Session lifecycle remains valid" "$result" "0"
}
