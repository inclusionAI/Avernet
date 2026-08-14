#!/bin/bash

WORK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

CONFIG_DIR="$WORK_DIR/configs"
VENV_DIR="$WORK_DIR/.venv"
APP_PORT="8888"
APP_MODE=""
APP_LOG_DIR="$HOME/logs/gateway"

mkdir -p "$WORK_DIR/tmp"
PID_FILE="$WORK_DIR/tmp/app.pid"
PORT_FILE="$WORK_DIR/tmp/app.port"
LOG_FILE="$WORK_DIR/tmp/app.log"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1" | tee -a "$LOG_FILE"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1" | tee -a "$LOG_FILE"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1" | tee -a "$LOG_FILE"
}

log_usage() {
    echo -e "${BLUE}Usage:${NC} $0 {start|stop|restart|status} [options]"
    echo ""
    echo "Commands:"
    echo "  start    - Start the application"
    echo "  stop     - Stop the application"
    echo "  restart  - Restart the application"
    echo "  status   - Show application status"
    echo ""
    echo "Options:"
    echo "  --debug   - Enable debug mode (debugpy, default port 5678)"
    echo "  --env     - Environment: dev/prepub/prod (default: none)"
    echo "  --mode    - Run mode: bare/sofa (default: bare)"
}

check_venv() {
    if [[ ! -d "$VENV_DIR" ]]; then
        log_warn "Virtual environment not found, creating with uv..."
        if ! command -v uv &> /dev/null; then
            log_error "uv not installed. Install with: curl -LsSf https://astral.sh/uv/install.sh | sh"
            return 1
        fi
        cd "$WORK_DIR" && uv sync
        if [[ $? -ne 0 ]]; then
            log_error "uv sync failed"
            return 1
        fi
        log_info "Virtual environment created"
    fi

    PYTHON_BIN="$VENV_DIR/bin/python"
    if [[ ! -x "$PYTHON_BIN" ]]; then
        log_error "Python interpreter not available: $PYTHON_BIN"
        return 1
    fi
    return 0
}

check_config() {
    if [[ ! -d "$CONFIG_DIR" ]]; then
        log_error "Config directory not found: $CONFIG_DIR"
        return 1
    fi
    return 0
}

is_running() {
    if [[ -f "$PID_FILE" ]]; then
        OLD_PID=$(cat "$PID_FILE")
        if kill -0 "$OLD_PID" 2>/dev/null; then
            return 0
        fi
    fi
    return 1
}

check_port() {
    local port=$1
    if command -v lsof &> /dev/null; then
        if lsof -nP -iTCP:"$port" -sTCP:LISTEN &> /dev/null; then
            log_error "Port $port is already in use"
            lsof -nP -iTCP:"$port" -sTCP:LISTEN | tee -a "$LOG_FILE"
            return 1
        fi
    elif command -v netstat &> /dev/null; then
        if netstat -tuln | grep -q ":$port "; then
            log_error "Port $port is already in use"
            netstat -tuln | grep ":$port " | tee -a "$LOG_FILE"
            return 1
        fi
    else
        log_warn "Cannot check port usage (lsof or netstat not found)"
    fi
    return 0
}

do_start() {
    local debug_port=""
    local env_name=""

    while [[ $# -gt 0 ]]; do
        case $1 in
            --debug)
                debug_port="${2:-5678}"
                shift 2
                ;;
            --mode)
                APP_MODE="$2"
                shift 2
                ;;
            --env)
                env_name="$2"
                shift 2
                ;;
            *)
                shift
                ;;
        esac
    done

    local config_file="$CONFIG_DIR/application.yaml"
    if [[ -n "$env_name" ]]; then
        case "$env_name" in
            dev)
                config_file="$CONFIG_DIR/application-dev.yaml"
                ;;
            prepub)
                config_file="$CONFIG_DIR/application-prepub.yaml"
                ;;
            prod)
                config_file="$CONFIG_DIR/application.yaml"
                ;;
            *)
                log_error "Unknown environment: $env_name (supported: dev/prepub/prod)"
                exit 1
                ;;
        esac
    fi

    if [[ ! -f "$config_file" ]]; then
        log_error "Config file not found: $config_file"
        exit 1
    fi

    if is_running; then
        OLD_PID=$(cat "$PID_FILE")
        log_error "Application is already running (PID: $OLD_PID)"
        log_info "To restart, run: $0 stop"
        exit 1
    fi

    check_port "$APP_PORT" || exit 1

    if [[ -n "$debug_port" ]]; then
        check_port "$debug_port" || exit 1
    fi

    if [[ -f "$PID_FILE" ]]; then
        log_warn "Stale PID file found, cleaning up..."
        rm -f "$PID_FILE"
    fi

    log_info "Starting gateway application..."
    log_info "Config file: $config_file"
    log_info "Log file: $LOG_FILE"

    if [[ -z "$APP_MODE" ]]; then
        APP_MODE="bare"
    fi

    # Regenerate OpenAPI schemas from upstreams before launch so /docs and /redoc
    # reflect the latest API changes. Skips gracefully if upstream dependencies
    # aren't installed.
    if [[ -n "$debug_port" ]]; then
        log_info "Debug port: $debug_port (app starts immediately without waiting for debugger)"
        SERVER_ENV="$env_name" nohup "$VENV_DIR/bin/python" -m debugpy --listen "0.0.0.0:$debug_port" \
            "$WORK_DIR/src/gateway/community/main.py" -c "$CONFIG_DIR" --mode "$APP_MODE" >> "$LOG_FILE" 2>&1 &
    else
        SERVER_ENV="$env_name" nohup "$VENV_DIR/bin/python" \
            "$WORK_DIR/src/gateway/community/main.py" -c "$CONFIG_DIR" --mode "$APP_MODE" >> "$LOG_FILE" 2>&1 &
    fi

    APP_PID=$!

    if wait_for_health "$APP_PID" "$APP_PORT"; then
        echo "$APP_PID" > "$PID_FILE"
        echo "$APP_PORT" > "$PORT_FILE"
        log_info "Application started successfully (PID: $APP_PID)"
        log_info "Health check passed: http://localhost:$APP_PORT/health"
        if [[ -n "$debug_port" ]]; then
            log_info "Debug port open: localhost:$debug_port"
        fi
        return 0
    else
        log_error "Application failed to start (health check timeout), check log: $LOG_FILE"
        kill "$APP_PID" 2>/dev/null || true
        exit 1
    fi
}

