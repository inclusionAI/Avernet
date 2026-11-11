#!/usr/bin/env bash
# Caller-config storage legs against an isolated BCS process, preserving the
# coverage runner's profile path (coverage for the §10.7.2 bounded
# double-encoded bot_info compatibility).
E2E_TESTS_STORIES+=(story_caller_config_storage)

story_caller_config_storage() {
    info "Story: double-encoded bot_info heals on caller sync; deeper damage fails closed"
    local result=0
    python3 "$SCRIPT_DIR/caller_config_storage_story.py" \
        --binary "${BCS_BIN:-$SCRIPT_DIR/../../target/debug/bcs}" || result=$?
    assert_eq "Caller config storage compat: heal + fail-closed legs" "$result" "0"
}
