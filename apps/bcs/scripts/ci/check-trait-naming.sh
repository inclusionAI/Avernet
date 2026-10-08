#!/usr/bin/env bash
# scripts/check-trait-naming.sh — LINT-4
# application/core → Service 结尾；port → Port 结尾
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASELINE="$SCRIPT_DIR/baseline/trait-naming.txt"

SRC=crates/service-api/bcs-service-api/src

if [[ ! -d "$SRC/application" || ! -d "$SRC/core" || ! -d "$SRC/port" ]]; then
  echo "SKIP [LINT-4]: application/core/port 未就位（等 P2）"; exit 77
fi

if ! command -v rg >/dev/null; then
  echo "SKIP [LINT-4]: ripgrep 未安装"; exit 77
fi

in_baseline() {
  local name=$1
  [[ -f "$BASELINE" ]] || return 1
  grep -qE "^${name}([[:space:]]|$)" "$BASELINE" 2>/dev/null
}

baseline_note() {
  local name=$1
  [[ -f "$BASELINE" ]] || return
  grep -E "^${name}([[:space:]]|$)" "$BASELINE" 2>/dev/null | head -1 | cut -f2-
}

fail=0

check_dir() {
  local dir=$1 expect=$2
  while IFS= read -r line; do
    file=$(echo "$line" | cut -d: -f1)
    lineno=$(echo "$line" | cut -d: -f2)
    content=$(echo "$line" | cut -d: -f3-)
    name=$(echo "$content" | sed -nE 's/.*pub trait ([A-Za-z0-9_]+).*/\1/p')
    [[ -z "$name" ]] && continue

    if [[ ! "$name" =~ ${expect}$ ]]; then
      if in_baseline "$name"; then
        echo "BASELINE [LINT-4]: ${file}:${lineno} trait ${name} - $(baseline_note "$name")"
        continue
      fi
      echo "FAIL [LINT-4]: ${file}:${lineno} trait \"${name}\" should end with ${expect} (CONTEXT.md section 3)"
      fail=1
    fi
  done < <(rg --no-heading -n '^pub trait \w+' "$dir" 2>/dev/null || true)
}

check_dir "$SRC/application" "Service"
check_dir "$SRC/core" "Service"
check_dir "$SRC/port" "Port"

exit $fail
