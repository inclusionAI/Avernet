#!/usr/bin/env bash
set -euo pipefail
checker="$(cd "$(dirname "$0")/.." && pwd)/check-r25-conformance.sh"
fixture=$(mktemp -d)
trap 'rm -rf "$fixture"' EXIT
mkdir -p "$fixture/crates/test-support/bcs-test-support/src/contract"
mkdir -p "$fixture/crates/service-api" "$fixture/crates/plugin-api"
mkdir -p "$fixture/crates/services/example/tests/cases" "$fixture/bin"
printf '#!/bin/sh\nprintf "conformance_fixture: test\\n"\n' > "$fixture/bin/cargo"
chmod +x "$fixture/bin/cargo"
export PATH="$fixture/bin:$PATH"
casefile="$fixture/crates/services/example/tests/cases/conformance_fixture.rs"
printf 'bcs_test_support::contract::fixture_contract_tests();\n' > "$casefile"
(cd "$fixture" && bash "$checker")
printf 'fn conformance_fixture() {}\n' > "$casefile"
if (cd "$fixture" && bash "$checker"); then
  echo 'FAIL: missing centralized harness accepted'; exit 1
fi
rm "$casefile"
if (cd "$fixture" && bash "$checker"); then
  echo 'FAIL: empty conformance inventory accepted'; exit 1
fi