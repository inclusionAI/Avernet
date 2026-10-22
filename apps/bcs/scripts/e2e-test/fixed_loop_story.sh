#!/usr/bin/env bash
# Exercise Loop workflows through the public HTTP API and the instrumented CLI.

if [[ -n "$(mock_view_base_url)" ]]; then
    E2E_TESTS_STORIES+=(story_fixed_loop_human_editor_workflow)
    E2E_TESTS_STORIES+=(story_nested_fixed_loop_workflows)
    E2E_TESTS_STORIES+=(story_state_machine_result_queue)
fi

story_state_machine_result_queue() {
    info "Story: one-shot result reaches its owner and becomes queued observer context"
    local result=0
    python3 "$SCRIPT_DIR/result_queue_story.py" \
        --base-url "$BCS_API_BASE_URL" --mock-url "$(mock_view_base_url)" \
        --mock-sink-url "$(mock_sink_base_url)" \
        --human "$BCS_MOCK_USER_ID" || result=$?
    assert_eq "Result admission preserves owner delivery, observer context, and unique history" "$result" "0"
}

story_fixed_loop_human_editor_workflow() {
    info "Story: Human input, editor decisions, Loop exits, and one-shot publication"
    get_bcs_cli_bin >/dev/null || return
    ensure_cli_token PM >/dev/null || return
    local result=0
    BCS_E2E_DRIVER_TOKEN="$BCS_CLI_TOKEN" \
        python3 "$SCRIPT_DIR/fixed_loop_story.py" \
        --base-url "$BCS_API_BASE_URL" --mock-url "$(mock_view_base_url)" \
        --mock-sink-url "$(mock_sink_base_url)" \
        --cli "$BCS_CLI_BIN_PATH" --driver "$BOT_PM_UUID" \
        --human "$BCS_MOCK_USER_ID" || result=$?
    assert_eq "Loop workflows preserve inputs, decisions, history, and final publication" "$result" "0"
}

story_nested_fixed_loop_workflows() {
    info "Story: Nested Bot and three-level Human Loop routing, isolation, and history"
    get_bcs_cli_bin >/dev/null || return
    local result=0
    python3 "$SCRIPT_DIR/nested_fixed_loop_story.py" \
        --base-url "$BCS_API_BASE_URL" --mock-url "$(mock_view_base_url)" \
        --mock-sink-url "$(mock_sink_base_url)" \
        --cli "$BCS_CLI_BIN_PATH" --driver "$BOT_PM_UUID" \
        --human "$BCS_MOCK_USER_ID" || result=$?
    assert_eq "Nested Loop workflows preserve paths, isolation, skips, history, and stale-event fences" "$result" "0"
}
