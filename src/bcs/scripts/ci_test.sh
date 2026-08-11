#!/usr/bin/env bash
set -euo pipefail

# BCS/BCN pre-push CI gate.
#
# Owner: 章梧
# Runs the full BCS Rust workspace unit tests with single-pass semantics
# (retries=0, no masking of flaky tests by retrying).
#
# Two gate modes (toggled by --fast-fail):
#   --fast-fail (pre-push): nextest fails fast (stops on first failure), the
#                script propagates the non-zero exit -> push is rejected.
#                Requires 100% pass to proceed. Surfaces failures quickly.
#   without --fast-fail (bcs-test.aci.yml): runs all tests (--no-fail-fast),
#                script exits 0 (does not block); the pass rate is delegated
#                to a downstream ACI checkRule (e.g. casePassRate >= 90).
#
# Dispatched by scripts/ci/pre_push.sh BCS branch (passes --fast-fail).
# The bcs-test.aci.yml CI job calls this with --coverage (without --fast-fail,
# runs to completion and emits a report).
# Local manual usage:
#   bash src/bcs/scripts/ci_test.sh --fast-fail          # gate at 100%, fail fast
#   bash src/bcs/scripts/ci_test.sh --coverage            # run all + coverage, no gating
#
# Enabled by default; set OCB_PRE_PUSH_ENABLE_BCS=0 to skip temporarily.

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bcs_dir="$(cd "$script_dir/.." && pwd)"

base=""
head="HEAD"
coverage=0
fast_fail=0

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --base)
      base="$2"
      shift 2
      ;;
    --head)
      head="$2"
      shift 2
      ;;
    --coverage)
      coverage=1
      shift
      ;;
    --report-only)
      # Legacy alias, equivalent to omitting --fast-fail (backward compat).
      fast_fail=0
      shift
      ;;
    --fast-fail)
      fast_fail=1
      shift
      ;;
    -h|--help)
      sed -n '2,21p' "$0"
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

cd "$bcs_dir"

echo "========== BCS pre-push gate =========="
echo "bcs_dir: $bcs_dir"
echo "base: ${base:-<none>}"
echo "head: $head"
[[ "$coverage" -eq 1 ]] && echo "mode: TEST + COVERAGE (llvm-cov)" || echo "mode: TEST ONLY (pass rate)"
if [[ "$fast_fail" -eq 1 ]]; then
  echo "gate: FAST-FAIL (stop on first failure, non-zero exit; for pre-push)"
else
  echo "gate: RUN-ALL (run all tests, exit 0; pass rate delegated to downstream checkRule)"
fi
date

# ---- Environment check ----
# Any missing prerequisite blocks BCS build/test. See src/bcs/CLAUDE.md Prerequisites:
# Rust/Cargo, cargo-nextest, protobuf. --coverage additionally needs cargo-llvm-cov + llvm-tools.
# scripts/install_cargo_test_tools.sh installs the two cargo subcommands from prebuilt binaries.
env_status=0
check_bin() {
  local bin="$1"
  local hint="$2"
  if command -v "$bin" >/dev/null 2>&1; then
    echo "  ✓ $bin -> $(command -v "$bin")"
  else
    echo "  ✗ $bin not installed. $hint" >&2
    env_status=1
  fi
}

echo "--- environment check ---"
check_bin cargo "Install Rust toolchain via rustup: https://rustup.rs"
check_bin cargo-nextest "Run: bash scripts/install_cargo_test_tools.sh nextest"
# protobuf: BCS build.rs depends on protoc; CI images usually have it, local may not.
check_bin protoc "Install protobuf compiler (macOS: brew install protobuf; Linux: yum/apt install protobuf-compiler)"
if [[ "$coverage" -eq 1 ]]; then
  check_bin cargo-llvm-cov "Run: bash scripts/install_cargo_test_tools.sh llvm-cov"
  # llvm-cov requires the llvm-tools component. rustup component list --installed
  # prints entries with a target suffix (e.g. llvm-tools-aarch64-apple-darwin),
  # so match with ^llvm-tools.
  if ! rustup component list --installed 2>/dev/null | grep -q '^llvm-tools'; then
    echo "  ✗ llvm-tools component not installed. Run: rustup component add llvm-tools" >&2
    env_status=1
  else
    echo "  ✓ llvm-tools (rustup component)"
  fi
