#!/usr/bin/env bash
set -Eeuo pipefail
modules_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
roots=(
  "$modules_root/clawevolve"
  "$modules_root/clawinsight"
  "$modules_root/workflow"
)
credential_pattern='(AKIA|ASIA|LTAI)[A-Z0-9]{12,}|BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY|(^|[^A-Za-z0-9_])sk-[A-Za-z0-9_-]{20,}|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}|DEFAULT_[A-Z0-9_]*(TOKEN|SECRET|API_KEY)[[:space:]]*=[[:space:]]*"[^"]{12,}"|huamei_api_token.{0,100}[A-Za-z0-9+/]{40,}'
if grep -RIE \
  --exclude-dir=node_modules \
  --exclude-dir=dist \
  --exclude-dir=__tests__ \
  --exclude='*.test.ts' \
  --exclude=check-public-boundary.sh \
  -e "$credential_pattern" \
  "${roots[@]}"; then
  echo "credential pattern found" >&2
  exit 1
fi
echo "Avernet evolverun credential boundary check passed"
