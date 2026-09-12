#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Load the real CLI parser and dispatcher without sourcing runtime modules or
# local credentials. All service/model operations below are test doubles.
eval "$(sed -n '/^main() {$/,/^# 执行主函数/p' "$ROOT/scripts/singlebox.sh")"

run_case() (
    container="$1"
    export OCB_LIFECYCLE_OWNER="$2"
    shift 2
    LOCAL_MODE=true STANDALONE_MODE=false BOTS_PROFILE_DIR=''
    DEP_DIR=/unused LOG_DIR=/unused BCN_PLUGIN_VERSION=latest
    with_bcs_coverage=0
    # Simulate only Docker detection; preserve all other parser predicates.
    [() {
        if [[ "$*" == '-f /.dockerenv ]' ]]; then
            [[ "$container" == yes ]]
        else
            builtin [ "$@"
        fi
    }
    load_engine_type() { :; }
    apply_singlebox_mode_defaults() { :; }
    resolve_bcs_server_env() { :; }
    mkdir() { :; }
    ensure_git_hooks_installed() { :; }
    show_local_mode_info() { :; }
    bcn_plugin_mode() { echo source; }
    log_error() { echo "$*" >&2; }
    singlebox_model_config_required_for_services() { return 1; }
    singlebox_mock_model_stop_required_for_services() { return 1; }
    singlebox_mock_model_stop() { :; }
    singlebox_mock_model_start() { :; }
    sleep() { :; }
    stop_service() { echo "dispatch:stop:$1"; }
    start_service() { echo "dispatch:start:$1"; }
    restart_service() { echo "dispatch:restart:$1"; }
    clean_service() { echo "dispatch:clean:$1"; }
    show_status() { echo "dispatch:status:$1"; }
    main "$@"
)

count=0
assert_case() {
    local expected="$1" output rc=0
    shift
    output="$(run_case "$@" 2>&1)" || rc=$?
    case "$expected" in
        blocked)
            if [[ "$rc" != 1 || "$output" != *"Refusing 'singlebox.sh"* || "$output" == *dispatch:* ]]; then
                printf 'FAIL: %s (exit %s)\n%s\n' "$*" "$rc" "$output" >&2
                exit 1
            fi
            ;;
        allowed)
            if [[ "$rc" != 0 || "$output" != *dispatch:* || "$output" == *Refusing* ]]; then
                printf 'FAIL: %s (exit %s)\n%s\n' "$*" "$rc" "$output" >&2
                exit 1
            fi
            ;;
    esac
    count=$((count + 1))
}

for action in stop restart clean; do
    assert_case blocked yes '' "$action"
    for target in bcs bcs_bots bcs_frontend all; do
        assert_case blocked yes '' "$action" "$target"
        assert_case blocked yes bot "$action" "$target"
        assert_case allowed no '' "$action" "$target"
        assert_case allowed yes entrypoint "$action" "$target"
    done
    # Reject the whole request before dispatching even a safe leading target.
    assert_case blocked yes '' "$action" bots bcs
    assert_case blocked yes '' "$action" --bcs-env local bcs_frontend
    assert_case allowed yes '' "$action" bots
    assert_case allowed yes '' "$action" bots --profile-dir /test/profile
done
assert_case allowed yes '' status all
assert_case allowed yes '' start bcs
printf 'PASS: %s container lifecycle guard cases (stubbed services; no live stack)\n' "$count"
