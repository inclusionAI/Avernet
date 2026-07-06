#!/usr/bin/env bash
# scripts/check-waivers.sh — WAIVER-1
# 校验 docs/waivers/ 下所有 waiver 文件格式完整、未过期
set -euo pipefail

WAIVER_DIR=docs/waivers

if [[ ! -d "$WAIVER_DIR" ]]; then
  echo "SKIP [WAIVER-1]: $WAIVER_DIR 不存在"; exit 77
fi

required_fields=(rule owner reason risk compensating_controls expiry removal_plan)
today=$(date -u +%Y-%m-%d)

fail=0
warn=0

for f in "$WAIVER_DIR"/*.md; do
  [[ -f "$f" ]] || continue
  name=$(basename "$f")

  # 必填字段
  for field in "${required_fields[@]}"; do
    if ! grep -qE "^${field}:" "$f"; then
      echo "FAIL [WAIVER-1]: ${name} missing field \"${field}\""
      fail=1
    fi
  done

  # 过期检查
  expiry=$(grep -E '^expiry:' "$f" | head -1 | sed -E 's/expiry:[[:space:]]*//; s/[[:space:]]*$//')
  if [[ -n "$expiry" ]]; then
    if [[ "$expiry" < "$today" ]]; then
      # 超期天数
      diff_days=$(( ($(date -d "$today" +%s 2>/dev/null || date -j -f %Y-%m-%d "$today" +%s) - $(date -d "$expiry" +%s 2>/dev/null || date -j -f %Y-%m-%d "$expiry" +%s)) / 86400 ))
      if (( diff_days > 30 )); then
        echo "FAIL [WAIVER-1]: ${name} expired ${diff_days} days ago (expiry: ${expiry}), over 30-day grace period"
        fail=1
      else
        echo "WARN [WAIVER-1]: ${name} expired ${diff_days} days ago (expiry: ${expiry}); renew or remove it"
        warn=$((warn + 1))
      fi
    fi
  fi
done

[[ $warn -gt 0 ]] && echo "[WAIVER-1] $warn 条 waiver 接近或已过期"
exit $fail
