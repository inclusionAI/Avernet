#!/usr/bin/env bash
# Integration tests are composition roots (arch.rules.md Rule 5); production
# imports, including source directories named tests, retain the same rules.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHECKER="$SCRIPT_DIR/../check-import-rules.sh"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
command -v rg >/dev/null || { echo 'ripgrep is required' >&2; exit 1; }
failures=0
cases=0

run_case() {
  local name=$1 expected=$2 path=$3 import=$4
  local root="$WORK/$name" rc=0
  mkdir -p "$root/$(dirname "$path")" \
    "$root/crates/service-api/bcs-service-api/src/"{application,core,port}
  printf '%s\n' "$import" > "$root/$path"
  (cd "$root" && bash "$CHECKER") > "$root/output" 2>&1 || rc=$?
  cases=$((cases + 1))
  if [[ $rc -ne $expected ]]; then
    echo "FAIL: $name expected exit $expected, got $rc"
    cat "$root/output"
    failures=$((failures + 1))
  else
    echo "PASS: $name"
  fi
}

run_case service_integration 0 crates/services/fixture/tests/support/wiring.rs \
  'use bcs_service_api::application::caller_container::CallerContainerPreparation;'
run_case adapter_integration 0 crates/adapters/ws/fixture/tests/wiring.rs \
  'use bcs_service_api::core::GroupCoreService;'
run_case client_integration 0 crates/external-clients/fixture/tests/wiring.rs \
  'use bcs_service_api::application::MessageFlowService;'
run_case service_production 1 crates/services/fixture/src/lib.rs \
  'use bcs_service_api::application::MessageFlowService;'
run_case source_named_tests 1 crates/services/fixture/src/tests/wiring.rs \
  'use bcs_service_api::application::MessageFlowService;'
run_case adapter_core 1 crates/adapters/ws/fixture/src/lib.rs \
  'use bcs_service_api::core::GroupCoreService;'
run_case adapter_port 1 crates/adapters/ws/fixture/src/lib.rs \
  'use bcs_service_api::port::BotDeliveryPort;'
run_case client_core 1 crates/external-clients/fixture/src/lib.rs \
  'use bcs_service_api::core::GroupCoreService;'
run_case client_application 1 crates/external-clients/fixture/src/lib.rs \
  'use bcs_service_api::application::MessageFlowService;'
run_case contract_core 1 crates/service-api/bcs-service-api/src/core/lib.rs \
  'use crate::application::MessageFlowService;'
run_case contract_port 1 crates/service-api/bcs-service-api/src/port/lib.rs \
  'use crate::core::GroupCoreService;'

[[ $failures -eq 0 ]] || { echo "$failures/$cases cases failed"; exit 1; }
echo "PASS: $cases import-scope regression cases"
