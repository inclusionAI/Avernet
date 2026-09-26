#!/usr/bin/env bash
# scripts/arch-check.sh
# 统一入口：跑所有架构检查。CI 调用这一个脚本即可。
# 各检查脚本独立，可单独运行。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BCS_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$BCS_ROOT"

# 颜色（CI 环境也支持）
RED=$'\033[0;31m'; GRN=$'\033[0;32m'; YLW=$'\033[0;33m'; NC=$'\033[0m'

declare -a CHECKS=(
  "check-deps.sh:DEP-1~8:18"
  "check-import-rules.sh:LINT-1:10"
  "check-port-purity.sh:LINT-2:5"
  "check-forbidden-symbols.sh:LINT-3:12"
  "check-config-validation.sh:CFG-1:10"
  "check-store-boundaries.sh:R10:10"
  "check-interceptor-chain.sh:R12:8"
  "check-trait-naming.sh:LINT-4:3"
  "check-conformance-entries.sh:TEST-1:14"
  "check-protocol-compat.sh:TEST-2:10"
  "check-pr-gate.sh:PR-1:6"
  "check-waivers.sh:WAIVER-1:3"
  "check-public-api.sh:API-1:6"
  "check-r25-conformance.sh:R25:10"
  "check-baseline-not-growing.sh:BASELINE:3"
)

total=0; passed=0; failed=0; skipped=0
total_weight=0; passed_weight=0; failed_weight=0; skipped_weight=0
failed_ids=()

for entry in "${CHECKS[@]}"; do
  IFS=: read -r script id weight <<< "$entry"
  total=$((total + 1))
  total_weight=$((total_weight + weight))

  echo
  echo "═══ Running $id ($script, weight=$weight) ═══"
  if bash "$SCRIPT_DIR/$script"; then
    passed=$((passed + 1))
    passed_weight=$((passed_weight + weight))
    echo "${GRN}PASS [$id]${NC}"
  else
    rc=$?
    if [[ $rc -eq 77 ]]; then
      skipped=$((skipped + 1))
      skipped_weight=$((skipped_weight + weight))
      echo "${YLW}SKIP [$id]${NC}"
    else
      failed=$((failed + 1))
      failed_weight=$((failed_weight + weight))
      failed_ids+=("$id")
      echo "${RED}FAIL [$id]${NC}"
    fi
  fi
done

echo
echo "═══════════════════════════════════════════"
echo "Total: $total  Pass: $passed  Skip: $skipped  Fail: $failed"
echo "Weights: total=$total_weight  pass=$passed_weight  skip=$skipped_weight  fail=$failed_weight"
scored=$((passed_weight + failed_weight))
if [[ $scored -gt 0 ]]; then
  score=$(((passed_weight * 100 + scored / 2) / scored))
else
  score=100
fi
echo "Score: ${score}/100 (constitution-weighted, skip excluded)"
[[ $failed -gt 0 ]] && echo "${RED}Failed: ${failed_ids[*]}${NC}"
echo "═══════════════════════════════════════════"

exit $failed
