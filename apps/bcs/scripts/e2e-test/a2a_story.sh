#!/usr/bin/env bash
# Public A2A peer; exercise the real BCS registration and delivery boundaries.
E2E_TESTS_STORIES+=(story_provider_operator_publishes_a2a_agent)
story_provider_operator_publishes_a2a_agent() {
    info "Story: Provider operator publishes an A2A agent and a human converses with it"
    local result=0
    local prefix
    for prefix in /a2a /api/v1/collaboration/a2a; do
        python3 "$SCRIPT_DIR/a2a_story.py" --base-url "$BCS_API_BASE_URL" \
            --owner "$BCS_MOCK_USER_ID" --a2a-prefix "$prefix" || result=$?
    done
    assert_eq "A2A Card management and live protocol delivery" "$result" "0"
}
