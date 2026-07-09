#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SINGLEBOX="${ROOT}/scripts/singlebox.sh"

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

run_singlebox_status() {
  env -u SINGLEBOX_MODEL_CONFIG_MODE \
    PATH="${HOME}/.cargo/bin:${PATH}" \
    SINGLEBOX_MODEL_CONFIG_MODE=mock \
    "$SINGLEBOX" "$@" status all 2>&1
}

test_default_mode_is_standalone() {
  local output
  output="$(run_singlebox_status)"

  grep -q "STANDALONE MODE ENABLED" <<<"$output" || fail "default mode should be standalone"
  grep -q ".standalone-openclaw" <<<"$output" || fail "default mode should use standalone OpenClaw root"
}

test_local_mode_is_rejected() {
  local output

  if output="$(run_singlebox_status --local)"; then
    fail "--local should be rejected"
  fi
  grep -q -- "--local has been removed" <<<"$output" || fail "--local rejection should explain removal"
}

test_default_mode_is_standalone
test_local_mode_is_rejected

printf 'PASS: singlebox default mode tests\n'
