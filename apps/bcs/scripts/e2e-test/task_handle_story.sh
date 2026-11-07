#!/usr/bin/env bash
# Exercise Manager/Worker task controls through the live Singlebox server.
E2E_TESTS_STORIES+=(story_manager_worker_task_handles)

story_manager_worker_task_handles() {
    info "Story: Manager/Worker task lifecycle, status, cancellation and timeouts"
    local result=0
    node "$SCRIPT_DIR/../task_handle_runtime_probe.cjs" "$BCS_API_BASE_URL" || result=$?
    assert_eq "Task controls preserve lifecycle outcomes and delivery references" "$result" "0"
}
