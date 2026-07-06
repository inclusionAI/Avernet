#!/usr/bin/env bash
# R25: contract harness and conformance entry gate.
set -euo pipefail

if ! command -v rg >/dev/null; then
  echo "SKIP [R25]: ripgrep not installed"
  exit 77
fi

to_snake() {
  echo "$1" | sed -E 's/([a-z0-9])([A-Z])/\1_\2/g; s/([A-Z])([A-Z][a-z])/\1_\2/g' | tr '[:upper:]' '[:lower:]'
}

contract_root="crates/test-support/bcs-test-support/src/contract"
fail=0

check_r25_1_trait_has_harness() {
  local missing=0
  while IFS= read -r line; do
    local file trait snake harness
    file=$(echo "$line" | cut -d: -f1)
    trait=$(echo "$line" | sed -nE 's/.*pub trait ([A-Za-z0-9_]+).*/\1/p')
    [[ -z "$trait" ]] && continue
    if grep -q "CONFORMANCE_WAIVED" "$file"; then
      continue
    fi
    snake=$(to_snake "$trait")
    harness="${snake}_contract_tests"
    if ! rg -q "pub async fn ${harness}|pub fn ${harness}" "$contract_root"; then
      echo "FAIL [R25.1]: trait $trait missing harness $harness"
      missing=1
    fi
  done < <(rg --no-heading -n 'pub trait [A-Za-z0-9_]+(CoreService|Service|Port|Repo|Plugin|Lifecycle|Interceptor)\b' \
    crates/service-api crates/plugin-api crates/services --glob '*.rs' 2>/dev/null || true)

  if [[ $missing -eq 0 ]]; then
    echo "PASS [R25.1]: public traits have contract harness entries"
  fi
  return $missing
}

check_r25_2_conformance_uses_harness() {
  local missing=0
  local found=0
  while IFS= read -r file; do
    found=1
    if ! rg -q "bcs_test_support::contract::" "$file"; then
      echo "FAIL [R25.2]: $file does not call bcs_test_support::contract"
      missing=1
    fi
  done < <(find crates -path '*/tests/conformance_*.rs' -type f | sort)

  if [[ $found -eq 0 ]]; then
    echo "FAIL [R25.2]: no conformance_*.rs entries found"
    return 1
  fi
  if [[ $missing -eq 0 ]]; then
    echo "PASS [R25.2]: conformance entries call centralized harnesses"
  fi
  return $missing
}

has_conformance_entry_for_impl() {
  local trait="$1"
  local type_name="$2"
  local harness
  harness="$(to_snake "$trait")_contract_tests"

  while IFS= read -r file; do
    if rg -q "\\b${type_name}\\b" "$file" && rg -q "\\b${harness}\\b" "$file"; then
      return 0
    fi
  done < <(find crates -path '*/tests/conformance_*.rs' -type f | sort)

  return 1
}

check_r25_2_boundary_impls_have_entries() {
  local missing=0
  while IFS= read -r line; do
    local file trait type_name
    file=$(echo "$line" | cut -d: -f1)
    trait=$(echo "$line" | sed -nE 's/.*impl ([A-Za-z0-9_]+) for ([A-Za-z0-9_]+).*/\1/p')
    type_name=$(echo "$line" | sed -nE 's/.*impl ([A-Za-z0-9_]+) for ([A-Za-z0-9_]+).*/\2/p')
    [[ -z "$trait" || -z "$type_name" ]] && continue

    # Empty/Noop adapters are fallback test doubles or null ports. They still
    # compile against the trait, but are not scored as concrete product impls.
    if [[ "$type_name" =~ ^(Empty|Noop) ]]; then
      continue
    fi

    if grep -q "CONFORMANCE_WAIVED" "$file"; then
      continue
    fi

    if ! has_conformance_entry_for_impl "$trait" "$type_name"; then
      echo "FAIL [R25.2]: impl $trait for $type_name in $file lacks conformance entry"
      missing=1
    fi
  done < <(rg --no-heading -n '^impl [A-Za-z0-9_]+(CoreService|Service|Port|Repo|Plugin|Lifecycle|Interceptor)\b for [A-Za-z0-9_]+\b' \
    crates/services --glob '*.rs' --glob '!**/tests/**' 2>/dev/null || true)

  if [[ $missing -eq 0 ]]; then
    echo "PASS [R25.2]: production boundary impls have conformance entries"
  fi
  return $missing
}

check_r25_3_conformance_discoverable() {
  local listed
  listed=$(cargo test --workspace --no-fail-fast -- --list 2>/dev/null || true)
  if echo "$listed" | rg -q 'conformance_'; then
    echo "PASS [R25.3]: cargo test discovers conformance_ tests"
    return 0
  fi
  echo "FAIL [R25.3]: cargo test did not discover conformance_ tests"
  return 1
}

check_r25_1_trait_has_harness || fail=1
check_r25_2_conformance_uses_harness || fail=1
check_r25_2_boundary_impls_have_entries || fail=1
check_r25_3_conformance_discoverable || fail=1

exit $fail
