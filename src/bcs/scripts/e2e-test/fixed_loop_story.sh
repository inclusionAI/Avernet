#!/usr/bin/env bash
# Exercise Loop workflows through the public HTTP API and the instrumented CLI.

if [[ -n "${BCS_E2E_MOCK_BASE_URL:-}" ]]; then
    E2E_TESTS_STORIES+=(story_fixed_loop_human_editor_workflow)
fi

story_fixed_loop_human_editor_workflow() {
    info "Story: Human input, editor decisions, Loop exits, and one-shot publication"
    get_bcs_cli_bin >/dev/null || return
    ensure_cli_token PM >/dev/null || return
    local result=0
    BCS_E2E_DRIVER_TOKEN="$BCS_CLI_TOKEN" \
        python3 "$SCRIPT_DIR/fixed_loop_story.py" \
        --base-url "$BCS_API_BASE_URL" --mock-url "$BCS_E2E_MOCK_BASE_URL" \
        --cli "$BCS_CLI_BIN_PATH" --driver "$BOT_PM_UUID" \
        --human "$BCS_MOCK_USER_ID" || result=$?
    assert_eq "Loop workflows preserve inputs, decisions, history, and final publication" "$result" "0"
}
