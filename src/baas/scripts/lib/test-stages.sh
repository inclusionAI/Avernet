source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/app-lifecycle.sh"

run_arch_tests() {
    log_stage
    echo "[ARCHITECTURE] test-arch: architecture enforcement tests"
    mkdir -p "$REPORT_DIR"
    _run_pytest uv run pytest tests/architecture \
        --junitxml="$REPORT_DIR/arch.xml" \
        --cov=src/secbaas \
        --cov-report=xml:"$REPORT_DIR/cov-arch.xml" \
        --cov-report=html:"$REPORT_DIR/html-arch" \
        --color=yes
    local rc=$?
    if [[ $rc -ne 0 ]]; then log_error "test-arch failed"; fi
    return $rc
}

run_unit_tests() {
    log_stage
    echo "[UNIT] test-ut: unit tests"
    mkdir -p "$REPORT_DIR"
    _run_pytest uv run pytest tests/unit \
        -v -m "not integration and not e2e and not baseline" \
        --cov=src/secbaas \
        --junitxml="$REPORT_DIR/ut.xml" \
        --cov-report=xml:"$REPORT_DIR/cov-ut.xml" \
        --cov-report=html:"$REPORT_DIR/html-ut" \
        --color=yes
    local rc=$?
    if [[ $rc -ne 0 ]]; then log_error "test-ut failed"; fi
    return $rc
}

run_integration_tests() {
    log_stage
    echo "[INTEGRATION] test-it: integration tests"
    mkdir -p "$REPORT_DIR"
    _run_pytest uv run pytest tests/integration/ -v -m integration \
        --junitxml="$REPORT_DIR/it.xml" \
        --cov=src/secbaas \
        --cov-report=xml:"$REPORT_DIR/cov-it.xml" \
        --cov-report=html:"$REPORT_DIR/html-it" \
        --color=yes
    local rc=$?
    if [[ $rc -ne 0 ]]; then log_error "test-it failed"; fi
    return $rc
}

run_ci_tests() {
    log_stage
    echo "[CI] test-ci: arch + unit + integration (exclude e2e-boot and e2e-asgi)"
    mkdir -p "$REPORT_DIR"
    _run_pytest uv run pytest tests/ \
        -v \
        --cov=src/secbaas \
        --ignore=tests/e2e \
        --junitxml="$REPORT_DIR/ci.xml" \
        --cov-report=xml:"$REPORT_DIR/cov-ci.xml" \
        --cov-report=html:"$REPORT_DIR/html-ci" \
        --color=yes
    local rc=$?
    _clean_skipped_from_report "$REPORT_DIR/ci.xml"
    if [[ $rc -ne 0 ]]; then log_error "test-ci failed"; fi
    return $rc
}

run_e2e_asgi_tests() {
    log_stage
    echo "[ASGI] test-asgi: in-process ASGI tests"
    mkdir -p "$REPORT_DIR"

    # TestClient does not need a running app — it runs against the ASGI app
    # in the same process using ASGITransport. The bootstrap_init fixture
    # initializes the full DI container with it-sqlite overlay.
    _run_pytest uv run pytest tests/e2e/asgi/ -v --durations=0 \
        --tb=short \
        --cov=src/secbaas \
        --cov-report=xml:"$REPORT_DIR/cov-asgi.xml" \
        --cov-report=html:"$REPORT_DIR/htmlcov-asgi" \
        --junitxml="$REPORT_DIR/asgi.xml" --color=yes
    local rc=$?
    if [[ $rc -ne 0 ]]; then log_error "test-asgi failed"; fi
    return $rc
}

run_e2e_boot_tests() {
    local mode="${1:-${_BAAS_MODE:-bare}}"
    local overlay="${2:-${_BAAS_OVERLAY:-e2e-sqlite}}"
    log_stage
    log_sub "E2E Baseline tests"
    mkdir -p "$REPORT_DIR"
    export _BAAS_MODE="$mode"
    export SESSION_LABEL="baseline"

    _start_app "$overlay"

    _run_pytest uv run pytest tests/e2e/boot -v --durations=0 --log-cli-level=INFO \
        --tb=short -m "e2e" \
        --color=yes
    local rc=$?

    _stop_app
    return $rc
}


#run_e2e_tests() {
#    local mode="${1:-${_BAAS_MODE:-bare}}"
#    local overlay="${2:-${_BAAS_OVERLAY:-e2e-sqlite}}"
#    log_stage
#    echo "[E2E] test-e2e: end-to-end tests"
#    mkdir -p "$REPORT_DIR"
#    local ec=0
#
#    rm -rf "$COVERAGE_E2E_DIR"
#
#    run_asgi_tests || ec=$((ec + $?))
#
#    if [[ $ec -ne 0 ]]; then log_error "test-e2e: some sub-runs failed"; fi
#    return $ec
#}