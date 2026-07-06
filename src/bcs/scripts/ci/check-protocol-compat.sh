#!/usr/bin/env bash
# scripts/check-protocol-compat.sh — TEST-2
# bcs-protocol 必须有指定类别的 wire 兼容测试
set -euo pipefail

TEST_DIR=crates/service-api/bcs-protocol/tests

if [[ ! -d "$TEST_DIR" ]]; then
  echo "SKIP [TEST-2]: $TEST_DIR 不存在"; exit 77
fi

required=(
  "frame_v1_v2_fixture"
  "frame_round_trip"
  "version_matrix"
  "error_codes_stable"
  "deprecated_payload_readable"
)

fail=0
for cat in "${required[@]}"; do
  # 允许 protocol_compat_<cat>.rs 或 protocol_compat_<cat>_xxx.rs
  if ! ls "$TEST_DIR"/protocol_compat_"$cat"*.rs >/dev/null 2>&1; then
    echo "FAIL [TEST-2]: missing protocol_compat_${cat}*.rs (wire compatibility ${cat})"
    fail=1
  fi
done

exit $fail
