#!/bin/bash
#
# start_local_claude_code.sh — claude_code engine local launcher
#
# Usage:
#   ./scripts/start_local_claude_code.sh start   # Start relay + engine
#   ./scripts/start_local_claude_code.sh stop    # Stop both
#   ./scripts/start_local_claude_code.sh status  # Check running state
#
# Prerequisites:
#   - relay repo at ../../../teamclaw-aicoding-relay (or RELAY_DIR env var)
#   - engine .venv exists (run `uv sync` first)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENGINE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$ENGINE_DIR/../.." && pwd)"

# Relay location: default to sibling repo of the ocb project root
RELAY_DIR="${RELAY_DIR:-${PROJECT_ROOT}/../teamclaw-aicoding-relay}"

RELAY_PORT="${RELAY_PORT:-18900}"
ENGINE_PORT="${ENGINE_PORT:-20003}"
LOG_DIR="${ENGINE_DIR}/scripts/.local_logs"

RED=$'\033[0;31m'
GREEN=$'\033[0;32m'
YELLOW=$'\033[1;33m'
CYAN=$'\033[1;36m'
NC=$'\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

kill_port() {
    local port=$1
    local pids
    pids=$(lsof -ti :"$port" 2>/dev/null || true)
    if [ -n "$pids" ]; then
        echo "$pids" | xargs kill -9 2>/dev/null || true
        log_info "Killed process(es) on port $port"
    fi
}

check_port() {
    lsof -ti :"$1" > /dev/null 2>&1
}

wait_for_port() {
    local port=$1
    local name=$2
    local max_wait=${3:-30}
    local waited=0
    while [ "$waited" -lt "$max_wait" ]; do
        if check_port "$port"; then
            log_info "$name ready on port $port"
            return 0
        fi
        sleep 0.5
        waited=$((waited + 1))
    done
    log_error "$name did not start within $((max_wait / 2))s on port $port"
    return 1
}

do_start() {
    mkdir -p "$LOG_DIR"

    # --- Preflight checks ---
    if [ ! -d "$RELAY_DIR" ] || [ ! -f "$RELAY_DIR/package.json" ]; then
        log_error "Relay not found at: $RELAY_DIR"
        log_error "Set RELAY_DIR env var or clone teamclaw-aicoding-relay as sibling"
        exit 1
    fi

    if [ ! -f "$ENGINE_DIR/.venv/bin/activate" ]; then
        log_error "Engine .venv not found. Run 'cd $ENGINE_DIR && uv sync' first"
        exit 1
    fi

    # --- Start relay ---
    if check_port "$RELAY_PORT"; then
        log_warn "Port $RELAY_PORT already in use — assuming relay is running"
    else
        log_info "Starting relay from: $RELAY_DIR"
        cd "$RELAY_DIR"
        PORT="$RELAY_PORT" nohup npx tsx src/server.ts >> "$LOG_DIR/relay.log" 2>&1 &
        cd "$ENGINE_DIR"
        wait_for_port "$RELAY_PORT" "Relay" 30 || exit 1
    fi

    # --- Start engine adapter ---
    if check_port "$ENGINE_PORT"; then
        log_warn "Port $ENGINE_PORT already in use — killing existing process"
        kill_port "$ENGINE_PORT"
        sleep 1
    fi

    log_info "Starting engine adapter (claude_code) on port $ENGINE_PORT"
    cd "$ENGINE_DIR"

    # Use venv python directly (avoid 'source activate' which has hardcoded paths)
    local VENV_PYTHON="$ENGINE_DIR/.venv/bin/python"
    if [ ! -x "$VENV_PYTHON" ]; then
        log_error "Python not found at $VENV_PYTHON"
        exit 1
    fi

    export CHAT_ENGINE=claude_code
    export CLAUDE_CODE_RELAY_URL="ws://localhost:${RELAY_PORT}"
    export ZERO_CHECK_ENABLED=false
    export SERVER_PORT="$ENGINE_PORT"
    export SERVER_HOST="0.0.0.0"
    export PYTHONPATH="$ENGINE_DIR/src:${PYTHONPATH:-}"

    nohup "$VENV_PYTHON" -m uvicorn engine.community.api.app:app \
        --host "$SERVER_HOST" \
        --port "$ENGINE_PORT" \
        --log-level info \
        >> "$LOG_DIR/engine.log" 2>&1 &

    wait_for_port "$ENGINE_PORT" "Engine" 20 || exit 1

    echo ""
    echo "================================================"
    echo -e "  ${CYAN}claude_code engine local environment${NC}"
    echo "================================================"
    echo ""
    echo -e "  ${GREEN}Relay:${NC}       ws://localhost:${RELAY_PORT}"
    echo -e "  ${GREEN}Engine:${NC}      http://localhost:${ENGINE_PORT}"
    echo ""
    echo -e "  ${GREEN}Endpoints:${NC}"
    echo "    Health:      http://localhost:${ENGINE_PORT}/health"
    echo "    Readiness:   http://localhost:${ENGINE_PORT}/readiness"
    echo "    Status:      http://localhost:${ENGINE_PORT}/api/engine/status"
    echo "    WebSocket:   ws://localhost:${ENGINE_PORT}/api/claude-code/ws"
    echo "    API Docs:    http://localhost:${ENGINE_PORT}/docs"
    echo ""
    echo -e "  ${GREEN}Logs:${NC}"
    echo "    Relay:       $LOG_DIR/relay.log"
    echo "    Engine:      $LOG_DIR/engine.log"
    echo ""
    echo -e "  ${GREEN}Test:${NC}"
    echo "    python $ENGINE_DIR/scripts/test_local_claude_code.py"
    echo ""
    echo -e "  ${YELLOW}Stop:${NC}  $0 stop"
    echo ""
}

do_stop() {
    log_info "Stopping claude_code local environment..."
    kill_port "$ENGINE_PORT"
    kill_port "$RELAY_PORT"
    # Also kill tsx relay subprocess
    pkill -f "tsx.*server.ts" 2>/dev/null || true
    pkill -f "engine.community.api.app" 2>/dev/null || true
    log_info "All services stopped"
}

do_status() {
    echo -e "${CYAN}Service Status:${NC}"
    if check_port "$RELAY_PORT"; then
        echo -e "  Relay (port $RELAY_PORT):   ${GREEN}RUNNING${NC}"
    else
        echo -e "  Relay (port $RELAY_PORT):   ${RED}STOPPED${NC}"
    fi
    if check_port "$ENGINE_PORT"; then
        echo -e "  Engine (port $ENGINE_PORT): ${GREEN}RUNNING${NC}"
    else
        echo -e "  Engine (port $ENGINE_PORT): ${RED}STOPPED${NC}"
    fi
}

case "${1:-}" in
    start)
        do_start
        ;;
    stop)
        do_stop
        ;;
    status)
        do_status
        ;;
    *)
        echo "Usage: $0 {start|stop|status}"
        echo ""
        echo "  start   Start relay + engine adapter (claude_code)"
        echo "  stop    Stop both services"
        echo "  status  Show running state"
        exit 1
        ;;
esac
