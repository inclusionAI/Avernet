#!/usr/bin/env bash
# Isolated production BCS process, preserving the coverage runner's profile path.
E2E_TESTS_STORIES+=(story_group_card_persistence)

story_group_card_persistence() {
    info "Story: Group Card templates follow Group creation, rename and deletion"
    local result=0
    python3 "$SCRIPT_DIR/group_card_story.py" \
        --binary "${BCS_BIN:-$SCRIPT_DIR/../../target/debug/bcs}" || result=$?
    assert_eq "Group Card lifecycle persists matching templates without orphaned rows" "$result" "0"
}
