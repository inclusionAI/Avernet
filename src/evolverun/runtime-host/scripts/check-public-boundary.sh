#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

FORBIDDEN='(?i:alipay\.com|antfin\.com|code\.alipay\.com|reg\.docker\.alibaba-inc\.com|@alipay/|antsys[-_]|clawweb)'
SECRET_PATTERN='(?i:(?:access|secret)[_-]?key\s*[:=]\s*[A-Za-z0-9_+/=-]{16,}|BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY|AKIA[0-9A-Z]{16})'

if rg -n --hidden --glob '!dist/**' --glob '!node_modules/**' \
  "$FORBIDDEN" src tests README.md package.json; then
  echo "Runtime host public boundary check failed" >&2
  exit 1
fi

if rg -n --hidden --glob '!dist/**' --glob '!node_modules/**' \
  "$SECRET_PATTERN" src tests README.md package.json; then
  echo "Runtime host secret boundary check failed" >&2
  exit 1
fi

echo "Runtime host public boundary check passed"
