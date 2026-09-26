#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${SCRIPT_DIR}/run_bcs_mixed_provider.sh"

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

assert_contains() {
  local haystack="$1"
  local needle="$2"
  printf '%s' "$haystack" | grep -F -- "$needle" >/dev/null || {
    printf 'Expected output to contain:\n  %s\n\nActual output:\n%s\n' "$needle" "$haystack" >&2
    exit 1
  }
}

test_help_mentions_provider_mode() {
  local out
  out="$(bash "${TARGET}" --help)"
  assert_contains "$out" "OpenClaw WS bot + Claude Code Provider bot"
  assert_contains "$out" "teamclaw-aicoding-relay"
  assert_contains "$out" "--mode sse|callback-2.0|callback-1.0"
}

test_dry_run_uses_provider_bridge_not_relay_bcs_bridge() {
  local out
  out="$(bash "${TARGET}" --dry-run --no-frontend start)"
  assert_contains "$out" "DRY RUN"
  assert_contains "$out" "BCS_AUTO_ONBOARD=0"
  assert_contains "$out" "BCS_MOCK_USER_STAFF_NO=410025"
  assert_contains "$out" "singlebox.sh start bcs"
  assert_contains "$out" "RELAY_DIR=/Users/ray/ant/projects/teamclaw-aicoding-relay"
  assert_contains "$out" "CHAT_ENGINE=claude_code"
  assert_contains "$out" "mock_provider_bridge.py"
  assert_contains "$out" "--permission-mode bypassPermissions"
  assert_contains "$out" "protocol_version=2.0"
  assert_contains "$out" "coordination.mode=mcporter_mcp"
  assert_contains "$out" "coordination.mcp_server=bcs"
  assert_contains "$out" "coordination.mcporter_command=mcporter"
  if printf '%s' "$out" | grep -F "dev:bcs" >/dev/null; then
    fail "dry-run must not start relay's direct BCS bridge"
  fi
}

test_dry_run_callback_mode_wires_bot_events() {
  local out
  out="$(bash "${TARGET}" --dry-run --no-frontend --mode callback-2.0 start)"
  assert_contains "$out" "--mode callback-2.0"
  assert_contains "$out" "--bcs-events-url http://127.0.0.1:21000/bot/events"
  assert_contains "$out" "provider_admin_token"
}

test_dry_run_uses_external_frontend_dir_by_default() {
  local out
  out="$(bash "${TARGET}" --dry-run start)"
  assert_contains "$out" "FRONTEND_DIR=/Users/ray/ant/projects/open-claw"
  assert_contains "$out" "tnpm run devs:local"
}

test_dry_run_allows_staff_no_override() {
  local out
  out="$(bash "${TARGET}" --dry-run --no-frontend --staff-no 197262 start)"
  assert_contains "$out" "mock BCS user staff_no: 197262"
  assert_contains "$out" "BCS_MOCK_USER_STAFF_NO=197262"
}

test_help_mentions_provider_mode
test_dry_run_uses_provider_bridge_not_relay_bcs_bridge
test_dry_run_callback_mode_wires_bot_events
test_dry_run_uses_external_frontend_dir_by_default
test_dry_run_allows_staff_no_override

printf 'PASS: run_bcs_mixed_provider dry-run tests\n'
