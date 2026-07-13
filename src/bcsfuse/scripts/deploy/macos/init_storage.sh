#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# BCSFuse Open-Core Storage Initialization (Phase 3.1)
# =============================================================================
# Purpose: Initialize MySQL schema and Qdrant local storage (idempotent)
# Features:
#   - NO data destruction (no DROP DATABASE, no DROP TABLE)
#   - NO rm -rf on existing data
#   - Idempotent (safe to run multiple times)
#   - Logs all operations to .runtime/logs/deploy.log
#
# Usage:
#   ./scripts/deploy/macos/init_storage.sh
#
# Exit codes:
#   0 - Success
#   1 - Failure (MySQL connection failed, schema init failed)
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

  # Source environment variables
  set -a
  source "$env_file"
  set +a

  return 0
}

# =============================================================================
# Main Initialization Logic
# =============================================================================

main() {
  print_section "BCSFUSE_OPEN_CORE_STORAGE_INIT"

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

  # Initialize deploy log
  local deploy_log="$BCSFUSE_ROOT/.runtime/logs/deploy.log"
  mkdir -p "$(dirname "$deploy_log")"
  log_deploy "INIT_STORAGE_START"

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
    log_deploy "INIT_STORAGE_FAILED: no env file"
    return 1
  fi

  # Step 3: Validate critical environment variables
  print_section "ENVIRONMENT_VALIDATE"

  local missing_vars=""

  if [ -z "${MYSQL_HOST:-}" ]; then missing_vars="${missing_vars}MYSQL_HOST "; fi
  if [ -z "${MYSQL_PORT:-}" ]; then missing_vars="${missing_vars}MYSQL_PORT "; fi
  if [ -z "${MYSQL_USER:-}" ]; then missing_vars="${missing_vars}MYSQL_USER "; fi
  if [ -z "${MYSQL_PASSWORD:-}" ]; then missing_vars="${missing_vars}MYSQL_PASSWORD "; fi
  if [ -z "${MYSQL_DATABASE:-}" ]; then missing_vars="${missing_vars}MYSQL_DATABASE "; fi

  echo "- mysql_host: ${MYSQL_HOST:-MISSING}"
  echo "- mysql_port: ${MYSQL_PORT:-MISSING}"
  echo "- mysql_user: ${MYSQL_USER:-MISSING}"
  echo "- mysql_database: ${MYSQL_DATABASE:-MISSING}"
  echo "- mysql_password: SET"

  if [ -n "$missing_vars" ]; then
    echo "- missing_vars: $missing_vars"
    echo "- result: FAIL"
    echo ""
    echo "[ERROR] Missing MySQL environment variables: $missing_vars" >&2
    log_deploy "INIT_STORAGE_FAILED: missing MySQL vars"
    return 1
  fi

  echo "✓ mysql_config: PASS"

  # Step 4: Check MySQL connection
  print_section "MYSQL_CONNECTION_CHECK"

  local mysql_client_available="NO"
  if command -v mysql >/dev/null 2>&1; then
    mysql_client_available="YES"
    echo "✓ mysql_client: $(command -v mysql)"
  else
    echo "⚠ mysql_client: NOT_FOUND (will use Python)"
  fi

  # Use Python to check MySQL connection
  echo "[INFO] Checking MySQL connection via Python..."

  local mysql_check_result
  mysql_check_result=$(python3 - 2>&1 <<'PY'
import os
import sys

try:
    import mysql.connector

    host = os.getenv("MYSQL_HOST", "localhost")
    port = int(os.getenv("MYSQL_PORT", "3306"))
    user = os.getenv("MYSQL_USER", "root")
    password = os.getenv("MYSQL_PASSWORD", "")
    database = os.getenv("MYSQL_DATABASE", "bcsfuse_oss")

    conn = mysql.connector.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        autocommit=True,
    )

    cursor = conn.cursor(buffered=True)
    cursor.execute("SELECT 1")
    cursor.fetchall()
    cursor.close()
    conn.close()

    print("PASS")
    sys.exit(0)

except Exception as e:
    print(f"FAIL: {e}", file=sys.stderr)
    sys.exit(1)
PY
) || echo "FAIL"

  if echo "$mysql_check_result" | grep -q "PASS"; then
    echo "✓ mysql_connection: PASS"
    log_deploy "MYSQL_CONNECTION_PASS"
  else
    echo "✗ mysql_connection: FAIL"
    echo "- error: $mysql_check_result"
    echo ""
    echo "========================================"
    echo "MYSQL_INIT_NEEDS_MANUAL_ACTION"
    echo "========================================"
    echo ""
    echo "MySQL connection failed. Please ensure:"
    echo "  1. MySQL server is running at ${MYSQL_HOST}:${MYSQL_PORT}"
    echo "  2. Database '${MYSQL_DATABASE}' exists"
    echo "  3. User '${MYSQL_USER}' has access permissions"
    echo ""
    echo "Manual database creation SQL:"
    echo "  CREATE DATABASE IF NOT EXISTS ${MYSQL_DATABASE} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
    echo ""
    log_deploy "MYSQL_CONNECTION_FAILED: $mysql_check_result"
    return 1
  fi

  # Step 5: Count existing tables
  print_section "MYSQL_TABLES_CHECK"

  local tables_before=""
  local tables_before_count=0

  if [ "$mysql_client_available" = "YES" ]; then
    tables_before=$(mysql -h"${MYSQL_HOST}" -P"${MYSQL_PORT}" -u"${MYSQL_USER}" -p"${MYSQL_PASSWORD}" -D"${MYSQL_DATABASE}" -N -e "SHOW TABLES;" 2>/dev/null || echo "")

    if [ -n "$tables_before" ]; then
      tables_before_count=$(echo "$tables_before" | wc -l | tr -d ' ')
    fi
  fi

  echo "- tables_existing_before: $tables_before_count"
  log_deploy "MYSQL_TABLES_BEFORE: $tables_before_count"

  # Step 6: Run schema setup
  print_section "MYSQL_SCHEMA_INIT"

  local schema_script="$BCSFUSE_ROOT/tests/smoke/runtime_mysql_schema_setup.py"

  if [ ! -f "$schema_script" ]; then
    echo "✗ schema_script: NOT_FOUND"
    echo "- schema_script: $schema_script"
    echo "- result: FAIL"
    echo ""
    echo "[ERROR] MySQL schema setup script not found" >&2
    log_deploy "SCHEMA_SCRIPT_NOT_FOUND"
    return 1
  fi

  echo "✓ schema_script: FOUND"
  echo "- schema_script: $schema_script"
  echo "[INFO] Running schema setup (idempotent, no data loss)..."

  if python3 "$schema_script" setup 2>&1 | tee -a "$deploy_log"; then
    echo "✓ schema_init: PASS"
    log_deploy "MYSQL_SCHEMA_INIT_PASS"
  else
    echo "✗ schema_init: FAIL"
    echo "- result: FAIL"
    echo ""
    echo "[ERROR] MySQL schema initialization failed" >&2
    log_deploy "MYSQL_SCHEMA_INIT_FAILED"
    return 1
  fi

  # Step 7: Count tables after init
  print_section "MYSQL_TABLES_VERIFY"

  local tables_after=""
  local tables_after_count=0

  if [ "$mysql_client_available" = "YES" ]; then
    tables_after=$(mysql -h"${MYSQL_HOST}" -P"${MYSQL_PORT}" -u"${MYSQL_USER}" -p"${MYSQL_PASSWORD}" -D"${MYSQL_DATABASE}" -N -e "SHOW TABLES;" 2>/dev/null || echo "")

    if [ -n "$tables_after" ]; then
      tables_after_count=$(echo "$tables_after" | wc -l | tr -d ' ')
    fi
  fi

  echo "- tables_created_or_verified: $tables_after_count"
  log_deploy "MYSQL_TABLES_AFTER: $tables_after_count"

  # Required tables
  local required_tables=(
    "workers"
    "worker_runtime_state"
    "worker_profile_content"
    "worker_audit_log"
  )

  echo ""
  echo "[INFO] Verifying required tables..."

  local missing_tables=""
  for table in "${required_tables[@]}"; do
    if echo "$tables_after" | grep -q "^${table}$"; then
      echo "✓ table_$table: EXISTS"
      log_deploy "TABLE_EXISTS: $table"
    else
      echo "✗ table_$table: MISSING"
      missing_tables="${missing_tables}${table} "
      log_deploy "TABLE_MISSING: $table"
    fi
  done

  if [ -n "$missing_tables" ]; then
    echo "- missing_tables: $missing_tables"
    echo "- result: FAIL"
    echo ""
    echo "[ERROR] Some required tables are missing" >&2
    log_deploy "INIT_STORAGE_FAILED: missing tables"
    return 1
  fi

  # Step 8: Initialize Qdrant local storage
  print_section "QDRANT_STORAGE_INIT"

  # Set default Qdrant path if not specified
  local qdrant_path="${QDRANT_LOCAL_PATH:-$BCSFUSE_ROOT/.runtime/data/qdrant}"

  echo "- qdrant_path: $qdrant_path"
  echo "- vector_backend: ${VECTOR_BACKEND:-qdrant_local}"

  if [ "${VECTOR_BACKEND:-qdrant_local}" != "qdrant_local" ]; then
    echo "⚠ vector_backend: NOT_QDRANT_LOCAL"
    echo "[WARN] VECTOR_BACKEND is not 'qdrant_local', open-core only supports local mode" >&2
  fi

  # Check if path exists
  if [ -d "$qdrant_path" ]; then
    echo "- qdrant_path_exists: YES"
    echo "- qdrant_path_created: NO"
    log_deploy "QDRANT_PATH_EXISTS: $qdrant_path"
  else
    echo "- qdrant_path_exists: NO"
    echo "[INFO] Creating Qdrant storage directory..."

    if mkdir -p "$qdrant_path"; then
      echo "✓ qdrant_path_created: YES"
      log_deploy "QDRANT_PATH_CREATED: $qdrant_path"
    else
      echo "✗ qdrant_path_created: FAILED"
      echo "- result: FAIL"
      echo ""
      echo "[ERROR] Failed to create Qdrant storage directory" >&2
      log_deploy "QDRANT_PATH_CREATION_FAILED"
      return 1
    fi
  fi

  # Check for lock file
  local lock_file="$qdrant_path/.lock"
  if [ -f "$lock_file" ]; then
    echo "⚠ qdrant_lock_detected: YES"
    echo "[WARN] Qdrant lock file found: $lock_file" >&2
    echo "[WARN] Another process may be using this storage" >&2
    log_deploy "QDRANT_LOCK_DETECTED"
  else
    echo "✓ qdrant_lock_detected: NO"
  fi

  # Check git ignore
  if [ -d ".git" ]; then
    if git check-ignore -v "$qdrant_path" >/dev/null 2>&1; then
      echo "✓ qdrant_git_ignored: YES"
      log_deploy "QDRANT_GIT_IGNORED"
    else
      echo "⚠ qdrant_git_ignored: NO"
      echo "[WARN] Qdrant path should be in .gitignore" >&2
    fi
  fi

  # Final result
  print_section "INIT_STORAGE_RESULT"

  echo "- bcsfuse_root: $BCSFUSE_ROOT"
  echo "- mysql_host: ${MYSQL_HOST}"
  echo "- mysql_database: ${MYSQL_DATABASE}"
  echo "- mysql_connection: PASS"
  echo "- mysql_tables_before: $tables_before_count"
  echo "- mysql_tables_after: $tables_after_count"
  echo "- mysql_tables_created: $(( tables_after_count - tables_before_count ))"
  echo "- destructive_operations: NO"
  echo "- qdrant_path: $qdrant_path"
  echo "- qdrant_backend: ${VECTOR_BACKEND:-qdrant_local}"
  echo "- result: PASS"

  log_deploy "INIT_STORAGE_PASS"

  echo ""
  echo "========================================"
  echo "STORAGE_INIT_COMPLETE"
  echo "========================================"
  echo ""
  echo "MySQL schema initialized: $tables_after_count tables"
  echo "Qdrant storage path: $qdrant_path"
  echo ""
  echo "Next step: ./scripts/deploy/macos/start_local.sh"
  echo ""

  return 0
}

main "$@"