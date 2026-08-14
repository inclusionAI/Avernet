source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

run_unit_tests() {
    log_stage
    echo "[UNIT] test-ut: unit tests"
    mkdir -p "$REPORT_DIR"
    _run_pytest uv run pytest tests/ \
        -m "not integration and not e2e" \
        -v \
        --junitxml="$REPORT_DIR/ut.xml" \
        --cov=src/gateway \
        --cov-report=xml:"$REPORT_DIR/coverage.xml" \
        --cov-report=html:"$REPORT_DIR/html" \
        --color=yes
    local rc=$?
    if [[ $rc -ne 0 ]]; then log_error "test-ut failed"; fi
    return $rc
}

run_ci_tests() {
    log_stage
    echo "[CI] test-ci: lint + unit tests"
    mkdir -p "$REPORT_DIR"
    _run_pytest uv run pytest tests/ \
        -m "not e2e" \
        -v \
        --junitxml="$REPORT_DIR/TEST-unit.xml" \
        --cov=src/gateway \
        --cov-report=xml:"$REPORT_DIR/TEST-cov.xml" \
        --cov-report=html:"$REPORT_DIR/html" \
        --color=yes
    local rc=$?
    _clean_skipped_from_report "$REPORT_DIR/TEST-unit.xml"
    if [[ $rc -ne 0 ]]; then log_error "test-ci failed"; fi
    return $rc
}

run_arch_tests() {
    log_stage
    echo "[ARCH] test-arch: architecture enforcement"
    mkdir -p "$REPORT_DIR"
    _run_pytest uv run pytest tests/architecture/ \
        -v \
        --junitxml="$REPORT_DIR/arch.xml" \
        --color=yes
    local rc=$?
    if [[ $rc -ne 0 ]]; then log_error "test-arch failed"; fi
    return $rc
}

run_integration_tests() {
    log_stage
    echo "[INTEGRATION] test-it: integration tests"
    mkdir -p "$REPORT_DIR"
    _run_pytest uv run pytest tests/integration/ \
        -m integration \
        -v \
        --junitxml="$REPORT_DIR/it.xml" \
        --cov=src/gateway --cov-append \
        --cov-report=xml:"$REPORT_DIR/coverage.xml" \
        --cov-report=html:"$REPORT_DIR/html" \
        --color=yes
    local rc=$?
    if [[ $rc -ne 0 ]]; then log_error "test-it failed"; fi
    return $rc
}

run_e2e_tests() {
    local mode="${1:-${_GATEWAY_MODE:-bare}}"
    log_stage
    echo "[E2E] test-e2e: end-to-end smoke tests (mode=$mode)"
    if [[ ! -d tests/e2e/ ]] || [[ -z "$(find tests/e2e/ -name 'test_*.py' -type f 2>/dev/null)" ]]; then
        echo "[E2E] No E2E test files found — skipping"
        return 0
    fi
    mkdir -p "$REPORT_DIR"
    source "$(dirname "${BASH_SOURCE[0]}")/app-lifecycle.sh"
    _start_app "$mode"
    _run_pytest uv run pytest tests/e2e/ \
        -v \
        --durations=0 \
        --junitxml="$REPORT_DIR/e2e.xml" \
        --color=yes
    local rc=$?
    _stop_app
    if [[ $rc -ne 0 ]]; then log_error "test-e2e failed"; fi
    return $rc
}