wait_for_health() {
    local pid=$1
    local port=$2
    local max_attempts=30
    local attempt=1

    log_info "Waiting for application to be ready..."

    while [ $attempt -le $max_attempts ]; do
        if ! kill -0 "$pid" 2>/dev/null; then
            log_error "Application process exited unexpectedly, check log: $LOG_FILE"
            if [[ -f "$LOG_FILE" ]]; then
                log_error "---- Recent error log ----"
                tail -30 "$LOG_FILE" | while IFS= read -r line; do
                    echo -e "${RED}|${NC} $line"
                done
                log_error "---------------------------"
            fi
            return 1
        fi

        if check_health_endpoint "$port"; then
            return 0
        fi

        sleep 2
        attempt=$((attempt + 1))
    done

    if [[ -f "$LOG_FILE" ]]; then
        log_error "---- Recent error log ----"
        tail -30 "$LOG_FILE" | while IFS= read -r line; do
            echo -e "${RED}|${NC} $line"
        done
        log_error "---------------------------"
    fi
    return 1
}

get_pid_by_port() {
    local port=$1
    if command -v lsof &> /dev/null; then
        lsof -t -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | head -1
    elif command -v netstat &> /dev/null; then
        netstat -tuln 2>/dev/null | grep ":$port " | awk '{print $7}' | cut -d'/' -f1 | head -1
    fi
}

do_stop() {
    local stopped=false
    local stop_port="$APP_PORT"

    if [[ -f "$PORT_FILE" ]]; then
        stop_port=$(cat "$PORT_FILE")
    fi

    if is_running; then
        OLD_PID=$(cat "$PID_FILE")
        log_info "Stopping application (PID: $OLD_PID)..."

        kill "$OLD_PID" 2>/dev/null

        for i in {1..10}; do
            if ! kill -0 "$OLD_PID" 2>/dev/null; then
                rm -f "$PID_FILE"
                log_info "Application stopped"
                stopped=true
                break
            fi
            sleep 1
        done

        if [[ "$stopped" != "true" ]]; then
            log_warn "Timeout waiting for shutdown, force killing..."
            kill -9 "$OLD_PID" 2>/dev/null
            rm -f "$PID_FILE"
            log_info "Application force stopped"
            stopped=true
        fi
    else
        rm -f "$PID_FILE"
    fi

    PORT_PID=$(get_pid_by_port "$stop_port")
    if [[ -n "$PORT_PID" ]]; then
        log_warn "Port $stop_port still occupied (PID: $PORT_PID), stopping..."
        kill "$PORT_PID" 2>/dev/null
        sleep 1
        if kill -0 "$PORT_PID" 2>/dev/null; then
            kill -9 "$PORT_PID" 2>/dev/null
        fi
        log_info "Stopped process on port $stop_port (PID: $PORT_PID)"
    elif [[ "$stopped" != "true" ]]; then
        log_warn "Application is not running"
    fi

    rm -f "$PORT_FILE"
}

do_status() {
    local port="${APP_PORT}"

    if [[ -f "$PORT_FILE" ]]; then
        port=$(cat "$PORT_FILE")
    fi

    if is_running; then
        OLD_PID=$(cat "$PID_FILE")
        if check_health_endpoint "$port"; then
            echo -e "${GREEN}● Application running${NC} (PID: $OLD_PID, health: OK)"
        else
            echo -e "${YELLOW}● Application running${NC} (PID: $OLD_PID, health: FAIL)"
        fi
    else
        echo -e "${RED}○ Application not running${NC}"
    fi
}

check_health_endpoint() {
    local port=$1
    curl --noproxy '*' -s "http://127.0.0.1:${port}/health" > /dev/null 2>&1
}

case "${1:-start}" in
    start)
        shift 2>/dev/null || true
        check_venv || exit 1
        check_config || exit 1
        do_start "$@"
        ;;
    stop)
        do_stop
        ;;
    restart)
        shift 2>/dev/null || true
        do_stop
        check_venv || exit 1
        check_config || exit 1
        do_start "$@"
        ;;
    status)
        do_status
        ;;
    *)
        log_error "Unknown command: $1"
        log_usage
        exit 1
        ;;
esac
