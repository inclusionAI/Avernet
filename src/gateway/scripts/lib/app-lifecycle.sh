source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

_HEALTH_PORT="${GATEWAY_E2E_PORT:-8888}"

_health_check() {
    local max_attempts="${1:-30}"
    log_sub "Waiting for app to become healthy on :$_HEALTH_PORT ..."
    for i in $(seq "$max_attempts" -1 1); do
        if curl --noproxy '*' -sf "http://127.0.0.1:$_HEALTH_PORT/health" >/dev/null 2>&1; then
            log_info "App is healthy (port $_HEALTH_PORT)"
            return 0
        fi
        echo "  Waiting... ${i}s"
        sleep 1
    done
    log_error "App did not become healthy after ${max_attempts}s"
    return 1
}

_start_app() {
    local mode="${1:-bare}"

    log_sub "Starting gateway app (mode=$mode)..."
    bash "$SCRIPT_DIR/app.sh" stop 2>/dev/null || true
    mkdir -p "$HOME/logs/gateway"
    bash "$SCRIPT_DIR/app.sh" start --mode "$mode"
    _health_check
}

_stop_app() {
    log_sub "Stopping gateway app..."
    bash "$SCRIPT_DIR/app.sh" stop
    _wait_for_stop
}

_wait_for_stop() {
    log_sub "Waiting for app to stop..."
    for i in $(seq 15 -1 1); do
        if ! curl --noproxy '*' -sf "http://127.0.0.1:$_HEALTH_PORT/health" >/dev/null 2>&1; then
            log_info "App is stopped"
            return 0
        fi
        echo "  Waiting... ${i}s"
        sleep 1
    done
    log_info "App is stopped"
}