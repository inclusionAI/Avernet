#!/usr/bin/env bash
# Exercise StateMachine run and node lifecycle transitions (retry, human
# respond, judge skip, timeout, rerun, cancel) through the public HTTP API,
# the bcs-cli, and the Provider callback intake.

if [[ -n "$(mock_view_base_url)" ]]; then
    E2E_TESTS_STORIES+=(story_state_machine_run_node_lifecycle)
fi

story_state_machine_run_node_lifecycle() {
    info "Story: StateMachine run/node lifecycle — retry, human gate, judge skip, timeout, rerun, abort"
    get_bcs_cli_bin >/dev/null || return
    ensure_cli_token PM >/dev/null || return
    local result=0
    BCS_E2E_DRIVER_TOKEN="$BCS_CLI_TOKEN" \
        python3 "$SCRIPT_DIR/run_node_lifecycle_story.py" \
        --base-url "$BCS_API_BASE_URL" --mock-url "$(mock_view_base_url)" \
        --mock-sink-url "$(mock_sink_base_url)" \
        --cli "$BCS_CLI_BIN_PATH" --driver "$BOT_PM_UUID" \
        --human "$BCS_MOCK_USER_ID" || result=$?
    assert_eq "Run and node lifecycle preserve retry, human input, judge branches, timeout, rerun, and abort semantics" "$result" "0"
}
