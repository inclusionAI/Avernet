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

test_local_mode_remains_explicit_opt_out() {
  local output
  output="$(run_singlebox_status --local)"

  if grep -q "STANDALONE MODE ENABLED" <<<"$output"; then
    fail "--local should opt out of standalone mode"
  fi
}

test_default_mode_is_standalone
test_local_mode_remains_explicit_opt_out

printf 'PASS: singlebox default mode tests\n'
