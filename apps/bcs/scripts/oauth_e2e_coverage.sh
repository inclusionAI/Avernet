#!/usr/bin/env bash
set -euo pipefail
# OAuth companion uses real BCS processes and provider clients. Only remote
# providers are served locally via an allowlisted, non-forwarding HTTPS stub.
bcs_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cov_runtime="$bcs_dir/target/cov-e2e/llvm-cov-target"
mkdir -p "$cov_runtime" "$bcs_dir/target/tmp"
# Only our companion profiles are removed; shared-stack/caller evidence stays.
python3 - "$cov_runtime" <<'PYTHON'
from pathlib import Path
import sys
for profile in Path(sys.argv[1]).glob("oauth-*.profraw"):
    profile.unlink()
PYTHON
# Never allow a prior successful artifact to survive a build/startup failure.
printf '%s\n' '{"status":"failed","scenarios":[],"checks":[]}' > "$bcs_dir/target/cov-e2e/oauth_e2e.json"
case "${1:-}" in
  "") bash "$bcs_dir/scripts/build_e2e_companions.sh" oauth ;;
  --run-only) ;; # Supervisor already built both targets from this checkout.
  *) echo "usage: $0 [--run-only]" >&2; exit 2 ;;
esac
if [[ ! -x "$cov_runtime/debug/bcs-oauth-e2e" ]]; then
  echo "missing instrumented oauth companion executable" >&2
  exit 1
fi
LLVM_PROFILE_FILE="$cov_runtime/oauth-%m-%p.profraw" \
  python3 "$bcs_dir/scripts/e2e-test/oauth/story.py" \
    --binary "$cov_runtime/debug/bcs-oauth-e2e" \
    --report "$bcs_dir/target/cov-e2e/oauth_e2e.json"
