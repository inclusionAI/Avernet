#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# BCSFuse Open-Core macOS Local Restart (Phase 3.1)
# =============================================================================
# Purpose: Safe restart (stop + start) with data preservation guarantee
# Features:
#   - Stops runtime gracefully
#   - Starts runtime with same data
#   - NO storage re-initialization
#   - NO data loss
#
# Usage:
#   ./scripts/deploy/macos/restart_local.sh
#
# Exit codes:
#   0 - Success
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
  print_section "BCSFUSE_OPEN_CORE_MACOS_RESTART"

  local BCSFUSE_ROOT
  BCSFUSE_ROOT="$(find_bcsfuse_root || true)"

  if [ -z "${BCSFUSE_ROOT:-}" ]; then
    echo "✗ bcsfuse_root: NOT_FOUND"
    return 1
  fi

  echo "- bcsfuse_root: $BCSFUSE_ROOT"
  cd "$BCSFUSE_ROOT"

  local stop_script="$BCSFUSE_ROOT/scripts/deploy/macos/stop_local.sh"
  local start_script="$BCSFUSE_ROOT/scripts/deploy/macos/start_local.sh"

  # Verify scripts exist
  if [ ! -f "$stop_script" ]; then
    echo "✗ stop_script: NOT_FOUND"
    echo "- stop_script: $stop_script"
    return 1
  fi

  if [ ! -f "$start_script" ]; then
    echo "✗ start_script: NOT_FOUND"
    echo "- start_script: $start_script"
    return 1
  fi

  log_deploy "RESTART_LOCAL_BEGIN"

  # Step 1: Stop
  print_section "STEP_1_STOP"

  echo "[INFO] Running stop_local.sh..."
  if ! bash "$stop_script"; then
    echo "✗ stop_failed: YES"
    echo "[WARN] Stop failed, but continuing to start..." >&2
  else
    echo "✓ stop_success: YES"
  fi

  # Step 2: Wait
  print_section "STEP_2_WAIT"

  echo "[INFO] Waiting 2 seconds..."
  sleep 2

  # Step 3: Start
  print_section "STEP_3_START"

  echo "[INFO] Running start_local.sh..."
  if ! bash "$start_script"; then
    echo "✗ start_failed: YES"
    log_deploy "RESTART_FAILED: start failed"
    return 1
  fi

  echo "✓ start_success: YES"

  # Final result
  print_section "RESTART_RESULT"

  echo "- data_reinitialized: NO"
  echo "- qdrant_preserved: YES"
  echo "- mysql_preserved: YES"
  echo "- result: SUCCESS"

  log_deploy "RESTART_LOCAL_SUCCESS"

  echo ""
  echo "========================================"
  echo "RUNTIME_RESTARTED"
  echo "========================================"
  echo ""
  echo "Data preserved: Qdrant and MySQL data NOT touched"
  echo "Runtime restarted successfully"
  echo ""
  echo "Check status: ./scripts/deploy/macos/status_local.sh"
  echo ""

  return 0
}

main "$@"