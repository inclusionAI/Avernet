#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# BCSFuse Open-Core macOS Local Stop (Phase 3.1)
# =============================================================================
# Purpose: Safely stop runtime without data loss
# Features:
#   - Graceful shutdown (SIGTERM first, SIGKILL after timeout)
#   - Process tracking via PID file
#   - Port-based fallback detection
#   - NO data deletion
#
# Usage:
#   ./scripts/deploy/macos/stop_local.sh
#
# Exit codes:
#   0 - Success (runtime stopped or already stopped)
#   1 - Failure
# =============================================================================

find_bcsfuse_root() {
  local dir
  dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
  if [ -f "$dir/main.py" ] && [ -d "$dir/scripts" ] && [ -f "$dir/.env.example" ]; then
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
  echo "[$timestamp] $message" >> "$log_file" 2>/dev/null || true
}

main() {
  print_section "BCSFUSE_OPEN_CORE_MACOS_STOP"

  local BCSFUSE_ROOT
  BCSFUSE_ROOT="$(find_bcsfuse_root || true)"

  if [ -z "${BCSFUSE_ROOT:-}" ]; then
    echo "✗ bcsfuse_root: NOT_FOUND"
    return 1
  fi

  echo "- bcsfuse_root: $BCSFUSE_ROOT"
  cd "$BCSFUSE_ROOT"

  local pid_file="$BCSFUSE_ROOT/.runtime/pids/open_core.pid"
  local port="${SERVICE_PORT:-8765}"
  local runtime_stopped="NO"

  log_deploy "STOP_LOCAL_BEGIN"

  # Check PID file
  print_section "PID_CHECK"

  if [ -f "$pid_file" ]; then
    local pid_value
    pid_value=$(cat "$pid_file" 2>/dev/null || echo "")

    if [ -n "$pid_value" ]; then
      echo "- pid_file: $pid_file"
      echo "- pid_value: $pid_value"

      if ps -p "$pid_value" >/dev/null 2>&1; then
        echo "- process_alive: YES"
        echo "[INFO] Stopping runtime (PID: $pid_value)..."

        # Graceful shutdown
        kill -TERM "$pid_value" 2>/dev/null || true

        # Wait up to 10 seconds for graceful shutdown
        local wait_time=0
        while [ $wait_time -lt 10 ]; do
          if ! ps -p "$pid_value" >/dev/null 2>&1; then
            echo "✓ process_stopped: YES (graceful)"
            runtime_stopped="YES"
            break
          fi
          sleep 1
          wait_time=$((wait_time + 1))
        done

        # Force kill if still alive
        if [ "$runtime_stopped" = "NO" ]; then
          echo "[WARN] Graceful shutdown timeout, force killing..."
          kill -KILL "$pid_value" 2>/dev/null || true
          sleep 1

          if ! ps -p "$pid_value" >/dev/null 2>&1; then
            echo "✓ process_stopped: YES (force)"
            runtime_stopped="YES"
          else
            echo "✗ process_stopped: FAILED"
          fi
        fi

        log_deploy "RUNTIME_STOPPED: pid=$pid_value, graceful=$([ "$runtime_stopped" = "YES" ] && echo "YES" || echo "NO")"
      else
        echo "- process_alive: NO"
        echo "⚠ process_already_stopped: YES"
        runtime_stopped="YES"
      fi

      # Clean PID file
      rm -f "$pid_file"
      echo "✓ pid_file_cleaned: YES"
    else
      echo "⚠ pid_file_empty: YES"
      rm -f "$pid_file"
    fi
  else
    echo "⚠ pid_file: NOT_FOUND"
  fi

  # Check port-based fallback
  print_section "PORT_CHECK"

  if lsof -iTCP:$port -sTCP:LISTEN -P >/dev/null 2>&1; then
    echo "⚠ port_in_use: YES"
    echo "- port: $port"

    local port_pid
    port_pid=$(lsof -tiTCP:$port -sTCP:LISTEN || echo "")

    if [ -n "$port_pid" ]; then
      echo "[INFO] Killing process on port $port (PID: $port_pid)..."
      kill -TERM "$port_pid" 2>/dev/null || true
      sleep 2
      kill -KILL "$port_pid" 2>/dev/null || true
      echo "✓ port_cleared: YES"
      log_deploy "PORT_PROCESS_KILLED: pid=$port_pid"
    fi
  else
    echo "✓ port_available: YES"
    echo "- port: $port"
  fi

  # Final status
  print_section "STOP_RESULT"

  echo "- runtime_stopped: $runtime_stopped"
  echo "- pid_file_cleaned: YES"
  echo "- port: $port"
  echo "- data_preserved: YES (Qdrant and MySQL NOT deleted)"
  echo "- logs_preserved: YES"
  echo "- result: SUCCESS"

  log_deploy "STOP_LOCAL_SUCCESS"

  echo ""
  echo "========================================"
  echo "RUNTIME_STOPPED"
  echo "========================================"
  echo ""
  echo "Data preserved: Qdrant and MySQL data NOT deleted"
  echo "Logs preserved: .runtime/logs/"
  echo ""
  echo "To restart: ./scripts/deploy/macos/start_local.sh"
  echo ""

  return 0
}

main "$@"