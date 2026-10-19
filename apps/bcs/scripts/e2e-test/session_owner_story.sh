#!/usr/bin/env bash
# Real public-API authorization story; no demo Bot or external model dependency.
E2E_TESTS_STORIES+=(story_session_owner_permissions)

story_session_owner_permissions() {
    info "Story: Session-only members read Group details and owners control only their Bot mode"
    local result=0
    python3 "$SCRIPT_DIR/session_owner_story.py" --base-url "$BCS_API_BASE_URL" || result=$?
    assert_eq "Session ownership, Human scope, and membership revocation remain isolated" "$result" "0"
}