fi

if [[ "$env_status" -ne 0 ]]; then
  echo "Environment check failed; BCS pre-push gate aborted. Install the missing tools above and retry." >&2
  exit 2
fi

# Single-pass gate (retries=0, no flaky masking): first failure fails the whole run.
#
# --fast-fail (default for pre-push): nextest stops on first failure (no --no-fail-fast),
#   script propagates the non-zero exit -> push rejected. Fast feedback, saves CI time.
# Without --fast-fail (CI coverage): add --no-fail-fast to run all tests, exit 0 (no block),
#   pass rate delegated to downstream ACI checkRule.
#
# --coverage: uses cargo llvm-cov nextest to compile once and emit both junit
# (via [profile.ci.junit]) and cobertura (--cobertura --output-path), per bcs-test.aci.yml.
# llvm-cov uses source instrumentation (-Cinstrument-coverage) with an isolated target dir
# (target/llvm-cov-target). Note: llvm-cov needs --profile ci to write junit
# (this script injects the [profile.ci.junit] table).
nextest_fail_args=()
if [[ "$fast_fail" -eq 0 ]]; then
  nextest_fail_args+=(--no-fail-fast)
fi

if [[ "$coverage" -eq 1 ]]; then
  mkdir -p ./testresult
  # Copy the committed .config/nextest.toml to a temp file and append [profile.ci.junit]
  # there. Using a temp file (instead of mutating the repo file) avoids git seeing a dirty
  # working tree; trap cleans up on exit. --config-file takes an absolute path (nextest
  # requirement) so nextest loads this temporary config.
  tmp_nextest="$(mktemp -t bcs_ci_nextest.XXXXXX.toml)"
  cleanup() { rm -f "$tmp_nextest"; }
  trap cleanup EXIT
  cp .config/nextest.toml "$tmp_nextest"
  printf '\n[profile.ci.junit]\npath = "%s/testresult/junit.xml"\n' "$bcs_dir" >> "$tmp_nextest"
  # Disk optimization (full instrumented build can fill the disk easily).
  export CARGO_PROFILE_DEV_DEBUG=line-tables-only
  export CARGO_PROFILE_TEST_DEBUG=line-tables-only
  export CARGO_INCREMENTAL=0
  echo "--- running cargo llvm-cov nextest (single-pass + coverage) ---"
  set +e
  cargo llvm-cov nextest --config-file "$tmp_nextest" --profile ci --retries 0 ${nextest_fail_args[@]+"${nextest_fail_args[@]}"} \
    --cobertura --output-path ./testresult/cobertura.xml
  status=$?
  set -e
else
  echo "--- running cargo nextest (single-pass, 100% required) ---"
  set +e
  cargo nextest run --profile default --retries 0 ${nextest_fail_args[@]+"${nextest_fail_args[@]}"}
  status=$?
  set -e
fi

echo "========== BCS pre-push gate finished (status=$status) =========="
if [[ "$fast_fail" -eq 0 ]]; then
  # Non-fast-fail (CI coverage scenario): do not block. Test results are still written
  # to junit/cobertura (if --coverage); the pass rate is delegated to a downstream
  # ACI checkRule (e.g. casePassRate >= 90).
  if [[ "$status" -ne 0 ]]; then
    echo "BCS unit tests have failures, but non-fast-fail mode does not block. See the Summary above / junit.xml for the pass rate; downstream gate will judge." >&2
  fi
  exit 0
fi

if [[ "$status" -ne 0 ]]; then
  echo "BCS unit tests did not pass 100% (single-pass verdict). Push blocked; fix and retry." >&2
fi
exit "$status"
