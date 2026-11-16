#!/usr/bin/env bash
# Internal Session handler rejection ladders (auth, roles, 404s, conflicts)
# through live HTTP; no external model or demo Bot dependency.
E2E_TESTS_STORIES+=(story_session_management_edges)

story_session_management_edges() {
    info "Story: internal Session handlers reject unauthorized, invalid, and missing-entity requests"
    local result=0
    python3 "$SCRIPT_DIR/session_management_edges_story.py" --base-url "$BCS_API_BASE_URL" || result=$?
    assert_eq "Session management edge contracts hold on the live stack" "$result" "0"
}
