#!/usr/bin/env bash
# Isolated negative tests for the runtime-store boundary; no real source is edited.
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fixture=$(mktemp -d)
trap 'rm -rf "$fixture"' EXIT
mkdir -p "$fixture/scripts/ci"
cp "$script_dir/../check-store-boundaries.sh" "$fixture/scripts/ci/"
for crate in bot group friend relation; do
  mkdir -p "$fixture/crates/services/bcs-$crate/src"
done
checker="$fixture/scripts/ci/check-store-boundaries.sh"
bash "$checker" >/dev/null
probe="$fixture/crates/services/bcs-bot/src/probe.rs"
for symbol in StorePlugin KvStorePlugin JsonStorePlugin ZSetStorePlugin StreamStorePlugin bcs_store_api; do
  printf 'use %s;\n' "$symbol" > "$probe"
  if bash "$checker" > "$fixture/output" 2>&1; then
    echo "FAIL: boundary accepted $symbol"
    exit 1
  fi
  grep -q 'FAIL \[R10\]' "$fixture/output"
done
rm "$probe"
bash "$checker" >/dev/null
echo 'PASS: runtime store symbols are rejected in core services'
