#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

FORBIDDEN='(?i:alipay\.com|antfin\.com|code\.alipay\.com|reg\.docker\.alibaba-inc\.com|@alipay/|clawweb|\b(?:baas|arca|ais)\b|server/repositories/)'
SECRET_PATTERN='(?i:(?:access|secret)[_-]?key\s*[:=]\s*[A-Za-z0-9_+/=-]{16,}|BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY|AKIA[0-9A-Z]{16})'

scan_targets=(src tests README.md package.json)
for optional_path in skills runtime config; do
  [ ! -e "$optional_path" ] || scan_targets+=("$optional_path")
done

if rg -n --hidden \
  --glob '!dist/**' \
  --glob '!node_modules/**' \
  "$FORBIDDEN" "${scan_targets[@]}"; then
  echo "Clawevolve public boundary check failed" >&2
  exit 1
fi

if rg -n --hidden \
  --glob '!dist/**' \
  --glob '!node_modules/**' \
  "$SECRET_PATTERN" "${scan_targets[@]}"; then
  echo "Clawevolve secret boundary check failed" >&2
  exit 1
fi

echo "Clawevolve public boundary check passed"
