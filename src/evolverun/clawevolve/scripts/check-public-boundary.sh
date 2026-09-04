#!/usr/bin/env bash
set -Eeuo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if grep -RIE '(AKIA|BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY|(^|[^A-Za-z0-9_])sk-[A-Za-z0-9_-]{20,})' "$root" --exclude-dir=node_modules --exclude-dir=dist --exclude=check-public-boundary.sh; then
  echo "credential pattern found" >&2
  exit 1
fi
echo "Clawevolve credential boundary check passed"
