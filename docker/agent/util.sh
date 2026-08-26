#!/bin/bash

##############################################
# util.sh - Common utility functions
#
# Provides logging and helper functions shared
# by entrypoint.sh and start_service.sh.
#
# Reference: ocb/dockers/arca-openclaw/util.sh
##############################################

# Default log file path
DEFAULT_LOG_FILE="/home/admin/logs/start_service.log"

# --- Logging ---

log() {
    local level="${1:-INFO}"
    shift
    local message="$*"
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')

    local log_file="${LOG_FILE:-$DEFAULT_LOG_FILE}"

    # Ensure log directory exists
    local log_dir
    log_dir=$(dirname "$log_file")
    [ -d "$log_dir" ] || mkdir -p "$log_dir" 2>/dev/null || true

    local prefix="[$timestamp]"

    # Output to terminal with optional colour
    case "$level" in
        INFO)
            echo "${prefix} [INFO] $message" >&1 ;;
        WARN)
            echo "${prefix} [WARN] $message" >&2 ;;
        ERROR)
            echo "${prefix} [ERROR] $message" >&2 ;;
        SUCCESS|OK)
            echo "\033[1;32m${prefix} [OK] $message\033[0m" >&1 ;;
        FAIL)
            echo "\033[1;31m${prefix} [FAIL] $message\033[0m" >&2 ;;
        SECTION)
            echo "\033[1;34m${prefix} ===== $message =====\033[0m" >&1 ;;
        *)
            echo "${prefix} [$level] $message" >&1 ;;
    esac

    # Append to log file (plain text, no colour)
    if [ -w "$(dirname "$log_file")" ] 2>/dev/null; then
        echo "${prefix} [$level] $message" >> "$log_file" 2>/dev/null || true
    fi
}

info()    { log INFO "$*"; }
warn()    { log WARN "$*"; }
error()   { log ERROR "$*"; }
success() { log SUCCESS "$*"; }
fail()    { log FAIL "$*"; }
section() { log SECTION "$*"; }

# --- Log file management ---

set_log_file() {
    LOG_FILE="$1"
}

get_log_file() {
    echo "${LOG_FILE:-$DEFAULT_LOG_FILE}"
}

# --- Helpers ---

# Read a property value from a key=value file (strips whitespace around = and EOL)
# Usage: val=$(read_property /path/to/file key)
read_property() {
    local file="$1"
    local key="$2"
    grep "^${key}" "$file" 2>/dev/null | sed "s/^${key} *= *//" | sed 's/ *$//'
}

# Get environment type from AGENTCLAW_ENV or env variable
# Usage: env_type=$(get_env_type)
get_env_type() {
    echo "${AGENTCLAW_ENV:-${env:-dev}}"
}
