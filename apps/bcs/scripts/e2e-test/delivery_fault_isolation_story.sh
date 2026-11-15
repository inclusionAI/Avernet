#!/usr/bin/env bash
# Isolated production BCS process, preserving the coverage runner's profile path.
E2E_TESTS_STORIES+=(story_delivery_fault_isolation)

story_delivery_fault_isolation() {
    info "Story: corrupt delivery rows are isolated and the queue keeps moving"
    local result=0
    python3 "$SCRIPT_DIR/delivery_fault_isolation_story.py" \
        --binary "${BCS_BIN:-$SCRIPT_DIR/../../target/debug/bcs}" || result=$?
    assert_eq "Corrupt delivery rows never stall healthy deliveries or restart recovery" "$result" "0"
}
