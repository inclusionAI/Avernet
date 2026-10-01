#!/usr/bin/env bash
# R10: service crates must not depend directly on storage plugins.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BCS_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

if ! command -v rg >/dev/null; then
  echo "SKIP [R10]: ripgrep not installed"
  exit 77
fi

targets=(
  "crates/services/bcs-bot/src"
  "crates/services/bcs-group/src"
  "crates/services/bcs-friend/src"
  "crates/services/bcs-relation/src"
  "crates/services/bcs-proposal/src"
)

# Prevalidate: every expected target tree must exist. A missing tree is a
# broken scan, not a clean pass.
for target in "${targets[@]}"; do
  if [[ ! -d "$BCS_ROOT/$target" ]]; then
    echo "FAIL [R10]: expected target directory missing: $target"
    exit 1
  fi
done

# rg exit codes: 0 = match found, 1 = no match, >1 = scan error.
# Run from BCS_ROOT so the relative target paths never depend on the caller's cwd.
set +e
rg_output=$(cd "$BCS_ROOT" && rg -n 'DbPlugin|CachePlugin' "${targets[@]}" 2>&1)
rg_rc=$?
set -e

if [[ $rg_rc -gt 1 ]]; then
  echo "$rg_output"
  echo "FAIL [R10]: rg scan error (exit $rg_rc); refusing to report a clean pass"
  exit 1
fi

if [[ $rg_rc -eq 0 ]]; then
  echo "$rg_output"
  echo "FAIL [R10]: service crates import or name DbPlugin/CachePlugin directly"
  echo "          Move storage plugin usage into the matching crates/services/*-store crate."
  exit 1
fi

echo "PASS [R10]: service crates do not reference DbPlugin/CachePlugin directly"
