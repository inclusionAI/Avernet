#!/usr/bin/env bash
set -euo pipefail
# Only required-features companion binaries; never replace the production bcs.
bcs_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
case "${1:-all}" in
  caller) bins=(--bin bcs-caller-e2e) ;;
  oauth) bins=(--bin bcs-oauth-e2e) ;;
  all) bins=(--bin bcs-caller-e2e --bin bcs-oauth-e2e) ;;
  *) echo "expected caller, oauth or all" >&2; exit 2 ;;
esac
cd "$bcs_dir"
mkdir -p target/tmp target/cov-e2e/llvm-cov-target
export RUSTFLAGS="-Cinstrument-coverage -Clink-dead-code"
export CARGO_INCREMENTAL=0
export CARGO_PROFILE_DEV_DEBUG=line-tables-only
export CARGO_TARGET_DIR="$bcs_dir/target/cov-e2e/llvm-cov-target"
export LLVM_PROFILE_FILE="$bcs_dir/target/tmp/companion-build-%m-%p.profraw"
cargo build -p bcs --features test-utils "${bins[@]}"
