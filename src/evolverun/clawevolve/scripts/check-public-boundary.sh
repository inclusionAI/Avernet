#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

FORBIDDEN='(?i:alipay\.com|antfin\.com|code\.alipay\.com|reg\.docker\.alibaba-inc\.com|@alipay/|clawweb|\b(?:baas|arca|ais)\b|server/repositories/)'

if rg -n --hidden \
  --glob '!dist/**' \
  --glob '!node_modules/**' \
  "$FORBIDDEN" src tests README.md package.json; then
  echo "Clawevolve public boundary check failed" >&2
  exit 1
fi

echo "Clawevolve public boundary check passed"
