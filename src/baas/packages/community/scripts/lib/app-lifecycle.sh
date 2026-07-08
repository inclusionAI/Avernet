source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

_health_check() {
    log_sub "Waiting for app to become healthy..."
    for i in $(seq 30 -1 1); do
        if curl -sf http://localhost:8888/health > /dev/null 2>&1; then
            log_info "App is healthy"
            return 0
        fi
        echo "  Waiting... ${i}s"
        sleep 1
    done
    log_error "App did not become healthy after 30s"
    return 1
}

_start_app() {
    local overlay="${1:-}"
    local mock="${2:-}"
    local mode="${3:-${_BAAS_MODE:-bare}}"

    # Unset any previous PAAS_MOCK_* vars to prevent leakage between e2e sub-runs
    for var in "${!PAAS_MOCK_@}"; do unset "$var"; done

    # Stop any running app first (app.sh start fails if already running)
    bash "$SCRIPT_DIR/app.sh" stop 2>/dev/null || true
    rm -rf "$HOME/logs/secbaas"/*

    if [[ -n "$overlay" ]]; then
        export SOFAPY_CONFIG_OVERLAY="$overlay"
    fi
    if [[ -n "$mock" ]]; then
        export PAAS_MOCK_MODE=true
        export "$mock=true"
        log_info "Mock mode: PAAS_MOCK_MODE=true, $mock=true"
        bash "$SCRIPT_DIR/app.sh" start --mock --mode "$mode"
    else
        bash "$SCRIPT_DIR/app.sh" start --mode "$mode"
    fi
    _health_check
}

_wait_for_stop() {
    log_sub "Waiting for app to stop..."
    for i in $(seq 15 -1 1); do
        if ! curl -sf http://localhost:8888/health > /dev/null 2>&1; then
            log_info "App is stopped"
            return 0
        fi
        echo "  Waiting... ${i}s"
        sleep 1
    done
    if ! curl -sf http://localhost:8888/health > /dev/null 2>&1; then
        log_info "App is stopped"
        return 0
    fi
    log_error "App did not stop after 15s"
    return 1
}

_stop_app() {
    bash "$SCRIPT_DIR/app.sh" stop
    _wait_for_stop
}