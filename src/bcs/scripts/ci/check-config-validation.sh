#!/usr/bin/env bash
# scripts/check-config-validation.sh — CFG-1
# 执行 bootstrap 配置校验相关测试，并检查 P0 行为是否有显式覆盖。
set -euo pipefail

if ! command -v cargo >/dev/null; then
  echo "SKIP [CFG-1]: cargo 不可用"; exit 77
fi

if [[ ! -d crates/bootstrap/bcs ]]; then
  echo "SKIP [CFG-1]: crates/bootstrap/bcs 不存在"; exit 77
fi

fail=0

if ! cargo test -p bcs --lib config --quiet; then
  echo "FAIL [CFG-1]: bootstrap config/config_loader 单元测试失败"
  fail=1
fi

CONFIG_SCOPE=(crates/bootstrap/bcs/src/config.rs crates/bootstrap/bcs/src/config_loader.rs crates/bootstrap/bcs/tests)

if ! rg -q 'deny_unknown_fields|unknown_(key|field)|unknown key|unknown field' "${CONFIG_SCOPE[@]}" 2>/dev/null; then
  echo "FAIL [CFG-1]: 缺未知配置 key 拒绝的显式测试或 serde deny_unknown_fields 覆盖（CI.enforce.bcs.md §E）"
  fail=1
fi

if ! rg -q 'missing_.*(required|field)|required_.*missing|missing required|required field' "${CONFIG_SCOPE[@]}" 2>/dev/null; then
  echo "FAIL [CFG-1]: 缺必填字段缺失拒绝的显式测试覆盖（CI.enforce.bcs.md §E）"
  fail=1
fi

if ! rg -q 'invalid_.*enum|enum_.*invalid|非法.*enum|enum.*非法' "${CONFIG_SCOPE[@]}" 2>/dev/null; then
  echo "FAIL [CFG-1]: 缺非法 enum 值拒绝的显式测试覆盖（CI.enforce.bcs.md §E）"
  fail=1
fi

[[ $fail -eq 0 ]] && echo "PASS [CFG-1]: bootstrap config validation tests passed"
exit $fail
