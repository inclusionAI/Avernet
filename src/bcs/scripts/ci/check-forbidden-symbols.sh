#!/usr/bin/env bash
# scripts/check-forbidden-symbols.sh — LINT-3a / LINT-3b
# 禁 transport 框架（CI.enforce.md §C） + 禁 env 访问（§D）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASELINE="$SCRIPT_DIR/baseline/forbidden-symbols.txt"

if ! command -v rg >/dev/null; then
  echo "SKIP [LINT-3]: ripgrep 未安装"; exit 77
fi

fail=0

# ─── baseline 查询（bash 3.2 兼容，不用 declare -A）───
in_baseline() {
  local key=$1
  [[ -f "$BASELINE" ]] || return 1
  grep -qF "$key" "$BASELINE" 2>/dev/null
}
baseline_note() {
  local key=$1
  [[ -f "$BASELINE" ]] || return
  grep -F "$key" "$BASELINE" 2>/dev/null | head -1 | cut -f2-
}

# ─── 检查函数 ───
# 用法：check_symbol <SCOPE> <SYMBOL> <DOC_REF>
check_symbol() {
  local scope=$1 sym=$2 doc=$3
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    file=$(echo "$line" | cut -d: -f1)
    lineno=$(echo "$line" | cut -d: -f2)
    key="${file}::${sym}"
    if in_baseline "$key"; then
      echo "BASELINE [LINT-3]: ${file}:${lineno} ${sym} - $(baseline_note "$key")"
      continue
    fi
    echo "FAIL [LINT-3]: ${file}:${lineno} matched ${sym} (${doc})"
    fail=1
  done < <(rg --no-heading -n -F "$sym" "$scope" \
    --glob '!crates/services/bcs-config/**' \
    --glob '!**/tests/**' 2>/dev/null || true)
}

# ─── LINT-3b: env 访问 ───
# 范围：crates/service-api/ 全域 + crates/services/ 全域
# 允许：crates/bootstrap/、crates/services/bcs-config/、crates/tools/、tests
for scope in crates/service-api crates/services; do
  [[ -d "$scope" ]] || continue
  # services/bcs-config 是 loader，允许；用 --glob 排除
  check_symbol "$scope" "std::env::var" "CI.enforce.md §D / CONTEXT.md §2"
  check_symbol "$scope" "std::env::vars" "CI.enforce.md §D / CONTEXT.md §2"
  check_symbol "$scope" "dotenv" "CI.enforce.md §D / CONTEXT.md §2"
done

# ─── LINT-3a: Cargo.toml 禁用依赖 ───
forbidden_deps=(axum tonic hyper reqwest tokio-tungstenite sqlx redis layotto)
for scope in crates/service-api; do
  [[ -d "$scope" ]] || continue
  for dep in "${forbidden_deps[@]}"; do
    while IFS= read -r line; do
      file=$(echo "$line" | cut -d: -f1)
      lineno=$(echo "$line" | cut -d: -f2)
      key="${file}::${dep}"
      if in_baseline "$key"; then
        echo "BASELINE [LINT-3]: ${file}:${lineno} dep ${dep} - $(baseline_note "$key")"
        continue
      fi
      echo "FAIL [LINT-3]: ${file}:${lineno} Cargo.toml depends on ${dep} (CI.enforce.md section C)"
      fail=1
    done < <(rg --no-heading -n "^${dep}\s*=" "$scope" --glob 'Cargo.toml' 2>/dev/null || true)
  done
done

exit $fail
