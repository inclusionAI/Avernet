#!/usr/bin/env bash
# Real deleted-member isolation through Workbench, durable queue and Provider.

if [[ -n "$(mock_view_base_url)" ]]; then
    E2E_TESTS_STORIES+=(story_queued_target_failures)
fi

story_queued_target_failures() {
    info "Story: a deleted Bot cannot block healthy queued group recipients"
    local result=0
    python3 "$SCRIPT_DIR/queued_target_failure_story.py" \
        --base-url "$BCS_API_BASE_URL" --mock-url "$(mock_view_base_url)" \
        --mock-sink-url "$(mock_sink_base_url)" \
        --human "$BCS_MOCK_USER_ID" || result=$?
    assert_eq "Deleted targets fail independently while healthy Bots complete" "$result" "0"
}
