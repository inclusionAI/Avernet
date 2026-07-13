#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# BCSFuse Open-Core macOS Local Start (Phase 3.1)
# =============================================================================
# Purpose: Start open-core runtime with fixed paths
# Features:
#   - Fixed log path: .runtime/logs/open_core_runtime.log
#   - Fixed PID path: .runtime/pids/open_core.pid
#   - Fixed Qdrant path: .runtime/data/qdrant (unless externally set)
#   - Restart guarantee: does NOT clear Qdrant or MySQL
#   - Idempotent: if already running, health check and exit 0
#
# Usage:
#   ./scripts/deploy/macos/start_local.sh
#
# Exit codes:
#   0 - Success (runtime started or already running)
#   1 - Failure
# =============================================================================

# =============================================================================
# Helper Functions
# =============================================================================

find_bcsfuse_root() {
  local dir
  dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

  if [ -f "$dir/main.py" ] \
    && [ -d "$dir/scripts" ] \
    && [ -f "$dir/.env.example" ]; then
    echo "$dir"
    return 0
  fi

  return 1
}

print_section() {
  echo ""
  echo "========================================"
  echo "$1"
  echo "========================================"
}

log_deploy() {
  local message="$1"
  local log_file="${BCSFUSE_ROOT:-.}/.runtime/logs/deploy.log"
  local timestamp
  timestamp=$(date '+%Y-%m-%d %H:%M:%S')
  echo "[$timestamp] $message" >> "$log_file"
}

load_env() {
  local env_file="$1"

  if [ ! -f "$env_file" ]; then
    return 1
  fi

  set -a
  source "$env_file"
  set +a

  return 0
}

# =============================================================================
# Main Start Logic
# =============================================================================

