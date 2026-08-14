source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/checks.sh"
source "$(dirname "${BASH_SOURCE[0]}")/test-stages.sh"

run_ci_pipeline() {
    cd "$GATEWAY_DIR" || return 1

    _init_log

    echo ""
    echo "==========================================="
    echo "  GATEWAY CI Pipeline"
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
