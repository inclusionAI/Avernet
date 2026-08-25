#!/bin/bash

WORK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

VENV_DIR="$WORK_DIR/.venv"
APP_PORT="${SANDBOXPROXY_PORT:-8888}"
APP_MODE="${SANDBOXPROXY_RUN_MODE:-bare}"
APP_JWT_SECRET="${SANDBOXPROXY_JWT_SECRET:-proxy-dev-secret-not-for-prod}"

mkdir -p "$WORK_DIR/logs"
PID_FILE="$WORK_DIR/logs/app.pid"
PORT_FILE="$WORK_DIR/logs/app.port"
LOG_FILE="$WORK_DIR/logs/app.log"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $1" | tee -a "$LOG_FILE"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1" | tee -a "$LOG_FILE"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1" | tee -a "$LOG_FILE"; }

log_usage() {
    echo "Usage: $0 {start|stop|restart|status} [--mode bare]"
    echo "  start    - start the app"
    echo "  stop     - stop the app"
    echo "  restart  - restart the app"
    echo "  status   - show app status"
}

check_venv() {
    if [[ ! -d "$VENV_DIR" ]]; then
        if ! command -v uv &> /dev/null; then
            log_error "uv not installed"
            return 1
        fi
        (cd "$WORK_DIR" && uv sync) || return 1
    fi
    return 0
}

is_running() {
    [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

get_pid_by_port() {
    local port=$1
    if command -v lsof &> /dev/null; then
        lsof -t -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | head -1
    fi
}

check_port() {
    local port=$1
    if command -v lsof &> /dev/null; then
        if lsof -nP -iTCP:"$port" -sTCP:LISTEN &> /dev/null; then
            log_error "Port $port is already in use"
            lsof -nP -iTCP:"$port" -sTCP:LISTEN | tee -a "$LOG_FILE"
            return 1
        fi
    fi
    return 0
}

start_detached() {
    # Replace the shell in-place so $! is the real server PID (not a nohup shim).
    nohup python3 -c 'import os, sys; os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])' \
        "$@" >> "$LOG_FILE" 2>&1 &
}

wait_for_health() {
    local pid=$1
    local port=$2
    local max_attempts=30
    local attempt=1

    log_info "Waiting for application to be ready..."
    while [[ $attempt -le $max_attempts ]]; do
        if ! kill -0 "$pid" 2>/dev/null; then
            log_error "Application process exited unexpectedly — see $LOG_FILE"
            tail -30 "$LOG_FILE" | while IFS= read -r line; do
                echo -e "${RED}|${NC} $line"
            done
            return 1
        fi
        if curl --noproxy '*' -sf "http://127.0.0.1:$port/health" >/dev/null 2>&1; then
            return 0
        fi
        sleep 2
        attempt=$((attempt + 1))
    done
    log_error "Application did not become healthy after $((max_attempts * 2))s — see $LOG_FILE"
    return 1
}

do_start() {
    local mode="$APP_MODE"
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --mode) APP_MODE="$2"; shift 2 ;;
            *) shift ;;
        esac
    done
    mode="$APP_MODE"

    if is_running; then
        log_error "App already running (PID: $(cat "$PID_FILE"))"
        exit 1
    fi
    check_venv || exit 1
    check_port "$APP_PORT" || exit 1
    [ -f "$PID_FILE" ] && rm -f "$PID_FILE"

    log_info "Starting sandbox-proxy (mode=$mode, port=$APP_PORT)..."
    export SANDBOXPROXY_PORT="$APP_PORT"
    export SANDBOXPROXY_JWT_SECRET="$APP_JWT_SECRET"
    cd "$WORK_DIR" && start_detached "$VENV_DIR/bin/python" \
        src/sandboxproxy/community/main.py --mode "$mode" --config configs/

    local pid=$!
    if wait_for_health "$pid" "$APP_PORT"; then
        echo "$pid" > "$PID_FILE"
        echo "$APP_PORT" > "$PORT_FILE"
        log_info "App started (PID: $pid)"
        log_info "Health: http://localhost:$APP_PORT/health"
    else
        log_error "App failed to start — see $LOG_FILE"
        kill "$pid" 2>/dev/null || true
        exit 1
    fi
}

do_stop() {
    local stopped=false
    if is_running; then
        local pid
        pid="$(cat "$PID_FILE")"
        log_info "Stopping app (PID: $pid)..."
        kill "$pid" 2>/dev/null
        for _ in $(seq 1 10); do
            kill -0 "$pid" 2>/dev/null || break
            sleep 1
        done
        kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null
        stopped=true
    else
        rm -f "$PID_FILE"
        log_warn "App not running"
    fi

    # Fallback: kill any orphan still holding the port (stale PID file case).
    local orphan
    orphan="$(get_pid_by_port "$APP_PORT")"
    if [[ -n "$orphan" ]]; then
        log_warn "Killing orphan process on port $APP_PORT (PID: $orphan)"
        kill "$orphan" 2>/dev/null
        sleep 1
        kill -0 "$orphan" 2>/dev/null && kill -9 "$orphan" 2>/dev/null
        stopped=true
    fi

    rm -f "$PID_FILE"
    $stopped && log_info "App stopped"
}

do_status() {
    if is_running; then
        echo "● App running (PID: $(cat "$PID_FILE"))"
    else
        echo "○ App not running"
    fi
}

case "${1:-start}" in
    start)   do_start "${@:2}" ;;
    stop)    do_stop ;;
    restart) do_stop; do_start "${@:2}" ;;
    status)  do_status ;;
    *)       log_error "unknown command: $1"; log_usage; exit 1 ;;
esac