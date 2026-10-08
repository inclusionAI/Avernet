#!/usr/bin/env bash
# scripts/check-protocol-compat.sh — TEST-2
# bcs-protocol 必须有指定类别的 wire 兼容测试
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BCS_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

TEST_DIR="$BCS_ROOT/crates/contracts/bcs-protocol/tests"

if [[ ! -d "$TEST_DIR" ]]; then
  echo "FAIL [TEST-2]: $TEST_DIR 不存在（协议测试目录缺失，不允许静默通过）"
  exit 1
fi

required=(
  "coordination_contract_alignment"
  "domain_contracts"
  "provider_bot_connection_mode_dto"
  "provider_bot_webhook_dto"
)

fail=0
for file in "${required[@]}"; do
  if ! ls "$TEST_DIR"/"$file".rs >/dev/null 2>&1; then
    echo "FAIL [TEST-2]: missing $file.rs (protocol wire contract test)"
    fail=1
  fi
done

exit $fail