main() {
  print_section "BCSFUSE_OPEN_CORE_MACOS_START"

  # Step 1: Find BCSFuse root
  local BCSFUSE_ROOT
  BCSFUSE_ROOT="$(find_bcsfuse_root || true)"

  if [ -z "${BCSFUSE_ROOT:-}" ]; then
    echo "✗ bcsfuse_root: NOT_FOUND"
    echo ""
    echo "[ERROR] BCSFuse root not found" >&2
    return 1
  fi

  echo "- bcsfuse_root: $BCSFUSE_ROOT"
  cd "$BCSFUSE_ROOT"

  # Resolve Python interpreter (venv preferred if bootstrap_local.sh was run)
  local PYTHON_CMD="python3"
  if [ -x "${BCSFUSE_ROOT}/.venv/bin/python" ]; then
    PYTHON_CMD="${BCSFUSE_ROOT}/.venv/bin/python"
  fi

  # Initialize runtime directories
  local runtime_dir="$BCSFUSE_ROOT/.runtime"
  local logs_dir="$runtime_dir/logs"
  local pids_dir="$runtime_dir/pids"
  local data_dir="$runtime_dir/data"
  local env_dir="$runtime_dir/env"

  mkdir -p "$logs_dir" "$pids_dir" "$data_dir" "$env_dir"

  # Initialize deploy log
  log_deploy "START_LOCAL_BEGIN"

  # Step 2: Load environment
  print_section "ENVIRONMENT_LOAD"

  local env_file="$BCSFUSE_ROOT/.runtime/env/.env.local"
  local env_loaded="NO"

  if [ -f "$env_file" ]; then
    echo "- env_file: $env_file"
    if load_env "$env_file"; then
      env_loaded="YES"
      echo "✓ env_loaded: YES"
      log_deploy "ENV_LOADED from $env_file"
    else
      echo "✗ env_loaded: FAILED"
    fi
  else
    echo "✗ env_file: NOT_FOUND"
    echo "- env_file: $env_file"
  fi

  if [ "$env_loaded" = "NO" ]; then
    echo "- result: FAIL"
    echo ""
    echo "[ERROR] No environment file found" >&2
    echo "[HINT] Run: ./scripts/deploy/macos/bootstrap_local.sh" >&2
    log_deploy "START_FAILED: no env file"
    return 1
  fi

  # Step 3: Set fixed paths
  print_section "FIXED_PATHS"

  local log_file="$logs_dir/open_core_runtime.log"
  local pid_file="$pids_dir/open_core.pid"
  local qdrant_path_default="$data_dir/qdrant"
  local qdrant_path="${QDRANT_LOCAL_PATH:-$qdrant_path_default}"
  local port="${SERVICE_PORT:-8765}"

  # Export fixed paths
  export OPEN_CORE_RUNTIME_LOG="$log_file"
  export QDRANT_LOCAL_PATH="$qdrant_path"
  export SERVICE_PORT="$port"
  export BCSFUSE_SERVER_HOST="${BCSFUSE_SERVER_HOST:-127.0.0.1}"
  export BCSFUSE_SERVER_PORT="$port"
  export RUNTIME_MODE="${RUNTIME_MODE:-runtime}"
  export BCSFUSE_PROVIDER_MODE="${BCSFUSE_PROVIDER_MODE:-runtime}"

  echo "- runtime_log: $log_file"
  echo "- pid_file: $pid_file"
  echo "- qdrant_path: $qdrant_path"
  echo "- service_port: $port"
  echo "- service_host: ${BCSFUSE_SERVER_HOST}"

  log_deploy "FIXED_PATHS: log=$log_file, pid=$pid_file, qdrant=$qdrant_path"

  # Step 4: Check if already running
  print_section "RUNTIME_STATUS_CHECK"

  local already_running="NO"
  local stale_pid="NO"
  local pid_value=""

  if [ -f "$pid_file" ]; then
    pid_value=$(cat "$pid_file" 2>/dev/null || echo "")

    if [ -n "$pid_value" ] && ps -p "$pid_value" >/dev/null 2>&1; then
      already_running="YES"
      echo "- pid_file: $pid_file"
      echo "- pid_value: $pid_value"
      echo "- process_alive: YES"
      echo "✓ runtime_already_running: YES"
      log_deploy "RUNTIME_ALREADY_RUNNING: pid=$pid_value"
    else
      stale_pid="YES"
      echo "- pid_file: $pid_file"
      echo "- pid_value: $pid_value"
      echo "- process_alive: NO"
      echo "⚠ stale_pid_detected: YES"
      log_deploy "STALE_PID_DETECTED: pid=$pid_value"
    fi
  else
    echo "- pid_file: NOT_FOUND"
    echo "- runtime_already_running: NO"
  fi

  # If already running, health check and exit
  if [ "$already_running" = "YES" ]; then
    print_section "HEALTH_CHECK"

    local health_url="http://localhost:$port/health"

    echo "- health_url: $health_url"
    echo "[INFO] Checking health endpoint..."

    local health_result
    health_result=$(curl -fsS "$health_url" 2>&1 || echo "FAIL")

    if echo "$health_result" | grep -qi "healthy\|ok\|pass"; then
      echo "✓ health: PASS"
      echo "- health_response: $health_result"
      echo ""
      echo "========================================"
      echo "RUNTIME_ALREADY_RUNNING"
      echo "========================================"
      echo "- pid: $pid_value"
      echo "- port: $port"
      echo "- health: PASS"
      echo "- result: SUCCESS"
      echo ""
      echo "[INFO] Runtime is already running and healthy" >&2
      log_deploy "RUNTIME_ALREADY_RUNNING_HEALTHY"
      return 0
    else
      echo "✗ health: FAIL"
      echo "- health_response: $health_result"
      echo ""
      echo "========================================"
      echo "RUNTIME_RUNNING_UNHEALTHY"
      echo "========================================"
      echo "[WARN] Runtime is running but unhealthy, attempting restart..." >&2
      log_deploy "RUNTIME_UNHEALTHY attempting restart"

      # Kill unhealthy process
      if [ -n "$pid_value" ]; then
        echo "[INFO] Stopping unhealthy runtime (PID: $pid_value)..."
        kill -TERM "$pid_value" 2>/dev/null || true
        sleep 2
        kill -KILL "$pid_value" 2>/dev/null || true
        rm -f "$pid_file"
      fi

      already_running="NO"
    fi
  fi

  # Step 5: Clean stale PID if needed
  if [ "$stale_pid" = "YES" ]; then
    echo "[INFO] Cleaning stale PID file..."
    rm -f "$pid_file"
    log_deploy "STALE_PID_CLEANED"
  fi

  # Step 6: Check port availability
  print_section "PORT_CHECK"

  if lsof -iTCP:$port -sTCP:LISTEN -P >/dev/null 2>&1; then
    echo "⚠ port_available: NO"
    echo "- port: $port"
    echo "[WARN] Port $port is already in use" >&2

    # Find and kill process on port
    local port_pid
    port_pid=$(lsof -tiTCP:$port -sTCP:LISTEN || echo "")

    if [ -n "$port_pid" ]; then
      echo "[INFO] Killing process on port $port (PID: $port_pid)..."
      kill -TERM "$port_pid" 2>/dev/null || true
      sleep 2
      kill -KILL "$port_pid" 2>/dev/null || true
      log_deploy "PORT_CONFLICT_KILLED: pid=$port_pid"
    fi

    # Verify port is free
    sleep 1
    if lsof -iTCP:$port -sTCP:LISTEN -P >/dev/null 2>&1; then
      echo "✗ port_available: STILL_BUSY"
      echo "- result: FAIL"
      echo ""
      echo "[ERROR] Port $port is still in use after kill attempt" >&2
      log_deploy "START_FAILED: port busy"
      return 1
    else
      echo "✓ port_available: YES (after cleanup)"
    fi
  else
    echo "✓ port_available: YES"
    echo "- port: $port"
  fi

  # Step 7: Ensure Qdrant path exists
  print_section "QDRANT_STORAGE_CHECK"

  if [ ! -d "$qdrant_path" ]; then
    echo "[INFO] Creating Qdrant storage directory..."
    mkdir -p "$qdrant_path"
    echo "✓ qdrant_path_created: YES"
    log_deploy "QDRANT_PATH_CREATED: $qdrant_path"
  else
    echo "✓ qdrant_path_exists: YES"
  fi

  # Check for lock file
  local lock_file="$qdrant_path/.lock"
  if [ -f "$lock_file" ]; then
    echo "⚠ qdrant_lock_detected: YES"
    echo "[WARN] Qdrant lock file found: $lock_file" >&2
    echo "[WARN] Another process may be using Qdrant storage" >&2
  else
    echo "✓ qdrant_lock_detected: NO"
  fi

  # Step 8: Start runtime
  print_section "RUNTIME_START"

  echo "[INFO] Starting open-core runtime..."
  echo "- entrypoint: main.py"
  echo "- log: $log_file"
  echo "- pid: $pid_file"

  # Clear old log
  : > "$log_file"

  # Start runtime (prefer venv python so dependencies are resolvable)
  nohup "$PYTHON_CMD" main.py >> "$log_file" 2>&1 &
  local runtime_pid=$!

  echo "$runtime_pid" > "$pid_file"

  echo "✓ runtime_started: YES"
  echo "- pid: $runtime_pid"
  log_deploy "RUNTIME_STARTED: pid=$runtime_pid"

  # Wait for startup
  echo "[INFO] Waiting for runtime to start... (5 seconds)"
  sleep 5

  # Step 9: Verify process is alive
  print_section "PROCESS_CHECK"

  if ! ps -p "$runtime_pid" >/dev/null 2>&1; then
    echo "✗ process_alive: NO"
    echo "- pid: $runtime_pid"
    echo ""
    echo "========================================"
    echo "RUNTIME_STARTUP_FAILED"
    echo "========================================"
    echo ""
    echo "[ERROR] Runtime process exited early" >&2
    echo "[HINT] Check log: $log_file" >&2
    tail -80 "$log_file" >&2 || true
    log_deploy "RUNTIME_EXITED_EARLY"
    return 1
  fi

  echo "✓ process_alive: YES"
  echo "- pid: $runtime_pid"

  # Step 10: Verify port is listening
  print_section "PORT_LISTEN_CHECK"

  if ! lsof -iTCP:$port -sTCP:LISTEN -P >/dev/null 2>&1; then
    echo "✗ port_listening: NO"
    echo ""
    echo "========================================"
    echo "RUNTIME_PORT_NOT_LISTENING"
    echo "========================================"
    echo ""
    echo "[ERROR] Runtime not listening on port $port" >&2
    echo "[HINT] Check log: $log_file" >&2
    tail -120 "$log_file" >&2 || true
    log_deploy "RUNTIME_PORT_NOT_LISTENING"
    return 1
  fi

  echo "✓ port_listening: YES"
  echo "- port: $port"

  # Step 11: Health check
  print_section "HEALTH_CHECK"

  local health_url="http://localhost:$port/health"
  local health_check_max_retries=10
  local health_check_retry=0
  local health_passed="NO"

  echo "- health_url: $health_url"
  echo "- max_retries: $health_check_max_retries"

  while [ $health_check_retry -lt $health_check_max_retries ]; do
    health_check_retry=$((health_check_retry + 1))
    echo "[INFO] Health check attempt $health_check_retry/$health_check_max_retries..."

    local health_result
    health_result=$(curl -fsS "$health_url" 2>&1 || echo "FAIL")

    if echo "$health_result" | grep -qi "healthy\|ok\|pass"; then
      health_passed="YES"
      echo "✓ health: PASS"
      echo "- health_response: $health_result"
      break
    else
      echo "⚠ health: RETRY ($health_check_retry)"
      sleep 2
    fi
  done

  if [ "$health_passed" = "NO" ]; then
    echo "✗ health: FAIL"
    echo ""
    echo "========================================"
    echo "RUNTIME_HEALTH_CHECK_FAILED"
    echo "========================================"
    echo ""
    echo "[WARN] Runtime started but health check failed" >&2
    echo "[HINT] Check log: $log_file" >&2
    tail -40 "$log_file" >&2 || true
    log_deploy "RUNTIME_HEALTH_FAILED"
    return 1
  fi

  # Final result
  print_section "START_RESULT"

  echo "- bcsfuse_root: $BCSFUSE_ROOT"
  echo "- runtime_pid: $runtime_pid"
  echo "- runtime_port: $port"
  echo "- runtime_log: $log_file"
  echo "- qdrant_path: $qdrant_path"
  echo "- process_alive: YES"
  echo "- port_listening: YES"
  echo "- health_check: PASS"
  echo "- restart_clears_data: NO"
  echo "- result: SUCCESS"

  log_deploy "START_LOCAL_SUCCESS: pid=$runtime_pid, port=$port"

  echo ""
  echo "========================================"
  echo "RUNTIME_STARTED_SUCCESSFULLY"
  echo "========================================"
  echo ""
  echo "PID: $runtime_pid"
  echo "Port: $port"
  echo "Log: $log_file"
  echo "Qdrant: $qdrant_path"
  echo ""
  echo "Health: curl http://localhost:$port/health"
  echo "Providers: curl http://localhost:$port/providers"
  echo ""
  echo "Next step: ./scripts/deploy/macos/status_local.sh"
  echo ""

  return 0
}

main "$@"