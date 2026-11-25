#!/usr/bin/env bash
# Real managed queue cancellation, correlation and history through HTTP + WS.

if [[ -n "$(mock_view_base_url)" ]]; then
    E2E_TESTS_STORIES+=(story_user_cancels_queued_messages)
fi

story_user_cancels_queued_messages() {
    info "Story: user cancels queued targets without losing sent or neighbouring messages"
    local result=0
    python3 "$SCRIPT_DIR/queued_message_cancellation_story.py" \
        --base-url "$BCS_API_BASE_URL" --mock-url "$(mock_view_base_url)" \
        --mock-sink-url "$(mock_sink_base_url)" \
        --human "$BCS_MOCK_USER_ID" || result=$?
    assert_eq "Queued cancellation preserves correlation, reload history and sent siblings" "$result" "0"
}
