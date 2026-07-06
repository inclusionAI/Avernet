#!/usr/bin/env bash
# scripts/check-port-purity.sh — LINT-2
# port/ 不引用 application/ 或 core/ 的 pub 类型
set -euo pipefail

SRC=crates/service-api/bcs-service-api/src

if [[ ! -d "$SRC/port" ]]; then
  echo "SKIP [LINT-2]: $SRC/port 未就位（等 bcs-service-api/src 重组完成）"
  exit 77
fi

if ! command -v rg >/dev/null; then
  echo "SKIP [LINT-2]: ripgrep 未安装"; exit 77
fi

fail=0

# 取出 application/ 和 core/ 下所有 pub 类型名
types=$(rg --no-heading '^pub (struct|enum|trait|type) (\w+)' "$SRC/application" "$SRC/core" -or '$2' 2>/dev/null | sort -u || true)

if [[ -z "$types" ]]; then
  echo "SKIP [LINT-2]: application/core 下未找到 pub 类型"
  exit 77
fi

for ty in $types; do
  # -w 整词匹配；忽略 application/ 和 core/ 内部的自引用
  matches=$(rg --no-heading -nw "$ty" "$SRC/port/" 2>/dev/null || true)
  if [[ -n "$matches" ]]; then
    echo "FAIL [LINT-2]: port/ references ${ty} (expected std / third-party / types / bcs_protocol)"
    echo "$matches"
    fail=1
  fi
done

# 升级提示：grep 版可能误报，需要严格版时切到 syn 解析
if [[ $fail -eq 0 ]]; then
  echo "PASS [LINT-2]: port/ 不引用 application/core 类型"
fi

exit $fail
