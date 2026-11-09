#!/usr/bin/env bash
set -euo pipefail
# Companion live-process story using the same instrumented target/profile pool.
# The synthetic channel is linked only into a required-features test executable;
# the ordinary BCS binary and production configuration remain unchanged.
bcs_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cov_runtime="$bcs_dir/target/cov-e2e/llvm-cov-target"
mkdir -p "$cov_runtime" "$bcs_dir/target/tmp"
case "${1:-}" in
  "") bash "$bcs_dir/scripts/build_e2e_companions.sh" caller ;;
  --run-only) ;; # Supervisor already built both targets from this checkout.
  *) echo "usage: $0 [--run-only]" >&2; exit 2 ;;
esac
if [[ ! -x "$cov_runtime/debug/bcs-caller-e2e" ]]; then
  echo "missing instrumented caller companion executable" >&2
  exit 1
fi
LLVM_PROFILE_FILE="$cov_runtime/caller-%m-%p.profraw" \
  python3 "$bcs_dir/scripts/e2e-test/caller_execution_story.py" \
    --binary "$cov_runtime/debug/bcs-caller-e2e"
LLVM_PROFILE_FILE="$cov_runtime/task-cache-%m-%p.profraw" \
  python3 "$bcs_dir/scripts/e2e-test/task_cache_story.py" \
    --binary "$cov_runtime/debug/bcs-caller-e2e"

LLVM_PROFILE_FILE="$cov_runtime/custom-binding-%m-%p.profraw" \
  python3 "$bcs_dir/scripts/e2e-test/custom_binding_story.py" \
    --binary "$cov_runtime/debug/bcs-caller-e2e"
