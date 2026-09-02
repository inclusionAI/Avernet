#!/usr/bin/env bash
set -Eeuo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
pattern='alipay\.(com|net)|antgroup|antfin|code\.alipay|reg\.docker\.alibaba-inc|/mnt/|\\/mnt\\/|/home/admin|/Users/|antsys-|antchat|GLM-|Layotto-MIST|other_manual_'
if grep -RIE "$pattern" "$root" --exclude-dir=node_modules --exclude-dir=dist --exclude=COPY_MANIFEST.tsv --exclude=check-public-boundary.sh; then
  echo "public boundary check failed" >&2
  exit 1
fi
if grep -RIE '(AKIA|BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY|sk-[A-Za-z0-9_-]{20,})' "$root" --exclude-dir=node_modules --exclude-dir=dist --exclude=check-public-boundary.sh; then
  echo "credential pattern found" >&2
  exit 1
fi
echo "Clawevolve public boundary check passed"
