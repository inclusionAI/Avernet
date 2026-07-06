#!/usr/bin/env bash
# R10: service crates must not depend directly on storage plugins.
set -euo pipefail

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

if rg -n 'DbPlugin|CachePlugin' "${targets[@]}"; then
  echo "FAIL [R10]: service crates import or name DbPlugin/CachePlugin directly"
  echo "          Move storage plugin usage into the matching crates/services/*-store crate."
  exit 1
fi

echo "PASS [R10]: service crates do not reference DbPlugin/CachePlugin directly"
