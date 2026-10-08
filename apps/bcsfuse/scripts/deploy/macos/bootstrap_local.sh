#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# BCSFuse Open-Core macOS Local Bootstrap (Phase 3.1)
# =============================================================================
# Purpose: One-click macOS local dependency preparation
# Features:
#   - Idempotent (safe to run multiple times)
#   - No data destruction
#   - Creates .runtime/ directory structure
#   - Generates .runtime/env/.env.local from template (if not exists)
#   - Calls init_storage.sh automatically
#
# Usage:
#   ./scripts/deploy/macos/bootstrap_local.sh
#
# Exit codes:
#   0 - Success
#   1 - Failure (missing dependencies, validation failed)
# =============================================================================

# =============================================================================
# Helper Functions
# =============================================================================

find_bcsfuse_root() {
  local dir
  dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

  # Check for open-core marker files
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

check_command() {
  local cmd="$1"
  if command -v "$cmd" >/dev/null 2>&1; then
    echo "✓ $cmd: $(command -v "$cmd")"
    return 0
  else
    echo "✗ $cmd: NOT_FOUND"
    return 1
  fi
}

check_python_version() {
  local min_major=3
  local min_minor=12

  if ! command -v python3 >/dev/null 2>&1; then
    echo "✗ python3: NOT_FOUND"
    return 1
  fi

  local version
  version=$(python3 --version 2>&1 | awk '{print $2}')
  local major
  major=$(echo "$version" | cut -d. -f1)
  local minor
  minor=$(echo "$version" | cut -d. -f2)

  if [ "$major" -gt "$min_major" ] || { [ "$major" -eq "$min_major" ] && [ "$minor" -ge "$min_minor" ]; }; then
    echo "✓ python_version: $version (>= $min_major.$min_minor)"
    return 0
  else
    echo "✗ python_version: $version (< $min_major.$min_minor)"
    return 1
  fi
}

# =============================================================================
# Main Bootstrap Logic
# =============================================================================

main() {
  print_section "BCSFUSE_OPEN_CORE_MACOS_BOOTSTRAP"

  # Step 1: Find BCSFuse root
  local BCSFUSE_ROOT
  BCSFUSE_ROOT="$(find_bcsfuse_root || true)"

  if [ -z "${BCSFUSE_ROOT:-}" ]; then
    echo "✗ bcsfuse_root: NOT_FOUND"
    echo ""
    echo "[ERROR] BCSFuse root not found" >&2
    echo "[HINT] Expected to find: main.py + scripts/ + .env.example" >&2
    return 1
  fi

  echo "- bcsfuse_root: $BCSFUSE_ROOT"
  cd "$BCSFUSE_ROOT"

  # Step 2: Check Python
  print_section "PYTHON_CHECK"
  local python_ok=true

  if check_command python3; then
    :
  else
    python_ok=false
  fi

  if check_python_version; then
    :
  else
    python_ok=false
  fi

  if [ "$python_ok" = false ]; then
    echo "- result: FAIL"
    echo ""
    echo "[ERROR] Python 3.12+ required" >&2
    return 1
  fi

  # Step 3: Check bash
  print_section "BASH_CHECK"
  if check_command bash; then
    :
  else
    echo "- result: FAIL"
    echo ""
    echo "[ERROR] bash required" >&2
    return 1
  fi

  # Step 4: Check curl (optional but recommended)
  print_section "CURL_CHECK"
  if check_command curl; then
    :
  else
    echo "⚠ curl: NOT_FOUND (optional, but recommended for health checks)"
  fi

  # Step 5: Check uv or pip
  print_section "PACKAGE_MANAGER_CHECK"
  local pkg_manager=""
  local install_cmd=""

  if command -v uv >/dev/null 2>&1; then
    pkg_manager="uv"
    install_cmd="uv sync"
    echo "✓ package_manager: uv ($(uv --version))"
  else
    echo "✗ package_manager: NOT_FOUND"
    echo "- result: FAIL"
    echo ""
    echo "[ERROR] uv not found (required for dependency management)" >&2
    echo "[HINT] Install uv: pip install uv" >&2
    return 1
  fi

  echo "- install_command: $install_cmd"

  # Step 6: Check MySQL client (optional, but recommended)
  print_section "MYSQL_CLIENT_CHECK"
  if command -v mysql >/dev/null 2>&1; then
    echo "✓ mysql_client: $(command -v mysql)"
  else
    echo "⚠ mysql_client: NOT_FOUND (optional, but recommended for manual verification)"
  fi

  # Step 7: Check venv
  print_section "VENV_CHECK"
  local venv_path="$BCSFUSE_ROOT/.venv"
  local venv_exists="NO"
  local venv_created="NO"

  if [ -d "$venv_path" ]; then
    venv_exists="YES"
    echo "- venv_path: $venv_path"
    echo "- venv_exists: YES"
    echo "- venv_created: NO"
  else
    echo "- venv_path: $venv_path"
    echo "- venv_exists: NO"
    echo "[INFO] Creating virtual environment..."

    if python3 -m venv "$venv_path"; then
      venv_created="YES"
      echo "✓ venv_created: YES"
    else
      echo "✗ venv_created: FAILED"
      echo "- result: FAIL"
      echo ""
      echo "[ERROR] Failed to create virtual environment" >&2
      return 1
    fi
  fi

  # Step 8: Install dependencies
  print_section "DEPENDENCIES_INSTALL"

  local deps_installed="NO"

  echo "[INFO] Installing dependencies with uv..."

  if uv sync; then
    deps_installed="YES"
    echo "✓ dependencies_installed: YES"
  else
    echo "✗ dependencies_installed: FAILED"
    echo "- result: FAIL"
    echo ""
    echo "[ERROR] uv sync failed" >&2
    return 1
  fi

  # Step 9: Create .runtime/ directory structure
  print_section "RUNTIME_DIRECTORY_SETUP"

  local runtime_dir="$BCSFUSE_ROOT/.runtime"
  local logs_dir="$runtime_dir/logs"
  local pids_dir="$runtime_dir/pids"
  local data_dir="$runtime_dir/data"
  local env_dir="$runtime_dir/env"
  local qdrant_dir="$data_dir/qdrant"

  echo "- runtime_dir: $runtime_dir"

  mkdir -p "$logs_dir" "$pids_dir" "$data_dir" "$env_dir" "$qdrant_dir"

  echo "✓ logs_dir: $logs_dir"
  echo "✓ pids_dir: $pids_dir"
  echo "✓ data_dir: $data_dir"
  echo "✓ env_dir: $env_dir"
  echo "✓ qdrant_dir: $qdrant_dir"

  # Step 10: Create .runtime/.gitignore
  print_section "RUNTIME_GITIGNORE_SETUP"

  local gitignore_file="$runtime_dir/.gitignore"

  if [ -f "$gitignore_file" ]; then
    echo "- gitignore_exists: YES"
    echo "- gitignore_created: NO"
  else
    cat > "$gitignore_file" << 'EOF'
# BCSFuse Open-Core Runtime Directory
# This directory contains runtime data, logs, and environment files.
# DO NOT commit contents, but keep the directory structure.

# Logs
logs/

# PIDs
pids/

# Data
data/

# Environment (may contain secrets)
env/

# Ignore all except this .gitignore
*
!.gitignore
EOF

    echo "✓ gitignore_created: YES"
    echo "- gitignore_path: $gitignore_file"
  fi

  # Step 11: Generate .runtime/env/.env.local from template
  print_section "ENV_FILE_SETUP"

  local env_file="$env_dir/.env.local"
  local env_template="$BCSFUSE_ROOT/.env.example"

  if [ -f "$env_file" ]; then
    echo "- env_file: $env_file"
    echo "- env_exists: YES"
    echo "- env_created: NO"
    echo "- env_overwritten: NO"

    # Check for placeholders
    if grep -qE "(change_me|your_)" "$env_file" 2>/dev/null; then
      echo "- requires_user_edit: YES"
      echo ""
      echo "[WARN] Environment file contains placeholders" >&2
      echo "[HINT] Edit $env_file with real credentials before starting runtime" >&2
    else
      echo "- requires_user_edit: NO"
    fi
  else
    if [ ! -f "$env_template" ]; then
      echo "✗ env_template: NOT_FOUND"
      echo "- env_template: $env_template"
      echo "- result: FAIL"
      echo ""
      echo "[ERROR] Environment template not found" >&2
      return 1
    fi

    echo "- env_template: $env_template"
    echo "[INFO] Creating environment file from template..."

    cp "$env_template" "$env_file"

    if [ -f "$env_file" ]; then
      echo "✓ env_created: YES"
      echo "- env_file: $env_file"
      echo "- requires_user_edit: YES"
      echo ""
      echo "========================================"
      echo "NEXT_STEP: EDIT_ENV_FILE"
      echo "========================================"
      echo ""
      echo "1. Edit the environment file:"
      echo "   vi $env_file"
      echo ""
      echo "2. Update the following placeholders:"
      echo "   - MYSQL_HOST, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE"
      echo "   - LLM_BASE_URL, LLM_AUTH_TOKEN"
      echo "   - EMBEDDING_BASE_URL, EMBEDDING_AUTH_TOKEN"
      echo "   - EMBEDDING_DIMENSION (must be 4096)"
      echo ""
      echo "3. Set critical Qdrant path:"
      echo "   export QDRANT_LOCAL_PATH=\"$qdrant_dir\""
      echo ""
      echo "4. Then run: ./scripts/deploy/macos/init_storage.sh"
      echo ""
    else
      echo "✗ env_created: FAILED"
      echo "- result: FAIL"
      echo ""
      echo "[ERROR] Failed to create environment file" >&2
      return 1
    fi
  fi

  # Step 12: Call init_storage.sh
  print_section "INIT_STORAGE"

  local init_storage_script="$BCSFUSE_ROOT/scripts/deploy/macos/init_storage.sh"

  if [ ! -f "$init_storage_script" ]; then
    echo "⚠ init_storage_script: NOT_FOUND"
    echo "- init_storage_script: $init_storage_script"
    echo "- result: SKIP"
    echo ""
    echo "[WARN] init_storage.sh not found (will be created in next step)" >&2
  else
    if [ ! -x "$init_storage_script" ]; then
      chmod +x "$init_storage_script"
    fi

    echo "[INFO] Running init_storage.sh..."

    if bash "$init_storage_script"; then
      echo "✓ init_storage: PASS"
    else
      echo "✗ init_storage: FAIL"
      echo "- result: FAIL"
      echo ""
      echo "[ERROR] Storage initialization failed" >&2
      return 1
    fi
  fi

  # Final result
  print_section "BOOTSTRAP_RESULT"

  echo "- bcsfuse_root: $BCSFUSE_ROOT"
  echo "- python_version: $(python3 --version 2>&1 | awk '{print $2}')"
  echo "- package_manager: $pkg_manager"
  echo "- venv_path: $venv_path"
  echo "- venv_exists: $venv_exists"
  echo "- venv_created: $venv_created"
  echo "- dependencies_installed: $deps_installed"
  echo "- runtime_dir: $runtime_dir"
  echo "- env_file: $env_file"
  echo "- qdrant_default_path: $qdrant_dir"
  echo "- result: PASS"

  echo ""
  echo "========================================"
  echo "BOOTSTRAP_COMPLETE"
  echo "========================================"
  echo ""
  echo "Next steps:"
  echo "  1. Edit $env_file with your credentials"
  echo "  2. Run: ./scripts/deploy/macos/start_local.sh"
  echo ""

  return 0
}

main "$@"