source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/checks.sh"
source "$(dirname "${BASH_SOURCE[0]}")/test-stages.sh"

run_ci_pipeline() {
    cd "$PROXY_DIR" || return 1
    _PROXY_MODE="${1:-${_PROXY_MODE:-bare}}"
    _PROXY_OVERLAY="${2:-${_PROXY_OVERLAY:-e2e-sqlite}}"

    _init_log

    echo ""
    echo "==========================================="
    echo "  SANDBOXPROXY CI Pipeline"
    echo "==========================================="

    if ! run_check_basic; then
        log_error "check-basic failed — aborting"
        return 1
    fi

    for fn in run_ci_tests run_e2e_tests; do
        if ! $fn; then
            FAILED_STAGES+=("$fn")
        fi
    done

    _summary "$SECONDS"
}