source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/app-lifecycle.sh"

run_arch_tests() {
    log_stage
    echo "[ARCH] test-arch: architecture enforcement tests"
    mkdir -p "$REPORT_DIR"
    _run_pytest uv run pytest tests/architecture/ -v --junitxml="$REPORT_DIR/arch.xml" --color=yes
    local rc=$?
    if [[ $rc -ne 0 ]]; then log_error "test-arch failed"; fi
    return $rc
}

run_unit_tests() {
    log_stage
    echo "[UNIT] test-ut: unit tests"
    mkdir -p "$REPORT_DIR"
    _run_pytest uv run pytest tests/ \
        -m "not integration and not e2e" \
        --ignore=tests/architecture -v \
        --junitxml="$REPORT_DIR/ut.xml" \
        --cov=src/secbaas \
        --cov-report=xml:"$REPORT_DIR/coverage.xml" \
        --cov-report=html:"$REPORT_DIR/html" \
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
        --cov=src/secbaas --cov-append \
        --cov-report=xml:"$REPORT_DIR/coverage.xml" \
        --cov-report=html:"$REPORT_DIR/html" \
        --color=yes
    local rc=$?
    if [[ $rc -ne 0 ]]; then log_error "test-it failed"; fi
    return $rc
}

run_ci_tests() {
    log_stage
    echo "[CI] test-ci: arch + unit (exclude e2e)"
    mkdir -p "$REPORT_DIR"
    _run_pytest uv run pytest tests/ \
        -m "not e2e" \
        -v \
        --junitxml="$REPORT_DIR/TEST-unit.xml" \
        --cov=src/secbaas \
        --cov-report=xml:"$REPORT_DIR/TEST-cov.xml" \
        --cov-report=html:"$REPORT_DIR/html" \
        --color=yes
    local rc=$?
    _clean_skipped_from_report "$REPORT_DIR/TEST-unit.xml"
    if [[ $rc -ne 0 ]]; then log_error "test-ci failed"; fi
    return $rc
}

run_e2e_crud() {
    local mode="${1:-${_BAAS_MODE:-bare}}"
    local overlay="${2:-${_BAAS_OVERLAY:-e2e-sqlite}}"
    log_sub "E2E CRUD tests"
    mkdir -p "$REPORT_DIR"
    export _BAAS_MODE="$mode"
    _start_app "$overlay"
    _run_pytest uv run pytest tests/e2e/crud/ -v --durations=0 --log-cli-level=INFO \
        --tb=short -m "e2e and crud" \
        --junitxml="$REPORT_DIR/e2e-crud.xml" --color=yes
    local rc=$?
    _stop_app
    return $rc
}

run_e2e_sync() {
    local mode="${1:-${_BAAS_MODE:-bare}}"
    local overlay="${2:-${_BAAS_OVERLAY:-e2e-sqlite}}"
    log_sub "E2E Sync tests"
    mkdir -p "$REPORT_DIR"
    export _BAAS_MODE="$mode"
    _start_app "$overlay"
    _run_pytest uv run pytest tests/e2e/mock_paas_success/sync/ -v --durations=0 \
        --log-cli-level=INFO --tb=short \
        -m "e2e and sync" --junitxml="$REPORT_DIR/e2e-sync.xml" --color=yes
    local rc=$?
    _stop_app
    return $rc
}

run_e2e_async() {
    local mode="${1:-${_BAAS_MODE:-bare}}"
    local overlay="${2:-${_BAAS_OVERLAY:-e2e-sqlite}}"
    log_sub "E2E Async tests"
    mkdir -p "$REPORT_DIR"
    export _BAAS_MODE="$mode"
    _start_app "$overlay"
    _run_pytest uv run pytest tests/e2e/mock_paas_success/async/ -v --durations=0 \
        --log-cli-level=INFO --tb=short \
        -m "e2e and async_hook" --junitxml="$REPORT_DIR/e2e-async.xml" --color=yes
    local rc=$?
    _stop_app
    return $rc
}

