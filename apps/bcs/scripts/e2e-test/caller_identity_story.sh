#!/usr/bin/env bash
# Caller identity end-to-end against the scripted Provider mock.

if [[ -n "${BCS_E2E_MOCK_BASE_URL:-}" ]]; then
    E2E_TESTS_STORIES+=(story_caller_identity_guarded_dispatch)
fi

story_caller_identity_guarded_dispatch() {
    info "Story: caller identity rides protected dispatch, config sync, and the lease surface"
    local result=0
    python3 "$SCRIPT_DIR/caller_identity_story.py" \
        --base-url "$BCS_API_BASE_URL" --mock-url "$BCS_E2E_MOCK_BASE_URL" \
        --human "$BCS_MOCK_USER_ID" \
        --driver "${BOT_PM_UUID:-}" || result=$?
    assert_eq "Caller identity preserved through guarded dispatch, config sync and lease commands" "$result" "0"
}