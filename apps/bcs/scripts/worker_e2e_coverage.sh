#!/usr/bin/env bash
set -euo pipefail
# Real Worker process with an explicitly memory-only Store mock; no remote load.
bcs_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cov_runtime="$bcs_dir/target/cov-e2e/llvm-cov-target"
mkdir -p "$cov_runtime" "$bcs_dir/target/tmp"
printf '%s\n' '{"status":"failed","checks":[]}' > "$bcs_dir/target/cov-e2e/worker_e2e.json"
python3 - "$cov_runtime" <<'PYTHON'
from pathlib import Path
import sys
for profile in Path(sys.argv[1]).glob("worker-*.profraw"):
    profile.unlink()
PYTHON
case "${1:-}" in
  "") bash "$bcs_dir/scripts/build_e2e_companions.sh" worker ;;
  --run-only) ;;
  *) echo "usage: $0 [--run-only]" >&2; exit 2 ;;
esac
if [[ ! -x "$cov_runtime/debug/bcs-worker-e2e" ]]; then
  echo "missing instrumented worker companion executable" >&2
  exit 1
fi
LLVM_PROFILE_FILE="$cov_runtime/worker-%m-%p.profraw" \
  python3 "$bcs_dir/scripts/e2e-test/worker_runtime_story.py" \
    --binary "$cov_runtime/debug/bcs-worker-e2e" \
    --report "$bcs_dir/target/cov-e2e/worker_e2e.json"
