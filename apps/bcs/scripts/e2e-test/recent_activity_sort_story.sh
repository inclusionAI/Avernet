#!/usr/bin/env bash
# Real public-API recent-activity sort story; no demo Bot or external model dependency.
E2E_TESTS_STORIES+=(story_recent_activity_sort)

story_recent_activity_sort() {
    info "Story: V1 Group/Session lists order by sort=last_activity over real HTTP state"
    local result=0
    python3 "$SCRIPT_DIR/recent_activity_sort_story.py" --base-url "$BCS_API_BASE_URL" || result=$?
    assert_eq "sort=last_activity re-keys Group and Session lists by the D1 activity key" "$result" "0"
}
