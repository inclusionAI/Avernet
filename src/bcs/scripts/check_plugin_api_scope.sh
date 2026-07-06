#!/usr/bin/env bash
# Ensures services/* do not depend on the sample plugin-api trait during
# first-round migration. Owner follow-up wires services through plugin-api
# deliberately; the guard will be relaxed then.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BCS_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

HITS=$(grep -rnE '^use bcs_cache_api::|^use bcs_db_api::' \
    --include='*.rs' \
    "$BCS_ROOT/crates/services" 2>/dev/null | grep -v '^//' || true)

if [ -n "$HITS" ]; then
    echo "ERROR: services/* should not use bcs_cache_api / bcs_db_api in first round:"
    echo "$HITS"
    exit 1
fi

echo "OK: plugin-api sample crates are isolated from services/*"
