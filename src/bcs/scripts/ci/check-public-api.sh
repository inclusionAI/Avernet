#!/usr/bin/env bash
# scripts/check-public-api.sh — API-1
# crates/service-api/* 的 pub 项变更必须升版本 + PR 含 propagation analysis
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/ci-env.sh"
BASE=$(resolve_bcs_base_ref .)
PR_BODY_FILE=${PR_BODY_FILE:-/tmp/pr_body.txt}
BCS_PREFIX=$(git rev-parse --show-prefix 2>/dev/null || true)

if ! command -v cargo >/dev/null; then
  echo "SKIP [API-1]: cargo 不可用"; exit 77
fi

if ! cargo public-api --version >/dev/null 2>&1; then
  echo "SKIP [API-1]: cargo-public-api 未安装（建议 cargo install cargo-public-api）"
  exit 77
fi

fail=0

for crate in bcs-config-api bcs-protocol bcs-service-api; do
  crate_path="crates/service-api/$crate"
  [[ -d "$crate_path" ]] || continue

  # 取 diff
  diff=$(cargo public-api --diff-git-checkouts "$BASE" HEAD -p "$crate" 2>/dev/null || true)
  [[ -z "$diff" ]] && continue

  echo "[API-1] $crate 的公开 API 发生变化："
  echo "$diff" | sed 's/^/  /'

  has_breaking=false
  if echo "$diff" | grep -qE '^-'; then
    has_breaking=true
  fi

  # 取当前版本和 base 版本
  current=$(grep -E '^version[[:space:]]*=' "$crate_path/Cargo.toml" | head -1 | sed -E 's/.*"(.+)".*/\1/')
  base=$(git show "$BASE:${BCS_PREFIX}${crate_path}/Cargo.toml" 2>/dev/null | grep -E '^version[[:space:]]*=' | head -1 | sed -E 's/.*"(.+)".*/\1/' || true)

  if [[ -z "$base" ]]; then
    echo "[API-1] 无法读取 base 版本，跳过版本号检查"
    continue
  fi

  cur_major="${current%%.*}"
  base_major="${base%%.*}"

  if $has_breaking && [[ "$cur_major" == "$base_major" ]]; then
    echo "FAIL [API-1]: ${crate} has breaking public API changes but major version did not change (${base} -> ${current})"
    fail=1
  fi

  if [[ "$current" == "$base" ]]; then
    echo "FAIL [API-1]: ${crate} has public API changes but version did not change (${base} -> ${current})"
    fail=1
  fi

  # PR 必须含 propagation analysis
  if [[ -f "$PR_BODY_FILE" ]]; then
    if ! grep -qiE '影响的[[:space:]]*consumer' "$PR_BODY_FILE"; then
      echo "FAIL [API-1]: $crate 修改了 pub API，PR 描述缺 Propagation analysis"
      fail=1
    fi
  fi
done

exit $fail
