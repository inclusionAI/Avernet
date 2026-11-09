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
story_chat_final_coordination_claim() {
    info "Story: chat-final coordination claim for provider manager bots (relay G1 gated)"
    local result=0
    python3 "$SCRIPT_DIR/coordination_chat_final_story.py" \
        --base-url "$BCS_API_BASE_URL" --mock-url "$BCS_E2E_MOCK_BASE_URL" \
        --human "$BCS_MOCK_USER_ID" || result=$?
    assert_eq "chat-final coordination claim settles stored-intent frames silently" "$result" "0"
}

if [[ -n "${BCS_E2E_MOCK_BASE_URL:-}" ]]; then
    E2E_TESTS_STORIES+=("story_chat_final_coordination_claim")
fi

story_owner_mode_master_relay() {
    info "Story: owner-mode master delegates through the session relay capture"
    local result=0
    python3 "$SCRIPT_DIR/caller_owner_mode_relay_story.py" \
        --base-url "$BCS_API_BASE_URL" --mock-url "$BCS_E2E_MOCK_BASE_URL" \
        --human "$BCS_MOCK_USER_ID" || result=$?
    assert_eq "Owner-mode master mints the session-capture delegation toward a caller worker" "$result" "0"
}

if [[ -n "${BCS_E2E_MOCK_BASE_URL:-}" ]]; then
    E2E_TESTS_STORIES+=("story_owner_mode_master_relay")
fi

run_caller_delegation_chain() {
    local mode="$1" result=0
    info "Story: leased Owner Manager delegates to a Caller Worker (context=$mode)"
    python3 "$SCRIPT_DIR/caller_delegation_chain_story.py" \
        --base-url "$BCS_API_BASE_URL" --mock-url "$BCS_E2E_MOCK_BASE_URL" \
        --human "$BCS_MOCK_USER_ID" --context-delivery "$mode" || result=$?
    assert_eq "Owner lineage and protected Worker delivery with $mode initialization" "$result" "0"
}

story_caller_delegation_chain_send() { run_caller_delegation_chain send; }
story_caller_delegation_chain_inject() { run_caller_delegation_chain inject; }

if [[ -n "${BCS_E2E_MOCK_BASE_URL:-}" ]]; then
    E2E_TESTS_STORIES+=("story_caller_delegation_chain_send" "story_caller_delegation_chain_inject")
fi

story_native_task_caller_delegation() {
    info "Story: native WS task dispatch inherits Caller identity and rejects unsafe replay"
    local result=0
    python3 "$SCRIPT_DIR/native_task_caller_story.py" \
        --base-url "$BCS_API_BASE_URL" --mock-url "$BCS_E2E_MOCK_BASE_URL" \
        --human "$BCS_MOCK_USER_ID" || result=$?
    assert_eq "Native Caller task parent binding, replay fences and result delivery" "$result" "0"
}

if [[ -n "${BCS_E2E_MOCK_BASE_URL:-}" ]]; then
    E2E_TESTS_STORIES+=("story_native_task_caller_delegation")
fi

story_late_caller_manager_protected_result() {
    info "Story: late enabled Manager receives Worker results over the protected transport"
    local result=0
    python3 "$SCRIPT_DIR/protected_result_leg_story.py" \
        --base-url "$BCS_API_BASE_URL" --mock-url "$BCS_E2E_MOCK_BASE_URL" \
        --human "$BCS_MOCK_USER_ID" || result=$?
    assert_eq "Late-enabled Manager parks identity and rides protected result and failure legs" "$result" "0"
}

if [[ -n "${BCS_E2E_MOCK_BASE_URL:-}" ]]; then
    E2E_TESTS_STORIES+=("story_late_caller_manager_protected_result")
fi
