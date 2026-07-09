#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

check_no_private_domains() {
  local private_domain_pattern
  private_domain_pattern="$(
    printf '%s' 'ali''pay[.](com|net)|agentclawproxy-[a-z]+[.]example[.]com'
  )"
  local files=(
    "${ROOT}/scripts/modules/backend.sh"
    "${ROOT}/scripts/ci/singlebox_coverage.sh"
    "${ROOT}/scripts/singlebox.sh"
    "${ROOT}/scripts/modules/demo_bot.sh"
  )

  if grep -nE "$private_domain_pattern" "${files[@]}"; then
    fail "open-source singlebox scripts must not contain private or placeholder company domains"
  fi
}

check_no_private_domains

printf 'PASS: open-source domain guard tests\n'
