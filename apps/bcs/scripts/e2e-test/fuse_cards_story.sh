#!/usr/bin/env bash
# Use the same instrumented production binary and LLVM profile pool as Singlebox.
E2E_TESTS_STORIES+=(story_fuse_cards)

story_fuse_cards() {
    info "Story: Fuse v2 Card synchronization and discovery"
    local result=0
    python3 "$SCRIPT_DIR/fuse_cards_story.py" \
        --binary "${BCS_BIN:-$SCRIPT_DIR/../../target/debug/bcs}" || result=$?
    assert_eq "Fuse v2 Agent/Group publication, search compatibility and bounded retries" "$result" "0"
}
