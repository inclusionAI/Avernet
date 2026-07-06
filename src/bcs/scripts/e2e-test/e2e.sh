#!/bin/bash
# e2e.sh — BCS E2E Test Runner (Local Mode)
#
# Prerequisites:
#   BCS must be running with the 5 demo bots onboarded. The standard way is to
#   start BCS + bots via singlebox with the default 5bots_profile:
#
#     ./scripts/singlebox.sh --local start bcs_bots
#
#   This onboards 5 bots (CEO-马斯克 / 产品-乔布斯 / 研发-Linus / 验证-图灵 /
#   客服-张勇), which e2e resolves by name (see BOT_*_ID defaults in common.sh).
#   Override the BCS endpoint with BCS_API_BASE_URL if it is not on
#   http://127.0.0.1:21000.
#
# Usage:
#   ./e2e.sh                  # Run all tests
#   ./e2e.sh -t group         # Run group tests only
#   ./e2e.sh -t friends       # Run friends tests only
#   ./e2e.sh -t group friends # Run multiple test suites
#   ./e2e.sh -l               # List available tests
#   ./e2e.sh --skip-setup     # Skip BCS health check and ensure-human
#   ./e2e.sh --help           # Show usage

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source shared library
source "$SCRIPT_DIR/common.sh"

# ============================================================================
# Parse Arguments
# ============================================================================

SELECTED_SUITES=()
SKIP_SETUP=false
LIST_ONLY=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        -t|--test)
            shift
            while [[ $# -gt 0 && ! "$1" =~ ^- ]]; do
                SELECTED_SUITES+=("$1")
                shift
            done
            ;;
        -l|--list)
            LIST_ONLY=true
            shift
            ;;
        --skip-setup)
            SKIP_SETUP=true
            shift
            ;;
        --help|-h)
            echo "Usage: $0 [options]"
            echo ""
            echo "Options:"
            echo "  -t <suite...>    Run specific test suites (group, friends)"
            echo "  -l, --list       List available tests"
            echo "  --skip-setup     Skip BCS health check and ensure-human"
            echo "  --help           Show this help"
            echo ""
            echo "Examples:"
            echo "  $0                # Run all tests"
            echo "  $0 -t group       # Run group tests only"
            echo "  $0 -t group friends"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# ============================================================================
# Source Test Suites
# ============================================================================

source "$SCRIPT_DIR/group.sh"
source "$SCRIPT_DIR/friends.sh"

# ============================================================================
# Collect All Tests (Bash 3.2 compatible — no associative arrays)
# ============================================================================

ALL_SUITES=(group friends)
ALL_TESTS=()

for test_name in "${E2E_TESTS_GROUP[@]}"; do
    ALL_TESTS+=("group:$test_name")
done
for test_name in "${E2E_TESTS_FRIENDS[@]}"; do
    ALL_TESTS+=("friends:$test_name")
done

# ============================================================================
# List Mode
# ============================================================================

if [ "$LIST_ONLY" = true ]; then
    echo "Available test suites:"
    echo "  group:"
    for test_name in "${E2E_TESTS_GROUP[@]}"; do
        echo "    - $test_name"
    done
    echo "  friends:"
    for test_name in "${E2E_TESTS_FRIENDS[@]}"; do
        echo "    - $test_name"
    done
    exit 0
fi

# ============================================================================
# Filter Tests by Selected Suites
# ============================================================================

RUN_TESTS=()

if [ ${#SELECTED_SUITES[@]} -eq 0 ]; then
    RUN_TESTS=("${ALL_TESTS[@]}")
else
    for selected in "${SELECTED_SUITES[@]}"; do
        for entry in "${ALL_TESTS[@]}"; do
            suite="${entry%%:*}"
            if [ "$suite" = "$selected" ]; then
                RUN_TESTS+=("$entry")
            fi
        done
    done
fi

if [ ${#RUN_TESTS[@]} -eq 0 ]; then
    fail "No tests matched. Available suites: ${ALL_SUITES[*]}"
    exit 1
fi

# ============================================================================
# Setup
# ============================================================================

echo ""
info "BCS E2E Test Runner"
info "==================="

if [ "$SKIP_SETUP" = false ]; then
    wait_for_health || exit 2
    ensure_human || exit 2
    resolve_all_bot_uuids || exit 2
else
    info "Skipping setup (--skip-setup)"
    resolve_all_bot_uuids || exit 2
fi

# Always clean up leftover groups driven by the PMO bot before running tests.
# The BCS enforces a per-driver active-group cap; without this, repeated e2e
# runs accumulate groups and `POST /groups` starts returning 400.
cleanup_driver_groups "$BOT_PMO_UUID"

# ============================================================================
# Run Tests
# ============================================================================

START_TIME=$(date +%s)

echo ""
info "Running ${#RUN_TESTS[@]} test(s)..."

for entry in "${RUN_TESTS[@]}"; do
    suite="${entry%%:*}"
    test_name="${entry#*:}"
    echo ""
    info "[$suite] $test_name"
    if $test_name; then
        : # test function handles its own assertions
    else
        fail "$test_name exited with error"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
    fi
done

END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))

# ============================================================================
# Summary
# ============================================================================

echo ""
summary
info "Time: ${ELAPSED}s"

if [ "$TESTS_FAILED" -gt 0 ]; then
    exit 1
elif [ "$TESTS_TOTAL" -eq 0 ]; then
    warn "No tests were run"
    exit 1
else
    exit 0
fi
