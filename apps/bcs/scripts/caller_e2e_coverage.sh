#!/usr/bin/env bash
set -euo pipefail
# Companion live-process story using the same instrumented target/profile pool.
# The synthetic channel is linked only into a required-features test executable;
# the ordinary BCS binary and production configuration remain unchanged.
bcs_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cov_runtime="$bcs_dir/target/cov-e2e/llvm-cov-target"
mkdir -p "$cov_runtime" "$bcs_dir/target/tmp"
(
  cd "$bcs_dir"
  export RUSTFLAGS="-Cinstrument-coverage -Clink-dead-code"
  export CARGO_INCREMENTAL=0
  export CARGO_PROFILE_DEV_DEBUG=line-tables-only
  export CARGO_TARGET_DIR="$cov_runtime"
  export LLVM_PROFILE_FILE="$bcs_dir/target/tmp/caller-build-%m-%p.profraw"
  cargo build -p bcs --features test-utils --bin bcs-caller-e2e
)
LLVM_PROFILE_FILE="$cov_runtime/caller-%m-%p.profraw" \
  python3 "$bcs_dir/scripts/e2e-test/caller_execution_story.py" \
    --binary "$cov_runtime/debug/bcs-caller-e2e"
