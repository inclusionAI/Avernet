#!/usr/bin/env bash
set -euo pipefail
# Generate coverage for error-handling paths that the BCS e2e HTTP tests never
# trigger. Builds and runs targeted integration tests with the same coverage
# instrumentation, so profraw lands in the same target directory and is merged
# by the final `cargo llvm-cov report` step in e2e_coverage.sh.
#
# The BCS libraries (bcs-message-flow, bcs-http) contain no #[cfg(test)] code,
# so `cargo test --no-run --profile=dev` reuses the already-instrumented library objects
# and only compiles the test harness.
#
# CARGO_TARGET_DIR is aligned with prepare_bcs_coverage_bin (singlebox.sh):
#   <bcs_dir>/target/cov-e2e/llvm-cov-target
# This ensures test binaries land in the same directory as the instrumented
# BCS objects, so cargo reuses them and cargo-llvm-cov can attribute coverage.
#
# Covered paths:
#   group_messages_contract::session_chat_keeps_internal_error_as_server_error
#     → sessions.rs: InternalError → 502 (BAD_GATEWAY)
#   operation_output_uncertainty::committed_admission_with_lost_ack
#     → web_send.rs: committed_in_previous_attempt + effective_rejected
#   operation_output_uncertainty::definitive_admission_rejection
#     → web_send.rs: rejected=true path
#   operation_output_uncertainty::unknown_storage_failure_without_readback
#     → web_send.rs: non-rejected error path
#   contract_bot_event::manager_worker_unknown_target_emits_public_group_notice
#     → dispatch.rs: orphan bot skip path

bcs_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Align CARGO_TARGET_DIR with prepare_bcs_coverage_bin:
#   cov_root="$BCS_DIR/target/cov-e2e"
#   cov_dir="$cov_root/llvm-cov-target"
#   export CARGO_TARGET_DIR="$cov_dir"
cov_dir="$bcs_dir/target/cov-e2e/llvm-cov-target"
mkdir -p "$cov_dir"

echo "--- error-path coverage: building test targets ---"

(
  cd "$bcs_dir"
  export CARGO_TARGET_DIR="$cov_dir"
  export RUSTFLAGS="-Cinstrument-coverage -Clink-dead-code"
  export CARGO_INCREMENTAL=0
  export CARGO_PROFILE_DEV_DEBUG=line-tables-only

  cargo test --no-run --profile=dev \
    -p bcs-http --test group_messages_contract \
    -p bcs-message-flow --test operation_output_uncertainty \
    -p bcs-message-flow --test contract_bot_event \
    2>&1 | tail -5
)

echo "--- error-path coverage: running tests ---"

run_test_binary() {
  local pattern="$1"
  local test_names="$2"
  local found=0
  for t in "$cov_dir/debug/deps/${pattern}"-*; do
    if [[ -x "$t" ]] && ! [[ "$t" =~ \.d$ ]]; then
      # Profraw lands directly in $cov_dir (llvm-cov-target), where
      # e2e_coverage.sh's `cargo llvm-cov report` reads it via
      #   CARGO_TARGET_DIR=<bcs_dir>/target/cov-e2e
      #   → <CARGO_TARGET_DIR>/llvm-cov-target/
      LLVM_PROFILE_FILE="$cov_dir/error-path-test-%m-%p.profraw" \
        "$t" $test_names 2>&1 | tail -3
      found=1
      break
    fi
  done
  if [[ "$found" -eq 0 ]]; then
    echo "WARN: test binary matching '${pattern}' not found in $cov_dir/debug/deps/"
  fi
}

# sessions.rs InternalError → 502
run_test_binary "group_messages_contract" \
  "session_chat_keeps_internal_error_as_server_error"

# web_send.rs admission retry paths
run_test_binary "operation_output_uncertainty" \
  "committed_admission_with_lost_ack_retains_managed_but_releases_unsent_direct definitive_admission_rejection_releases_every_unused_run unknown_storage_failure_without_readback_keeps_managed_identity"

# dispatch.rs orphan bot skip path
run_test_binary "contract_bot_event" \
  "manager_worker_unknown_target_emits_public_group_notice"

echo "--- error-path coverage: done ---"
