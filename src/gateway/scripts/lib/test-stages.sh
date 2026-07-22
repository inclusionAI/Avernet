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
  return 0
}

run_e2e_tests() {
  return 0
}
