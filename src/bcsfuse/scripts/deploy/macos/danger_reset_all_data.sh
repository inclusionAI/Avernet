#!/usr/bin/env bash

# =============================================================================
# ⚠️  DANGER: BCSFuse Open-Core Data Reset (Phase 3.1)
# =============================================================================
# ⚠️  WARNING: This script DELETES ALL DATA
# =============================================================================
# Purpose: Complete data reset (Qdrant + MySQL tables)
# Usage: ONLY for development/testing reset, NOT for production
#
# DESTROYS:
#   - All Qdrant vector data
#   - All MySQL table data (workers, profiles, vectors, audit logs)
#   - All runtime logs
#
# DOES NOT DESTROY:
#   - MySQL database itself
#   - MySQL user/schema permissions
#   - Environment files
#   - Code
#
# Usage:
#   ./scripts/deploy/macos/danger_reset_all_data.sh --confirm-reset
#
# Exit codes:
#   0 - Data reset successful
#   1 - Reset not confirmed or failed
# =============================================================================

set -euo pipefail

# Confirmation check
if [ "${1:-}" != "--confirm-reset" ]; then
  echo "========================================" >&2
  echo "⚠️  DANGER: DATA DESTRUCTION SCRIPT" >&2
  echo "========================================" >&2
  echo "" >&2
  echo "This script will DELETE ALL DATA:" >&2
  echo "  - All Qdrant vector data" >&2
  echo "  - All MySQL table data (workers, profiles, vectors, audit logs)" >&2
  echo "  - All runtime logs" >&2
  echo "" >&2
  echo "This action CANNOT be undone." >&2
  echo "" >&2
  echo "If you are ABSOLUTELY SURE, run:" >&2
  echo "  ./scripts/deploy/macos/danger_reset_all_data.sh --confirm-reset" >&2
  echo "" >&2
  exit 1
fi

find_bcsfuse_root() {
  local dir
  dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
  if [ -f "$dir/main.py" ] && [ -d "$dir/scripts" ] && [ -f "$dir/.env.example" ]; then
    echo "$dir"
    return 0
  fi
  return 1
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
  echo "========================================"
  echo "⚠️  DANGER: DATA RESET IN PROGRESS"
  echo "========================================"
  echo ""

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

  local qdrant_path="${QDRANT_LOCAL_PATH:-$BCSFUSE_ROOT/.runtime/data/qdrant}"

  # Step 1: Stop runtime if running
  echo ""
  echo "========================================"
  echo "STEP 1: STOP RUNTIME"
  echo "========================================"

  local stop_script="$BCSFUSE_ROOT/scripts/deploy/macos/stop_local.sh"
  if [ -f "$stop_script" ]; then
    echo "[INFO] Stopping runtime..."
    bash "$stop_script" || echo "[WARN] Stop failed, continuing anyway..."
  else
    echo "[WARN] stop_local.sh not found, skipping..."
  fi

  # Step 2: Reset Qdrant
  echo ""
  echo "========================================"
  echo "STEP 2: RESET QDRANT"
  echo "========================================"

  if [ -d "$qdrant_path" ]; then
    echo "[DANGER] Deleting Qdrant data: $qdrant_path"
    rm -rf "$qdrant_path"
    echo "✓ qdrant_deleted: YES"
  else
    echo "- qdrant_path: NOT_FOUND"
  fi

  # Step 3: Reset MySQL tables
  echo ""
  echo "========================================"
  echo "STEP 3: RESET MYSQL TABLES"
  echo "========================================"

  local schema_script="$BCSFUSE_ROOT/tests/smoke/runtime_mysql_schema_setup.py"

  if [ -f "$schema_script" ]; then
    echo "[DANGER] Dropping and recreating MySQL tables..."

    # Drop tables
    if python3 "$schema_script" cleanup 2>&1; then
      echo "✓ tables_dropped: YES"
    else
      echo "⚠ tables_dropped: FAILED (some tables may not exist)"
    fi

    # Recreate tables
    if python3 "$schema_script" setup 2>&1; then
      echo "✓ tables_recreated: YES"
    else
      echo "✗ tables_recreated: FAILED"
      return 1
    fi
  else
    echo "✗ schema_script: NOT_FOUND"
    echo "[ERROR] Cannot reset MySQL tables without schema script"
    return 1
  fi

  # Step 4: Clear logs
  echo ""
  echo "========================================"
  echo "STEP 4: CLEAR LOGS"
  echo "========================================"

  local logs_dir="$BCSFUSE_ROOT/.runtime/logs"
  if [ -d "$logs_dir" ]; then
    echo "[DANGER] Clearing logs: $logs_dir"
    rm -rf "$logs_dir"/*
    mkdir -p "$logs_dir"
    echo "✓ logs_cleared: YES"
  fi

  # Step 5: Clear PIDs
  echo ""
  echo "========================================"
  echo "STEP 5: CLEAR PIDS"
  echo "========================================"

  local pids_dir="$BCSFUSE_ROOT/.runtime/pids"
  if [ -d "$pids_dir" ]; then
    echo "[INFO] Clearing PIDs: $pids_dir"
    rm -rf "$pids_dir"/*
    mkdir -p "$pids_dir"
    echo "✓ pids_cleared: YES"
  fi

  # Final result
  echo ""
  echo "========================================"
  echo "⚠️  DATA RESET COMPLETE"
  echo "========================================"
  echo ""
  echo "DELETED:"
  echo "  ✓ Qdrant vector data: $qdrant_path"
  echo "  ✓ MySQL table data: workers, worker_profile_content, worker_runtime_state, worker_audit_log"
  echo "  ✓ Runtime logs"
  echo "  ✓ PIDs"
  echo ""
  echo "PRESERVED:"
  echo "  ✓ MySQL database: ${MYSQL_DATABASE:-bcsfuse_oss}"
  echo "  ✓ Environment files"
  echo "  ✓ Code"
  echo ""
  echo "Next steps:"
  echo "  1. Start runtime: ./scripts/deploy/macos/start_local.sh"
  echo "  2. (Optional) Re-run bootstrap: ./scripts/deploy/macos/bootstrap_local.sh"
  echo ""

  return 0
}

main "$@"