run_one_e2e_test() {
    local test="$1"

    # If given just a filename (no directory separators), search for it under tests/
    if [[ "$test" != */* ]]; then
        local resolved
        resolved=$(find tests/ -name "$test" -type f 2>/dev/null | head -1)
        if [[ -n "$resolved" ]]; then
            test="$resolved"
            log_info "Resolved to: $test"
        else
            log_error "Could not find test file: $test"
            return 1
        fi
    fi

    log_sub "E2E single test: $test"
    mkdir -p "$REPORT_DIR"
    _run_pytest uv run pytest "$test" -v --durations=0 --log-cli-level=INFO \
        --tb=short -m "e2e" --color=yes
}

run_e2e_mock_failure_hook() {
    local mode="${1:-${_BAAS_MODE:-bare}}"
    local overlay="${2:-${_BAAS_OVERLAY:-e2e-sqlite}}"
    log_sub "E2E Mock failure: hook"
    mkdir -p "$REPORT_DIR"
    export _BAAS_MODE="$mode"
    _start_app "$overlay" "PAAS_MOCK_HOOK_FAILURE"
    _run_pytest uv run pytest tests/e2e/mock_paas_failure/ -v --durations=0 \
        --log-cli-level=INFO --tb=short \
        -m "mock_paas_hook_failure" --junitxml="$REPORT_DIR/e2e-mock-hook.xml" --color=yes
    local rc=$?
    _stop_app
    return $rc
}

run_e2e_mock_failure_create() {
    local mode="${1:-${_BAAS_MODE:-bare}}"
    local overlay="${2:-${_BAAS_OVERLAY:-e2e-sqlite}}"
    log_sub "E2E Mock failure: create"
    mkdir -p "$REPORT_DIR"
    export _BAAS_MODE="$mode"
    _start_app "$overlay" "PAAS_MOCK_CREATE_FAILURE"
    _run_pytest uv run pytest tests/e2e/mock_paas_failure/ -v --durations=0 \
        --log-cli-level=INFO --tb=short \
        -m "mock_paas_create_failure" --junitxml="$REPORT_DIR/e2e-mock-create.xml" --color=yes
    local rc=$?
    _stop_app
    return $rc
}

run_e2e_mock_failure_destroy() {
    local mode="${1:-${_BAAS_MODE:-bare}}"
    local overlay="${2:-${_BAAS_OVERLAY:-e2e-sqlite}}"
    log_sub "E2E Mock failure: destroy"
    mkdir -p "$REPORT_DIR"
    export _BAAS_MODE="$mode"
    _start_app "$overlay" "PAAS_MOCK_DESTROY_FAILURE"
    _run_pytest uv run pytest tests/e2e/mock_paas_failure/ -v --durations=0 \
        --log-cli-level=INFO --tb=short \
        -m "mock_paas_destroy_failure" --junitxml="$REPORT_DIR/e2e-mock-destroy.xml" --color=yes
    local rc=$?
    _stop_app
    return $rc
}

run_e2e_mock_failure_device_not_found() {
    local mode="${1:-${_BAAS_MODE:-bare}}"
    local overlay="${2:-${_BAAS_OVERLAY:-e2e-sqlite}}"
    log_sub "E2E Mock failure: device-not-found"
    mkdir -p "$REPORT_DIR"
    export _BAAS_MODE="$mode"
    _start_app "$overlay" "PAAS_MOCK_DEVICE_NOT_FOUND"
    _run_pytest uv run pytest tests/e2e/mock_paas_failure/ -v --durations=0 \
        --log-cli-level=INFO --tb=short \
        -m "mock_paas_device_not_found" \
        --junitxml="$REPORT_DIR/e2e-mock-device-not-found.xml" --color=yes
    local rc=$?
    _stop_app
    return $rc
}

run_e2e_tests() {
    local mode="${1:-${_BAAS_MODE:-bare}}"
    local overlay="${2:-${_BAAS_OVERLAY:-e2e-sqlite}}"
    log_stage
    echo "[E2E] test-e2e: end-to-end tests"
    mkdir -p "$REPORT_DIR"
    local ec=0

    run_e2e_crud "$@" || ec=$((ec + $?))
    run_e2e_sync "$@" || ec=$((ec + $?))
    run_e2e_async "$@" || ec=$((ec + $?))
    run_e2e_mock_failure_hook "$@" || ec=$((ec + $?))
    run_e2e_mock_failure_create "$@" || ec=$((ec + $?))
    run_e2e_mock_failure_destroy "$@" || ec=$((ec + $?))
    run_e2e_mock_failure_device_not_found "$@" || ec=$((ec + $?))

    if [[ $ec -ne 0 ]]; then log_error "test-e2e: some sub-runs failed"; fi
    return $ec
}