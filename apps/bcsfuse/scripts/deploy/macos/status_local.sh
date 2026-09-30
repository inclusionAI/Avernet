#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# BCSFuse Open-Core macOS Local Status (Phase 3.1)
# =============================================================================
# Purpose: Report service status with data location info
# Features:
#   - Service running status
#   - PID and port info
#   - Health endpoint check
#   - Data locations (Qdrant, MySQL, logs)
#   - Table count and collection info
#
# Usage:
#   ./scripts/deploy/macos/status_local.sh
#
# Exit codes:
#   0 - Service is running and healthy
#   1 - Service is not running or unhealthy
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

main() {
  print_section "BCSFUSE_OPEN_CORE_MACOS_STATUS"

  local BCSFUSE_ROOT
  BCSFUSE_ROOT="$(find_bcsfuse_root || true)"

  if [ -z "${BCSFUSE_ROOT:-}" ]; then
    echo "✗ bcsfuse_root: NOT_FOUND"
    return 1
  fi

  echo "- bcsfuse_root: $BCSFUSE_ROOT"
  cd "$BCSFUSE_ROOT"

  # Load environment
  local env_file="$BCSFUSE_ROOT/.runtime/env/.env.local"

  if [ -f "$env_file" ]; then
    load_env "$env_file" || true
  fi

  local port="${SERVICE_PORT:-8765}"
  local pid_file="$BCSFUSE_ROOT/.runtime/pids/open_core.pid"
  local log_file="$BCSFUSE_ROOT/.runtime/logs/open_core_runtime.log"
  local qdrant_path="${QDRANT_LOCAL_PATH:-$BCSFUSE_ROOT/.runtime/data/qdrant}"

  # Status variables
  local service_running="NO"
  local pid_value=""
  local health_status="FAIL"
  local port_status="FREE"

  # Step 1: Check PID file
  print_section "SERVICE_STATUS"

  if [ -f "$pid_file" ]; then
    pid_value=$(cat "$pid_file" 2>/dev/null || echo "")

    if [ -n "$pid_value" ] && ps -p "$pid_value" >/dev/null 2>&1; then
      service_running="YES"
      echo "✓ service_running: YES"
      echo "- pid: $pid_value"

      # Get process info
      local proc_info
      proc_info=$(ps -p "$pid_value" -o pid,ppid,command | tail -1 || echo "unknown")
      echo "- process_info: $proc_info"
    else
      echo "✗ service_running: NO"
      echo "- pid_file_stale: YES"
    fi
  else
    echo "✗ service_running: NO"
    echo "- pid_file: NOT_FOUND"
  fi

  # Step 2: Check port
  print_section "PORT_STATUS"

  if lsof -iTCP:$port -sTCP:LISTEN -P >/dev/null 2>&1; then
    port_status="LISTEN"
    echo "✓ port_status: LISTEN"
    echo "- port: $port"

    # Get process on port
    local port_pid port_proc
    port_pid=$(lsof -tiTCP:$port -sTCP:LISTEN || echo "")
    if [ -n "$port_pid" ]; then
      port_proc=$(ps -p "$port_pid" -o command | tail -1 || echo "unknown")
      echo "- port_pid: $port_pid"
      echo "- port_process: $port_proc"
    fi
  else
    port_status="FREE"
    echo "✗ port_status: FREE"
    echo "- port: $port"
  fi

  # Step 3: Health check
  if [ "$service_running" = "YES" ]; then
    print_section "HEALTH_CHECK"

    local health_url="http://localhost:$port/health"
    local health_result
    health_result=$(curl -fsS "$health_url" 2>&1 || echo "FAIL")

    if echo "$health_result" | grep -qi "healthy\|ok\|pass"; then
      health_status="PASS"
      echo "✓ health_status: PASS"
    else
      health_status="FAIL"
      echo "✗ health_status: FAIL"
    fi

    echo "- health_url: $health_url"
    echo "- health_response: ${health_result:0:200}"
  fi

  # Step 4: Data locations
  print_section "DATA_LOCATIONS"

  echo "- runtime_log: $log_file"
  echo "- runtime_log_exists: $([ -f "$log_file" ] && echo "YES" || echo "NO")"

  echo "- qdrant_path: $qdrant_path"
  echo "- qdrant_exists: $([ -d "$qdrant_path" ] && echo "YES" || echo "NO")"

  if [ -d "$qdrant_path" ]; then
    local qdrant_lock="$qdrant_path/.lock"
    echo "- qdrant_locked: $([ -f "$qdrant_lock" ] && echo "YES" || echo "NO")"

    # Check for collections
    local qdrant_collections=""
    if [ -d "$qdrant_path/collection" ]; then
      qdrant_collections=$(ls -1 "$qdrant_path/collection" 2>/dev/null | tr '\n' ' ' || echo "")
    fi
    echo "- qdrant_collections: ${qdrant_collections:-none}"
  fi

  echo "- mysql_host: ${MYSQL_HOST:-localhost}"
  echo "- mysql_port: ${MYSQL_PORT:-3306}"
  echo "- mysql_database: ${MYSQL_DATABASE:-bcsfuse_oss}"

  # Check MySQL tables if client available
  if command -v mysql >/dev/null 2>&1 && [ -n "${MYSQL_HOST:-}" ]; then
    local tables_count
    tables_count=$(mysql -h"${MYSQL_HOST}" -P"${MYSQL_PORT}" -u"${MYSQL_USER}" -p"${MYSQL_PASSWORD}" -D"${MYSQL_DATABASE}" -N -e "SHOW TABLES;" 2>/dev/null | wc -l | tr -d ' ' || echo "0")
    echo "- mysql_tables_count: $tables_count"
  fi

  # Final summary
  print_section "STATUS_SUMMARY"

  echo "- service_running: $service_running"
  echo "- port_status: $port_status"
  echo "- health_status: $health_status"
  echo "- pid: ${pid_value:-not_found}"
  echo "- port: $port"
  echo "- log_file: $log_file"
  echo "- qdrant_path: $qdrant_path"
  echo "- mysql_database: ${MYSQL_DATABASE:-not_set}"

  # Exit code
  if [ "$service_running" = "YES" ] && [ "$health_status" = "PASS" ]; then
    echo "- result: HEALTHY"
    echo ""
    echo "Runtime is running and healthy"
    return 0
  elif [ "$service_running" = "YES" ]; then
    echo "- result: UNHEALTHY"
    echo ""
    echo "[WARN] Runtime is running but unhealthy" >&2
    return 1
  else
    echo "- result: STOPPED"
    echo ""
    echo "Runtime is not running"
    return 1
  fi
}

main "$@"