#!/usr/bin/env bash
# R12: outbound dispatch must depend on MessageInterceptor chains, not a
# concrete SecurityInterceptor singleton.
set -euo pipefail

if ! command -v rg >/dev/null; then
  echo "SKIP [R12]: ripgrep not installed"
  exit 77
fi

if rg -n 'Option<SecurityInterceptor>|security_interceptor:\s*Option' crates/services crates/bootstrap -g '*.rs'; then
  echo "FAIL [R12]: concrete SecurityInterceptor singleton found"
  echo "          Use InterceptorChain / MessageInterceptor instead."
  exit 1
fi

echo "PASS [R12]: outbound dispatch is not wired to a SecurityInterceptor singleton"
